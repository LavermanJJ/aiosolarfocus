"""Firmware versions order themselves, and parse from what people actually type."""

from __future__ import annotations

import pytest

from aiosolarfocus.const import ApiVersion


def test_versions_order_by_firmware() -> None:
    assert ApiVersion.V_21_140 < ApiVersion.V_22_090 < ApiVersion.V_25_030 < ApiVersion.V_26_020


def test_label_is_what_the_controller_prints() -> None:
    assert ApiVersion.V_26_020.label == "26.020"
    assert ApiVersion.V_23_010.label == "23.010"


@pytest.mark.parametrize("text", ["26.020", "v26.020", "V_26_020", 26020, ApiVersion.V_26_020])
def test_parse_accepts_every_form_a_caller_has(text: str | int | ApiVersion) -> None:
    assert ApiVersion.parse(text) is ApiVersion.V_26_020


def test_a_newer_firmware_than_we_know_clamps_to_the_newest_we_do() -> None:
    """Solarfocus will ship 26.030, and an installation should keep working when they do."""
    assert ApiVersion.parse("26.030") is ApiVersion.V_26_020
    assert ApiVersion.parse("99.999") is ApiVersion.V_26_020


def test_a_version_between_two_we_know_resolves_to_the_older() -> None:
    """25.025 has the 25.020 register set; it cannot have registers 25.030 introduced."""
    assert ApiVersion.parse("25.025") is ApiVersion.V_25_020


def test_clamping_can_be_refused() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        ApiVersion.parse("26.030", clamp=False)


@pytest.mark.parametrize("text", ["", "twenty-six", "26.", "V_99_999"])
def test_nonsense_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="api version"):
        ApiVersion.parse(text)


def test_a_firmware_older_than_anything_supported_is_refused() -> None:
    """Clamping down would claim registers the controller has never had."""
    with pytest.raises(ValueError, match="older than"):
        ApiVersion.parse("19.010")
