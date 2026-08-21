"""The socket to the controller, and the only module that names pymodbus.

One client, one lock, and errors that are exceptions rather than return codes.
The `asnyc-api` branch of the predecessor kept a synchronous connector alive
beside the asynchronous one and connected both, so every device cost two TCP
sockets and the two could disagree about whether it was reachable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Protocol

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusException
from pymodbus.pdu import ExceptionResponse

from .const import DEFAULT_DEVICE_ID, DEFAULT_PORT, DEFAULT_TIMEOUT, RegisterKind, Write
from .exceptions import (
    DeviceFailureError,
    IllegalAddressError,
    IllegalValueError,
    SolarfocusConnectionError,
    SolarfocusError,
    SolarfocusProtocolError,
    SolarfocusRegisterError,
    SolarfocusTimeoutError,
)

_LOGGER = logging.getLogger(__name__)

_ILLEGAL_FUNCTION = 0x01
_ILLEGAL_ADDRESS = 0x02
_ILLEGAL_VALUE = 0x03
_DEVICE_FAILURE = 0x04


class Transport(Protocol):
    """What the client needs of a controller. The fake controller implements it too."""

    @property
    def connected(self) -> bool:
        """Whether there is a usable socket right now."""
        ...

    async def connect(self) -> None:
        """Open the socket, or raise `SolarfocusConnectionError`."""
        ...

    async def disconnect(self) -> None:
        """Close the socket. Safe to call when it is already closed."""
        ...

    async def read(self, kind: RegisterKind, address: int, count: int) -> tuple[int, ...]:
        """Read `count` registers, or raise."""
        ...

    async def write(self, writes: Sequence[Write]) -> None:
        """Put these words at these addresses, with nothing of ours in between."""
        ...

    async def probe(self, kind: RegisterKind, address: int, count: int = 1) -> tuple[int, ...] | None:
        """Read to find out whether the controller has an address. A refusal returns None."""
        ...


class ModbusTransport:
    """One socket to one controller."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        device_id: int = DEFAULT_DEVICE_ID,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        reconnect_delay: float = 1.0,
        reconnect_delay_max: float = 60.0,
    ) -> None:
        self.host = host
        self.port = port
        self.device_id = device_id
        self._client = AsyncModbusTcpClient(
            host,
            port=port,
            timeout=timeout,
            reconnect_delay=reconnect_delay,
            reconnect_delay_max=reconnect_delay_max,
            # One attempt per request. pymodbus retries inside the transaction,
            # and three retries at a three second timeout across twenty-odd
            # reads is a refresh that outlasts a ten second poll interval
            # several times over - so a controller that has gone away would
            # stall the caller rather than fail it.
            retries=1,
        )
        # Not for safety - pymodbus already serialises its own transactions -
        # but so that a group of writes goes out with no read of ours between
        # them, and so a small controller sees one outstanding request rather
        # than twenty arriving at once.
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        """Whether there is a usable socket right now."""
        return bool(self._client.connected)

    @property
    def address(self) -> str:
        """`host:port`, for a message someone has to act on."""
        return f"{self.host}:{self.port}"

    async def connect(self) -> None:
        """Open the socket. Idempotent, and raises rather than returning False."""
        if self.connected:
            return
        async with self._lock:
            await self._connect_holding_the_lock()

    async def _connect_holding_the_lock(self) -> None:
        """Connect, unless another task got there while this one waited for the lock.

        Separate from `connect` so the second look at `connected` is a fresh
        one: it is a property over a socket that another task can have opened in
        the meantime, and a type checker reading the two checks in one function
        has every reason to think the second cannot be true.
        """
        if self.connected:
            return
        try:
            opened = await self._client.connect()
        except TimeoutError as error:
            raise SolarfocusTimeoutError(f"{self.address} did not answer in time", context="connecting") from error
        except (ConnectionException, OSError) as error:
            raise SolarfocusConnectionError(f"could not reach {self.address}", context="connecting") from error
        if not opened:
            raise SolarfocusConnectionError(f"could not reach {self.address}", context="connecting")
        _LOGGER.debug("Connected to %s", self.address)

    async def disconnect(self) -> None:
        """Close the socket. Safe to call when it is already closed."""
        self._client.close()

    async def read(self, kind: RegisterKind, address: int, count: int) -> tuple[int, ...]:
        """Read `count` registers starting at `address`."""
        context = f"reading {kind.value} {address}-{address + count - 1}"
        async with self._lock:
            result = await self._call(kind, address, count, context)

        registers = getattr(result, "registers", None)
        if registers is None or len(registers) != count:
            got = "nothing" if registers is None else f"{len(registers)} registers"
            raise SolarfocusProtocolError(f"asked for {count} registers and got {got}", context=context)
        return tuple(registers)

    async def probe(self, kind: RegisterKind, address: int, count: int = 1) -> tuple[int, ...] | None:
        """Find out whether the controller has an address.

        A refusal is the answer here rather than a failure, so it comes back as
        None and is logged at debug. Note that a 32-bit register is only handed
        out whole: it refuses a read of one exactly the way a missing address
        does, so a caller that gets None for `count=1` should try `count=2`
        before concluding anything.
        """
        try:
            return await self.read(kind, address, count)
        except SolarfocusRegisterError as error:
            _LOGGER.debug("Controller has no %s %s: %s", kind.value, address, error)
            return None
        except SolarfocusProtocolError as error:
            _LOGGER.debug("Controller answered %s %s oddly: %s", kind.value, address, error)
            return None

    async def write(self, writes: Sequence[Write]) -> None:
        """Put these words at these addresses, with nothing of ours in between."""
        if not writes:
            return
        async with self._lock:
            for kind, address, words in writes:
                context = f"writing {kind.value} {address}"
                if kind is not RegisterKind.HOLDING:
                    raise SolarfocusError("only holding registers can be written", context=context)
                try:
                    result = await self._client.write_registers(address, list(words), device_id=self.device_id)
                except TimeoutError as error:
                    raise SolarfocusTimeoutError(f"{self.address} did not answer in time", context=context) from error
                except (ConnectionException, OSError) as error:
                    raise SolarfocusConnectionError(str(error) or "the connection dropped", context=context) from error
                except ModbusException as error:
                    raise SolarfocusProtocolError(str(error), context=context) from error
                _raise_for(result, context)

    async def _call(self, kind: RegisterKind, address: int, count: int, context: str) -> Any:
        reader = self._client.read_input_registers if kind is RegisterKind.INPUT else self._client.read_holding_registers
        try:
            result = await reader(address, count=count, device_id=self.device_id)
        except TimeoutError as error:
            raise SolarfocusTimeoutError(f"{self.address} did not answer in time", context=context) from error
        except (ConnectionException, OSError) as error:
            raise SolarfocusConnectionError(str(error) or "the connection dropped", context=context) from error
        except ModbusException as error:
            raise SolarfocusProtocolError(str(error), context=context) from error
        _raise_for(result, context)
        return result


def _raise_for(result: Any, context: str) -> None:
    """Turn a Modbus exception response into one of ours.

    `IllegalAddressError` is the one that matters: it means the configured
    firmware claims a register this controller does not have, which is a fault
    the owner can fix. The client reports it against the components whose
    registers were in that read rather than as a lost connection.
    """
    if isinstance(result, ExceptionResponse):
        code = result.exception_code
        if code == _ILLEGAL_ADDRESS:
            raise IllegalAddressError("this firmware has no such register", context=context)
        if code == _ILLEGAL_VALUE:
            raise IllegalValueError("the controller rejected the value", context=context)
        if code == _DEVICE_FAILURE:
            raise DeviceFailureError("the controller failed while carrying out the request", context=context)
        if code == _ILLEGAL_FUNCTION:
            raise SolarfocusRegisterError("the controller does not support that request", context=context)
        raise SolarfocusRegisterError(f"the controller answered with exception code {code}", context=context)
    if result is None or (hasattr(result, "isError") and result.isError()):
        raise SolarfocusProtocolError(f"the controller answered {result!r}", context=context)
