"""The fake controller reproduces the ways the real one misbehaves.

If these stop holding, every planner test below them is checking a comment.
"""

from __future__ import annotations

import pytest

from aiosolarfocus.const import RegisterKind
from aiosolarfocus.exceptions import IllegalAddressError
from aiosolarfocus.testing import FakeController

pytestmark = pytest.mark.asyncio
INPUT = RegisterKind.INPUT


async def controller(**kwargs: object) -> FakeController:
    fake = FakeController(**kwargs)  # type: ignore[arg-type]
    await fake.connect()
    return fake


async def test_an_address_the_firmware_does_not_have_is_refused() -> None:
    fake = await controller(values={(INPUT, 100): 1}, mapped=[(INPUT, 100)])
    with pytest.raises(IllegalAddressError):
        await fake.read(INPUT, 101, 1)


async def test_a_32_bit_register_refuses_a_read_of_one() -> None:
    """Exactly the way a missing address does.

    Probing presence therefore needs a count=2 fallback, or every 32-bit
    counter in the map looks absent.
    """
    fake = await controller(values={(INPUT, 100): 0, (INPUT, 101): 5}, wide=[(INPUT, 100)])
    with pytest.raises(IllegalAddressError):
        await fake.read(INPUT, 100, 1)
    assert await fake.read(INPUT, 100, 2) == (0, 5)


async def test_a_read_starting_at_an_unmapped_address_is_refused_however_long_it_is() -> None:
    fake = await controller(values={(INPUT, 101): 7}, mapped=[(INPUT, 101)])
    with pytest.raises(IllegalAddressError):
        await fake.read(INPUT, 100, 4)


async def test_a_read_spanning_a_gap_comes_back_the_right_length_and_the_wrong_values() -> None:
    """The controller packs the next mapped registers in rather than padding the hole.

    Nothing in the protocol says this happened. It is the whole reason the read
    planner never spans a gap, and there is no way to detect it after the fact.
    """
    fake = await controller(
        values={(INPUT, 100): 10, (INPUT, 101): 11, (INPUT, 103): 13, (INPUT, 104): 14},
        mapped=[(INPUT, 100), (INPUT, 101), (INPUT, 103), (INPUT, 104)],
    )
    # Asking for 100-103 does not give 10, 11, <nothing>, 13. It gives the four
    # mapped registers from 100 onwards, shifted into the wrong positions.
    assert await fake.read(INPUT, 100, 4) == (10, 11, 13, 14)


async def test_reads_are_recorded_so_a_test_can_count_round_trips() -> None:
    fake = await controller(values={(INPUT, 100): 1, (INPUT, 101): 2})
    await fake.read(INPUT, 100, 2)
    await fake.read(INPUT, 100, 1)
    assert fake.reads == [(INPUT, 100, 2), (INPUT, 100, 1)]
    assert fake.round_trips == 2


async def test_probing_reports_a_refusal_as_an_answer_rather_than_an_error() -> None:
    fake = await controller(values={(INPUT, 100): 1}, mapped=[(INPUT, 100)])
    assert await fake.probe(INPUT, 100) == (1,)
    assert await fake.probe(INPUT, 101) is None
