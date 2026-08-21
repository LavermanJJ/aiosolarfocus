"""What a caller says their controller has.

Explicit configuration is the primary path: a caller names the system, the
firmware and how many of each component the installation has. `detect` can work
all of that out from the controller, but it is a helper a caller opts into, not
something that happens on the way to connecting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from typing import NamedTuple

from .components import COMPONENTS, ComponentId, spec_for
from .const import DEFAULT_DEVICE_ID, DEFAULT_PORT, DEFAULT_TIMEOUT, ApiVersion, Systems
from .exceptions import SolarfocusConfigError
from .layout import Layout


class ComponentKey(NamedTuple):
    """Which component, and which one of it.

    `number` is 1-based, as a caller counts them and as the controller's own
    labelling does. Not called `index` because that is `tuple.index`.
    """

    id: ComponentId
    number: int

    def __str__(self) -> str:
        """`heating_circuits.2`, or just `heat_pump` where there is only ever one."""
        return self.id.value if spec_for(self.id).max_count == 1 else f"{self.id.value}.{self.number}"


@dataclass(frozen=True, slots=True, kw_only=True)
class SolarfocusConfig:
    """One controller, and what is wired to it.

    Every count field is named for its `ComponentId`, which is what lets
    `count_of` work without a third copy of the component list. A test holds
    the two together.
    """

    host: str
    port: int = DEFAULT_PORT
    device_id: int = DEFAULT_DEVICE_ID
    system: Systems = Systems.VAMPAIR
    api_version: ApiVersion = ApiVersion.V_21_140
    timeout: float = DEFAULT_TIMEOUT

    heating_circuits: int = 1
    buffers: int = 1
    boilers: int = 1
    fresh_water_modules: int = 0
    circulations: int = 0
    differential_modules: int = 0
    solar: int = 0

    fresh_water_module_cascade: bool = False
    circulation_module: bool = False
    photovoltaic: bool = False
    #: None means "whatever this system has": a vampair has a heat pump and
    #: every other system has a biomass boiler.
    heat_pump: bool | None = None
    biomass_boiler: bool | None = None

    def __post_init__(self) -> None:
        """Refuse a configuration no controller could have, saying which field and why."""
        if not self.host:
            raise SolarfocusConfigError("host is required")
        if self.timeout <= 0:
            raise SolarfocusConfigError(f"timeout must be positive, not {self.timeout}")

        for spec in COMPONENTS:
            count = self.count_of(spec.id)
            if count == 0:
                continue
            if not spec.available(self.api_version, self.system):
                reason = f"{self.system.value} has no {spec.label}" if spec.systems is not None else f"{spec.label}s arrived in firmware {spec.since.label}"
                raise SolarfocusConfigError(f"{spec.id.value}={count} but {reason}, and this controller runs {self.api_version.label}")
            limit = spec.limit(self.api_version)
            if count > limit:
                extra = "" if limit == spec.max_count else f" on firmware {self.api_version.label}"
                raise SolarfocusConfigError(f"{spec.id.value}={count}, but a controller addresses at most {limit} {spec.label}{'s' if limit != 1 else ''}{extra}")

    def count_of(self, component_id: ComponentId) -> int:
        """How many of this component the caller says there are."""
        value = getattr(self, component_id.value)
        if value is None:
            # The system decides: a vampair has the heat pump, everything else
            # has the biomass boiler.
            return int(spec_for(component_id).available(self.api_version, self.system))
        return int(value)

    def component_keys(self) -> tuple[ComponentKey, ...]:
        """Every component instance this configuration says exists."""
        return tuple(ComponentKey(spec.id, number + 1) for spec in COMPONENTS for number in range(self.count_of(spec.id)) if spec.available(self.api_version, self.system))

    def layouts(self) -> Mapping[ComponentKey, Layout]:
        """Resolve every component instance against this system and firmware."""
        resolved = {}
        for key in self.component_keys():
            spec = spec_for(key.id)
            input_base, holding_base = spec.bases(key.number - 1, self.api_version)
            resolved[key] = Layout.resolve(spec.component, self.api_version, self.system, input_base, holding_base)
        return resolved

    @property
    def address(self) -> str:
        """`host:port`, for a message someone has to act on."""
        return f"{self.host}:{self.port}"


def count_field_names() -> frozenset[str]:
    """The configuration fields that say how many of a component there are."""
    ids = {component_id.value for component_id in ComponentId}
    return frozenset(field.name for field in fields(SolarfocusConfig) if field.name in ids)
