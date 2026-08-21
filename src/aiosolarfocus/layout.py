"""Working out which registers a component has here, and at what address.

This is the only place firmware versions and systems branch. A component class
declares its registers once; `Layout.resolve` decides which of them this
firmware on this system actually has, and turns each offset into the absolute
address the planner will ask the controller for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .const import ApiVersion, RegisterKind, Systems
from .exceptions import SolarfocusConfigError
from .registers import Register, RegisterInfo

if TYPE_CHECKING:
    from .components.base import Component


@dataclass(frozen=True, slots=True)
class ResolvedRegister:
    """One register of one component instance, pinned to an absolute address."""

    register: Register[Any]
    name: str
    kind: RegisterKind
    address: int
    width: int

    @property
    def addresses(self) -> range:
        """Every address this register occupies - two of them when it is 32-bit."""
        return range(self.address, self.address + self.width)

    def info(self) -> RegisterInfo:
        """Describe this register to a caller that has to build a UI out of it."""
        register = self.register
        return RegisterInfo(
            name=self.name,
            kind=self.kind,
            address=self.address,
            width=self.width,
            signed=register.signed,
            scale=register.scale,
            unit=register.unit,
            access=register.access,
            bounds=register.bounds,
            step=register.step,
            since=register.since,
            systems=register.systems,
            enum=register.enum,
            doc=register.doc,
        )


@dataclass(frozen=True, eq=False, slots=True)
class Layout:
    """The register set of one component instance on one controller."""

    component: type[Component]
    api_version: ApiVersion
    system: Systems
    input_base: int | None
    holding_base: int | None
    #: Sorted by kind then address, which is the order the planner wants.
    registers: tuple[ResolvedRegister, ...]
    by_name: Mapping[str, ResolvedRegister]

    def of_kind(self, kind: RegisterKind) -> tuple[ResolvedRegister, ...]:
        """The registers of one Modbus table, in address order."""
        return tuple(resolved for resolved in self.registers if resolved.kind is kind)

    @staticmethod
    def resolve(
        component: type[Component],
        api_version: ApiVersion,
        system: Systems,
        input_base: int | None = None,
        holding_base: int | None = None,
    ) -> Layout:
        """Decide this component's register set, cached on the arguments.

        There are few distinct combinations of them - eight heating circuits on
        one controller share four of the five - so the cache is small and lives
        for the process. Written out rather than `functools.cache` because that
        decorator's stubs insist every argument be `Hashable`, and mypy does not
        believe a class object is.
        """
        key = (component, api_version, system, input_base, holding_base)
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        layout_version = max(api_version, component.layout_as_of.get(system, api_version))

        resolved: list[ResolvedRegister] = []
        for register in component.declared():
            if not register.available(api_version, system):
                continue
            offset = register.offset_for(layout_version)
            if offset is None:  # pragma: no cover - `available` already ruled this out
                continue

            if register.absolute:
                address = offset
            else:
                base = input_base if register.kind is RegisterKind.INPUT else holding_base
                if base is None or base < 0:
                    # This component has no block of that kind on this
                    # controller - a buffer before 22.090 has no holding
                    # registers at all - so the register is simply not there.
                    continue
                address = base + offset

            resolved.append(ResolvedRegister(register, register.name, register.kind, address, register.width))

        resolved.sort(key=lambda item: (item.kind is RegisterKind.HOLDING, item.address))
        _check_no_overlap(component, system, api_version, resolved)

        layout = Layout(
            component=component,
            api_version=api_version,
            system=system,
            input_base=input_base,
            holding_base=holding_base,
            registers=tuple(resolved),
            by_name=MappingProxyType({item.name: item for item in resolved}),
        )
        _CACHE[key] = layout
        return layout


_CacheKey = tuple[type["Component"], ApiVersion, Systems, int | None, int | None]
_CACHE: dict[_CacheKey, Layout] = {}


def _check_no_overlap(component: type[Component], system: Systems, api_version: ApiVersion, resolved: list[ResolvedRegister]) -> None:
    """Refuse a table that reads one address twice, or reads half of a 32-bit register.

    Two registers at one address is always a mistake in the table - a copied
    offset, a renumbering applied to one register and not its neighbour - and it
    is the kind of mistake that produces plausible readings rather than an
    error. Checking it here checks it for every component on every system and
    every firmware, rather than for whichever one someone wrote a test for.
    """
    # Keyed by table as well as address: input 1100 and holding 1100 are
    # different registers on the controller, and several components read one of
    # each at the same number.
    claimed: dict[tuple[RegisterKind, int], str] = {}
    for item in resolved:
        for address in item.addresses:
            previous = claimed.get((item.kind, address))
            if previous is not None:
                raise SolarfocusConfigError(
                    f"{component.__name__} declares both {previous!r} and {item.name!r} at {item.kind.value} register {address}",
                    context=f"{system.value} on {api_version.label}",
                )
            claimed[(item.kind, address)] = item.name
