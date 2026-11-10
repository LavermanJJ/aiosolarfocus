"""The transport, against a real Modbus server over a real socket.

This is the one seam `FakeController` cannot cover: the device id keyword, the
framing, and what pymodbus makes of an exception response.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pymodbus.exceptions import ModbusIOException
from pymodbus.pdu import ExceptionResponse

from aiosolarfocus.const import RegisterKind
from aiosolarfocus.exceptions import (
    DeviceFailureError,
    IllegalAddressError,
    IllegalValueError,
    SolarfocusConnectionError,
    SolarfocusError,
    SolarfocusProtocolError,
    SolarfocusRegisterError,
    SolarfocusTimeoutError,
)
from aiosolarfocus.testing.server import running_server
from aiosolarfocus.transport import ModbusTransport, _raise_for

pytestmark = pytest.mark.asyncio
INPUT = RegisterKind.INPUT
HOLDING = RegisterKind.HOLDING

VALUES = {
    (INPUT, 1100): 304,
    (INPUT, 1101): 215,
    (INPUT, 1102): 480,
    (HOLDING, 32600): 450,
    (HOLDING, 32602): 0,
}


async def test_a_block_read_comes_back_in_order() -> None:
    async with running_server(VALUES) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        try:
            assert await transport.read(INPUT, 1100, 3) == (304, 215, 480)
        finally:
            await transport.disconnect()


async def test_holding_registers_are_a_different_table_from_input_ones() -> None:
    async with running_server(VALUES) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        try:
            assert await transport.read(HOLDING, 32600, 1) == (450,)
        finally:
            await transport.disconnect()


async def test_an_address_the_server_does_not_have_raises_illegal_address() -> None:
    """Exception code 2, which the client turns into a per-component failure."""
    async with running_server(VALUES) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        try:
            with pytest.raises(IllegalAddressError):
                await transport.read(INPUT, 9999, 1)
        finally:
            await transport.disconnect()


async def test_probing_reports_a_refusal_as_an_answer() -> None:
    async with running_server(VALUES) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        try:
            assert await transport.probe(INPUT, 1100) == (304,)
            assert await transport.probe(INPUT, 9999) is None
        finally:
            await transport.disconnect()


async def test_a_write_lands_and_can_be_read_back() -> None:
    async with running_server(VALUES) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        try:
            await transport.write([(HOLDING, 32600, (480,))])
            assert await transport.read(HOLDING, 32600, 1) == (480,)
        finally:
            await transport.disconnect()


async def test_a_negative_value_survives_the_round_trip() -> None:
    """The wire carries unsigned words; the sign is the library's business."""
    async with running_server({(HOLDING, 33407): 0}) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        try:
            await transport.write([(HOLDING, 33407, (0xFA24,))])
            assert await transport.read(HOLDING, 33407, 1) == (0xFA24,)
        finally:
            await transport.disconnect()


async def test_a_controller_that_is_not_there_raises_rather_than_returning_false() -> None:
    """Rather than returning False and logging, as the predecessor did.

    That is why its callers had to consult a separate `is_connected` flag
    afterwards to work out what a failure had meant.
    """
    transport = ModbusTransport("127.0.0.1", 1)
    with pytest.raises(SolarfocusConnectionError, match="could not reach"):
        await transport.connect()


async def test_reading_without_connecting_first_says_so() -> None:
    transport = ModbusTransport("127.0.0.1", 1)
    with pytest.raises(SolarfocusConnectionError):
        await transport.read(INPUT, 1100, 1)


async def test_connecting_twice_is_harmless() -> None:
    async with running_server(VALUES) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        try:
            await transport.connect()
            assert transport.connected
            assert await transport.read(INPUT, 1100, 1) == (304,)
        finally:
            await transport.disconnect()
        assert not transport.connected


async def test_a_lost_connection_is_not_reconnected_behind_the_callers_back() -> None:
    """One socket per controller, however the last one was lost.

    Given a reconnect delay, pymodbus answers a lost connection with a task of
    its own that calls its `connect()` until one succeeds, and that `connect()`
    puts the new transport in place without closing whatever is already there.
    The caller's `connect()` races it, and the socket that loses the race is
    open, unowned and never closed. Counted on a real controller: 23
    established sessions from one client after a day of dropouts, at which
    point the controller stopped answering new ones.
    """
    async with running_server(VALUES) as port:
        transport = ModbusTransport("127.0.0.1", port)
        await transport.connect()
        protocol = transport._client.ctx
        opened = 0
        create = protocol.call_create

        async def counting_create(*args: Any, **kwargs: Any) -> Any:
            nonlocal opened
            opened += 1
            return await create(*args, **kwargs)

        protocol.call_create = counting_create
        try:
            protocol.connection_lost(ConnectionResetError("the controller went away"))
            lost = transport.connected
            assert not lost

            await transport.connect()
            # Long enough for the reconnect task pymodbus would start at its
            # default delay of a tenth of a second, and at the one second this
            # transport used to hand it.
            await asyncio.sleep(1.5)

            assert transport.connected
            assert opened == 1
            assert await transport.read(INPUT, 1100, 1) == (304,)
        finally:
            await transport.disconnect()


