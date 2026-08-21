"""The circulation group: input block 900, stride 25."""

from __future__ import annotations

from ..registers import celsius, flag
from .base import Component


class Circulation(Component):
    """A hot water circulation loop."""

    temperature = celsius(0, doc="Zirkulationstemperatur")
    pump = flag(1, doc="Zirkulationspumpe Ein/Aus")
