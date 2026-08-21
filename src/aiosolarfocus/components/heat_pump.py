"""The vampair heat pump: input block 2300, holding block 33404."""

from __future__ import annotations

from typing import ClassVar

from ..const import ApiVersion
from ..enums import HeatPumpSgReadyMode
from ..registers import HOLDING, READ_WRITE, celsius, code, energy, enum_, flag, unscaled, watts
from .base import Component

_V20 = ApiVersion.V_20_110
_V25 = ApiVersion.V_25_030


def _ratio(output: float | None, input_: float | None) -> float | None:
    """Output over input, or None when there is nothing to divide.

    None rather than 0.0 when the heat pump is not drawing power: a zero is a
    reading, and Home Assistant records it as one. The predecessor's
    `PerformanceCalculator` returned 0.0 here, so an idle heat pump reported a
    coefficient of performance of zero all night.
    """
    if output is None or not input_:
        return None
    return round(output / input_, 2)


class HeatPump(Component):
    """The heat pump of a vampair system.

    25.030 inserted registers into the input block, moving everything from
    `defrost_active` down. The addresses below map the version that introduced a
    layout to the offset it put the register at, so the renumbering is data the
    table carries rather than a second copy of the table in an else branch - the
    predecessor wrote it as a fifteen-line if/else pair whose two halves differed
    only by offset.
    """

    derived: ClassVar[tuple[str, ...]] = (
        "cop_heating",
        "cop_cooling",
        "seasonal_performance",
        "seasonal_performance_heating",
        "seasonal_performance_drinking_water",
    )

    supply_temperature = celsius(0, doc="Vorlauftemperatur Wärmepumpe")
    return_temperature = celsius(1, doc="Rücklauftemperatur Wärmepumpe")
    flow_rate = unscaled(2, unit="l/h", doc="Durchfluss")
    compressor_speed = unscaled(3, unit="rpm", doc="Kompressordrehzahl")
    evu_lock_active = flag(4, doc="EVU – Lock aktiv")

    defrost_active = flag({_V20: 5, _V25: 6}, doc="Defrost aktiv")
    boiler_charge = flag({_V20: 6, _V25: 7}, doc="Boilerladung")

    thermal_energy_total = energy({_V20: 7, _V25: 10}, doc="Gesamtenergie thermisch Heizung + Trinkwassererwärmung")
    thermal_energy_drinking_water = energy({_V20: 9, _V25: 12}, doc="thermische Energie Trinkwassererwärmung")
    thermal_energy_heating = energy({_V20: 11, _V25: 14}, doc="thermische Energie Heizung")
    electrical_energy_total = energy({_V20: 13, _V25: 16}, doc="Gesamtenergie elektrisch Heizung + Trinkwassererwärmung")
    electrical_energy_drinking_water = energy({_V20: 15, _V25: 18}, doc="elektr. Energie Trinkwassererwärmung")
    electrical_energy_heating = energy({_V20: 17, _V25: 20}, doc="elektr. Energie Heizung")

    electrical_power = watts({_V20: 19, _V25: 22}, doc="aktuell aufgenommene elektr. Leistung")
    thermal_power_cooling = watts({_V20: 20, _V25: 23}, doc="aktuelle thermische Leistung Kühlen")
    thermal_power_heating = watts({_V20: 21, _V25: 24}, doc="aktuelle thermische Leistung Heizen")

    thermal_energy_cooling = energy({_V20: 22, _V25: 26}, doc="thermische Energie Kühlung")
    electrical_energy_cooling = energy({_V20: 24, _V25: 28}, doc="elekt. Energie Kühlung")

    #: An open enumeration - the firmware keeps adding states - so an int, with
    #: VAMPAIR_STATE in enums.py for anything that wants to name it.
    vampair_state = code({_V20: 26, _V25: 30}, since=ApiVersion.V_20_110, doc="vampair Status")

    #: 2408 is in the biomass boiler's block: both components read the same
    #: outdoor sensor, and only one of them is ever present, so this always costs
    #: a round trip of its own. Declared absolute rather than as offset 108 from
    #: 2300 so that it is plainly one address elsewhere rather than the far end
    #: of a 109-register block - which is how the predecessor sized its read
    #: buffer for the heat pump, to hold twenty values.
    outdoor_temperature = celsius(2408, absolute=True, doc="Außentemperatur")

    evu_lock = flag(0, kind=HOLDING, access=READ_WRITE, signed=True, doc="EVU – Lock")
    smart_grid = enum_(1, HeatPumpSgReadyMode, kind=HOLDING, access=READ_WRITE, signed=True, doc="Betriebsart SG – Ready")
    outdoor_temperature_external = celsius(2, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_20_110, bounds=(-50.0, 60.0), doc="Außentemperatur extern")

    @property
    def cop_heating(self) -> float | None:
        """Coefficient of performance while heating, right now."""
        return _ratio(self.thermal_power_heating, self.electrical_power)

    @property
    def cop_cooling(self) -> float | None:
        """Coefficient of performance while cooling, right now."""
        return _ratio(self.thermal_power_cooling, self.electrical_power)

    @property
    def seasonal_performance(self) -> float | None:
        """Thermal energy delivered per unit of electrical energy taken, overall."""
        return _ratio(self.thermal_energy_total, self.electrical_energy_total)

    @property
    def seasonal_performance_heating(self) -> float | None:
        """The same, counting only heating."""
        return _ratio(self.thermal_energy_heating, self.electrical_energy_heating)

    @property
    def seasonal_performance_drinking_water(self) -> float | None:
        """The same, counting only hot water."""
        return _ratio(self.thermal_energy_drinking_water, self.electrical_energy_drinking_water)

    async def set_evu_lock(self, locked: bool) -> None:
        """Block or release the heat pump on behalf of the utility."""
        await self.write(HeatPump.evu_lock, locked)

    async def set_sg_ready_mode(self, mode: HeatPumpSgReadyMode) -> None:
        """Tell the heat pump what the SG Ready signal is."""
        await self.write(HeatPump.smart_grid, mode)

    async def set_outdoor_temperature(self, celsius: float) -> None:
        """Feed the heat pump an outdoor temperature from a sensor of your own."""
        await self.write(HeatPump.outdoor_temperature_external, celsius)
