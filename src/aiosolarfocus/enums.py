"""The enumerations the controller uses, and the German text it names them with.

Two kinds live here, and the register tables treat them differently.

*Closed* enumerations - a mode, a cooling flag - are fully documented and the
firmware does not extend them, so a register declared with `enum_()` decodes to
one of these `IntEnum` members.

*Open* enumerations - the operating states - grow with every firmware. A
register declared with `code()` stays an `int`, and the `*_STATE` tables below
name the codes we know without `HeatingCircuitState(99)` blowing up on one we
do not. The tables are the controller's own German wording, kept verbatim so a
reading here matches what the owner sees on the machine; translating them is
the presentation layer's job.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum
from types import MappingProxyType


class HeatingCircuitMode(IntEnum):
    """How a heating circuit decides when to run."""

    ALWAYS_ON = 0
    REDUCED_OPERATION = 1
    AUTOMATIC = 2
    OFF = 3


class HeatingCircuitCooling(IntEnum):
    """Whether a heating circuit is heating or cooling."""

    HEATING = 0
    COOLING = 1


class HeatingCircuitHeatingMode(IntEnum):
    """What a heating circuit is allowed to do.

    Requires cooling and room temperature influence to be enabled on the
    controller; without them the register is there but does nothing.
    """

    HEATING = 0
    COOLING = 1
    HEATING_AND_COOLING = 2


class DomesticHotWaterMode(IntEnum):
    """When the boiler is allowed to charge."""

    ALWAYS_OFF = 0
    ALWAYS_ON = 1
    MONDAY_SUNDAY = 2
    BLOCK_WISE = 3
    DAY_WISE = 4


class BufferMode(IntEnum):
    """When the buffer is allowed to charge."""

    ALWAYS_OFF = 0
    ALWAYS_ON = 1
    SCHEDULED = 2


class HeatPumpSgReadyMode(IntEnum):
    """The SG Ready signal the utility or a home energy manager is giving."""

    DEACTIVATE = 0
    EVU_LOCK = 1
    NORMAL_OPERATION = 2
    RECOMMENDED = 3
    FORCED = 4


def _table(entries: Mapping[int, str]) -> Mapping[int, str]:
    return MappingProxyType(dict(entries))


HEATING_CIRCUIT_STATE = _table(
    {
        0: "Heizkreis ist ausgeschaltet",
        1: "Absenkbetrieb",
        2: "Heizbetrieb",
        3: "Ferienbetrieb",
        4: "Estrichprogramm",
        5: "Frostschutzbetrieb",
        6: "Kaminkehrer",
        7: "Heizkreis nicht freigeschaltet",
        8: "Wärmeableitung",
        9: "Außenabschalttemperatur Heizbetrieb erreicht",
        10: "Raumsolltemperatur Heizbetrieb erreicht",
        11: "Trinkwasserspeichervorrang ist aktiv",
        12: "Dauerheizbetrieb",
        13: "Dauerabsenkbetrieb",
        14: "Aussenfühlerunterbrechung",
        15: "min. Energiequellentemperatur unterschritten",
        16: "Vorlauffühler defekt",
        17: "min. Energiequellentemperatur unterschritten, Frostschutzbetrieb",
        18: "Testlauf Pumpe ist aktiv",
        19: "Partybetrieb",
        20: "Begrenzungsthermostat ist offen",
        21: "Pumpen Nachlauf",
        22: "Defrost",
        23: "Kühlbetrieb",
        24: "Kühlen hat Vorrang",
        25: "Heizen hat Vorrang",
        26: "Pool hat Vorrang",
        27: "Außenabschalttemperatur Absenkbetrieb erreicht",
        28: "Raumsolltemperatur Absenkbetrieb erreicht",
        29: "Min. Rücklauftemperatur – Regelung vampair",
        30: "Außenabschalttemperatur Kühlen erreicht",
        31: "warte auf Kühlbetrieb der Wärmepumpe",
    }
)

BUFFER_STATE = _table(
    {
        0: "Status nicht vorhanden",
        1: "Bereitschaft",
        2: "Puffer wird beladen",
        3: "Frostschutzbetrieb",
        4: "Kaminkehrer",
        5: "Wärmeableitung",
        6: "Testlauf Pumpe ist aktiv",
        7: "Trinkwasserspeicher wird beladen",
    }
)

BOILER_STATE = _table(
    {
        0: "Boilerstatus nicht vorhanden",
        1: "Bereitschaft",
        2: "Laden",
        3: "Frostschutz",
        4: "Rauchfangkehrermodus",
        5: "Legionellenschutz",
        6: "Anforderung",
        7: "Energiequelle zu heiß",
        8: "Blockadeschutz",
        9: "einmalige Freigabe aktiv",
        10: "Fühler Kurzschluss",
        11: "Fühler Unterbrechung",
        12: "Ferienbetrieb",
        13: "Defrost",
    }
)

VAMPAIR_STATE = _table(
    {
        0: "Bereitschaft",
        1: "Heizbetrieb",
        2: "Heizbetrieb, Trinkwasserspeicherladung",
        3: "Kühlbetrieb",
        4: "Manueller Betrieb",
        5: "EVU-Lock aktiv",
        6: "keine Zeitfreigabe, Wärmepumpe aus",
        7: "Außentemperatursperre, Wärmepumpe aus",
        8: "elektrische Zusatzheizung aktiv",
        9: "Fremdkessel aktiv, Wärmepumpe aus",
        10: "Kühlanforderung",
        11: "manuelle Leistungsvorgabe",
        12: "Wärmepumpe ausgeschaltet",
    }
)


def describe(table: Mapping[int, str], code: int | None) -> str | None:
    """Name a state code, or say plainly that we do not know this one.

    A therminator enumerates its states from 200 rather than 0, and every
    firmware adds codes, so an unknown value is expected rather than a fault.
    """
    if code is None:
        return None
    return table.get(code, f"Unbekannt ({code})")
