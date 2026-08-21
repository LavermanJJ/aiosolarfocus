"""The configuration refuses what no controller could have, and says why."""

from __future__ import annotations

from dataclasses import fields

import pytest

from aiosolarfocus.components import COMPONENTS, ComponentId
from aiosolarfocus.config import ComponentKey, SolarfocusConfig, count_field_names
from aiosolarfocus.const import ApiVersion, Systems
from aiosolarfocus.exceptions import SolarfocusConfigError


def test_every_component_has_a_configuration_field_named_after_it() -> None:
    """`count_of` reads the field by the component's id, so the two must agree.

    This is what keeps the registry from needing a third copy of the component
    list, which is where the predecessor's factory, manager and facade drifted.
    """
    names = {field.name for field in fields(SolarfocusConfig)}
    assert {component_id.value for component_id in ComponentId} <= names
    assert count_field_names() == {component_id.value for component_id in ComponentId}


def test_a_vampair_has_a_heat_pump_and_nothing_else_does() -> None:
    """The predecessor listed the biomass systems instead of naming the one exception.

    Pellet Elegance and Octoplus were in neither branch, so they were never read
    at all - and the read reported success, so every register kept its zero.
    """
    vampair = SolarfocusConfig(host="c", system=Systems.VAMPAIR)
    assert vampair.count_of(ComponentId.HEAT_PUMP) == 1
    assert vampair.count_of(ComponentId.BIOMASS_BOILER) == 0

    for system in (Systems.THERMINATOR, Systems.ECOTOP, Systems.PELLETELEGANCE, Systems.OCTOPLUS):
        config = SolarfocusConfig(host="c", system=system)
        assert config.count_of(ComponentId.BIOMASS_BOILER) == 1, system
        assert config.count_of(ComponentId.HEAT_PUMP) == 0, system


def test_more_of_a_component_than_a_controller_addresses_is_refused() -> None:
    with pytest.raises(SolarfocusConfigError, match="at most 8 heating circuits"):
        SolarfocusConfig(host="c", heating_circuits=9)


def test_a_firmware_that_addresses_one_solar_circuit_refuses_four() -> None:
    """25.030 raised the limit from one to four; nothing else changed count."""
    SolarfocusConfig(host="c", api_version=ApiVersion.V_26_020, solar=4)
    with pytest.raises(SolarfocusConfigError, match=r"at most 1 solar circuit on firmware 23\.020"):
        SolarfocusConfig(host="c", api_version=ApiVersion.V_23_020, solar=4)


def test_a_component_the_firmware_predates_is_refused_with_the_version_that_brought_it() -> None:
    with pytest.raises(SolarfocusConfigError, match=r"circulations arrived in firmware 25\.030"):
        SolarfocusConfig(host="c", api_version=ApiVersion.V_23_020, circulations=1)


def test_a_component_this_system_does_not_have_is_refused() -> None:
    with pytest.raises(SolarfocusConfigError, match="Ecotop has no heat pump"):
        SolarfocusConfig(host="c", system=Systems.ECOTOP, heat_pump=True)


@pytest.mark.parametrize(("field", "value"), [("host", ""), ("timeout", 0.0), ("timeout", -1.0)])
def test_a_controller_we_could_not_reach_is_refused(field: str, value: object) -> None:
    with pytest.raises(SolarfocusConfigError):
        SolarfocusConfig(**{"host": "c", field: value})  # type: ignore[arg-type]


def test_component_keys_read_the_way_someone_counts_them() -> None:
    config = SolarfocusConfig(host="c", heating_circuits=2, buffers=0, boilers=0, photovoltaic=True)
    # In registry order, and a vampair has its heat pump without being asked.
    assert [str(key) for key in config.component_keys()] == ["heating_circuits.1", "heating_circuits.2", "heat_pump", "photovoltaic"]
    assert ComponentKey(ComponentId.HEATING_CIRCUITS, 2).number == 2


def test_every_configured_component_gets_a_layout() -> None:
    config = SolarfocusConfig(host="c", api_version=ApiVersion.V_26_020, heating_circuits=3, buffers=2)
    layouts = config.layouts()
    assert set(layouts) == set(config.component_keys())
    assert all(layout.registers for layout in layouts.values())


def test_instances_of_one_component_land_on_their_own_addresses() -> None:
    """Two heating circuits must not resolve to the same block."""
    config = SolarfocusConfig(host="c", api_version=ApiVersion.V_26_020, heating_circuits=3, buffers=0, boilers=0)
    bases = [layout.input_base for key, layout in config.layouts().items() if key.id is ComponentId.HEATING_CIRCUITS]
    assert bases == [1100, 1150, 1200]


def test_the_registry_covers_every_component_id() -> None:
    assert {spec.id for spec in COMPONENTS} == set(ComponentId)
