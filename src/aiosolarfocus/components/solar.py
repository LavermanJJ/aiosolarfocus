"""The solar thermal circuit: input block 2100, stride 20."""

from __future__ import annotations

from ..const import ApiVersion
from ..registers import celsius, code, flag, percent, tenths, unscaled
from .base import Component


class Solar(Component):
    """A solar thermal circuit: collectors, the heat meter, and the store."""

    collector_temperature_1 = celsius(0, doc="Kollektortemperatur 1")
    collector_temperature_2 = celsius(1, doc="Kollektortemperatur 2")
    collector_supply_temperature = celsius(2, doc="Kollektorvorlauftemperatur")
    collector_return_temperature = celsius(3, doc="Kollektorrücklauftemperatur")
    #: The document is right that 2104 has no scale factor, and the predecessor
    #: was wrong to scale it by a tenth: a Therminator 2 reported 23.3 where the
    #: eco-manager-touch's own display read 233, so the raw register is the
    #: reading. The display also gives it as l/h, not the document's l - it is a
    #: rate, not a volume. See home-assistant-solarfocus#239.
    flow_heat_meter = unscaled(4, unit="l/h", doc="Durchfluss WMZ")
    current_power = tenths(5, unit="kW", doc="aktuelle Leistung")
    current_yield_heat_meter = unscaled(6, width=2, unit="Wh", doc="Ertrag WMZ")
    today_yield = unscaled(8, width=2, unit="Wh", doc="Tagesertrag")
    buffer_sensor_1 = celsius(10, doc="Speicherfühler 1")
    buffer_sensor_2 = celsius(11, doc="Speicherfühler 2")
    buffer_sensor_3 = celsius(12, doc="Speicherfühler 3")
    state = code(13, doc="Solar – Statuszeile")

    relay_o1 = flag(14, since=ApiVersion.V_25_030, doc="Relais O1 Ein/Aus")
    control_out_1 = percent(15, since=ApiVersion.V_25_030, signed=False, doc="Ansteuerung Out 1")
    relay_o2 = flag(16, since=ApiVersion.V_25_030, doc="Relais O2 Ein/Aus")
    control_out_2 = percent(17, since=ApiVersion.V_25_030, signed=False, doc="Ansteuerung Out 2")
