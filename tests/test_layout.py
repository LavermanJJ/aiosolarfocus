"""Resolution: which registers this firmware on this system has, and where."""

from __future__ import annotations

import pytest

from aiosolarfocus.components.base import Component
from aiosolarfocus.const import ApiVersion, RegisterKind, Systems
from aiosolarfocus.exceptions import SolarfocusConfigError
from aiosolarfocus.layout import Layout
from aiosolarfocus.registers import HOLDING, celsius, unscaled

from .conftest import Probe, build_probe


def address_of(probe: Probe, name: str) -> int:
    return probe.layout.by_name[name].address


def test_a_renumbered_block_resolves_to_the_right_address_on_both_firmwares() -> None:
    """25.030 inserted a register, moving everything below it down by one.

    The predecessor wrote this as a fifteen-line if/else pair that differed only
    by offset, once per component that had one.
    """
    assert address_of(build_probe(ApiVersion.V_21_140), "state") == 1103
    assert address_of(build_probe(ApiVersion.V_26_020), "state") == 1104


def test_a_system_can_have_a_later_layout_without_having_later_registers() -> None:
    """A therminator has the 25.030 block whatever firmware it runs.

    But it does not thereby gain the registers 25.020 introduced - which is the
    bug in the predecessor's `TherminatorHeatingCircuit`, whose constructor
    dropped `api_version` on the way to `super().__init__` and so resolved every
    other register at a default version too.
    """
    therminator = build_probe(ApiVersion.V_21_140, Systems.THERMINATOR)
    assert address_of(therminator, "state") == 1104
    assert therminator.supports("residual_oxygen") is False


def test_a_register_can_be_read_out_of_another_components_block() -> None:
    """The heat pump and the biomass boiler read the same outdoor sensor.

    Declaring it absolute rather than as an offset lets the planner fold it into
    a read the other component is already making.
    """
    assert address_of(build_probe(input_base=1100), "outdoor_temperature") == 2408
    assert address_of(build_probe(input_base=2300), "outdoor_temperature") == 2408


def test_a_register_only_one_system_has_appears_only_there() -> None:
    assert build_probe(system=Systems.OCTOPLUS).supports("octoplus_only") is True
    assert build_probe(system=Systems.VAMPAIR).supports("octoplus_only") is False


def test_a_component_with_no_holding_block_simply_has_no_holding_registers() -> None:
    """A buffer before 22.090 has none at all; the predecessor passed -1 around for this."""
    probe = build_probe(holding_base=None)
    assert probe.layout.of_kind(RegisterKind.HOLDING) == ()
    assert probe.supports("mode") is False


def test_registers_come_out_input_first_then_in_address_order() -> None:
    """The order the document is in, and the order someone reading a dump expects."""
    registers = build_probe().layout.registers
    kinds = [resolved.kind for resolved in registers]
    assert kinds == sorted(kinds, key=lambda kind: kind is RegisterKind.HOLDING)
    for kind in RegisterKind:
        addresses = [resolved.address for resolved in registers if resolved.kind is kind]
        assert addresses == sorted(addresses)


def test_resolution_is_cached_so_eight_heating_circuits_cost_one_resolution() -> None:
    first = Layout.resolve(Probe, ApiVersion.V_26_020, Systems.VAMPAIR, 1100, 32600)
    second = Layout.resolve(Probe, ApiVersion.V_26_020, Systems.VAMPAIR, 1100, 32600)
    assert first is second


def test_a_table_that_reads_one_address_twice_is_refused() -> None:
    """Always a mistake, and one that produces plausible readings rather than an error.

    Checking it at resolution checks it for every component on every system and
    every firmware, rather than for whichever one someone wrote a test for.
    """

    class Overlapping(Component):
        """Two registers at the same offset: a copied address, or a half-applied renumbering."""

        temperature_a = celsius(0)
        temperature_b = celsius(0)

    with pytest.raises(SolarfocusConfigError, match=r"temperature_a.*temperature_b"):
        Layout.resolve(Overlapping, ApiVersion.V_26_020, Systems.VAMPAIR, 1100)


def test_a_register_that_runs_into_the_next_one_is_refused() -> None:
    """Half of a 32-bit register is not a value, and the other half is someone else's."""

    class Colliding(Component):
        """A 32-bit counter whose second word is another register's first."""

        counter = unscaled(0, width=2)
        squeezed = unscaled(1)

    with pytest.raises(SolarfocusConfigError, match=r"counter.*squeezed"):
        Layout.resolve(Colliding, ApiVersion.V_26_020, Systems.VAMPAIR, 1100)


def test_the_two_modbus_tables_do_not_collide_with_each_other() -> None:
    """Input 1100 and holding 1100 are different registers on the controller."""

    class BothTables(Component):
        """One register in each table, at the same number."""

        reading = celsius(0)
        setpoint = celsius(0, kind=HOLDING)

    layout = Layout.resolve(BothTables, ApiVersion.V_26_020, Systems.VAMPAIR, 1100, 1100)
    assert len(layout.registers) == 2
