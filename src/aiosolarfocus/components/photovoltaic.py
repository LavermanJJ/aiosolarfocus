"""Photovoltaic and the house energy balance: input block 2500, holding block 33407."""

from __future__ import annotations

from ..const import ApiVersion
from ..registers import HOLDING, READ_WRITE, flag, unscaled, watts
from .base import Component


class Photovoltaic(Component):
    """What the array is making and what the house is doing with it.

    The three holding registers here are inputs to the controller, not outputs:
    they are how an external meter or home energy manager tells the heating
    system what the house is doing. Two of them are signed, which is why the
    Home Assistant integration carried its own two's complement - the library
    took `int(value)` and only the library knows the register is signed.
    """

    power = unscaled(0, width=2, unit="W", doc="Leistung PV")
    house_consumption = unscaled(2, width=2, unit="W", doc="Verbrauch")
    heatpump_consumption = unscaled(4, width=2, unit="W", doc="Verbrauch WP")
    grid_import = unscaled(6, width=2, unit="W", doc="Netzbezug")
    grid_export = unscaled(8, width=2, unit="W", doc="Einspeisung")

    overcharge_possible = flag(10, since=ApiVersion.V_21_140, signed=True, doc="PV Überladung möglich")
    overcharge_active = flag(11, since=ApiVersion.V_21_140, signed=True, doc="PV-Überladung aktiv")

    smart_meter = watts(0, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_21_140, doc="Smart Meter")
    photovoltaic = watts(1, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_21_140, doc="Photovoltaik")
    grid_im_export = watts(2, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_21_140, doc="Netzbezug / Einspeisung")
    #: 33415: the target electrical power for the heat generator during PV
    #: overcharge, given by an external home energy management system.
    hems_target_electrical_power = watts(8, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_26_020, doc="Elektrische Sollleistung HEMS (PV)")

    async def set_smart_meter(self, watts: int) -> None:
        """Report what the house meter is reading. Negative means exporting."""
        await self.write(Photovoltaic.smart_meter, watts)

    async def set_photovoltaic(self, watts: int) -> None:
        """Report what the array is making."""
        await self.write(Photovoltaic.photovoltaic, watts)

    async def set_grid_im_export(self, watts: int) -> None:
        """Report the grid balance. Negative means exporting."""
        await self.write(Photovoltaic.grid_im_export, watts)

    async def set_hems_target_electrical_watts(self, watts: int) -> None:
        """Set the power a home energy manager wants the heat generator to draw."""
        await self.write(Photovoltaic.hems_target_electrical_power, watts)
