"""The register descriptor: one declaration that is spec, accessor and metadata.

A `Register` is immutable and shared by every instance of the component that
declares it - it holds no value. The component instance holds decoded values,
keyed by the name `__set_name__` handed the register when the class body ran.
Class access gives you the spec (`HeatPump.supply_temperature`), instance access
gives you the reading (`heat_pump.supply_temperature`), and both type-check.

This is what replaces the predecessor's `DataValue`, which was the spec and the
current value in one mutable object, had its absolute address and its Modbus
handle monkey-patched in by `Component.initialize`, and expressed every firmware
difference as an `if` in a constructor.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, overload

from .const import NOT_SET_FLAG, OPEN_CHANNEL, Access, ApiVersion, RegisterKind, Systems

if TYPE_CHECKING:
    from .components.base import Component

_LOGGER = logging.getLogger(__name__)

#: Readability aliases, so a component class body reads as the table it is.
INPUT = RegisterKind.INPUT
HOLDING = RegisterKind.HOLDING
READ = Access.READ
READ_WRITE = Access.READ_WRITE

#: Unknown enumeration values already reported, so a firmware that grew a new
#: mode says so once rather than once per poll forever.
_reported_unknown: set[tuple[str, int]] = set()


@dataclass(frozen=True, slots=True)
class RegisterInfo:
    """Everything a caller needs to know about one register of one component.

    This is what lets Home Assistant stop carrying `min_required_version` and
    `unsupported_systems` on a hundred entity descriptions, and stop hand-copying
    each number entity's minimum, maximum and step out of the register document.
    """

    name: str
    kind: RegisterKind
    address: int
    width: int
    signed: bool
    scale: float
    unit: str | None
    access: Access
    bounds: tuple[float, float] | None
    step: float | None
    since: ApiVersion
    systems: frozenset[Systems] | None
    enum: type[IntEnum] | None
    doc: str

    @property
    def writable(self) -> bool:
        """Whether the controller accepts a write to this register."""
        return self.access is Access.READ_WRITE


@dataclass(frozen=True, slots=True)
class DerivedInfo:
    """A value the library works out from registers rather than reading.

    The heat pump's coefficients of performance are the only ones today. They
    matter to a consumer as much as any register does - in the predecessor they
    were `Part` objects, indistinguishable from registers to anything reading
    the component - so they are described here rather than being left as
    properties a caller has to know the names of.
    """

    name: str
    unit: str | None
    doc: str
    #: The registers it is worked out from. A firmware without one of them does
    #: not have the derived value either.
    depends_on: tuple[str, ...]

    @property
    def writable(self) -> bool:
        """Never: a derived value is not somewhere to put anything."""
        return False


class Derived[T]:
    """A value computed from registers, declared beside them and described like them.

    Used as a decorator, so the calculation and its description are one
    declaration - the same bargain `Register` makes:

        @derived(doc="Coefficient of performance while heating", depends_on=("a", "b"))
        def cop_heating(self) -> float | None:
            ...
    """

    __slots__ = ("_getter", "depends_on", "doc", "name", "unit")

    def __init__(self, getter: Callable[[Any], T | None], *, unit: str | None, doc: str, depends_on: tuple[str, ...]) -> None:
        self._getter = getter
        self.unit = unit
        self.doc = doc or (getter.__doc__ or "").strip().split("\n")[0]
        self.depends_on = depends_on
        self.name = getter.__name__

    def __set_name__(self, owner: type[Any], name: str) -> None:
        """Learn the attribute name the component declared this under."""
        self.name = name

    @overload
    def __get__(self, obj: None, owner: type[Any] | None = None) -> Derived[T]: ...

    @overload
    def __get__(self, obj: Component, owner: type[Any] | None = None) -> T | None: ...

    def __get__(self, obj: Component | None, owner: type[Any] | None = None) -> Derived[T] | T | None:
        """Class access gives the description; instance access works the value out."""
        if obj is None:
            return self
        return self._getter(obj)

    def info(self) -> DerivedInfo:
        """Describe this value to a caller that has to build a UI out of it."""
        return DerivedInfo(name=self.name, unit=self.unit, doc=self.doc, depends_on=self.depends_on)


def derived[T](*, unit: str | None = None, doc: str = "", depends_on: tuple[str, ...] = ()) -> Callable[[Callable[[Any], T | None]], Derived[T]]:
    """Declare a value computed from this component's registers."""

    def decorate(getter: Callable[[Any], T | None]) -> Derived[T]:
        return Derived(getter, unit=unit, doc=doc, depends_on=depends_on)

    return decorate


