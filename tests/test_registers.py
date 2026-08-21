"""The descriptor: one declaration that is the spec, the accessor and the metadata."""

from __future__ import annotations

from typing import assert_type

import pytest

from aiosolarfocus.const import Access, ApiVersion, RegisterKind
from aiosolarfocus.enums import HeatingCircuitMode
from aiosolarfocus.exceptions import ReadOnlyRegisterError, UnsupportedRegisterError, ValueOutOfRangeError
from aiosolarfocus.registers import Register

from .conftest import Probe, RecordingWriter, build_probe


def test_class_access_gives_the_spec_and_instance_access_gives_the_reading() -> None:
    """Both narrow, with no cast and no plugin. This is the whole point of the design.

    `assert_type` is checked by mypy, so this test failing to type-check is the
    failure that matters; at runtime it does nothing.
    """
    assert_type(Probe.supply_temperature, Register[float])
    assert_type(Probe.running, Register[bool])
    assert_type(Probe.mode, Register[HeatingCircuitMode])

    probe = build_probe()
    assert_type(probe.supply_temperature, float | None)
    assert_type(probe.running, bool | None)
    assert_type(probe.mode, HeatingCircuitMode | None)


def test_a_register_learns_the_name_it_was_declared_under() -> None:
    assert Probe.supply_temperature.name == "supply_temperature"
    assert Probe.humidity_external.name == "humidity_external"


def test_an_unread_component_reports_nothing_rather_than_zero() -> None:
    """The predecessor started every register at 0, so an unread heat pump read 0 degC."""
    probe = build_probe()
    assert probe.supply_temperature is None
    assert probe.available is False


def test_readings_decode_through_the_descriptor() -> None:
    probe = build_probe()
    probe.decode_readings({(RegisterKind.INPUT, 1100): 304, (RegisterKind.INPUT, 1102): 1})
    assert probe.supply_temperature == 30.4
    assert probe.running is True
    assert probe.available is True


def test_a_closed_enumeration_decodes_to_its_member() -> None:
    probe = build_probe()
    probe.decode_readings({(RegisterKind.HOLDING, 32600): 2})
    assert probe.mode is HeatingCircuitMode.AUTOMATIC


def test_an_enumeration_value_we_do_not_know_decodes_to_nothing() -> None:
    """A firmware that grew a new mode should not take a heating system's entities down."""
    probe = build_probe()
    probe.decode_readings({(RegisterKind.HOLDING, 32600): 99})
    assert probe.mode is None
    assert probe.raw(Probe.mode) == 99


def test_an_open_enumeration_stays_an_int() -> None:
    """A therminator enumerates its states from 200, and every firmware adds more."""
    probe = build_probe()
    probe.decode_readings({(RegisterKind.INPUT, 1104): 214})
    assert probe.state == 214


def test_a_half_read_32_bit_register_is_left_alone_rather_than_half_decoded() -> None:
    probe = build_probe()
    probe.decode_readings({(RegisterKind.INPUT, 1105): 0x0001})
    assert probe.thermal_energy is None


def test_the_raw_reading_survives_a_sentinel_so_absent_and_unread_can_be_told_apart() -> None:
    probe = build_probe()
    probe.decode_readings({(RegisterKind.INPUT, 1100): 2700})
    assert probe.supply_temperature is None
    assert probe.raw(Probe.supply_temperature) == 2700


def test_info_reports_what_a_number_entity_needs() -> None:
    """Home Assistant hand-copies these out of the register document today."""
    info = build_probe().info(Probe.target_temperature)
    assert (info.address, info.bounds, info.step, info.unit) == (32601, (0.0, 80.0), 0.5, "°C")
    assert info.access is Access.READ_WRITE
    assert info.writable is True


def test_available_registers_replaces_per_entity_version_gating() -> None:
    modern = build_probe(ApiVersion.V_26_020)
    ancient = build_probe(ApiVersion.V_21_140)
    assert "residual_oxygen" in modern.available_registers()
    assert "residual_oxygen" not in ancient.available_registers()
    assert ancient.supports("residual_oxygen") is False


@pytest.mark.asyncio
async def test_a_write_is_encoded_and_cached_so_the_caller_need_not_re_read() -> None:
    """Deletes the full-component `update()` the integration runs after every write."""
    writer = RecordingWriter()
    probe = build_probe(writer=writer)
    await probe.write(Probe.target_temperature, 45.0)
    assert writer.writes == [(RegisterKind.HOLDING, 32601, (450,))]
    assert probe.target_temperature == 45.0


@pytest.mark.asyncio
async def test_a_grouped_write_goes_out_together() -> None:
    """One hold of the transport lock, so no poll lands between the registers."""
    writer = RecordingWriter()
    probe = build_probe(writer=writer)
    await probe.write_many({Probe.mode: HeatingCircuitMode.AUTOMATIC, Probe.target_temperature: 21.0})
    assert writer.writes == [(RegisterKind.HOLDING, 32600, (2,)), (RegisterKind.HOLDING, 32601, (210,))]


@pytest.mark.asyncio
async def test_a_register_read_in_tenths_and_written_whole_round_trips() -> None:
    """See home-assistant-solarfocus issue #150."""
    writer = RecordingWriter()
    probe = build_probe(writer=writer)
    await probe.write(Probe.humidity_external, 44.0)
    assert writer.writes == [(RegisterKind.HOLDING, 32602, (44,))]
    probe.decode_readings({(RegisterKind.HOLDING, 32602): 440})
    assert probe.humidity_external == 44.0


@pytest.mark.asyncio
async def test_writing_a_read_only_register_is_refused() -> None:
    probe = build_probe(writer=RecordingWriter())
    with pytest.raises(ReadOnlyRegisterError, match="supply_temperature"):
        await probe.write(Probe.supply_temperature, 20.0)


@pytest.mark.asyncio
async def test_writing_a_register_this_controller_lacks_is_refused() -> None:
    probe = build_probe(ApiVersion.V_21_140, writer=RecordingWriter())
    with pytest.raises(UnsupportedRegisterError, match="residual_oxygen"):
        await probe.write(Probe.residual_oxygen, 5.0)


@pytest.mark.asyncio
async def test_an_out_of_range_write_never_reaches_the_wire() -> None:
    writer = RecordingWriter()
    probe = build_probe(writer=writer)
    with pytest.raises(ValueOutOfRangeError):
        await probe.write(Probe.target_temperature, 120.0)
    assert writer.writes == []
