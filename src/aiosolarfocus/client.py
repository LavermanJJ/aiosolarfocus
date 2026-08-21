"""The client: a configuration in, components you can read values off.

`update` raises only when the connection itself is the problem, and reports
everything else per component. That distinction is the whole reason the Home
Assistant coordinator currently has to consult `api.is_connected` after a failed
read to work out what the failure meant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from .components import ComponentId, spec_for
from .components.base import Component
from .config import ComponentKey, SolarfocusConfig
from .const import RegisterKind, Write
from .exceptions import SolarfocusConnectionError, SolarfocusError
from .planner import ReadPlan, plan
from .transport import ModbusTransport, Transport

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """What one refresh managed, and what it did not."""

    read: frozenset[ComponentKey]
    failed: Mapping[ComponentKey, SolarfocusError]
    round_trips: int
    duration: float

    @property
    def ok(self) -> bool:
        """Whether every configured component answered."""
        return not self.failed

    def __str__(self) -> str:
        """A line someone can put in a log."""
        summary = f"{len(self.read)} components in {self.round_trips} reads, {self.duration * 1000:.0f} ms"
        if not self.failed:
            return summary
        return f"{summary}; failed: " + ", ".join(f"{key} ({error.message})" for key, error in self.failed.items())


class SolarfocusClient:
    """One heating system.

    Components are plain attributes: `client.heating_circuits` is a list and
    `client.heat_pump` is one object or None. Reading a value off one is not
    asynchronous - it is the last reading, already decoded - so only the calls
    that talk to the controller are awaited.
    """

    def __init__(self, config: SolarfocusConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport: Transport = transport or ModbusTransport(config.host, config.port, config.device_id, timeout=config.timeout)

        self._components: dict[ComponentKey, Component] = {}
        for key, layout in config.layouts().items():
            spec = spec_for(key.id)
            number = key.number if spec.max_count > 1 else None
            self._components[key] = spec.component(layout, index=number, writer=self._write)

        self.read_plan: ReadPlan[ComponentKey] = plan(config.layouts())

    # -- getting at the components ----------------------------------------- #

    def __getattr__(self, name: str) -> Any:
        """Expose components as attributes: `client.buffers`, `client.heat_pump`.

        Only reached for names the instance does not have, so it cannot shadow
        anything real. A component the configuration says is not there reads as
        an empty list or as None rather than raising, because "this
        installation has no solar" is an answer, not a mistake.
        """
        try:
            component_id = ComponentId(name)
        except ValueError:
            raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}") from None
        found = self.of(component_id)
        if spec_for(component_id).max_count == 1:
            return found[0] if found else None
        return found

    def of(self, component_id: ComponentId) -> list[Component]:
        """Every instance of one component, in the order a caller counts them."""
        return [component for key, component in self._components.items() if key.id is component_id]

    def __getitem__(self, key: ComponentKey) -> Component:
        """One component instance by key."""
        return self._components[key]

    @property
    def components(self) -> Mapping[ComponentKey, Component]:
        """Every component this configuration says exists."""
        return dict(self._components)

    # -- the connection ----------------------------------------------------- #

    @property
    def connected(self) -> bool:
        """Whether there is a usable socket right now."""
        return self._transport.connected

    async def connect(self) -> None:
        """Open the connection. Idempotent; raises `SolarfocusConnectionError`."""
        await self._transport.connect()

    async def disconnect(self) -> None:
        """Close the connection."""
        await self._transport.disconnect()

    async def __aenter__(self) -> Self:
        """Connect, and hand back the client."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the connection, whatever happened inside."""
        await self.disconnect()

    # -- reading ------------------------------------------------------------ #

    async def update(self, *, components: Collection[ComponentId] | None = None) -> UpdateResult:
        """Read every configured component, or only the ones named.

        Raises when the connection itself is the problem, because that is the
        one failure that says nothing about any particular component: a socket
        dropping part way through a refresh would otherwise be reported as every
        component after it in the plan having gone quiet, which is a heating
        system greying out an arbitrary tail of itself over one lost connection.

        Everything else - a range this firmware refuses, a controller answering
        an exception - is attributed to the components whose registers were in
        that read and carried back in `failed`, and the rest of the refresh
        still happens.
        """
        started = time.monotonic()
        await self.connect()

        wanted = self._selected(components)
        reads = [read for read in self.read_plan.slices if read.components & wanted]

        readings: dict[tuple[RegisterKind, int], int] = {}
        failed: dict[ComponentKey, SolarfocusError] = {}
        for read in reads:
            try:
                words = await self._transport.read(read.kind, read.address, read.count)
            except SolarfocusConnectionError:
                # Not a statement about any component. Let it out, and let the
                # caller decide the whole system is unreachable.
                raise
            except SolarfocusError as error:
                _LOGGER.debug("Read %s failed: %s", read, error)
                for key in read.components & wanted:
                    failed.setdefault(key, error)
                continue
            readings.update(zip(((read.kind, address) for address in read.addresses), words, strict=True))

        for key in wanted:
            component = self._components[key]
            if key in failed:
                component.mark_failed(failed[key])
            else:
                component.decode_readings(readings)

        return UpdateResult(
            read=frozenset(wanted - set(failed)),
            failed=failed,
            round_trips=len(reads),
            duration=time.monotonic() - started,
        )

    def snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Every reading, for diagnostics.

        Replaces the downstream habit of reaching into `vars(component)` and
        filtering by an internal base class.
        """
        return {str(key): component.snapshot() for key, component in self._components.items()}

    def _selected(self, components: Collection[ComponentId] | None) -> set[ComponentKey]:
        if components is None:
            return set(self._components)
        chosen = set(components)
        return {key for key in self._components if key.id in chosen}

    async def _write(self, writes: Sequence[Write]) -> None:
        """How a component reaches the controller."""
        await self.connect()
        await self._transport.write(writes)

    def __repr__(self) -> str:
        """Name the controller, the system and what is on it."""
        return f"<SolarfocusClient {self.config.address} {self.config.system.value} {self.config.api_version.label}, {len(self._components)} components>"
