"""Shared fixtures: a small component that exercises the awkward parts of a table."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import ClassVar

from aiosolarfocus.components.base import Component, RegisterWriter
from aiosolarfocus.const import ApiVersion, Systems, Write
from aiosolarfocus.enums import HeatingCircuitMode
from aiosolarfocus.layout import Layout
from aiosolarfocus.registers import (
    HOLDING,
    READ_WRITE,
    celsius,
    code,
    energy,
    enum_,
    flag,
    percent,
    unscaled,
)


class Probe(Component):
    """A component built to have one of each thing that has ever gone wrong.

    A per-version renumbering, a 32-bit counter, an unsigned 32-bit counter, a
    register only newer firmware has, a register only one system has, a holding
    register written in a different unit than it is read in, and a system whose
    block is laid out as a later firmware laid it out.
    """

    layout_as_of: ClassVar[Mapping[Systems, ApiVersion]] = {Systems.THERMINATOR: ApiVersion.V_25_030}

    supply_temperature = celsius(0)
    flow_rate = unscaled(1, unit="l/h")
    running = flag(2)
    # 25.030 pushed everything from here down by one.
    state = code({ApiVersion.V_20_110: 3, ApiVersion.V_25_030: 4})
    thermal_energy = energy({ApiVersion.V_20_110: 4, ApiVersion.V_25_030: 5})
    cooling_energy = energy({ApiVersion.V_20_110: 6, ApiVersion.V_25_030: 7}, signed=False)
    residual_oxygen = percent(10, scale=0.1, since=ApiVersion.V_25_020)
    octoplus_only = celsius(11, systems=frozenset({Systems.OCTOPLUS}))
    # Read out of another component's block entirely.
    outdoor_temperature = celsius(2408, absolute=True)

    mode = enum_(0, HeatingCircuitMode, kind=HOLDING, access=READ_WRITE)
    target_temperature = celsius(1, kind=HOLDING, access=READ_WRITE, bounds=(0.0, 80.0), step=0.5)
    # Reported in tenths of a percent, accepted as a whole percent.
    humidity_external = percent(2, kind=HOLDING, access=READ_WRITE, scale=0.1, write_scale=1.0, bounds=(1.0, 100.0))


def build_probe(
    api_version: ApiVersion = ApiVersion.V_26_020,
    system: Systems = Systems.VAMPAIR,
    *,
    input_base: int | None = 1100,
    holding_base: int | None = 32600,
    writer: RegisterWriter | None = None,
) -> Probe:
    """A `Probe` resolved against one firmware and system, ready to be fed readings."""
    return Probe(Layout.resolve(Probe, api_version, system, input_base, holding_base), index=1, writer=writer)


class RecordingWriter:
    """A writer that keeps what it was asked to put on the wire."""

    def __init__(self) -> None:
        self.writes: list[Write] = []

    async def __call__(self, writes: Sequence[Write]) -> None:
        """Record the writes instead of putting them on a wire."""
        self.writes.extend(writes)
