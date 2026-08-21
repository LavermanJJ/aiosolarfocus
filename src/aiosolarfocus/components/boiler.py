"""The domestic hot water boiler: input block 500, holding block 32000, stride 50."""

from __future__ import annotations

from ..const import ApiVersion
from ..enums import DomesticHotWaterMode
from ..registers import HOLDING, READ_WRITE, celsius, code, enum_, flag
from .base import Component


class Boiler(Component):
    """A hot water tank: how warm it is, and when it may charge."""

    temperature = celsius(0, doc="Boiler – Temperatur")
    state = code(1, doc="Boiler Status")
    mode = enum_(2, DomesticHotWaterMode, doc="Boiler Freigabeart – Ist")

    target_temperature = celsius(0, kind=HOLDING, access=READ_WRITE, bounds=(0.0, 80.0), step=0.5, doc="Boiler – Solltemperatur")
    single_charge = flag(1, kind=HOLDING, access=READ_WRITE, signed=True, doc="Boiler – Einmalladung")
    holding_mode = enum_(2, DomesticHotWaterMode, kind=HOLDING, access=READ_WRITE, signed=True, doc="Boiler – Freigabeart")
    circulation = flag(3, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_20_110, signed=True, doc="Zirkulation anfordern")

    async def set_mode(self, mode: DomesticHotWaterMode) -> None:
        """Set when this boiler is allowed to charge."""
        await self.write(Boiler.holding_mode, mode)

    async def set_target_celsius(self, celsius: float) -> None:
        """Set the hot water setpoint."""
        await self.write(Boiler.target_temperature, celsius)

    async def set_single_charge(self, charging: bool) -> None:
        """Ask for one charge now, outside the schedule."""
        await self.write(Boiler.single_charge, charging)

    async def request_circulation(self) -> None:
        """Ask for a circulation run, so hot water reaches the tap sooner."""
        await self.write(Boiler.circulation, True)
