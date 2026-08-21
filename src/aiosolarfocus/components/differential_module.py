"""The differential module: input block 2200, stride 10."""

from __future__ import annotations

from ..registers import celsius, flag
from .base import Component


class DifferentialModule(Component):
    """Two control loops, each a relay and the pair of temperatures driving it."""

    relay_control_loop_o1 = flag(0, doc="Relais Regelkreis 1 O1 Ein/Aus")
    temperature_1_control_loop_1 = celsius(1, doc="Temperatur 1 Regelkreis 1")
    temperature_2_control_loop_1 = celsius(2, doc="Temperatur 2 Regelkreis 1")
    relay_control_loop_o2 = flag(3, doc="Relais Regelkreis 2 O2 Ein/Aus")
    temperature_1_control_loop_2 = celsius(4, doc="Temperatur 1 Regelkreis 2")
    temperature_2_control_loop_2 = celsius(5, doc="Temperatur 2 Regelkreis 2")
