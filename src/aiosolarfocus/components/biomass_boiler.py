"""The biomass boiler: input block 2400, holding block 33400 from 22.090."""

from __future__ import annotations

from ..const import NOT_WIRED_PERCENT, ApiVersion, Systems, every_system_but
from ..registers import HOLDING, READ_WRITE, celsius, code, flag, percent, tenths, unscaled
from .base import Component

#: The ecotop is the one biomass boiler without a chimney sweep function or a
#: pellet store to reset.
_NOT_ECOTOP = every_system_but(Systems.ECOTOP)

#: Ascheboxfüllstand's own "not present" marker: -999, seen on a real ecotop in
#: home-assistant-solarfocus#237. Nothing else in the document uses -999 this
#: way, so it is not part of the wider NOT_WIRED_PERCENT - just this register's
#: unsigned reading, the way sentinels are matched.
_ASH_CONTAINER_NOT_PRESENT = frozenset({2**16 - 999})

#: Registers 2409 and 2412 belong to a therminator, 2411 to an octoplus, and the
#: document says so in their names. Reading across them on a system that has
#: neither is what put a Pellet Elegance's return flow temperature one register
#: out of step: 2409 and 2413 are unmapped there, and a read spanning them comes
#: back compacted rather than padded, so every value after the gap shifts into
#: the wrong name. See home-assistant-solarfocus issue #217 and docs/protocol.md.
_THERMINATOR = frozenset({Systems.THERMINATOR})
_OCTOPLUS = frozenset({Systems.OCTOPLUS})


class BiomassBoiler(Component):
    """The pellet, log wood or combination boiler of a non-vampair system."""

    temperature = celsius(0, doc="Kesseltemperatur")
    status = code(1, doc="Statuszeile Kessel")
    time_of_operation_at_maintenance = unscaled(2, width=2, unit="min", doc="Betriebsminuten zum Wartungszeitpunkt")
    message_number = unscaled(4, doc="Nachrichtennummer")
    door_contact = flag(5, signed=True, doc="Türkontakt offen/geschlossen")
    cleaning = percent(6, sentinels=NOT_WIRED_PERCENT, doc="Kesselreinigung")
    ash_container = percent(7, sentinels=_ASH_CONTAINER_NOT_PRESENT, doc="Ascheboxfüllstand")
    outdoor_temperature = celsius(8, doc="Außentemperatur")
    #: The document calls this one therminator, and only a therminator maps it.
    boiler_operating_mode = code(9, signed=True, systems=_THERMINATOR, doc="Kesselbetriebsart therminator")

    #: 2410 is two different measurements at one address, and on one system it is
    #: none. The register document splits it three ways: the octoplus reads the
    #: bottom of its buffer there, the ecotop and the pellet elegance read the
    #: boiler's return flow, and on a therminator the address is unassigned.
    #: Declaring both names with disjoint `systems` says that once; declaring
    #: them without would have a therminator reporting whatever an unassigned
    #: register happens to hold, and would read one address twice.
    octoplus_buffer_temperature_bottom = celsius(10, systems=_OCTOPLUS, doc="SpeichertemperaturUnten octoplus")
    return_temperature = celsius(10, systems=frozenset({Systems.ECOTOP, Systems.PELLETELEGANCE}), doc="Rücklauftemperatur")
    octoplus_buffer_temperature_top = celsius(11, systems=_OCTOPLUS, doc="SpeichertemperaturOben octoplus")

    #: A log wood input is a therminator thing. The predecessar gated this as
    #: "not an ecotop" only because it shared an `if` with the sweep function,
    #: which every non-ecotop boiler does have.
    log_wood = flag(12, since=ApiVersion.V_22_090, systems=_THERMINATOR, doc="Stückholz therminator")

    pellet_usage_last_fill = tenths(14, width=2, signed=False, unit="kg", since=ApiVersion.V_23_010, doc="Pelletverbrauch seit letzter Lagerraumbefüllung")
    pellet_usage_total = tenths(16, width=2, signed=False, unit="kg", since=ApiVersion.V_23_010, doc="Pelletverbrauch gesamt seit Update auf V21.050 oder jünger")
    heat_energy_total = tenths(18, width=2, signed=False, unit="kWh", since=ApiVersion.V_23_010, doc="produzierte Wärmemenge gesamt seit Update auf V21.050 oder jünger")

    sweep_almost_done = flag(20, since=ApiVersion.V_23_080, signed=True, doc="Kaminkehrer kurz vor Ende")
    residual_oxygen_level = percent(21, scale=0.1, signed=False, since=ApiVersion.V_25_020, doc="Restsauerstoffgehalt")
    return_flow_booster_pump = flag(22, since=ApiVersion.V_25_020, doc="Rücklaufanhebungspumpe Ein/Aus")

    outdoor_temperature_external = celsius(6, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_23_010, bounds=(-50.0, 60.0), doc="Außentemperatur extern")
    sweep_function_start_stop = flag(10, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_22_090, systems=_NOT_ECOTOP, signed=True, doc="Kaminkehrerfunktion Start/Stopp")
    sweep_function_extend = flag(11, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_22_090, systems=_NOT_ECOTOP, signed=True, doc="Kaminkehrer Messung verlängern")
    pellet_usage_reset = flag(12, kind=HOLDING, access=READ_WRITE, since=ApiVersion.V_23_010, systems=_NOT_ECOTOP, signed=True, doc="Pelletvorratslagerraum befüllt")

    async def start_sweep(self) -> None:
        """Start the chimney sweep measurement run."""
        await self.write(BiomassBoiler.sweep_function_start_stop, True)

    async def stop_sweep(self) -> None:
        """Stop the chimney sweep measurement run."""
        await self.write(BiomassBoiler.sweep_function_start_stop, False)

    async def extend_sweep(self) -> None:
        """Give the sweep measurement more time."""
        await self.write(BiomassBoiler.sweep_function_extend, True)

    async def reset_pellet_store(self) -> None:
        """Tell the controller the pellet store has been refilled."""
        await self.write(BiomassBoiler.pellet_usage_reset, True)

    async def set_outdoor_temperature(self, celsius: float) -> None:
        """Feed the boiler an outdoor temperature from a sensor of your own."""
        await self.write(BiomassBoiler.outdoor_temperature_external, celsius)
