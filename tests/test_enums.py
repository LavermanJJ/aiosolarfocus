"""The state tables name what they can and say so plainly when they cannot."""

from __future__ import annotations

from aiosolarfocus.enums import BOILER_STATE, HEATING_CIRCUIT_STATE, describe


def test_a_known_state_gets_the_controllers_own_wording() -> None:
    """Kept verbatim so a reading matches what the owner sees on the machine."""
    assert describe(HEATING_CIRCUIT_STATE, 7) == "Heizkreis nicht freigeschaltet"
    assert describe(BOILER_STATE, 2) == "Laden"


def test_a_state_we_do_not_know_is_named_as_such_rather_than_raising() -> None:
    """A therminator enumerates from 200, and every firmware adds codes."""
    assert describe(HEATING_CIRCUIT_STATE, 214) == "Unbekannt (214)"


def test_nothing_read_is_nothing_described() -> None:
    assert describe(HEATING_CIRCUIT_STATE, None) is None


def test_the_tables_cannot_be_edited_by_a_caller() -> None:
    assert not hasattr(HEATING_CIRCUIT_STATE, "__setitem__") or HEATING_CIRCUIT_STATE.__class__.__name__ == "mappingproxy"
