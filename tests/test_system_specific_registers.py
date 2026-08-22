"""Registers only one system has, and the misreadings that follow from reading them anyway.

The controller answers a read that spans an unmapped address by packing the next
mapped register into the hole rather than padding it, so every value after the
gap shifts one position early - the right number of registers, the wrong names,
and nothing in the protocol to say so. See docs/protocol.md.

That makes a register the document assigns to one system a hazard for every
other system: a Pellet Elegance does not map 2409 or 2413, so a boiler read
spanning them reported its return flow temperature as 270.0 degC when the sensor
said 22.1 degC. See home-assistant-solarfocus issue #217.
"""

from __future__ import annotations

import csv
from importlib.resources import files

import pytest

from aiosolarfocus.client import SolarfocusClient
from aiosolarfocus.components import COMPONENTS, ComponentId
from aiosolarfocus.config import SolarfocusConfig
from aiosolarfocus.const import ApiVersion, RegisterKind, Systems
from aiosolarfocus.enums import BufferMode
from aiosolarfocus.layout import Layout
from aiosolarfocus.planner import plan
from aiosolarfocus.testing import FakeController

INPUT = RegisterKind.INPUT
NEWEST = ApiVersion.V_26_020

_SYSTEM_WORDS = {
    "vampair": Systems.VAMPAIR,
    "therminator": Systems.THERMINATOR,
    "ecotop": Systems.ECOTOP,
    "octoplus": Systems.OCTOPLUS,
    "pellet elegance": Systems.PELLETELEGANCE,
}


def _systems_named_by_the_document() -> dict[tuple[RegisterKind, int], set[Systems]]:
    """Addresses whose name or description names exactly one system."""
    text = (files("aiosolarfocus.data") / "registers.csv").read_text(encoding="utf-8")
    found: dict[tuple[RegisterKind, int], set[Systems]] = {}
    for row in csv.DictReader(text.splitlines()):
        blob = f"{row['Name']} {row['Description']}".lower()
        named = {system for word, system in _SYSTEM_WORDS.items() if word in blob}
        if len(named) == 1:
            kind = RegisterKind.INPUT if row["Register Type"] == "Input" else RegisterKind.HOLDING
            found[(kind, int(row["Register Address"]))] = named
    return found


def _claims() -> list[tuple[ComponentId, str, tuple[RegisterKind, int], frozenset[Systems]]]:
    """Every register, with the systems it is actually offered on."""
    rows = []
    for spec in COMPONENTS:
        for register in spec.component.declared():
            offered = frozenset(system for system in Systems if spec.available(NEWEST, system) and register.available(NEWEST, system))
            if not offered:
                continue
            input_base, holding_base = spec.bases(0, NEWEST)
            layout = Layout.resolve(spec.component, NEWEST, next(iter(offered)), input_base, holding_base)
            resolved = layout.by_name[register.name]
            rows.append((spec.id, register.name, (resolved.kind, resolved.address), offered))
    return rows


CLAIMS = _claims()


@pytest.mark.parametrize(("component_id", "name", "address", "offered"), CLAIMS, ids=[f"{c.value}-{n}" for c, n, _, _ in CLAIMS])
def test_a_register_the_document_gives_to_one_system_is_read_on_no_other(
    component_id: ComponentId,
    name: str,
    address: tuple[RegisterKind, int],
    offered: frozenset[Systems],
) -> None:
    """Reading one on a system that does not map it shifts every value after it.

    The document names the system in the register's own name or description -
    "Kesselbetriebsart therminator", "Buffer temperature X35 (therminator only)" -
    so this is checkable rather than a matter of taste.
    """
    named = _systems_named_by_the_document().get(address)
    if named is None:
        pytest.skip("the document names no single system for this address")
    where = f"{component_id.value}.{name} at {address[0].value} {address[1]}"
    offered_names = sorted(system.value for system in offered)
    named_names = sorted(system.value for system in named)
    assert offered <= named, f"{where} is offered on {offered_names}; the document gives it to {named_names}"


@pytest.mark.parametrize("system", [Systems.ECOTOP, Systems.PELLETELEGANCE])
def test_a_pellet_boiler_never_reads_the_therminator_and_octoplus_registers(system: Systems) -> None:
    """2409 and 2413 are unmapped on a Pellet Elegance, and 2411 and 2412 need not be."""
    config = SolarfocusConfig(host="c", system=system, api_version=NEWEST, heating_circuits=0, buffers=1, boilers=0)
    covered = {address for read in plan(config.layouts()).slices if read.kind is INPUT for address in read.addresses}
    assert covered.isdisjoint({2409, 2411, 2412, 2413})
    assert 2410 in covered, "the return flow temperature is this system's, and must still be read"


