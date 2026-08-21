"""A real Modbus/TCP server on localhost, for the tests a protocol-level fake cannot do.

`FakeController` stands in for the transport, so it proves everything above the
transport and nothing about the transport itself: not the device id keyword, not
the framing, not what pymodbus makes of an exception response. This runs the
real client against a real server over a real socket, so that one test covers
the seam.

Kept deliberately small. It is a sparse block of registers and nothing else -
it does not reproduce the eco manager-touch's compaction, because a stock Modbus
server cannot be made to misbehave that way. That is `FakeController`'s job.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from pymodbus.datastore import ModbusDeviceContext, ModbusServerContext, ModbusSparseDataBlock
from pymodbus.server import ModbusTcpServer

from ..const import RegisterKind

_Address = tuple[RegisterKind, int]


@asynccontextmanager
async def running_server(values: Mapping[_Address, int], *, device_id: int = 1) -> AsyncIterator[int]:
    """Serve `values` on a free localhost port, and yield the port.

    Only the addresses given exist; everything else answers illegal data
    address, which is what makes the refusal path testable end to end.
    """
    inputs = {address: value for (kind, address), value in values.items() if kind is RegisterKind.INPUT}
    holdings = {address: value for (kind, address), value in values.items() if kind is RegisterKind.HOLDING}

    # An empty sparse block trips pymodbus up on the first read, so a table
    # with nothing in it is simply not offered - which is also what a controller
    # without that table looks like.
    blocks: dict[str, Any] = {}
    if inputs:
        blocks["ir"] = ModbusSparseDataBlock(dict(inputs))
    if holdings:
        blocks["hr"] = ModbusSparseDataBlock(dict(holdings))

    device = ModbusDeviceContext(**blocks)
    server = ModbusTcpServer(ModbusServerContext(devices={device_id: device}, single=False), address=("127.0.0.1", 0))

    # `background=True` binds the socket and returns; `server.serving` is a
    # future that resolves when serving *stops*, so awaiting it here would wait
    # for the end of the test rather than the start of the server.
    await server.serve_forever(background=True)
    try:
        sockets = server.transport.sockets  # type: ignore[attr-defined]
        yield int(sockets[0].getsockname()[1])
    finally:
        await server.shutdown()
