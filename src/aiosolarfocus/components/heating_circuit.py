"""The heating circuit: input block 1100, holding block 32600, stride 50."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from ..const import ApiVersion, Systems
from ..enums import HeatingCircuitCooling, HeatingCircuitHeatingMode, HeatingCircuitMode
from ..registers import HOLDING, READ_WRITE, celsius, code, enum_, flag, percent
from .base import Component


class HeatingCircuit(Component):
    """One heating circuit: a flow celsius, a mixer, and when it may run."""

    #: A therminator and an ecotop are laid out the way 25.030 laid the block out
    #: for everyone else, whatever firmware they run - their block is longer by
    #: one from `circulator_pump` down. This is what the predecessor's
    #: `TherminatorHeatingCircuit` subclass was for, and that subclass dropped
    #: `api_version` on its way to `super().__init__`, so those systems also
    #: resolved every *other* register against a default version. On a 21.140
    #: therminator that declared `heating_mode` at 32608 - a register that
    #: firmware does not have - and, because the controller compacts a read that
    #: spans an unmapped address rather than padding it, silently shifted the
    #: three registers before it.
    layout_as_of: ClassVar[Mapping[Systems, ApiVersion]] = {
        Systems.THERMINATOR: ApiVersion.V_25_030,
        Systems.ECOTOP: ApiVersion.V_25_030,
    }

    supply_temperature = celsius(0, doc="Vorlauftemperatur")
    room_temperature = celsius(1, doc="Raumtemperatur")
    humidity = percent(2, scale=0.1, doc="Feuchte")
    limit_thermostat = flag(3, doc="Begrenzungsthermostat offen/geschlossen")

    circulator_pump = flag({ApiVersion.V_20_110: 4, ApiVersion.V_25_030: 5}, doc="Heizkreispumpe Ein/Aus")
    mixer_valve = percent({ApiVersion.V_20_110: 5, ApiVersion.V_25_030: 6}, signed=False, doc="Mischerstellung")
    state = code({ApiVersion.V_20_110: 6, ApiVersion.V_25_030: 7}, doc="Status Heizkreis")

    target_supply_temperature = celsius(0, kind=HOLDING, access=READ_WRITE, bounds=(0.0, 80.0), step=0.5, doc="Vorlaufsolltemperatur Heizen / Kühlen")
    cooling = enum_(2, HeatingCircuitCooling, kind=HOLDING, access=READ_WRITE, signed=True, doc="Kühlen Ein/Aus")
    mode = enum_(3, HeatingCircuitMode, kind=HOLDING, access=READ_WRITE, signed=True, doc="Heizkreisbetriebsart")
    target_room_temperature = celsius(5, kind=HOLDING, access=READ_WRITE, bounds=(0.0, 45.0), step=0.5, doc="Raumtemperatur Soll")
    indoor_temperature_external = celsius(6, kind=HOLDING, access=READ_WRITE, bounds=(-50.0, 60.0), doc="Raumtemperatur Ist extern")
    #: 32607 is reported in tenths of a percent and accepted as a whole percent -
    #: the controller rejects anything outside 1 to 100. The register document
    #: lists no scale factor at all; the controller is the authority here.
    #: See home-assistant-solarfocus issue #150.
    indoor_humidity_external = percent(7, kind=HOLDING, access=READ_WRITE, scale=0.1, write_scale=1.0, bounds=(1.0, 100.0), doc="Raumfeuchte ist extern")
    heating_mode = enum_(8, HeatingCircuitHeatingMode, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_22_090, signed=True, doc="Heizkreismodus")

    async def set_mode(self, mode: HeatingCircuitMode) -> None:
        """Set when this circuit is allowed to run."""
        await self.write(HeatingCircuit.mode, mode)

    async def set_cooling(self, cooling: HeatingCircuitCooling) -> None:
        """Switch this circuit between heating and cooling."""
        await self.write(HeatingCircuit.cooling, cooling)

    async def set_heating_mode(self, heating_mode: HeatingCircuitHeatingMode) -> None:
        """Set what this circuit is allowed to do, heating, cooling or both."""
        await self.write(HeatingCircuit.heating_mode, heating_mode)

    async def set_target_supply_celsius(self, celsius: float) -> None:
        """Set the flow setpoint."""
        await self.write(HeatingCircuit.target_supply_temperature, celsius)

    async def set_target_room_celsius(self, celsius: float) -> None:
        """Set the room setpoint."""
        await self.write(HeatingCircuit.target_room_temperature, celsius)

    async def set_indoor_celsius(self, celsius: float) -> None:
        """Feed this circuit a room temperature from a sensor of your own."""
        await self.write(HeatingCircuit.indoor_temperature_external, celsius)

    async def set_indoor_humidity(self, percentage: float) -> None:
        """Feed this circuit a room humidity from a sensor of your own."""
        await self.write(HeatingCircuit.indoor_humidity_external, percentage)

    async def set_operating_state(
        self,
        *,
        mode: HeatingCircuitMode | None = None,
        cooling: HeatingCircuitCooling | None = None,
        heating_mode: HeatingCircuitHeatingMode | None = None,
        target_supply_temperature: float | None = None,
    ) -> None:
        """Change several of this circuit's settings together.

        The register document asks for the flow setpoint, the cooling flag and
        the operating mode to be written as one; a poll landing between two of
        them leaves the controller in a state the document does not describe.
        The Home Assistant climate entity writes exactly this group, four
        separate blocking writes at a time, each followed by re-reading the
        whole component.
        """
        values = {
            HeatingCircuit.mode: mode,
            HeatingCircuit.cooling: cooling,
            HeatingCircuit.heating_mode: heating_mode,
            HeatingCircuit.target_supply_temperature: target_supply_temperature,
        }
        await self.write_many({register: value for register, value in values.items() if value is not None})
