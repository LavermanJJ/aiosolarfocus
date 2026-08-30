"""The command line, in the parts that need no controller."""

from __future__ import annotations

import pytest

from aiosolarfocus import __version__
from aiosolarfocus.__main__ import _parse_value, _render, _resolve_target, main
from aiosolarfocus.client import SolarfocusClient
from aiosolarfocus.components.heating_circuit import HeatingCircuit
from aiosolarfocus.config import SolarfocusConfig
from aiosolarfocus.const import ApiVersion, Systems
from aiosolarfocus.detect import Detection, detect_through
from aiosolarfocus.enums import HeatingCircuitMode
from aiosolarfocus.testing import FakeController, load_spec


def client() -> SolarfocusClient:
    config = SolarfocusConfig(host="c", system=Systems.VAMPAIR, api_version=ApiVersion.V_26_020, photovoltaic=True)
    return SolarfocusClient(config, transport=FakeController.for_config(config))


def test_registers_prints_the_map_without_connecting(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registers", "--system", "Vampair", "--api-version", "26.020", "--component", "heat_pump"]) == 0
    printed = capsys.readouterr().out
    assert "supply_temperature" in printed
    assert "2300" in printed
    assert "Vorlauftemperatur Wärmepumpe" in printed


def test_plan_prints_the_reads_without_connecting(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["plan", "--system", "Octoplus", "--api-version", "26.020", "--photovoltaic", "-v"]) == 0
    printed = capsys.readouterr().out
    # The fold that the predecessor could not make: three components' holding
    # blocks are one contiguous range.
    assert "holding 33406-33412 (7)" in printed
    assert "biomass_boiler, photovoltaic" in printed
    assert "round trips" in printed


def test_a_target_names_a_component_and_a_register() -> None:
    key, component, register = _resolve_target(client(), "heating_circuits.1.mode")
    assert str(key) == "heating_circuits.1"
    assert isinstance(component, HeatingCircuit)
    assert register == "mode"


def test_a_singleton_target_needs_no_number() -> None:
    key, _, register = _resolve_target(client(), "heat_pump.evu_lock")
    assert str(key) == "heat_pump"
    assert register == "evu_lock"


@pytest.mark.parametrize(
    ("target", "complaint"),
    [
        ("mode", "should look like"),
        ("heating_circuits.1.nonsense", "has no 'nonsense'"),
        ("heating_circuits.4.mode", "no heating_circuits 4"),
        ("biomass_boiler.status", "no biomass_boiler"),
    ],
)
def test_a_target_that_names_nothing_says_so(target: str, complaint: str) -> None:
    with pytest.raises(KeyError, match=complaint):
        _resolve_target(client(), target)


def test_a_value_is_read_the_way_the_register_reports_it() -> None:
    heating_circuit = client().heating_circuits[0]
    assert _parse_value(heating_circuit.info(HeatingCircuit.mode), "automatic") is HeatingCircuitMode.AUTOMATIC
    assert _parse_value(heating_circuit.info(HeatingCircuit.mode), "2") is HeatingCircuitMode.AUTOMATIC
    assert _parse_value(heating_circuit.info(HeatingCircuit.target_supply_temperature), "45.5") == 45.5
    assert _parse_value(client().heat_pump.info("evu_lock"), "on") is True


def test_a_value_that_is_not_one_of_the_modes_lists_the_modes() -> None:
    info = client().heating_circuits[0].info(HeatingCircuit.mode)
    with pytest.raises(SystemExit, match="always_on, reduced_operation, automatic, off"):
        _parse_value(info, "sideways")


def test_a_mode_is_shown_by_name_rather_than_by_its_number() -> None:
    """An IntEnum prints as its number since Python 3.11.

    Which is exactly what a mode column should not be showing.
    """
    assert _render(HeatingCircuitMode.AUTOMATIC) == "automatic (2)"
    assert _render(None) == "-"
    assert _render(30.4) == "30.4"


def test_version_is_printed_on_its_own_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_:
        main(["--version"])
    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == f"aiosolarfocus {__version__}"


def test_detect_says_which_version_read_the_controller(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The line people paste into an issue has to carry it.

    fklein1980 ran an old version against a controller, posted the output, and
    neither of us could tell from it - home-assistant-solarfocus#237.
    """

    async def fake_detect(host: str, port: int, device_id: int) -> Detection:
        fake = FakeController(dict.fromkeys(load_spec(), 0))
        await fake.connect()
        return await detect_through(fake)

    monkeypatch.setattr("aiosolarfocus.__main__.detect", fake_detect)
    assert main(["detect", "--host", "c"]) == 0
    assert f"aiosolarfocus {__version__}" in capsys.readouterr().out