@dataclass(frozen=True, eq=False, slots=True, kw_only=True)
class Register[T]:
    """One address in one component's block, and how to read and write it.

    Construct these through the helpers below rather than directly; they are
    what make a class body read as a table and what infer `T`.
    """

    kind: RegisterKind
    #: Offset inside the component's block, or - when the firmware renumbered
    #: the block - a mapping from the version that introduced a layout to the
    #: offset it put this register at. `offset_for` takes the highest key that
    #: is not newer than the configured firmware.
    at: int | Mapping[ApiVersion, int]
    #: `at` is a bare address rather than an offset. For the handful of
    #: registers a component reads out of another component's block.
    absolute: bool = False
    width: int = 1
    signed: bool = True
    #: Engineering value = raw * scale, always in that direction. The
    #: predecessor multiplied for input registers and divided for holding ones,
    #: which made every new register a chance to get the direction wrong.
    scale: float = 1.0
    #: For a register the controller reports in one unit and accepts in another.
    write_scale: float | None = None
    since: ApiVersion = ApiVersion.V_20_110
    until: ApiVersion | None = None
    #: None means every system.
    systems: frozenset[Systems] | None = None
    #: Raw unsigned readings that mean "no measurement" and decode to None.
    sentinels: frozenset[int] = frozenset()
    bounds: tuple[float, float] | None = None
    step: float | None = None
    decode_fn: Callable[[int], Any] | None = None
    encode_fn: Callable[[Any], int] | None = None
    enum: type[IntEnum] | None = None
    access: Access = Access.READ
    unit: str | None = None
    doc: str = ""
    #: Filled in by `__set_name__` when the component class body is evaluated.
    name: str = field(default="")

    def __set_name__(self, owner: type[Any], name: str) -> None:
        """Learn the attribute name the component declared this register under."""
        object.__setattr__(self, "name", name)

    @overload
    def __get__(self, obj: None, owner: type[Any] | None = None) -> Register[T]: ...

    @overload
    def __get__(self, obj: Component, owner: type[Any] | None = None) -> T | None: ...

    def __get__(self, obj: Component | None, owner: type[Any] | None = None) -> Register[T] | T | None:
        """Class access gives the spec; instance access gives the last reading."""
        if obj is None:
            return self
        return obj.value_of(self)

    def offset_for(self, version: ApiVersion) -> int | None:
        """Where this register sits on one firmware, or None if it has none.

        A per-version mapping whose lowest key is newer than `version` means the
        register did not exist yet, which is `since` said a second way and is
        answered the same: it is not available.
        """
        if isinstance(self.at, int):
            return self.at
        applicable = [introduced for introduced in self.at if introduced <= version]
        if not applicable:
            return None
        return self.at[max(applicable)]

    def available(self, version: ApiVersion, system: Systems) -> bool:
        """Whether this firmware, on this system, has this register at all."""
        return self.since <= version and (self.until is None or version <= self.until) and (self.systems is None or system in self.systems) and self.offset_for(version) is not None

    @property
    def writable(self) -> bool:
        """Whether the controller accepts a write to this register."""
        return self.access is Access.READ_WRITE


# --------------------------------------------------------------------------- #
# Typed construction helpers.
#
# These exist so a component class body reads as a table, and so `T` is inferred
# without every line carrying an annotation.
# --------------------------------------------------------------------------- #


