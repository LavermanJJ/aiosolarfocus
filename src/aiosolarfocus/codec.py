"""Words on the wire to values, and values back to words.

Pure functions: no I/O, no logging, no knowledge of components. Everything the
decoder needs is passed in, so this module is the one place sign, width, scaling
and sentinels are decided, and the one place a test has to look to check them.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from decimal import Decimal
from functools import cache
from typing import Any

from .exceptions import ValueOutOfRangeError


@cache
def _decimals(scale: float) -> int:
    """How many decimal places a scale factor can actually produce.

    `304 * 0.1` is 30.400000000000002 in binary floating point, and Home
    Assistant records that as the reading. Rounding to the precision the scale
    carries is not cosmetic - it is the difference between a temperature and a
    temperature with fifteen digits of noise on the end.
    """
    exponent = Decimal(str(scale)).as_tuple().exponent
    return max(0, -int(exponent))


def words_to_raw(words: Sequence[int], *, signed: bool) -> int:
    """Assemble the registers of one value into the integer they encode.

    Big-endian across registers, which is how the controller lays out its
    32-bit counters: the high word first.
    """
    raw = 0
    for word in words:
        raw = (raw << 16) | (word & 0xFFFF)
    if signed:
        bits = 16 * len(words)
        if raw & (1 << (bits - 1)):
            raw -= 1 << bits
    return raw


def raw_to_words(raw: int, *, width: int) -> tuple[int, ...]:
    """Split an integer into the registers that carry it, two's complement and all.

    Masking a negative integer in Python is exactly two's complement, which is
    why a caller never has to do this themselves. The predecessor made them:
    the Home Assistant integration carried its own `raw + (1 << (16 * count))`
    for the two signed holding registers it writes, because the library handed
    `int(value)` straight to pymodbus and only the library knows the register is
    signed.
    """
    bits = 16 * width
    unsigned = raw & ((1 << bits) - 1)
    return tuple((unsigned >> (16 * (width - 1 - index))) & 0xFFFF for index in range(width))


def decode(
    words: Sequence[int],
    *,
    signed: bool,
    scale: float,
    sentinels: Collection[int] = (),
    decode_fn: Callable[[int], Any] | None = None,
) -> Any:
    """Turn the registers of one value into what it means, or None.

    None means the controller is reporting one of the values an unconfigured or
    open sensor channel reports rather than a measurement. Sentinels are matched
    against the *unsigned* reading, because that is how they are documented -
    0xFFFF is written down as 65535, not as -1 - and a signed register would
    otherwise never match one.
    """
    unsigned = words_to_raw(words, signed=False)
    if unsigned in sentinels:
        return None

    raw = words_to_raw(words, signed=signed)
    if decode_fn is not None:
        return decode_fn(raw)
    if scale == 1.0:
        return raw
    return round(raw * scale, _decimals(scale))


def encode(
    value: Any,
    *,
    signed: bool,
    width: int,
    scale: float,
    write_scale: float | None = None,
    bounds: tuple[float, float] | None = None,
    encode_fn: Callable[[Any], int] | None = None,
    context: str | None = None,
) -> tuple[int, ...]:
    """Turn a value into the registers that carry it, refusing what will not fit.

    `write_scale` exists for the registers the controller reads out in one unit
    and accepts in another. There is one today - heating circuit 32607, reported
    in tenths of a percent and written as a whole percent - and the asymmetry is
    a fact about that register rather than a rule about holding registers, which
    is how the predecessor had it.
    """
    if bounds is not None:
        number = float(value)
        if not bounds[0] <= number <= bounds[1]:
            raise ValueOutOfRangeError(f"{number} is outside the accepted {bounds[0]} to {bounds[1]}", bounds=bounds, context=context)

    if encode_fn is not None:
        raw = int(encode_fn(value))
    else:
        effective = scale if write_scale is None else write_scale
        raw = round(float(value)) if effective == 1.0 else round(float(value) / effective)

    bits = 16 * width
    low, high = (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed else (0, (1 << bits) - 1)
    if not low <= raw <= high:
        raise ValueOutOfRangeError(f"{value!r} encodes to {raw}, which does not fit a {bits}-bit {'signed' if signed else 'unsigned'} register", context=context)

    return raw_to_words(raw, width=width)
