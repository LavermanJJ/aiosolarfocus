"""Work out what an installation looks like, by asking the controller.

An optional helper. Explicit configuration is the primary path - a caller names
the system, the firmware and the counts - and this exists so that a caller who
would rather not can hand a `Detection` straight back as a `SolarfocusConfig`.

There is no register listing the installed components, so this reads two
different things off the controller:

* **Which registers exist.** An address the firmware does not map is refused
  with illegal data address, so probing establishes the register set - the api
  version, and the layout of the components whose registers moved between
  versions. It says next to nothing about installed components: on a 26.020
  controller every documented register was mapped bar the X35 buffer sensors of
  the buffers that are not there.
* **What the registers say.** The specification defines "nicht vorhanden" and
  "nicht freigeschaltet" values for the components that repeat, and an
  unconfigured sensor channel reports a temperature far outside its range. That
  is what the counts are taken from.

The result carries the evidence it was reached from, because a heating system
this is wrong about is one whose owner has to be able to see why.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from .config import SolarfocusConfig
from .const import DEFAULT_DEVICE_ID, DEFAULT_PORT, DEFAULT_TIMEOUT, OPEN_CHANNEL, ApiVersion, RegisterKind, Systems
from .transport import ModbusTransport, Transport

_LOGGER = logging.getLogger(__name__)

INPUT = RegisterKind.INPUT
HOLDING = RegisterKind.HOLDING

#: What an unconfigured or open sensor channel reports instead of a temperature.
#: Exactly the read path's `OPEN_CHANNEL` plus -1 read as unsigned: -0.1 degC is
#: a real reading, so blanking it there would lose a sensor every frosty night,
#: while here a -1 is evidence that a channel is not configured. Derived from
#: `OPEN_CHANNEL` rather than repeated, so a marker learned from a controller
#: cannot land in one of the two and not the other.
NO_SENSOR = OPEN_CHANNEL | {2**16 - 1}

#: "Heizkreis nicht freigeschaltet": status 7 where the states are enumerated
#: from 0, and 212 in the block a therminator numbers them in - that block is not
#: the from-0 one shifted by 200, so the code has to be named separately. Both
#: are unambiguous, because the from-0 table stops at 31, so one set covers a
#: controller of either kind. Only 7 was here until fklein1980's therminator in
#: home-assistant-solarfocus#237 reported one circuit at 214 and seven at 212,
#: and was told it had eight heating circuits when it has one.
HEATING_CIRCUIT_DISABLED = frozenset({7, 212})

#: Boiler status 0 is "Boilerstatus nicht vorhanden" and buffer status 0 is
#: "Status nicht vorhanden". The therminator systems enumerate the same states
#: from 200, where the first is again the one meaning the component is not there.
NOT_PRESENT = frozenset({0, 200})

#: The code a controller that enumerates its component states from 200 starts at.
#: Which block is in use is a property of the system rather than the firmware:
#: both therminators in home-assistant-solarfocus#237 report their heating
#: circuit, buffer and boiler states from 200, and the ecotop and the three
#: pellet elegance dumps in the same issue all enumerate from 0 - on the same
#: 26.020 firmware. No from-0 table reaches 200, the longest stopping at 31, so a
#: single state that high says which block the controller is speaking.
STATE_BLOCK_200 = 200

#: A buffer temperature this high is not a real reading: no octoplus buffer
#: runs anywhere near 100 degC. NO_SENSOR's exact sentinels are not the whole
#: story here either - a real Pellet Elegance read 150.0 degC at this same
#: address (home-assistant-solarfocus#237), well past NO_SENSOR but just as far
#: from anything a buffer sensor reports.
IMPLAUSIBLE_BUFFER_TEMPERATURE = 1000

#: Kaminkehrerfunktion Start/Stopp - a function every biomass boiler but the
#: ecotop has (`_NOT_ECOTOP` in components/biomass_boiler.py). Read for
#: existence rather than content, so it is unaffected by 2410 and 2411 being
#: unreliable, but the two directions are not equally strong and this module's
#: own preamble says why: a refusal is good evidence for an ecotop, while a
#: mapped register is weak evidence against one, because the 26.020 controller
#: this was written against mapped every documented register bar the X35 sensors
#: of the buffers it did not have. No dump settles which way an ecotop goes -
#: none of the three in #237 is one. So this is the tie-break between the two
#: models nothing else separates rather than a reading of the model, and it
#: breaks towards the pellet elegance: guessing that costs an ecotop a sweep
#: button it will refuse, where guessing the other way costs a pellet elegance
#: the sweep and pellet-store-reset entities it does have.
CHIMNEY_SWEEP_HOLDING = 33410

#: Registers a version introduced, each picked to be there whatever the
#: installation looks like - the fourth instance of a component, or a setting -
#: so that the version they date the controller to does not depend on what is
#: plugged into it. Highest first: the first one the controller has wins.
#:
#: 25.020 and 25.030 added their registers together, so a controller on 25.020
#: is reported as 25.030. The layout that distinction is wanted for is probed
#: directly rather than derived from the version, so this costs nothing here.
VERSION_MARKERS: tuple[tuple[ApiVersion, RegisterKind, int], ...] = (
    (ApiVersion.V_26_020, HOLDING, 33415),  # HEMS target electrical power
    (ApiVersion.V_25_030, INPUT, 2230),  # differential module 4
    (ApiVersion.V_23_080, INPUT, 2420),  # sweep almost done
    (ApiVersion.V_23_040, INPUT, 802),  # fresh water cascade target temperature
    (ApiVersion.V_23_020, INPUT, 775),  # fresh water module 4 status
    (ApiVersion.V_23_010, HOLDING, 33412),  # pellet store refilled
    (ApiVersion.V_22_090, HOLDING, 32958),  # heating circuit 8 heating mode
    (ApiVersion.V_21_140, INPUT, 2511),  # pv overcharge active
)

HEATING_CIRCUIT_BASE, HEATING_CIRCUIT_STRIDE, HEATING_CIRCUIT_MAX = 1100, 50, 8
BOILER_BASE, BOILER_STRIDE, BOILER_MAX = 500, 50, 4
BUFFER_BASE, BUFFER_STRIDE, BUFFER_MAX = 1900, 20, 4
FRESH_WATER_BASE, FRESH_WATER_STRIDE, FRESH_WATER_MAX = 700, 25, 4
CIRCULATION_BASE, CIRCULATION_STRIDE, CIRCULATION_MAX = 900, 25, 4
SOLAR_BASE, SOLAR_STRIDE, SOLAR_MAX = 2100, 20, 4

#: What a solar circuit is counted on - the two collector temperatures, the flow
#: and return, and the first store sensor - and the state that is read with them
#: for the evidence but says nothing either way. See `_detect_counts`.
SOLAR_SENSORS = (0, 1, 2, 3, 10)
SOLAR_STATE = 13

DIFFERENTIAL_BASE, DIFFERENTIAL_STRIDE, DIFFERENTIAL_MAX = 2200, 10, 4

_THERMINATOR_LOG_MODES = range(1, 4)


@dataclass(frozen=True, slots=True)
class ComponentCounts:
    """How many of each repeated component an installation has."""

    heating_circuits: int = 0
    buffers: int = 0
    boilers: int = 0
    fresh_water_modules: int = 0
    circulations: int = 0
    differential_modules: int = 0
    solar: int = 0


@dataclass(frozen=True, slots=True)
class Detection:
    """What one controller says it is, and what that was read off."""

    api_version: ApiVersion
    system: Systems
    counts: ComponentCounts
    has_heat_pump: bool
    has_biomass_boiler: bool
    has_photovoltaic: bool
    has_fresh_water_module_cascade: bool
    has_circulation_module: bool
    evidence: Mapping[str, Any]
    reads: int

    @property
    def confident(self) -> bool:
        """Whether the heat generator identified itself.

        False when neither the heat pump nor a biomass boiler reported anything
        alive, in which case `system` is a default rather than a finding.
        """
        return self.has_heat_pump or self.has_biomass_boiler

    def config(self, host: str, **overrides: Any) -> SolarfocusConfig:
        """The configuration this installation would have been typed in as."""
        config = SolarfocusConfig(
            host=host,
            system=self.system,
            api_version=self.api_version,
            heating_circuits=self.counts.heating_circuits,
            buffers=self.counts.buffers,
            boilers=self.counts.boilers,
            fresh_water_modules=self.counts.fresh_water_modules,
            circulations=self.counts.circulations,
            differential_modules=self.counts.differential_modules,
            solar=self.counts.solar,
            fresh_water_module_cascade=self.has_fresh_water_module_cascade,
            circulation_module=self.has_circulation_module,
            photovoltaic=self.has_photovoltaic,
        )
        return replace(config, **overrides) if overrides else config


async def detect(
    host: str,
    port: int = DEFAULT_PORT,
    device_id: int = DEFAULT_DEVICE_ID,
    *,
    # Not a deadline for the whole run: it is the per-request Modbus timeout,
    # handed to the transport, the same one `SolarfocusConfig.timeout` sets.
    timeout: float = DEFAULT_TIMEOUT,  # noqa: ASYNC109
) -> Detection:
    """Probe a controller and work out what is on it.

    Around ninety single-register reads, which is a few seconds on the
    controllers this was written against: fine once, when somebody is setting
    the integration up, and not something to do on every refresh.
    """
    transport = ModbusTransport(host, port, device_id, timeout=timeout)
    await transport.connect()
    try:
        return await detect_through(transport)
    finally:
        await transport.disconnect()


async def detect_through(transport: Transport) -> Detection:
    """Detect over a connection somebody else opened.

    Goes through the same transport and the same lock, so a caller who is
    already connected pays no second socket for it.
    """
    return await _Prober(transport).run()


class _Prober:
    """One detection run, and the reads it took."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._reads = 0
        self._evidence: dict[str, Any] = {}

    async def run(self) -> Detection:
        """Probe the controller and work out what is on it."""
        api_version = ApiVersion.V_20_110
        for version, kind, address in VERSION_MARKERS:
            if await self._exists(kind, address):
                api_version = version
                break
        self._evidence["api_version_marker"] = api_version.label

        # Where the state registers of the repeated components sit. The library
        # derives these from the system and the version together - a therminator
        # heating circuit is laid out like a 25.030 one - but the controller can
        # simply be asked how far its blocks reach, which is both shorter and
        # right for a combination nobody has tried yet.
        heating_circuit_state_offset = 7 if await self._exists(INPUT, HEATING_CIRCUIT_BASE + 7) else 6
        buffer_state_offset = 4 if await self._exists(INPUT, BUFFER_BASE + 5) else 3
        heat_pump_is_modern = await self._exists(INPUT, 2330)
        self._evidence["layout"] = {
            "heating_circuit_state_offset": heating_circuit_state_offset,
            "buffer_state_offset": buffer_state_offset,
            "heat_pump": "25.030" if heat_pump_is_modern else "legacy",
        }

        # Read before the system is worked out rather than with the rest of the
        # counts, because which block the controller numbers these states in is
        # what tells a therminator from the pellet boilers. `_detect_counts`
        # takes them from here rather than reading the eight registers again.
        heating_circuit_states = [
            await self._value(INPUT, HEATING_CIRCUIT_BASE + HEATING_CIRCUIT_STRIDE * index + heating_circuit_state_offset) for index in range(HEATING_CIRCUIT_MAX)
        ]

        has_heat_pump, has_biomass_boiler, system = await self._detect_system(heat_pump_is_modern, heating_circuit_states)

        photovoltaic_power = await self._dword(INPUT, 2500)
        self._evidence["photovoltaic_power"] = photovoltaic_power

        # The cascade and the circulation module have no documented "not
        # present" value, so this goes by the same rule as solar: a live
        # reading counts, a flat zero does not. Reasoned from the specification
        # rather than measured - no installation with either was available.
        cascade_state = await self._value(INPUT, 800)
        cascade_flow = await self._value(INPUT, 801)
        circulation_supply = await self._value(INPUT, 850)
        self._evidence["fresh_water_module_cascade"] = [cascade_state, cascade_flow]
        self._evidence["circulation_module"] = circulation_supply

        counts = await self._detect_counts(heating_circuit_states, buffer_state_offset)
        counts = _clamp_to_version(counts, api_version)

        detection = Detection(
            api_version=api_version,
            system=system,
            counts=counts,
            has_heat_pump=has_heat_pump,
            has_biomass_boiler=has_biomass_boiler,
            has_photovoltaic=bool(photovoltaic_power),
            has_fresh_water_module_cascade=api_version >= ApiVersion.V_23_040 and (_live(cascade_state) or _live(cascade_flow)),
            has_circulation_module=api_version >= ApiVersion.V_23_040 and _live(circulation_supply),
            evidence=self._evidence,
            reads=self._reads,
        )
        _LOGGER.info("Detected %s on firmware %s in %d reads: %s", system.value, api_version.label, self._reads, counts)
        return detection

    async def _detect_system(self, heat_pump_is_modern: bool, heating_circuit_states: list[int | None]) -> tuple[bool, bool, Systems]:
        """Which heat generator is installed, and so which system this is."""
        supply = await self._value(INPUT, 2300)
        return_temperature = await self._value(INPUT, 2301)
        heat_pump_state = await self._value(INPUT, 2330 if heat_pump_is_modern else 2326)
        has_heat_pump = _live(supply) or _live(return_temperature) or _live(heat_pump_state)
        self._evidence["heat_pump"] = {"supply": supply, "return": return_temperature, "state": heat_pump_state}

        temperature = await self._value(INPUT, 2400)
        status = await self._value(INPUT, 2401)
        operating_mode = await self._value(INPUT, 2409)
        octoplus_bottom = await self._value(INPUT, 2410)
        octoplus_top = await self._value(INPUT, 2411)
        log_wood = await self._value(INPUT, 2412)
        pellets = await self._dword(INPUT, 2416)
        operating_minutes = await self._dword(INPUT, 2402)
        has_biomass_boiler = _live(temperature) or bool(pellets) or bool(operating_minutes)
        self._evidence["biomass_boiler"] = {
            "temperature": temperature,
            "status": status,
            "operating_mode": operating_mode,
            "log_wood": log_wood,
            "octoplus_buffer": [octoplus_bottom, octoplus_top],
            "pellet_usage_total": pellets,
            "operating_minutes": operating_minutes,
        }

        enumerates_from_200 = any(state is not None and state >= STATE_BLOCK_200 for state in heating_circuit_states)
        self._evidence["state_block"] = STATE_BLOCK_200 if enumerates_from_200 else 0

        # A heat pump is a vampair whatever else is on the controller, because
        # that is the component the library builds for it. Which biomass boiler
        # it is has only been reasoned from the specification and, since
        # home-assistant-solarfocus#237, three Pellet Elegance and one
        # Therminator dump - so the values it rests on are in the evidence for
        # an owner to argue with.
        if has_heat_pump or not has_biomass_boiler:
            system = Systems.VAMPAIR
        elif _plausible_buffer(octoplus_top):
            # Only 2411 is trusted here. 2410 is the same register an ecotop and
            # a pellet elegance read their return flow temperature from, so a
            # live reading there is evidence of a biomass boiler, not of an
            # octoplus specifically - all three Pellet Elegance dumps from #237
            # had a live return flow at 2410 and were detected as an octoplus
            # for exactly that reason.
            #
            # The cost of dropping 2410 from the test: an octoplus whose buffer
            # top sensor is not reporting - unconfigured, open, or reading
            # something no buffer reads - has nothing left to identify it and
            # falls through to the pellet elegance guess below, which relabels
            # 2410 as a return flow and drops both buffer temperatures. That is
            # the sensor of an integrated buffer, so it should be as configured
            # as the boiler is; the values it was judged on are in the evidence
            # either way.
            system = Systems.OCTOPLUS
        elif _live(log_wood) or (operating_mode is not None and operating_mode in _THERMINATOR_LOG_MODES):
            # Kesselbetriebsart 1-3 all burn logs, which only a therminator does.
            # Mode 0 is logs as well, but it is also what an unset register
            # reads, so it is not taken as evidence of anything.
            system = Systems.THERMINATOR
        elif enumerates_from_200:
            # A therminator that is not burning logs right now: mode 4 is
            # pellets, and a combination boiler on pellets says nothing about
            # the logs it can also burn. What still separates it is the block it
            # numbers its states in - see `STATE_BLOCK_200`. Both therminators in
            # #237 were read as pellet boilers without this, fklein1980's on
            # pellets and ragesoft's idling in mode 0, and a therminator read as
            # a pellet elegance loses its log wood and operating mode entities
            # and gains a return flow temperature from 2410, an address a
            # therminator does not assign.
            system = Systems.THERMINATOR
        else:
            # Neither octoplus nor therminator - which leaves ecotop and pellet
            # elegance, indistinguishable by anything read so far. The chimney
            # sweep function is the only thing that separates them at all, and
            # `CHIMNEY_SWEEP_HOLDING` says how far that goes: a refused register
            # makes this an ecotop, a mapped one leaves the pellet elegance as
            # the safer of two guesses rather than establishing it.
            has_chimney_sweep = await self._exists(HOLDING, CHIMNEY_SWEEP_HOLDING)
            self._evidence["chimney_sweep_holding"] = has_chimney_sweep
            system = Systems.PELLETELEGANCE if has_chimney_sweep else Systems.ECOTOP

        return has_heat_pump, has_biomass_boiler, system

    async def _detect_counts(self, heating_circuits: list[int | None], buffer_state_offset: int) -> ComponentCounts:
        """How many of each repeated component the controller is driving.

        The heating circuit states are handed in because `run` has already read
        them, to tell the system by the block they are numbered in.
        """

        async def instances(base: int, stride: int, maximum: int, offset: int = 0) -> list[int | None]:
            return [await self._value(INPUT, base + stride * index + offset) for index in range(maximum)]

        boilers = await instances(BOILER_BASE, BOILER_STRIDE, BOILER_MAX, 1)
        buffers = await instances(BUFFER_BASE, BUFFER_STRIDE, BUFFER_MAX, buffer_state_offset)
        circulations = await instances(CIRCULATION_BASE, CIRCULATION_STRIDE, CIRCULATION_MAX)

        # The fresh water module status has no documented enumeration, so it is
        # taken together with the temperature of the water it is delivering.
        fresh_water = [
            (await self._value(INPUT, FRESH_WATER_BASE + FRESH_WATER_STRIDE * index), await self._value(INPUT, FRESH_WATER_BASE + FRESH_WATER_STRIDE * index + 1))
            for index in range(FRESH_WATER_MAX)
        ]

        # The solar circuit has no state saying whether it is there, so it goes
        # by its sensor channels reading plain zero. A channel that is configured
        # but has no sensor on it reports 130.0 or 270.0 degC, which counts as
        # there rather than not: an unconfigured one reads 0, the same way the
        # buffers that are not there have no X35 register at all while the one
        # that is has it reading 270.0.
        #
        # `Solar - Statuszeile` is read with them for the evidence but left out
        # of the count, because it is nonzero either way round. The three
        # circuits fklein1980's therminator does not have each reported 201,
        # "Kollektorfühler Kurzschluss" in the from-200 block - an absent sensor
        # reading as a shorted one - and were counted as circuits for it
        # (home-assistant-solarfocus#237). It cannot argue the other way either:
        # in the from-0 block 0 is "Solarkreis in Betrieb", so a running circuit
        # and an absent one report the same thing.
        solar = [[await self._value(INPUT, SOLAR_BASE + SOLAR_STRIDE * index + offset) for offset in (*SOLAR_SENSORS, SOLAR_STATE)] for index in range(SOLAR_MAX)]

        # The differential module is read for the evidence but never counted.
        # The same rule as solar would have claimed one on the system this was
        # written against, whose owner could find none configured, and whose
        # three live channels each repeated a temperature belonging to another
        # component - the boiler, and the heat pump flow and return. Whether
        # that is a module wired to those same points or the controller filling
        # an unused block is not something the registers settle, and a detector
        # filling in a form should not invent a component it cannot see. Until
        # an installation with a known differential module can say what one
        # looks like, this stays at zero for the owner to raise.
        differential = [[await self._value(INPUT, DIFFERENTIAL_BASE + DIFFERENTIAL_STRIDE * index + offset) for offset in (1, 2, 4, 5)] for index in range(DIFFERENTIAL_MAX)]

        self._evidence["heating_circuit_states"] = heating_circuits
        self._evidence["boiler_states"] = boilers
        self._evidence["buffer_states"] = buffers
        self._evidence["fresh_water_modules"] = fresh_water
        self._evidence["circulation_temperatures"] = circulations
        self._evidence["solar"] = solar
        self._evidence["differential_modules"] = differential

        return ComponentCounts(
            heating_circuits=sum(1 for state in heating_circuits if state is not None and state not in HEATING_CIRCUIT_DISABLED),
            boilers=sum(1 for state in boilers if state is not None and state not in NOT_PRESENT),
            buffers=sum(1 for state in buffers if state is not None and state not in NOT_PRESENT),
            fresh_water_modules=sum(1 for state, temperature in fresh_water if _live(state) or _live(temperature)),
            circulations=sum(1 for temperature in circulations if _live(temperature)),
            solar=sum(1 for values in solar if any(_configured(value) for value in values[: len(SOLAR_SENSORS)])),
            differential_modules=0,
        )

    async def _probe(self, kind: RegisterKind, address: int, count: int = 1) -> tuple[int, ...] | None:
        self._reads += 1
        return await self._transport.probe(kind, address, count)

    async def _exists(self, kind: RegisterKind, address: int) -> bool:
        """Whether the firmware maps this address.

        A 32-bit register refuses a single-register read the same way a missing
        address does, so an address counts as absent only once it has refused
        both of its registers as well.
        """
        if await self._probe(kind, address) is not None:
            return True
        return await self._probe(kind, address, count=2) is not None

    async def _value(self, kind: RegisterKind, address: int) -> int | None:
        """One register, or None if the controller refused it."""
        registers = await self._probe(kind, address)
        return None if registers is None else registers[0]

    async def _dword(self, kind: RegisterKind, address: int) -> int | None:
        """One 32-bit register, or None if the controller refused it."""
        registers = await self._probe(kind, address, count=2)
        return None if registers is None else (registers[0] << 16) + registers[1]


