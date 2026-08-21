"""The read plan: what the controller is actually asked for, and on whose behalf."""

from __future__ import annotations

import pytest

from aiosolarfocus.components import ComponentId
from aiosolarfocus.components.base import Component
from aiosolarfocus.config import ComponentKey, SolarfocusConfig
from aiosolarfocus.const import MAX_REGISTERS_PER_READ, ApiVersion, RegisterKind, Systems
from aiosolarfocus.layout import Layout
from aiosolarfocus.planner import plan
from aiosolarfocus.registers import unscaled

from .conftest import Probe

EVERYTHING = {
    "heating_circuits": 1,
    "buffers": 1,
    "boilers": 1,
    "fresh_water_modules": 1,
    "circulations": 1,
    "differential_modules": 1,
    "solar": 1,
    "photovoltaic": True,
}


def config(system: Systems = Systems.VAMPAIR, api_version: ApiVersion = ApiVersion.V_26_020, **overrides: object) -> SolarfocusConfig:
    return SolarfocusConfig(host="controller", system=system, api_version=api_version, **{**EVERYTHING, **overrides})  # type: ignore[arg-type]


def test_consecutive_registers_become_one_read() -> None:
    layouts = {"probe": Layout.resolve(Probe, ApiVersion.V_26_020, Systems.VAMPAIR, 1100, 32600)}
    holding = [read for read in plan(layouts).slices if read.kind is RegisterKind.HOLDING]
    assert [(read.address, read.count) for read in holding] == [(32600, 3)]


def test_a_gap_splits_a_read_because_the_controller_would_not_pad_it() -> None:
    """A read spanning an unmapped address comes back the right length and the wrong values.

    There is no protocol signal for it, so never asking is the only defence.
    """
    inputs = [read for read in plan({"probe": Layout.resolve(Probe, ApiVersion.V_26_020, Systems.VAMPAIR, 1100, 32600)}).slices if read.kind is RegisterKind.INPUT]
    # 1103 is where the pre-25.030 layout kept `state`; on 26.020 nothing claims
    # it, so the read stops short rather than crossing it. 1109 is likewise
    # unclaimed, and 2408 is in another component's block entirely.
    assert [(read.address, read.count) for read in inputs] == [(1100, 3), (1104, 5), (1110, 1), (2408, 1)]


def test_components_that_interleave_are_read_together_and_the_overlap_once() -> None:
    """The heat pump, the photovoltaic and the biomass boiler share one holding range.

    33406 belongs to two of them. The predecessor read it twice, in two of five
    separate requests over one contiguous, fully mapped range.
    """
    read = next(read for read in plan(config(Systems.OCTOPLUS).layouts()).slices if read.kind is RegisterKind.HOLDING and read.address == 33406)
    assert read.count == 7
    assert {key.id for key in read.components} == {ComponentId.BIOMASS_BOILER, ComponentId.PHOTOVOLTAIC}


@pytest.mark.parametrize("system", list(Systems))
@pytest.mark.parametrize("api_version", list(ApiVersion))
def test_no_read_ever_splits_a_32_bit_register(system: Systems, api_version: ApiVersion) -> None:
    """The controller refuses a one-register read of one, and half a value is not a value."""
    layouts = config(system, api_version, **_supported(api_version)).layouts()
    covered = {(read.kind, address) for read in plan(layouts).slices for address in read.addresses}
    for layout in layouts.values():
        for resolved in layout.registers:
            wanted = {(resolved.kind, address) for address in resolved.addresses}
            assert wanted <= covered, f"{resolved.name} is only half covered"
    for read in plan(layouts).slices:
        for layout in layouts.values():
            for resolved in layout.registers:
                if resolved.kind is not read.kind or resolved.width == 1:
                    continue
                inside = [address for address in resolved.addresses if address in read.addresses]
                assert inside in ([], list(resolved.addresses)), f"{read} cuts {resolved.name} in half"


