"""Deciding which reads to make, once, for every component at the same time.

The predecessor let each component plan its own reads, so it never noticed that
different components' blocks interleave. On a 26.020 vampair the heat pump holds
33404-33406, the photovoltaic 33407-33409 and the biomass boiler 33406 and
33410-33412: one contiguous, fully mapped range read as five separate requests,
with 33406 fetched twice. Planning across every component at once folds those
into one read and drops the duplicate.

Two rules shape a plan, both learned from the controller rather than from the
specification. They are why this is a module and not a comprehension.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .const import MAX_REGISTERS_PER_READ, RegisterKind
from .layout import Layout


@dataclass(frozen=True, slots=True)
class ReadSlice[K]:
    """One request to the controller, and the components that want its answer."""

    kind: RegisterKind
    address: int
    count: int
    #: Every component with at least one register in this range. A slice that
    #: fails fails exactly these, and no others.
    components: frozenset[K]

    @property
    def addresses(self) -> range:
        """Every address this read covers."""
        return range(self.address, self.address + self.count)

    def __str__(self) -> str:
        """Describe the read the way the controller sees it."""
        last = self.address + self.count - 1
        return f"{self.kind.value} {self.address}-{last} ({self.count})"


@dataclass(frozen=True, slots=True)
class ReadPlan[K]:
    """Every read one refresh makes."""

    slices: tuple[ReadSlice[K], ...]

    @property
    def round_trips(self) -> int:
        """How many requests a refresh costs."""
        return len(self.slices)

    @property
    def registers_read(self) -> int:
        """How many registers a refresh asks for, duplicates already removed."""
        return sum(read.count for read in self.slices)

    def for_component(self, key: K) -> tuple[ReadSlice[K], ...]:
        """The reads that carry one component's registers."""
        return tuple(read for read in self.slices if key in read.components)

    def __str__(self) -> str:
        """One line per read, for the command line and for a review diff."""
        return "\n".join(str(read) for read in self.slices)


def plan[K](layouts: Mapping[K, Layout], *, max_count: int = MAX_REGISTERS_PER_READ) -> ReadPlan[K]:
    """Work out the reads that cover every register of every component.

    * **A read must not span an address the firmware does not map.** The
      controller does not pad such a read - it answers with the *next mapped*
      registers packed together, so every value after the gap silently shifts
      into the wrong name. Nothing in the answer says this happened, and there
      is no protocol signal for it, so the plan is the only defence. Everything
      a component declares is a register its firmware maps, so the rule comes
      to: merge runs of consecutive declared addresses and split anywhere else.

    * **A read must not start or end inside a 32-bit register.** The controller
      refuses a one-register read of one, and half of a 32-bit value is not a
      value.

    Deliberately conservative about the first rule: detection is optional in
    this library, so a plan may not assume an address is mapped just because the
    register document lists it. Bridging a gap with filler registers would make
    the plan shorter and would silently shift every reading after the filler on
    a controller that turned out not to have it.
    """
    wanted: dict[RegisterKind, dict[int, set[K]]] = {kind: {} for kind in RegisterKind}
    # Addresses a slice may not begin at, because the address before them is the
    # first half of the same 32-bit register.
    no_start: dict[RegisterKind, set[int]] = {kind: set() for kind in RegisterKind}

    for key, layout in layouts.items():
        for resolved in layout.registers:
            for offset, address in enumerate(resolved.addresses):
                wanted[resolved.kind].setdefault(address, set()).add(key)
                if offset:
                    no_start[resolved.kind].add(address)

    slices: list[ReadSlice[K]] = []
    for kind in RegisterKind:
        for run in _runs(sorted(wanted[kind])):
            for start, stop in _split(run, max_count, no_start[kind]):
                serves: set[K] = set()
                for address in range(start, stop):
                    serves |= wanted[kind][address]
                slices.append(ReadSlice(kind=kind, address=start, count=stop - start, components=frozenset(serves)))

    return ReadPlan(tuple(slices))


def _runs(addresses: list[int]) -> list[tuple[int, int]]:
    """Group sorted addresses into half-open runs of consecutive ones."""
    runs: list[tuple[int, int]] = []
    start: int | None = None
    previous: int | None = None
    for address in addresses:
        if start is None or previous is None:
            start = previous = address
            continue
        if address != previous + 1:
            runs.append((start, previous + 1))
            start = address
        previous = address
    if start is not None and previous is not None:
        runs.append((start, previous + 1))
    return runs


def _split(run: tuple[int, int], max_count: int, no_start: set[int]) -> list[tuple[int, int]]:
    """Cut a run into reads the controller will accept, never inside a wide register."""
    start, stop = run
    pieces: list[tuple[int, int]] = []
    while stop - start > max_count:
        cut = start + max_count
        while cut in no_start:
            cut -= 1
        pieces.append((start, cut))
        start = cut
    pieces.append((start, stop))
    return pieces
