"""Words to values and back: sign, width, scaling, sentinels, two's complement."""

from __future__ import annotations

import pytest

from aiosolarfocus.codec import decode, encode, raw_to_words, words_to_raw
from aiosolarfocus.const import OPEN_CHANNEL
from aiosolarfocus.exceptions import ValueOutOfRangeError


def test_a_temperature_is_rounded_to_the_precision_its_scale_carries() -> None:
    """304 * 0.1 is 30.400000000000002 in binary floating point.

    The predecessor passed that through, and Home Assistant recorded it as the
    reading, digits of noise and all.
    """
    assert decode([304], signed=True, scale=0.1) == 30.4
    assert decode([1234567 >> 16, 1234567 & 0xFFFF], signed=True, scale=0.001) == 1234.567


def test_an_unscaled_register_stays_an_int() -> None:
    value = decode([1500], signed=False, scale=1.0)
    assert value == 1500
    assert isinstance(value, int)


def test_a_negative_temperature_reads_negative() -> None:
    assert decode([0xFFFF], signed=True, scale=0.1) == -0.1
    assert decode([0xFF38], signed=True, scale=0.1) == -20.0


def test_a_32_bit_counter_is_assembled_high_word_first() -> None:
    assert words_to_raw([0x0001, 0x86A0], signed=False) == 100_000


@pytest.mark.parametrize("sentinel", sorted(OPEN_CHANNEL))
def test_an_open_sensor_channel_decodes_to_nothing(sentinel: int) -> None:
    """130.0 degC and 270.0 degC are what a channel with nothing wired to it reports.

    The predecessor reported them to Home Assistant as measurements.
    """
    assert decode([sentinel], signed=True, scale=0.1, sentinels=OPEN_CHANNEL) is None


def test_minus_one_is_not_treated_as_an_absent_sensor() -> None:
    """-0.1 degC is a perfectly good outdoor reading on a frosty night."""
    assert decode([0xFFFF], signed=True, scale=0.1, sentinels=OPEN_CHANNEL) == -0.1


def test_a_sentinel_is_matched_against_the_unsigned_reading() -> None:
    """The register document writes 0xFFFF down as 65535, not as -1."""
    assert decode([0xFFFF], signed=True, scale=0.1, sentinels=frozenset({65535})) is None


def test_a_negative_value_is_encoded_by_the_library() -> None:
    """The Home Assistant integration carried its own two's complement for this.

    It had to: `DataValue.commit` handed `int(self.value)` straight to pymodbus,
    and only the library knows the register is signed.
    """
    assert encode(-1500, signed=True, width=1, scale=1.0) == (0xFA24,)
    assert raw_to_words(-1, width=2) == (0xFFFF, 0xFFFF)


def test_a_write_scale_lets_a_register_be_read_and_written_in_different_units() -> None:
    """Heating circuit 32607 reports tenths of a percent and accepts whole percent.

    See home-assistant-solarfocus issue #150.
    """
    assert decode([440], signed=True, scale=0.1) == 44.0
    assert encode(44.0, signed=True, width=1, scale=0.1, write_scale=1.0) == (44,)


def test_scaling_is_symmetric_when_no_write_scale_is_given() -> None:
    assert encode(45.0, signed=True, width=1, scale=0.1) == (450,)
    assert decode([450], signed=True, scale=0.1) == 45.0


def test_a_value_outside_the_registers_bounds_is_refused_before_it_goes_out() -> None:
    with pytest.raises(ValueOutOfRangeError) as caught:
        encode(120.0, signed=True, width=1, scale=0.1, bounds=(0.0, 80.0))
    assert caught.value.bounds == (0.0, 80.0)


def test_a_value_too_big_for_the_register_is_refused() -> None:
    with pytest.raises(ValueOutOfRangeError, match="16-bit signed"):
        encode(40000, signed=True, width=1, scale=1.0)


def test_an_unsigned_register_refuses_a_negative_value() -> None:
    with pytest.raises(ValueOutOfRangeError, match="16-bit unsigned"):
        encode(-1, signed=False, width=1, scale=1.0)


def test_encoding_round_trips_through_decoding() -> None:
    for value in (-40.0, -0.1, 0.0, 21.5, 80.0):
        words = encode(value, signed=True, width=1, scale=0.1)
        assert decode(words, signed=True, scale=0.1) == value