@pytest.mark.parametrize("system", list(Systems))
@pytest.mark.parametrize("api_version", list(ApiVersion))
def test_every_wanted_register_is_read_exactly_once(system: Systems, api_version: ApiVersion) -> None:
    layouts = config(system, api_version, **_supported(api_version)).layouts()
    reads = plan(layouts).slices
    counted: dict[tuple[RegisterKind, int], int] = {}
    for read in reads:
        for address in read.addresses:
            counted[(read.kind, address)] = counted.get((read.kind, address), 0) + 1
    assert set(counted.values()) <= {1}, "an address is read more than once"

    wanted = {(resolved.kind, address) for layout in layouts.values() for resolved in layout.registers for address in resolved.addresses}
    assert set(counted) == wanted


@pytest.mark.parametrize("system", list(Systems))
@pytest.mark.parametrize("api_version", list(ApiVersion))
def test_no_read_exceeds_what_modbus_allows(system: Systems, api_version: ApiVersion) -> None:
    for read in plan(config(system, api_version, **_supported(api_version)).layouts()).slices:
        assert read.count <= MAX_REGISTERS_PER_READ


def test_a_long_run_is_split_without_cutting_a_wide_register() -> None:
    """No block in the map is near the limit today; the guard is for a firmware that is."""

    class LongBlock(Component):
        """Twelve consecutive registers with a 32-bit counter part way in."""

        a = unscaled(0)
        b = unscaled(1)
        c = unscaled(2)
        d = unscaled(3)
        counter = unscaled(4, width=2)
        f = unscaled(6)
        g = unscaled(7)
        h = unscaled(8)
        i = unscaled(9)
        j = unscaled(10)
        k = unscaled(11)

    layouts = {"long": Layout.resolve(LongBlock, ApiVersion.V_26_020, Systems.VAMPAIR, 1000)}
    reads = [(read.address, read.count) for read in plan(layouts, max_count=5).slices]
    # A cut five in would land between the two halves of the counter at 1004, so
    # it moves back to four. The rest divides as it comes.
    assert reads == [(1000, 4), (1004, 5), (1009, 3)]


def test_a_failed_read_is_attributed_to_the_components_in_it() -> None:
    """This is what lets one bad range fail one component instead of the whole system."""
    plan_ = plan(config().layouts())
    circuit = ComponentKey(ComponentId.HEATING_CIRCUITS, 1)
    reads = plan_.for_component(circuit)
    assert {(read.kind, read.address) for read in reads} == {
        (RegisterKind.INPUT, 1100),
        (RegisterKind.INPUT, 1105),
        (RegisterKind.HOLDING, 32600),
        (RegisterKind.HOLDING, 32602),
        (RegisterKind.HOLDING, 32605),
    }


def test_the_plan_for_a_vampair_is_what_it_was(snapshot: list[tuple[str, int, int]] | None = None) -> None:
    """A golden plan, so a table change shows its read-plan diff in review."""
    reads = [(read.kind.value, read.address, read.count) for read in plan(config().layouts()).slices]
    assert reads == [
        ("input", 500, 3),
        ("input", 700, 5),
        ("input", 900, 2),
        ("input", 1100, 4),
        ("input", 1105, 3),
        ("input", 1900, 2),
        ("input", 1903, 3),
        ("input", 2100, 18),
        ("input", 2200, 6),
        ("input", 2300, 5),
        ("input", 2306, 2),
        ("input", 2310, 15),
        ("input", 2326, 5),
        ("input", 2408, 1),
        ("input", 2500, 12),
        ("holding", 32000, 4),
        ("holding", 32600, 1),
        ("holding", 32602, 2),
        ("holding", 32605, 4),
        ("holding", 33404, 6),
        ("holding", 33415, 1),
        ("holding", 34000, 3),
    ]


def _supported(api_version: ApiVersion) -> dict[str, object]:
    """Trim the configuration to components the firmware in question can have."""
    return {
        "fresh_water_modules": 1 if api_version >= ApiVersion.V_23_020 else 0,
        "circulations": 1 if api_version >= ApiVersion.V_25_030 else 0,
        "differential_modules": 1 if api_version >= ApiVersion.V_25_030 else 0,
        "fresh_water_module_cascade": api_version >= ApiVersion.V_23_040,
        "circulation_module": api_version >= ApiVersion.V_23_040,
    }