def celsius(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[float]:
    """A temperature in tenths of a degree Celsius.

    The open-channel readings decode to None: a channel with nothing wired to it
    reports 130.0 degC or 270.0 degC, and the predecessor passed those through to
    Home Assistant as measurements.

    Named for its unit rather than for what it measures because several
    components have a register *called* `temperature`, and inside a class body
    that name would shadow this helper for every line below it.
    """
    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("sentinels", OPEN_CHANNEL)
    kwargs.setdefault("scale", 0.1)
    kwargs.setdefault("unit", "°C")
    return Register[float](at=at, **kwargs)


def tenths(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[float]:
    """Anything else the controller reports in tenths: a flow rate, a power, a percentage.

    Deliberately *not* `celsius` with the unit overridden: the open-channel
    sentinels are readings a temperature channel gives when nothing is wired to
    it, and 2700 litres through a heat meter is a perfectly good measurement.
    """
    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("scale", 0.1)
    return Register[float](at=at, **kwargs)


def energy(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[float]:
    """A 32-bit energy counter in watt-hours, reported as kWh."""
    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("width", 2)
    kwargs.setdefault("scale", 0.001)
    kwargs.setdefault("unit", "kWh")
    return Register[float](at=at, **kwargs)


def watts(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[int]:
    """A power reading in watts.

    Named for its unit for the same reason as `celsius`: `Photovoltaic` has a
    register called `power`.
    """
    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("unit", "W")
    return Register[int](at=at, **kwargs)


def percent(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[float]:
    """A percentage. Give `scale=0.1` for the registers reported in tenths."""
    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("unit", "%")
    return Register[float](at=at, **kwargs)


def flag(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[bool]:
    """A register that is only ever 0 or 1.

    0xFFFF decodes to None rather than to True. It is not a third state the
    document defines - it is what a flag reads when the controller has nothing
    to put there - and `bool(-1)` would report it as set.
    """
    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("signed", False)
    kwargs.setdefault("sentinels", NOT_SET_FLAG)
    kwargs.setdefault("decode_fn", bool)
    kwargs.setdefault("encode_fn", int)
    return Register[bool](at=at, **kwargs)


def code(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[int]:
    """An open enumeration - an operating state - that stays an int.

    Every firmware adds states, and a therminator enumerates its own from 200,
    so decoding to a closed `IntEnum` would raise on a perfectly good reading.
    The `*_STATE` tables in `enums.py` name the codes we know.
    """
    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("signed", False)
    return Register[int](at=at, **kwargs)


def enum_[E: IntEnum](at: int | Mapping[ApiVersion, int], enum: type[E], **kwargs: Any) -> Register[E]:
    """A closed enumeration: a mode, a cooling flag, an SG Ready signal.

    A value outside the enumeration decodes to None rather than raising, and
    says so in the log once, because a firmware that grew a new mode should not
    take a heating system's entities down with it.
    """

    def decode_fn(raw: int) -> E | None:
        try:
            return enum(raw)
        except ValueError:
            key = (enum.__qualname__, raw)
            if key not in _reported_unknown:
                _reported_unknown.add(key)
                _LOGGER.warning("Controller reported %s = %d, which this version of aiosolarfocus does not know", enum.__qualname__, raw)
            return None

    kwargs.setdefault("kind", INPUT)
    kwargs.setdefault("signed", False)
    kwargs.setdefault("decode_fn", decode_fn)
    kwargs.setdefault("encode_fn", int)
    kwargs.setdefault("enum", enum)
    return Register[E](at=at, **kwargs)


def unscaled(at: int | Mapping[ApiVersion, int], **kwargs: Any) -> Register[int]:
    """A plain integer reading: a flow rate, a speed, a count."""
    kwargs.setdefault("kind", INPUT)
    return Register[int](at=at, **kwargs)
