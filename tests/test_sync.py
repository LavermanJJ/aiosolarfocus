"""The blocking facade: five methods over a thread with a loop of its own."""

from __future__ import annotations

import asyncio

import pytest

from aiosolarfocus.config import SolarfocusConfig
from aiosolarfocus.const import ApiVersion, RegisterKind, Systems
from aiosolarfocus.enums import HeatingCircuitMode
from aiosolarfocus.exceptions import SolarfocusConfigError
from aiosolarfocus.sync import SolarfocusSync
from aiosolarfocus.testing import FakeController

INPUT = RegisterKind.INPUT
HOLDING = RegisterKind.HOLDING


def build(**values: int) -> tuple[SolarfocusSync, FakeController]:
    config = SolarfocusConfig(host="c", system=Systems.VAMPAIR, api_version=ApiVersion.V_26_020)
    readings = {(INPUT, 1100): 304, (INPUT, 2300): 285}
    fake = FakeController.for_config(config, readings)
    return SolarfocusSync(config, transport=fake), fake


def test_a_script_reads_values_without_awaiting_anything() -> None:
    """Reading a value was never asynchronous, so the facade need not wrap it."""
    sync, _ = build()
    with sync:
        sync.update()
        assert sync.client.heating_circuits[0].supply_temperature == 30.4
        assert sync.client.heat_pump.supply_temperature == 28.5


def test_anything_not_wrapped_goes_through_run() -> None:
    sync, fake = build()
    with sync:
        sync.run(sync.client.heating_circuits[0].set_mode(HeatingCircuitMode.AUTOMATIC))
    assert fake.writes == [(HOLDING, 32603, (2,))]


def test_the_context_manager_connects_and_closes() -> None:
    sync, fake = build()
    with sync:
        assert fake.connected
    assert not fake.connected


def test_closing_twice_is_harmless() -> None:
    sync, _ = build()
    sync.close()
    sync.close()
    assert "closed" in repr(sync)


def test_a_closed_client_says_so_rather_than_hanging() -> None:
    sync, _ = build()
    sync.close()
    with pytest.raises(SolarfocusConfigError, match="closed"):
        sync.update()


def test_building_one_inside_a_running_loop_is_refused() -> None:
    """The alternative failure mode is a deadlock.

    And a deadlock at three in the morning is not a teaching moment.
    """

    async def go() -> None:
        with pytest.raises(SolarfocusConfigError, match="running event loop"):
            SolarfocusSync(SolarfocusConfig(host="c"))

    asyncio.run(go())


def test_a_facade_left_open_says_so() -> None:
    sync, _ = build()
    with pytest.warns(ResourceWarning, match="never closed"):
        sync.__del__()
    sync.close()
