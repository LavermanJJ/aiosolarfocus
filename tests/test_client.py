"""The client: what a refresh reads, and what it does when part of one fails."""

from __future__ import annotations

import pytest

from aiosolarfocus.client import SolarfocusClient
from aiosolarfocus.components import ComponentId
from aiosolarfocus.components.boiler import Boiler
from aiosolarfocus.components.heat_pump import HeatPump
from aiosolarfocus.components.heating_circuit import HeatingCircuit
from aiosolarfocus.config import ComponentKey, SolarfocusConfig
from aiosolarfocus.const import ApiVersion, RegisterKind, Systems
from aiosolarfocus.enums import HeatingCircuitCooling, HeatingCircuitMode
from aiosolarfocus.exceptions import IllegalAddressError, SolarfocusConnectionError
from aiosolarfocus.testing import FakeController

pytestmark = pytest.mark.asyncio
INPUT = RegisterKind.INPUT
HOLDING = RegisterKind.HOLDING


def vampair(**overrides: object) -> SolarfocusConfig:
    settings: dict[str, object] = {
        "host": "controller",
        "system": Systems.VAMPAIR,
        "api_version": ApiVersion.V_26_020,
        "heating_circuits": 1,
        "buffers": 1,
        "boilers": 1,
        "photovoltaic": True,
    }
    settings.update(overrides)
    return SolarfocusConfig(**settings)  # type: ignore[arg-type]


def build(config: SolarfocusConfig | None = None, values: dict[tuple[RegisterKind, int], int] | None = None) -> tuple[SolarfocusClient, FakeController]:
    config = config or vampair()
    fake = FakeController.for_config(config, values)
    return SolarfocusClient(config, transport=fake), fake


def biomass(system: Systems = Systems.PELLETELEGANCE, **overrides: object) -> SolarfocusConfig:
    settings: dict[str, object] = {
        "host": "controller",
        "system": system,
        "api_version": ApiVersion.V_26_020,
        "heating_circuits": 1,
        "buffers": 1,
        "biomass_boiler": True,
    }
    settings.update(overrides)
    return SolarfocusConfig(**settings)  # type: ignore[arg-type]


async def test_a_refresh_reads_every_component_and_decodes_it() -> None:
    client, fake = build(values={(INPUT, 1100): 304, (INPUT, 2300): 285, (INPUT, 500): 512})
    result = await client.update()

    assert result.ok
    assert client.heating_circuits[0].supply_temperature == 30.4
    assert client.heat_pump.supply_temperature == 28.5
    assert client.boilers[0].temperature == 51.2
    assert result.round_trips == fake.round_trips


async def test_a_refresh_costs_the_reads_the_plan_says_it_will() -> None:
    client, fake = build()
    await client.update()
    assert fake.round_trips == client.read_plan.round_trips


async def test_only_the_named_components_are_read() -> None:
    client, fake = build()
    await client.update(components=[ComponentId.BOILERS])
    assert {read[1] for read in fake.reads} == {500, 32000}


async def test_components_are_plain_attributes() -> None:
    client, _ = build(vampair(heating_circuits=3))
    assert len(client.heating_circuits) == 3
    assert isinstance(client.heating_circuits[0], HeatingCircuit)
    assert isinstance(client.heat_pump, HeatPump)
    assert isinstance(client.boilers[0], Boiler)


async def test_a_component_the_installation_does_not_have_reads_as_nothing() -> None:
    """A system with no solar is an answer, not a mistake."""
    client, _ = build(vampair(solar=0))
    assert client.solar == []
    assert client.biomass_boiler is None


async def test_an_attribute_that_is_not_a_component_still_raises() -> None:
    client, _ = build()
    with pytest.raises(AttributeError, match="nonsense"):
        _ = client.nonsense


async def test_one_refused_range_fails_only_the_components_in_it() -> None:
    """The predecessor returned on the first failing read, taking out the component.

    Worse, its component manager returned on the first failing *instance*, so
    buffer 3 failing hid buffer 4 failing.
    """
    config = vampair()
    client, fake = build(config, values={(INPUT, 1100): 304})
    fake.unmap(INPUT, 2300)

    result = await client.update()

    assert not result.ok
    heat_pump = ComponentKey(ComponentId.HEAT_PUMP, 1)
    assert set(result.failed) == {heat_pump}
    assert isinstance(result.failed[heat_pump], IllegalAddressError)
    # Everything else still updated.
    assert client.heating_circuits[0].supply_temperature == 30.4
    assert client.heating_circuits[0].available
    assert not client.heat_pump.available


async def test_a_failed_component_keeps_the_readings_it_had() -> None:
    """Stale beats blank for a heating system: one dropped read should not empty a graph."""
    config = vampair()
    client, fake = build(config, values={(INPUT, 500): 512})
    await client.update()
    assert client.boilers[0].temperature == 51.2

    fake.unmap(INPUT, 500)
    result = await client.update()

    assert not result.ok
    assert client.boilers[0].temperature == 51.2
    assert client.boilers[0].available is False
    assert client.boilers[0].last_error is not None


async def test_a_dropped_connection_raises_instead_of_failing_every_component() -> None:
    """A socket dropping part way through says nothing about any component.

    Reporting it per component would grey out an arbitrary tail of the heating
    system - whichever components happened to come after it in the plan.
    """
    client, fake = build()
    fake.fail_with = SolarfocusConnectionError("the socket dropped")
    with pytest.raises(SolarfocusConnectionError):
        await client.update()