@pytest.mark.parametrize("system", [Systems.ECOTOP, Systems.PELLETELEGANCE, Systems.OCTOPLUS, Systems.VAMPAIR])
def test_only_a_therminator_reads_the_buffer_x35_sensor(system: Systems) -> None:
    config = SolarfocusConfig(host="c", system=system, api_version=NEWEST, heating_circuits=0, buffers=1, boilers=0)
    covered = {address for read in plan(config.layouts()).slices if read.kind is INPUT for address in read.addresses}
    assert 1902 not in covered


@pytest.mark.asyncio
async def test_a_pellet_elegance_reads_its_return_flow_temperature_and_not_its_neighbour() -> None:
    """The reported misreading: 270.0 degC where the sensor said 22.1 degC.

    Numbers are the ones read off the controller in
    home-assistant-solarfocus issue #217.
    """
    config = SolarfocusConfig(host="c", system=Systems.PELLETELEGANCE, api_version=NEWEST, heating_circuits=0, buffers=0, boilers=0)
    fake = FakeController.for_config(config, {(INPUT, 2400): 233, (INPUT, 2401): 1, (INPUT, 2410): 221, (INPUT, 2411): 2700})
    fake.unmap(INPUT, 2409, 2413)

    client = SolarfocusClient(config, transport=fake)
    result = await client.update()

    boiler = client.biomass_boiler
    assert result.ok, f"the boiler could not be read: {result.failed}"
    assert boiler.temperature == 23.3
    assert boiler.return_temperature == 22.1, "the return flow read its neighbour, one register early"
    assert not boiler.supports("boiler_operating_mode")
    assert not boiler.supports("octoplus_buffer_temperature_top")


@pytest.mark.asyncio
async def test_an_ecotop_buffer_reads_its_pump_and_not_its_status() -> None:
    """The other half of the same report.

    The screenshot showed "Ladepumpe: In Betrieb" when the pump was off - it was
    reading 1904, the status, because the read stepped over the unmapped 1902.
    """
    config = SolarfocusConfig(host="c", system=Systems.ECOTOP, api_version=NEWEST, heating_circuits=0, buffers=1, boilers=0)
    fake = FakeController.for_config(config, {(INPUT, 1900): 366, (INPUT, 1901): 323, (INPUT, 1903): 0, (INPUT, 1904): 1, (INPUT, 1905): 1})
    fake.unmap(INPUT, 1902)

    client = SolarfocusClient(config, transport=fake)
    result = await client.update()

    buffer = client.buffers[0]
    assert result.ok, f"the buffer could not be read: {result.failed}"
    assert buffer.top_temperature == 36.6
    assert buffer.pump is False, "the pump read the status register"
    assert buffer.state == 1
    assert buffer.mode is BufferMode.ALWAYS_ON
    assert not buffer.supports("x35_temperature")


@pytest.mark.asyncio
async def test_a_therminator_still_reads_everything_that_is_its_own() -> None:
    """The gating must not take these away from the system they belong to."""
    config = SolarfocusConfig(host="c", system=Systems.THERMINATOR, api_version=NEWEST, heating_circuits=0, buffers=1, boilers=0)
    fake = FakeController.for_config(config, {(INPUT, 1902): 300, (INPUT, 2409): 2, (INPUT, 2412): 1})
    client = SolarfocusClient(config, transport=fake)

    assert (await client.update()).ok
    assert client.buffers[0].x35_temperature == 30.0
    assert client.biomass_boiler.boiler_operating_mode == 2
    assert client.biomass_boiler.log_wood is True


@pytest.mark.asyncio
async def test_an_octoplus_still_reads_both_of_its_buffer_sensors() -> None:
    config = SolarfocusConfig(host="c", system=Systems.OCTOPLUS, api_version=NEWEST, heating_circuits=0, buffers=0, boilers=0)
    fake = FakeController.for_config(config, {(INPUT, 2410): 480, (INPUT, 2411): 620})
    client = SolarfocusClient(config, transport=fake)

    assert (await client.update()).ok
    assert client.biomass_boiler.octoplus_buffer_temperature_bottom == 48.0
    assert client.biomass_boiler.octoplus_buffer_temperature_top == 62.0
