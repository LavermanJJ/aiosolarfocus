"""The component registry: one row per component a controller can have.

Adding a component to this library is one row here and one file next to it.
That is deliberate: in the predecessor the same knowledge was spread over a
factory that computed base addresses, a manager that decided which components to
build, and a facade that hoisted them into attributes - and the three disagreed.
`ComponentFactory.fresh_water_module_cascade` and `.circulation_module` were
never called by the manager, so the facade's `fresh_water_module_cascade` was
always None and `update_fresh_water_module_cascade()` raised `AttributeError` on
every controller running 23.040 or newer. One list cannot drift from itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..const import ApiVersion, Systems, every_system_but
from .base import Component
from .biomass_boiler import BiomassBoiler
from .boiler import Boiler
from .buffer import Buffer
from .circulation import Circulation
from .differential_module import DifferentialModule
from .fresh_water_module import CirculationModule, FreshWaterModule, FreshWaterModuleCascade
from .heat_pump import HeatPump
from .heating_circuit import HeatingCircuit
from .photovoltaic import Photovoltaic
from .solar import Solar

__all__ = [
    "COMPONENTS",
    "BiomassBoiler",
    "Boiler",
    "Buffer",
    "Circulation",
    "Component",
    "ComponentId",
    "ComponentSpec",
    "DifferentialModule",
    "FreshWaterModule",
    "FreshWaterModuleCascade",
    "HeatPump",
    "HeatingCircuit",
    "Photovoltaic",
    "Solar",
    "spec_for",
]


class ComponentId(StrEnum):
    """How a caller names a component: in a config, on the command line, in a failure."""

    HEATING_CIRCUITS = "heating_circuits"
    BUFFERS = "buffers"
    BOILERS = "boilers"
    FRESH_WATER_MODULES = "fresh_water_modules"
    FRESH_WATER_MODULE_CASCADE = "fresh_water_module_cascade"
    CIRCULATION_MODULE = "circulation_module"
    CIRCULATIONS = "circulations"
    DIFFERENTIAL_MODULES = "differential_modules"
    SOLAR = "solar"
    HEAT_PUMP = "heat_pump"
    PHOTOVOLTAIC = "photovoltaic"
    BIOMASS_BOILER = "biomass_boiler"


@dataclass(frozen=True, slots=True, kw_only=True)
class ComponentSpec:
    """Where a component's registers live, and how many of it a controller may have."""

    id: ComponentId
    label: str
    component: type[Component]
    input_base: int | None = None
    holding_base: int | None = None
    #: How far apart consecutive instances are. Zero for a component a
    #: controller only ever has one of.
    input_stride: int = 0
    holding_stride: int = 0
    max_count: int = 1
    #: The firmware that introduced the component, and - separately - the one
    #: that gave it holding registers. A buffer predates its external
    #: temperature inputs by several versions.
    since: ApiVersion = ApiVersion.V_20_110
    holding_since: ApiVersion = ApiVersion.V_20_110
    systems: frozenset[Systems] | None = None

    @property
    def multiple(self) -> bool:
        """Whether a controller can have more than one of these."""
        return self.max_count > 1

    def available(self, api_version: ApiVersion, system: Systems) -> bool:
        """Whether this controller can have this component at all."""
        return self.since <= api_version and (self.systems is None or system in self.systems)

    def limit(self, api_version: ApiVersion) -> int:
        """How many of these this firmware supports.

        Before 25.030 a controller addressed one solar circuit; 25.030 gave it
        four. Nothing else changed count with a firmware.
        """
        if self.id is ComponentId.SOLAR and api_version < ApiVersion.V_25_030:
            return 1
        return self.max_count

    def bases(self, index: int, api_version: ApiVersion) -> tuple[int | None, int | None]:
        """The input and holding base addresses of the `index`-th instance, 0-based."""
        input_base = None if self.input_base is None else self.input_base + index * self.input_stride
        holding_base = None
        if self.holding_base is not None and api_version >= self.holding_since:
            holding_base = self.holding_base + index * self.holding_stride
        return input_base, holding_base


COMPONENTS: tuple[ComponentSpec, ...] = (
    ComponentSpec(
        id=ComponentId.HEATING_CIRCUITS,
        label="heating circuit",
        component=HeatingCircuit,
        input_base=1100,
        holding_base=32600,
        input_stride=50,
        holding_stride=50,
        max_count=8,
    ),
    ComponentSpec(
        id=ComponentId.BUFFERS,
        label="buffer",
        component=Buffer,
        input_base=1900,
        holding_base=34000,
        input_stride=20,
        holding_stride=50,
        max_count=4,
        holding_since=ApiVersion.V_22_090,
    ),
    ComponentSpec(
        id=ComponentId.BOILERS,
        label="boiler",
        component=Boiler,
        input_base=500,
        holding_base=32000,
        input_stride=50,
        holding_stride=50,
        max_count=4,
    ),
    ComponentSpec(
        id=ComponentId.FRESH_WATER_MODULES,
        label="fresh water module",
        component=FreshWaterModule,
        input_base=700,
        input_stride=25,
        max_count=4,
        since=ApiVersion.V_23_020,
    ),
    ComponentSpec(
        id=ComponentId.FRESH_WATER_MODULE_CASCADE,
        label="fresh water module cascade",
        component=FreshWaterModuleCascade,
        input_base=800,
        since=ApiVersion.V_23_040,
    ),
    ComponentSpec(
        id=ComponentId.CIRCULATION_MODULE,
        label="circulation module",
        component=CirculationModule,
        input_base=850,
        since=ApiVersion.V_23_040,
    ),
    ComponentSpec(
        id=ComponentId.CIRCULATIONS,
        label="circulation",
        component=Circulation,
        input_base=900,
        input_stride=25,
        max_count=4,
        since=ApiVersion.V_25_030,
    ),
    ComponentSpec(
        id=ComponentId.DIFFERENTIAL_MODULES,
        label="differential module",
        component=DifferentialModule,
        input_base=2200,
        input_stride=10,
        max_count=4,
        since=ApiVersion.V_25_030,
    ),
    ComponentSpec(
        id=ComponentId.SOLAR,
        label="solar circuit",
        component=Solar,
        input_base=2100,
        input_stride=20,
        max_count=4,
    ),
    ComponentSpec(
        id=ComponentId.HEAT_PUMP,
        label="heat pump",
        component=HeatPump,
        input_base=2300,
        holding_base=33404,
        systems=frozenset({Systems.VAMPAIR}),
    ),
    ComponentSpec(
        id=ComponentId.PHOTOVOLTAIC,
        label="photovoltaic",
        component=Photovoltaic,
        input_base=2500,
        holding_base=33407,
    ),
    ComponentSpec(
        id=ComponentId.BIOMASS_BOILER,
        label="biomass boiler",
        component=BiomassBoiler,
        input_base=2400,
        holding_base=33400,
        holding_since=ApiVersion.V_22_090,
        #: Every system but the vampair is a biomass boiler, so name the one
        #: that is not. The predecessor listed the boilers instead, and left
        #: Pellet Elegance and Octoplus - present in `Systems` and built by the
        #: factory, but in neither branch - never read at all, reporting success
        #: while every register kept its default of zero.
        systems=every_system_but(Systems.VAMPAIR),
    ),
)

_BY_ID = {spec.id: spec for spec in COMPONENTS}


def spec_for(component_id: ComponentId) -> ComponentSpec:
    """Look up one component's row."""
    return _BY_ID[component_id]
