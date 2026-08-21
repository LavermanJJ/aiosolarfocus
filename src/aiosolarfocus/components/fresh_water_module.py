"""Fresh water modules: the module itself, the cascade over them, and circulation."""

from __future__ import annotations

from ..const import ApiVersion
from ..registers import celsius, code, tenths, unscaled
from .base import Component


class FreshWaterModule(Component):
    """One fresh water module: input block 700, stride 25."""

    state = code(0, doc="Statuszeile")
    supply_temperature = celsius(1, since=ApiVersion.V_23_040, doc="WW-Vorlauftemperatur")
    flow_rate = tenths(2, since=ApiVersion.V_23_040, unit="l/min", doc="WW-Durchfluss")
    target_temperature = celsius(3, since=ApiVersion.V_23_040, doc="WW-Solltemperatur")
    valve = unscaled(4, signed=False, since=ApiVersion.V_23_040, doc="Ventilstellung FWM Kaskade")


class FreshWaterModuleCascade(Component):
    """The cascade over several fresh water modules: input block 800."""

    state = code(0, doc="Statuszeile Kaskade FWM")
    total_flow_rate = tenths(1, unit="l/min", doc="FWM Kaskade Gesamtdurchfluss")
    target_temperature = celsius(2, doc="FWM Kaskade Solltemperatur")


class CirculationModule(Component):
    """The hot water circulation module: input block 850."""

    dhw_supply_temperature = celsius(0, doc="Zirkulationsmodul WW-Vorlauftemperatur")
    dhw_flow_rate = tenths(1, unit="l/min", doc="Zirkulationsmodul WW-Durchfluss")
