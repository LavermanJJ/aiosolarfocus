"""Values the library works out, described the same way the ones it reads are.

The predecessor exposed the heat pump's coefficients of performance as `Part`
objects, indistinguishable from registers to anything reading the component -
and the Home Assistant integration has five sensors keyed on their names. A
consumer building entities from `available_registers` alone would drop all five,
which is why they are declared rather than left as bare properties.
"""

from __future__ import annotations

from typing import assert_type

import pytest

from aiosolarfocus.client import SolarfocusClient
from aiosolarfocus.components.heat_pump import HeatPump
from aiosolarfocus.config import SolarfocusConfig
from aiosolarfocus.const import ApiVersion, RegisterKind, Systems
from aiosolarfocus.exceptions import UnsupportedRegisterError
from aiosolarfocus.layout import Layout
from aiosolarfocus.registers import Derived, DerivedInfo, RegisterInfo
from aiosolarfocus.testing import FakeController

INPUT = RegisterKind.INPUT

SPF = ("cop_heating", "cop_cooling", "seasonal_performance", "seasonal_performance_heating", "seasonal_performance_drinking_water")


def heat_pump(readings: dict[int, int] | None = None) -> HeatPump:
    """A heat pump holding the given input registers. Addresses, not offsets."""
    layout = Layout.resolve(HeatPump, ApiVersion.V_26_020, Systems.VAMPAIR, 2300, 33404)
    component = HeatPump(layout)
    component.decode_readings({(INPUT, address): value for address, value in (readings or {}).items()})
    return component


def test_class_access_gives_the_description_and_instance_access_the_value() -> None:
    assert_type(HeatPump.cop_heating, Derived[float])
    assert_type(heat_pump().cop_heating, float | None)


def test_the_five_figures_the_predecessor_computed_are_all_here() -> None:
    assert {computed.name for computed in HeatPump.derived()} == set(SPF)


def test_a_coefficient_of_performance_is_worked_out_from_the_registers() -> None:
    component = heat_pump({2324: 3600, 2322: 900})
    assert component.cop_heating == 4.0


def test_an_idle_heat_pump_reports_nothing_rather_than_a_performance_of_zero() -> None:
    """The predecessor's calculator returned 0.0 here.

    Home Assistant records that as a measurement. Confirmed against a real
    vampair, which was idle at the time.
    """
    component = heat_pump({2324: 0, 2322: 0})
    assert component.cop_heating is None


def test_a_derived_value_is_offered_alongside_the_registers() -> None:
    """What a consumer building entities reads, and what `available_registers` alone drops."""
    values = heat_pump().available_values()
    assert set(SPF) <= set(values)
    assert "supply_temperature" in values
    assert set(heat_pump().available_registers()).isdisjoint(SPF)


def test_a_derived_value_describes_itself() -> None:
    info = heat_pump().info(HeatPump.cop_heating)
    assert_type(info, DerivedInfo)
    assert info.name == "cop_heating"
    assert info.doc == "Coefficient of performance while heating, right now."
    assert info.depends_on == ("thermal_power_heating", "electrical_power")
    assert info.writable is False


def test_asking_by_register_still_gets_a_register_back() -> None:
    """The common case must not make every caller narrow a union first."""
    info = heat_pump().info(HeatPump.supply_temperature)
    assert_type(info, RegisterInfo)
    assert info.address == 2300


@pytest.mark.parametrize("name", SPF)
def test_a_derived_value_is_supported_by_name_like_a_register(name: str) -> None:
    assert heat_pump().supports(name)


def test_a_name_that_is_neither_is_still_refused() -> None:
    with pytest.raises(UnsupportedRegisterError, match="nonsense"):
        heat_pump().info("nonsense")


def test_a_derived_value_is_in_the_diagnostics_snapshot() -> None:
    """It was in the predecessor's, by virtue of the filter it dumped with."""
    snapshot = heat_pump({2324: 3600, 2322: 900}).snapshot()
    assert snapshot["cop_heating"] == {"address": None, "kind": "derived", "raw": None, "value": 4.0, "unit": None}


def test_a_derived_value_whose_registers_this_firmware_lacks_is_not_offered() -> None:
    """A derived value is only as available as what it is worked out from."""

    class Sparse(HeatPump):
        """A heat pump whose power registers this firmware does not have."""

    layout = Layout.resolve(Sparse, ApiVersion.V_26_020, Systems.VAMPAIR, 2300, 33404)
    trimmed = Layout(
        component=Sparse,
        api_version=layout.api_version,
        system=layout.system,
        input_base=layout.input_base,
        holding_base=layout.holding_base,
        registers=tuple(r for r in layout.registers if r.name != "electrical_power"),
        by_name={name: r for name, r in layout.by_name.items() if name != "electrical_power"},
    )
    component = Sparse(trimmed)

    assert "cop_heating" not in component.available_derived()
    assert "cop_cooling" not in component.available_derived()
    assert "seasonal_performance" in component.available_derived()
    assert "cop_heating" not in component.snapshot()


def test_the_client_snapshot_carries_them_through() -> None:
    config = SolarfocusConfig(host="c", system=Systems.VAMPAIR, api_version=ApiVersion.V_26_020, heating_circuits=0, buffers=0, boilers=0)
    client = SolarfocusClient(config, transport=FakeController.for_config(config))
    assert "cop_heating" in client.snapshot()["heat_pump"]
