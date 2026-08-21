"""A controller that behaves like the real one, including the ways it misbehaves.

Four measured behaviours are the point of this. All were observed against an
eco manager-touch on firmware 26.020, and all three of the awkward ones are
things a well-behaved Modbus server would not do:

* An address the firmware does not map is refused with illegal data address.
* A 32-bit register refuses a read of one register, exactly the way a missing
  address does. Probing presence therefore needs a `count=2` fallback, or every
  32-bit counter looks absent.
* **A block read spanning an unmapped address returns the next mapped registers
  packed together rather than padding the hole.** The answer is the right length
  and the wrong values, and nothing in the protocol says so. This is what the
  read planner exists to avoid, and reproducing it here is what makes the
  planner's tests check that readings come out right rather than that a comment
  is present.
* A block read *starting* at an unmapped address is refused outright.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..config import SolarfocusConfig
from ..const import RegisterKind, Write
from ..exceptions import IllegalAddressError, SolarfocusConnectionError
from .spec import documented_addresses

_Address = tuple[RegisterKind, int]


class FakeController:
    """An in-process stand-in for a controller, satisfying the `Transport` protocol."""

    def __init__(
        self,
        values: Mapping[_Address, int] | None = None,
        *,
        mapped: Iterable[_Address] | None = None,
        wide: Iterable[_Address] = (),
        connected: bool = False,
    ) -> None:
        """Set up a controller.

        `mapped` is which addresses exist, defaulting to everything the register
        document lists; `values` is what they hold, defaulting to zero. `wide`
        names the first address of each 32-bit register, which is what makes a
        one-register read of it be refused.
        """
        self._values: dict[_Address, int] = dict(values or {})
        self._mapped: set[_Address] = set(mapped) if mapped is not None else set(documented_addresses())
        self._mapped |= set(self._values)
        self._wide = set(wide)
        self._connected = connected
        #: Every read asked for, so a test can assert on round trips.
        self.reads: list[tuple[RegisterKind, int, int]] = []
        self.writes: list[Write] = []
        #: Set to have the next request fail as a dropped socket would.
        self.fail_with: Exception | None = None

    # -- the Transport protocol -------------------------------------------- #

    @property
    def connected(self) -> bool:
        """Whether the socket is open."""
        return self._connected

    async def connect(self) -> None:
        """Open the socket."""
        self._connected = True

    async def disconnect(self) -> None:
        """Close the socket."""
        self._connected = False

    async def read(self, kind: RegisterKind, address: int, count: int) -> tuple[int, ...]:
        """Answer a block read the way the controller would."""
        self.reads.append((kind, address, count))
        self._maybe_fail()
        if not self._connected:
            raise SolarfocusConnectionError("not connected", context=f"reading {kind.value} {address}")

        if (kind, address) not in self._mapped:
            # A read that starts nowhere is refused, however long it is.
            raise IllegalAddressError("this firmware has no such register", context=f"reading {kind.value} {address}-{address + count - 1}")
        if count == 1 and (kind, address) in self._wide:
            # Half of a 32-bit register is not something the controller will hand out.
            raise IllegalAddressError("this register is only handed out whole", context=f"reading {kind.value} {address}")

        # The hole is not padded: the answer is packed with whatever is mapped
        # next, so a slice that crosses a gap silently shifts.
        answer: list[int] = []
        cursor = address
        while len(answer) < count:
            if (kind, cursor) in self._mapped:
                answer.append(self._values.get((kind, cursor), 0))
            cursor += 1
            if cursor > address + 10_000:  # pragma: no cover - a runaway read
                raise IllegalAddressError("ran off the end of the register map", context=f"reading {kind.value} {address}")
        return tuple(answer)

    async def write(self, writes: Sequence[Write]) -> None:
        """Take a group of writes, refusing an address that does not exist."""
        self._maybe_fail()
        if not self._connected:
            raise SolarfocusConnectionError("not connected", context="writing")
        for kind, address, words in writes:
            for offset, word in enumerate(words):
                if (kind, address + offset) not in self._mapped:
                    raise IllegalAddressError("this firmware has no such register", context=f"writing {kind.value} {address + offset}")
                self._values[(kind, address + offset)] = word
            self.writes.append((kind, address, tuple(words)))

    async def probe(self, kind: RegisterKind, address: int, count: int = 1) -> tuple[int, ...] | None:
        """Read to find out whether an address exists; a refusal is None, not an error."""
        try:
            return await self.read(kind, address, count)
        except IllegalAddressError:
            return None

    # -- setting one up ----------------------------------------------------- #

    @classmethod
    def for_config(cls, config: SolarfocusConfig, values: Mapping[_Address, int] | None = None, **kwargs: Any) -> FakeController:
        """A controller that has exactly what this configuration expects of it.

        Every address the register document lists, plus every address the
        configuration's own tables ask for - the two differ where the document's
        transcription is missing a row - and every 32-bit register marked as one,
        so a one-register read of a counter is refused the way a real one refuses it.
        """
        mapped: set[_Address] = set(documented_addresses())
        wide: set[_Address] = set()
        for layout in config.layouts().values():
            for resolved in layout.registers:
                mapped.update((resolved.kind, address) for address in resolved.addresses)
                if resolved.width > 1:
                    wide.add((resolved.kind, resolved.address))
        return cls(values, mapped=mapped, wide=wide, **kwargs)

    def set(self, kind: RegisterKind, address: int, value: int) -> None:
        """Put a value at an address, mapping it if it was not."""
        self._values[(kind, address)] = value
        self._mapped.add((kind, address))

    def unmap(self, kind: RegisterKind, *addresses: int) -> None:
        """Take an address away, as a firmware that predates it would have it."""
        for address in addresses:
            self._mapped.discard((kind, address))
            self._values.pop((kind, address), None)

    @property
    def round_trips(self) -> int:
        """How many reads have been asked for."""
        return len(self.reads)

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            error, self.fail_with = self.fail_with, None
            raise error