async def test_writing_nothing_asks_the_controller_nothing() -> None:
    transport = ModbusTransport("127.0.0.1", 1)
    await transport.write([])


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0x01, SolarfocusRegisterError),
        (0x02, IllegalAddressError),
        (0x03, IllegalValueError),
        (0x04, DeviceFailureError),
        (0x06, SolarfocusRegisterError),
    ],
)
async def test_each_modbus_exception_code_becomes_its_own_error(code: int, expected: type[Exception]) -> None:
    """Code 2 is the one that matters.

    It means the configured firmware claims a register this controller does not
    have, which is a fault the owner can fix - so the client reports it against
    the components in that read rather than as a lost connection.
    """
    with pytest.raises(expected):
        _raise_for(ExceptionResponse(3, code), "reading input 2300")


async def test_a_timeout_is_a_timeout_and_not_a_dropped_connection() -> None:
    """A slow controller must not be reported as a disconnected one.

    `TimeoutError` is a subclass of `OSError`, so the order the two handlers are
    written in decides whether `SolarfocusTimeoutError` can ever happen at all.
    """
    transport = ModbusTransport("127.0.0.1", 1)

    async def time_out(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    transport._client.read_input_registers = time_out  # type: ignore[assignment,method-assign]

    with pytest.raises(SolarfocusTimeoutError):
        await transport.read(INPUT, 1100, 1)


async def test_a_modbus_protocol_failure_is_reported_as_one() -> None:
    transport = ModbusTransport("127.0.0.1", 1)

    async def misbehave(*args: object, **kwargs: object) -> None:
        raise ModbusIOException("nothing came back")

    transport._client.read_input_registers = misbehave  # type: ignore[assignment,method-assign]

    with pytest.raises(SolarfocusProtocolError):
        await transport.read(INPUT, 1100, 1)


async def test_an_answer_of_the_wrong_length_is_refused() -> None:
    """A short answer decoded as if it were whole would shift every value after it."""

    class TwoRegisters:
        registers = (1, 2)

        def isError(self) -> bool:  # noqa: N802 - pymodbus spells it this way
            return False

    transport = ModbusTransport("127.0.0.1", 1)

    async def short(*args: object, **kwargs: object) -> TwoRegisters:
        return TwoRegisters()

    transport._client.read_input_registers = short  # type: ignore[assignment,method-assign]

    with pytest.raises(SolarfocusProtocolError, match="asked for 5 registers and got 2"):
        await transport.read(INPUT, 1100, 5)


async def test_only_holding_registers_can_be_written() -> None:
    transport = ModbusTransport("127.0.0.1", 1)
    with pytest.raises(SolarfocusError, match="only holding registers"):
        await transport.write([(INPUT, 1100, (1,))])


async def _silent_controller() -> tuple[asyncio.Server, int, list[bytes]]:
    """A controller that accepts the connection, reads the request and never answers."""
    received: list[bytes] = []

    async def swallow(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(256):
                received.append(data)
        finally:
            writer.close()

    server = await asyncio.start_server(swallow, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1], received


async def test_a_controller_that_stops_answering_is_a_timeout_and_asked_only_once() -> None:
    """The powerline case: the socket is up and nothing comes back.

    pymodbus reports that as a `ModbusIOException`, which read as a protocol
    error is a failure of whichever components were in that read - and the
    client then goes on to wait out the timeout of every read after it.
    """
    server, port, received = await _silent_controller()
    async with server:
        transport = ModbusTransport("127.0.0.1", port, timeout=0.2)
        await transport.connect()
        try:
            with pytest.raises(SolarfocusTimeoutError, match="did not answer in time"):
                await transport.read(INPUT, 1100, 1)
            await asyncio.sleep(0.05)
            assert len(received) == 1
            # A late answer must not be read as the answer to the next request.
            assert not transport.connected
        finally:
            await transport.disconnect()


async def test_a_write_nobody_answered_is_a_timeout_and_not_sent_twice() -> None:
    server, port, received = await _silent_controller()
    async with server:
        transport = ModbusTransport("127.0.0.1", port, timeout=0.2)
        await transport.connect()
        try:
            with pytest.raises(SolarfocusTimeoutError):
                await transport.write([(HOLDING, 32600, (480,))])
            await asyncio.sleep(0.05)
            assert len(received) == 1
        finally:
            await transport.disconnect()


async def test_a_cancelled_read_stays_cancelled() -> None:
    """A cancel is not turned into the error pymodbus raises in its place.

    Passed on as that error, the task that was told to stop carries on: a
    refresh being unloaded reads its remaining slices, and a timeout around it
    never fires.
    """
    server, port, _ = await _silent_controller()
    async with server:
        transport = ModbusTransport("127.0.0.1", port, timeout=5)
        await transport.connect()
        try:
            task = asyncio.create_task(transport.read(INPUT, 1100, 1))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert task.cancelled()

            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0.1):
                    await transport.read(INPUT, 1100, 1)
        finally:
            await transport.disconnect()
