"""A blocking front for `SolarfocusClient`, for scripts.

Small, because in this design only I/O is asynchronous. Reading a value off a
component is a dictionary lookup behind a descriptor - it was never a coroutine -
so a script reads `sync.client.heat_pump.supply_temperature` directly and only
the four calls that talk to the controller need wrapping.
"""

from __future__ import annotations

import asyncio
import threading
import warnings
from collections.abc import Collection, Coroutine
from types import TracebackType
from typing import Any, Self

from .client import SolarfocusClient, UpdateResult
from .components import ComponentId
from .config import SolarfocusConfig
from .detect import Detection
from .exceptions import SolarfocusConfigError
from .transport import Transport


class SolarfocusSync:
    """A heating system, without the await.

    Owns a thread with an event loop of its own; every coroutine is submitted to
    it and waited on. The underlying client belongs to that loop and must not be
    driven from another, which is why the client is built inside it.
    """

    def __init__(self, config: SolarfocusConfig, *, transport: Transport | None = None) -> None:
        # Set before anything that can raise, so `__del__` on a half-built
        # object has something to look at.
        self._closed = True
        if _inside_a_running_loop():
            raise SolarfocusConfigError(
                "SolarfocusSync cannot be built inside a running event loop: it would block that loop waiting on another. Use SolarfocusClient and await it instead."
            )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="aiosolarfocus", daemon=True)
        self._thread.start()
        self._closed = False
        self._client = self.run(_build(config, transport))

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @property
    def client(self) -> SolarfocusClient:
        """The asynchronous client underneath, for reading values off components."""
        return self._client

    def run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Run one coroutine on the client's loop and wait for it.

        The way to reach anything not wrapped below - a setter, detection -
        keeping the coroutine's own return type.
        """
        if self._closed:
            # The caller built the coroutine before getting here; closing it
            # keeps Python from complaining that it was never awaited, which
            # would bury the real complaint under a warning about a warning.
            coro.close()
            raise SolarfocusConfigError("this client has been closed")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect(self) -> None:
        """Open the connection."""
        self.run(self._client.connect())

    def disconnect(self) -> None:
        """Close the connection, keeping the loop for another connect."""
        self.run(self._client.disconnect())

    def update(self, *, components: Collection[ComponentId] | None = None) -> UpdateResult:
        """Read every configured component, or only the ones named."""
        return self.run(self._client.update(components=components))

    def detect(self) -> Detection:
        """Ask the controller what it is, over the connection already open."""
        return self.run(self._client.detect())

    def close(self) -> None:
        """Close the connection and stop the loop. Safe to call twice."""
        if self._closed:
            return
        try:
            self.run(self._client.disconnect())
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
            self._loop.close()

    def __enter__(self) -> Self:
        """Connect, and hand back the client."""
        self.connect()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the connection and stop the loop, whatever happened inside."""
        self.close()

    def __del__(self) -> None:
        """Say so if the loop thread was left running."""
        if not getattr(self, "_closed", True):
            warnings.warn(f"{type(self).__name__} was never closed; its event loop thread is still running", ResourceWarning, stacklevel=2)

    def __repr__(self) -> str:
        """Name the controller and say whether it is still usable."""
        return f"<SolarfocusSync {self._client.config.address}{' closed' if self._closed else ''}>"


async def _build(config: SolarfocusConfig, transport: Transport | None) -> SolarfocusClient:
    """Build the client on the loop that will drive it."""
    return SolarfocusClient(config, transport=transport)


def _inside_a_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True
