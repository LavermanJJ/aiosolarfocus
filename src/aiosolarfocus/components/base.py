"""The base component: a register table, the last readings, and how to write.

A component subclass is a class body of `Register` declarations and nothing
else - no constructor, no `if api_version >=` branches, no addresses computed at
runtime. What the firmware and the system make of that declaration is
`Layout.resolve`'s job; holding the values that come back is this class's.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, overload

from ..codec import decode, encode, raw_to_words, words_to_raw
from ..const import Access, ApiVersion, RegisterKind, Systems, Write
from ..exceptions import ReadOnlyRegisterError, SolarfocusError, UnsupportedRegisterError
from ..registers import Derived, DerivedInfo, Register, RegisterInfo

if TYPE_CHECKING:
    from ..layout import Layout


class RegisterWriter(Protocol):
    """How a component reaches the controller.

    Takes a sequence so a group of registers the controller wants written
    together goes out under one hold of the transport lock, with no poll landing
    between them.
    """

    async def __call__(self, writes: Sequence[Write]) -> None:
        """Put these words at these addresses."""
        ...


class Component:
    """One component of a heating system: a circuit, a buffer, the heat pump."""

    #: Systems whose block is laid out as a later firmware laid it out for
    #: everyone else. A therminator and an ecotop heating circuit have the
    #: 25.030 block whatever firmware they run, which is what the predecessor's
    #: `TherminatorHeatingCircuit` subclass was for - and that subclass dropped
    #: `api_version` on the way to `super().__init__`, so those systems also
    #: silently resolved every *other* register at the wrong version.
    layout_as_of: ClassVar[Mapping[Systems, ApiVersion]] = {}

    _declared: ClassVar[tuple[Register[Any], ...]] = ()
    _derived: ClassVar[tuple[Derived[Any], ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Collect the registers this component declares, including inherited ones.

        `__set_name__` has already run by the time this does, so every register
        knows the name it was declared under. Walking the MRO base-first and
        keying by name means a subclass that redeclares a register replaces it
        rather than adding a second one at the same address.
        """
        super().__init_subclass__(**kwargs)
        declared: dict[str, Register[Any]] = {}
        computed: dict[str, Derived[Any]] = {}
        for klass in reversed(cls.__mro__):
            for value in vars(klass).values():
                if isinstance(value, Register):
                    declared[value.name] = value
                elif isinstance(value, Derived):
                    computed[value.name] = value
        cls._declared = tuple(declared.values())
        cls._derived = tuple(computed.values())

    @classmethod
    def declared(cls) -> tuple[Register[Any], ...]:
        """Every register this component declares, before any firmware gating."""
        return cls._declared

    @classmethod
    def derived(cls) -> tuple[Derived[Any], ...]:
        """Every value this component works out from its registers."""
        return cls._derived

    def __init__(self, layout: Layout, *, index: int | None = None, writer: RegisterWriter | None = None) -> None:
        self.layout = layout
        #: 1-based, and None for the components a controller only ever has one
        #: of. It is what the caller counted from, so it is what error messages
        #: and the command line use.
        self.index = index
        self._writer = writer
        self._raw: dict[str, tuple[int, ...]] = {}
        self._values: dict[str, Any] = {}
        self.last_updated: float | None = None
        self.last_error: SolarfocusError | None = None

    # -- reading ----------------------------------------------------------- #

    @property
    def available(self) -> bool:
        """Whether the last attempt to read this component succeeded."""
        return self.last_updated is not None and self.last_error is None

    def value_of[V](self, register: Register[V]) -> V | None:
        """The last reading of one register, or None if there is not one.

        None means one of three things, and a caller almost always wants the
        same behaviour for all of them: this firmware or this system does not
        have the register; the component has not been read yet, or its last read
        failed; or the register is reporting one of the values an open sensor
        channel reports instead of a measurement. `raw` and `info` tell them
        apart for the callers that must.
        """
        value: V | None = self._values.get(register.name)
        return value

    def raw(self, register: Register[Any]) -> int | None:
        """The last reading as the controller sent it, before scaling.

        A sentinel reading has a raw value even though its decoded value is
        None, which is how a caller can tell "no sensor fitted" from "never
        read".
        """
        words = self._raw.get(register.name)
        if words is None:
            return None
        return words_to_raw(words, signed=register.signed)

    def supports(self, name: str) -> bool:
        """Whether this controller has a value of that name, computed or read."""
        return name in self.layout.by_name or name in self.available_derived()

    @overload
    def info(self, value: Register[Any]) -> RegisterInfo: ...

    @overload
    def info(self, value: Derived[Any]) -> DerivedInfo: ...

    @overload
    def info(self, value: str) -> RegisterInfo | DerivedInfo: ...

    def info(self, value: Register[Any] | Derived[Any] | str) -> RegisterInfo | DerivedInfo:
        """Describe one value of this component, computed or read.

        Asking with the register itself gets a `RegisterInfo` back rather than a
        union, so the common case - what are this register's bounds - does not
        make every caller narrow a type first.
        """
        name = value if isinstance(value, str) else value.name
        resolved = self.layout.by_name.get(name)
        if resolved is not None:
            return resolved.info()
        computed = self.available_derived().get(name)
        if computed is not None:
            return computed
        raise UnsupportedRegisterError(f"{type(self).__name__} has no {name!r}", context=self._context())

    def available_registers(self) -> Mapping[str, RegisterInfo]:
        """Every register this controller actually has, in address order."""
        return {resolved.name: resolved.info() for resolved in self.layout.registers}

    def available_derived(self) -> Mapping[str, DerivedInfo]:
        """Every computed value this controller has the registers for.

        A derived value is only as available as what it is worked out from, so a
        firmware missing one of its inputs does not have it either.
        """
        return {computed.name: computed.info() for computed in self._derived if all(name in self.layout.by_name for name in computed.depends_on)}

    def available_values(self) -> Mapping[str, RegisterInfo | DerivedInfo]:
        """Everything readable off this component, computed or read.

        What a consumer building entities wants: it replaces carrying a
        `min_required_version` and an `unsupported_systems` list on every one of
        a hundred entity descriptions, and - unlike `available_registers` alone -
        it does not quietly drop the coefficients of performance, which the
        predecessor exposed as though they were registers.
        """
        return {**self.available_registers(), **self.available_derived()}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Everything read, for diagnostics: raw words, decoded value, address.

        Replaces the downstream habit of reaching into `vars(component)` and
        filtering by an internal base class, which is why the derived values are
        here too - that filter caught them, and a dump without them would be a
        step backwards.
        """
        readings: dict[str, dict[str, Any]] = {
            resolved.name: {
                "address": resolved.address,
                "kind": resolved.kind.value,
                "raw": self.raw(resolved.register),
                "value": self._values.get(resolved.name),
                "unit": resolved.register.unit,
            }
            for resolved in self.layout.registers
        }
        for computed in self._derived:
            if computed.name not in self.available_derived():
                continue
            readings[computed.name] = {"address": None, "kind": "derived", "raw": None, "value": computed.__get__(self), "unit": computed.unit}
        return readings

    def decode_readings(self, readings: Mapping[tuple[RegisterKind, int], int]) -> None:
        """Take this component's registers out of a completed set of reads.

        The client reads slices that serve several components at once and hands
        every component the same flat mapping; each picks out its own addresses.
        A register whose addresses are not all present is left alone rather than
        half-decoded - that is what a failed slice looks like from here.
        """
        for resolved in self.layout.registers:
            words = tuple(readings[(resolved.kind, address)] for address in resolved.addresses if (resolved.kind, address) in readings)
            if len(words) != resolved.width:
                continue
            self._raw[resolved.name] = words
            register = resolved.register
            self._values[resolved.name] = decode(
                words,
                signed=register.signed,
                scale=register.scale,
                sentinels=register.sentinels,
                decode_fn=register.decode_fn,
            )
        self.last_updated = time.monotonic()
        self.last_error = None

    def mark_failed(self, error: SolarfocusError) -> None:
        """Record that this component could not be read, keeping its last values.

        Stale readings beat blanked ones for a heating system: a controller that
        drops one slice of one poll should not empty a graph.
        """
        self.last_error = error

    # -- writing ----------------------------------------------------------- #

    async def write(self, register: Register[Any], value: Any) -> None:
        """Write one register, refusing what the controller would refuse."""
        await self.write_many({register: value})

    async def write_many(self, values: Mapping[Register[Any], Any]) -> None:
        """Write several registers without a poll getting in between them.

        Modbus has no transaction, so this is not atomic against the
        controller's own logic - it is atomic against everything this library
        does. The register document requires the flow setpoint, the cooling flag
        and the operating mode of a heating circuit to be written together; a
        refresh landing between two of them leaves the controller in a state the
        document does not describe.
        """
        if self._writer is None:
            raise SolarfocusError("this component is not connected to a controller", context=self._context())

        writes: list[Write] = []
        encoded: list[tuple[Register[Any], tuple[int, ...]]] = []
        for register, value in values.items():
            resolved = self.layout.by_name.get(register.name)
            if resolved is None:
                raise UnsupportedRegisterError(f"{type(self).__name__} has no {register.name!r} on this controller", context=self._context())
            if register.access is not Access.READ_WRITE:
                raise ReadOnlyRegisterError(f"{register.name!r} is not writable", context=self._context())

            words = encode(
                value,
                signed=register.signed,
                width=register.width,
                scale=register.scale,
                write_scale=register.write_scale,
                bounds=register.bounds,
                encode_fn=register.encode_fn,
                context=f"{self._context()} {register.name}",
            )
            writes.append((resolved.kind, resolved.address, words))
            encoded.append((register, words))

        await self._writer(writes)

        # The controller took the value, so report it straight away rather than
        # waiting for the next poll. This is what lets a caller stop re-reading
        # the whole component after every single write.
        #
        # What to cache is what the *next read* will report, which is not the
        # words that just went out: a register with a `write_scale` is read in
        # one unit and written in another, so decoding the written words at the
        # read scale reports a tenth of the value that was written. Heating
        # circuit 32607 is the one, and 56 % came back as 5.6 % until the next
        # poll corrected it. See home-assistant-solarfocus#241.
        for register, words in encoded:
            written_scale = register.write_scale if register.write_scale is not None else register.scale
            value = decode(
                words,
                signed=register.signed,
                scale=written_scale,
                sentinels=register.sentinels,
                decode_fn=register.decode_fn,
            )
            self._values[register.name] = value
            self._raw[register.name] = words if written_scale == register.scale or value is None else raw_to_words(round(value / register.scale), width=register.width)

    # -- presentation ------------------------------------------------------ #

    def _context(self) -> str:
        return f"{type(self).__name__} {self.index}" if self.index is not None else type(self).__name__

    def __repr__(self) -> str:
        """Name the component and say how much of it has been read."""
        state = "unread" if self.last_updated is None else f"{len(self._values)}/{len(self.layout.registers)} read"
        if self.last_error is not None:
            state = f"failed: {self.last_error}"
        return f"<{self._context()} {state}>"