def _live(value: int | None) -> bool:
    """Whether a register is reporting a measurement rather than an empty channel."""
    return value is not None and value != 0 and value not in NO_SENSOR


def _plausible_buffer(value: int | None) -> bool:
    """Whether a reading could be an octoplus buffer sensor reporting.

    `_live` and a ceiling, and the sign taken off first: `_value` hands back the
    unsigned word, so a reading below zero - not a buffer either - would sail
    past a ceiling test as a number in the sixty thousands rather than fail it.
    """
    if value is None or not _live(value):
        return False
    tenths = value - (1 << 16) if value >= (1 << 15) else value
    return 0 < tenths < IMPLAUSIBLE_BUFFER_TEMPERATURE


def _configured(value: int | None) -> bool:
    """Whether a channel exists at all, sensor on it or not.

    Weaker than `_live`, for the components with no state register to ask. A
    configured channel whose sensor is missing reports one of the out-of-range
    temperatures rather than zero, so a sentinel is evidence that the component
    is there - only a flat zero says it is not.
    """
    return value is not None and value != 0


def _clamp_to_version(counts: ComponentCounts, api_version: ApiVersion) -> ComponentCounts:
    """Drop components the detected version cannot address.

    A component's registers are mapped whether or not the library can read them,
    so a controller can report a fresh water module over a firmware that has no
    fresh water module in it. Handing that count on would only make the
    configuration refuse to build.
    """
    if api_version < ApiVersion.V_23_020:
        counts = replace(counts, fresh_water_modules=0)
    if api_version < ApiVersion.V_25_030:
        counts = replace(counts, circulations=0, differential_modules=0, solar=min(counts.solar, 1))
    return counts