async def test_the_client_connects_itself_before_reading() -> None:
    client, fake = build()
    assert not client.connected
    await client.update()
    assert fake.connected


async def test_the_context_manager_connects_and_disconnects() -> None:
    config = vampair()
    fake = FakeController.for_config(config)
    async with SolarfocusClient(config, transport=fake) as client:
        assert client.connected
        await client.update()
    assert not fake.connected


async def test_a_write_reaches_the_controller_and_is_reflected_at_once() -> None:
    client, fake = build()
    await client.connect()
    await client.heating_circuits[0].set_target_supply_temperature(45.0)

    assert fake.writes == [(HOLDING, 32600, (450,))]
    assert client.heating_circuits[0].target_supply_temperature == 45.0
    # No re-read of the whole component: the predecessor's callers did that after
    # every single write.
    assert fake.round_trips == 0


async def test_a_grouped_write_goes_out_as_one_group() -> None:
    client, fake = build()
    await client.connect()
    await client.heating_circuits[0].set_operating_state(
        mode=HeatingCircuitMode.AUTOMATIC,
        cooling=HeatingCircuitCooling.HEATING,
        target_supply_temperature=18.0,
    )
    assert fake.writes == [(HOLDING, 32600, (180,)), (HOLDING, 32602, (0,)), (HOLDING, 32603, (2,))]


async def test_a_negative_value_is_written_as_the_controller_expects_it() -> None:
    """The Home Assistant integration carried its own two's complement for this."""
    client, fake = build()
    await client.connect()
    await client.photovoltaic.set_smart_meter(-1500)
    assert fake.writes == [(HOLDING, 33407, (0xFA24,))]
    assert client.photovoltaic.smart_meter == -1500


async def test_an_open_sensor_channel_reads_as_nothing_rather_than_270_degrees() -> None:
    client, _ = build(values={(INPUT, 1901): 2700})
    await client.update()
    assert client.buffers[0].bottom_temperature is None
    assert client.buffers[0].raw(type(client.buffers[0]).bottom_temperature) == 2700


async def test_an_unwired_humidity_channel_reads_as_nothing_rather_than_negative() -> None:
    """Regression for home-assistant-solarfocus#237.

    Two real Pellet Elegance controllers read 65535 here and had it decode to
    -0.1%, a percentage with no legitimate negative reading the way -0.1 degC
    is a legitimate outdoor one.
    """
    client, _ = build(biomass(), values={(INPUT, 1102): 65535})
    await client.update()
    assert client.heating_circuits[0].humidity is None
    assert client.heating_circuits[0].raw(type(client.heating_circuits[0]).humidity) == -1


@pytest.mark.parametrize("address", [2406, 2407])
@pytest.mark.parametrize(("raw", "marker"), [(2**16 - 1, "-1"), (2**16 - 999, "-999")])
async def test_an_absent_boiler_percentage_reads_as_nothing_whichever_marker_it_is_given(address: int, raw: int, marker: str) -> None:
    """Regression for home-assistant-solarfocus#237.

    Both markers on both registers, because the controller picks the marker and
    the register only decides which sensor is missing: two Pellet Elegance
    controllers published -1% cleaning (2406) with a real ash container, and a
    Therminator 2 - configured as an ecotop, which is how its dump is filed -
    published -999% ash container (2407) with a real cleaning.
    """
    client, _ = build(biomass(), values={(INPUT, address): raw})
    await client.update()
    assert client.biomass_boiler is not None
    register = type(client.biomass_boiler).cleaning if address == 2406 else type(client.biomass_boiler).ash_container
    assert client.biomass_boiler.value_of(register) is None, f"{marker} at {address} leaked through"
    assert client.biomass_boiler.raw(register) == int(marker)


async def test_an_external_outdoor_temperature_nobody_wrote_reads_as_nothing() -> None:
    """Regression for home-assistant-solarfocus#237.

    All three controllers published -999.9 degC from holding 33406, twenty
    times below the -50 degC the same register refuses to be written past.
    """
    client, _ = build(biomass(), values={(HOLDING, 33406): 2**16 - 9999})
    await client.update()
    assert client.biomass_boiler is not None
    assert client.biomass_boiler.outdoor_temperature_external is None
    assert client.biomass_boiler.raw(type(client.biomass_boiler).outdoor_temperature_external) == -9999


async def test_a_flag_the_controller_left_unset_reads_as_nothing_rather_than_true() -> None:
    """Regression for home-assistant-solarfocus#237.

    Two controllers read -1 from holding 32003, and `bool(-1)` reported a
    circulation request that nobody had made.
    """
    client, _ = build(biomass(boilers=1), values={(HOLDING, 32003): 2**16 - 1})
    await client.update()
    assert client.boilers[0].circulation is None
    assert client.boilers[0].raw(Boiler.circulation) == -1


async def test_the_snapshot_gives_diagnostics_everything_without_reaching_inside() -> None:
    client, _ = build(values={(INPUT, 1100): 304})
    await client.update()
    snapshot = client.snapshot()
    assert snapshot["heating_circuits.1"]["supply_temperature"] == {
        "address": 1100,
        "kind": "input",
        "raw": 304,
        "value": 30.4,
        "unit": "°C",
    }
