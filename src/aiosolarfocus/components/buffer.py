"""The buffer: input block 1900 stride 20, holding block 34000 stride 50."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from ..const import ApiVersion, Systems, every_system_but
from ..enums import BufferMode
from ..registers import HOLDING, READ_WRITE, celsius, code, enum_, flag
from .base import Component


class Buffer(Component):
    """A buffer tank: what it is holding and whether it is being charged."""

    #: As for the heating circuit, a therminator and an ecotop use the block
    #: 25.030 gave everyone else whatever firmware they run.
    layout_as_of: ClassVar[Mapping[Systems, ApiVersion]] = {
        Systems.THERMINATOR: ApiVersion.V_25_030,
        Systems.ECOTOP: ApiVersion.V_25_030,
    }

    top_temperature = celsius(0, doc="Puffertemperatur oben")
    bottom_temperature = celsius(1, doc="Puffertemperatur unten")
    #: The register document says of 1902: "therminator only".
    x35_temperature = celsius(2, systems=frozenset({Systems.THERMINATOR, Systems.ECOTOP}), doc="Puffertemperatur X35")

    pump = flag({ApiVersion.V_20_110: 2, ApiVersion.V_25_030: 3}, signed=True, doc="Puffer – Ladepumpe")
    state = code({ApiVersion.V_20_110: 3, ApiVersion.V_25_030: 4}, doc="Pufferstatus")
    mode = enum_({ApiVersion.V_20_110: 4, ApiVersion.V_25_030: 5}, BufferMode, doc="Puffer – Freigabeart")

    #: External buffer temperatures, fed to the controller from sensors of your
    #: own. A therminator and an ecotop do not have them: the predecessor
    #: reached the same conclusion by accident - `TherminatorBuffer.__init__`
    #: dropped the holding address on its way to `super().__init__` - and the
    #: Home Assistant integration states it deliberately, marking these three
    #: entities `unsupported_systems=[THERMINATOR, ECOTOP]`. Two independent
    #: sources agreeing is enough to carry the behaviour over rather than
    #: "fix" it against hardware nobody here can test.
    _external = every_system_but(Systems.THERMINATOR, Systems.ECOTOP)
    external_top_temperature_x44 = celsius(0, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_22_090, systems=_external, doc="Puffertemperatur oben X44 extern")
    external_middle_temperature_x36 = celsius(1, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_22_090, systems=_external, doc="Puffertemperatur unten/Mitte X36 extern")
    external_bottom_temperature_x35 = celsius(2, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_22_090, systems=_external, doc="Puffertemperatur unten X35 extern")

    async def set_external_top_temperature(self, celsius: float) -> None:
        """Feed the buffer a top temperature from a sensor of your own."""
        await self.write(Buffer.external_top_temperature_x44, celsius)

    async def set_external_middle_temperature(self, celsius: float) -> None:
        """Feed the buffer a middle temperature from a sensor of your own."""
        await self.write(Buffer.external_middle_temperature_x36, celsius)

    async def set_external_bottom_temperature(self, celsius: float) -> None:
        """Feed the buffer a bottom temperature from a sensor of your own."""
        await self.write(Buffer.external_bottom_temperature_x35, celsius)

    async def set_external_temperatures(self, *, top: float | None = None, middle: float | None = None, bottom: float | None = None) -> None:
        """Feed the buffer several external temperatures at once.

        One hold of the transport lock, so the controller sees a consistent set
        rather than a top from this minute and a bottom from the last.
        """
        values = {
            Buffer.external_top_temperature_x44: top,
            Buffer.external_middle_temperature_x36: middle,
            Buffer.external_bottom_temperature_x35: bottom,
        }
        await self.write_many({register: value for register, value in values.items() if value is not None})
