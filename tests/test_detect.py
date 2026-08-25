"""Detection: what the controller says it is, and what that was read off."""

from __future__ import annotations

import pytest

from aiosolarfocus.client import SolarfocusClient
from aiosolarfocus.config import SolarfocusConfig
from aiosolarfocus.const import ApiVersion, RegisterKind, Systems
from aiosolarfocus.detect import VERSION_MARKERS, detect_through
from aiosolarfocus.testing import FakeController, load_spec

pytestmark = pytest.mark.asyncio
INPUT = RegisterKind.INPUT
HOLDING = RegisterKind.HOLDING

#: Every 32-bit register in the document, so the fake refuses a read of one the
#: way a real controller does.
WIDE = frozenset((kind, address) for (kind, address), rows in load_spec().items() if rows[0].count > 1)

#: A vampair with one heating circuit, one buffer, one boiler and nothing else:
#: the shape of the installation this was originally written against.
VAMPAIR = {
    (INPUT, 1107): 2,  # heating circuit 1 in heating mode
    (INPUT, 1157): 7,  # circuit 2 "nicht freigeschaltet"
    (INPUT, 1207): 7,
    (INPUT, 1257): 7,
    (INPUT, 1307): 7,
    (INPUT, 1357): 7,
    (INPUT, 1407): 7,
    (INPUT, 1457): 7,
    (INPUT, 501): 1,  # boiler 1 in standby
    (INPUT, 1904): 1,  # buffer 1 in standby
    (INPUT, 2300): 285,  # heat pump flow 28.5 degC
    (INPUT, 2301): 240,
    (INPUT, 2330): 1,
    (INPUT, 2500): 0,  # no photovoltaic
    (INPUT, 2501): 0,
}


async def controller(values: dict[tuple[RegisterKind, int], int] | None = None, absent: list[tuple[RegisterKind, int]] | None = None) -> FakeController:
    """A controller with the whole documented map, holding zero unless told otherwise."""
    everything = dict.fromkeys(load_spec(), 0)
    everything.update(values or {})
    fake = FakeController(everything, wide=WIDE)
    for kind, address in absent or []:
        fake.unmap(kind, address)
    await fake.connect()
    return fake


async def test_a_vampair_reports_itself_as_one() -> None:
    detection = await detect_through(await controller(VAMPAIR))

    assert detection.system is Systems.VAMPAIR
    assert detection.api_version is ApiVersion.V_26_020
    assert detection.has_heat_pump
    assert not detection.has_biomass_boiler
    assert detection.confident


async def test_the_counts_come_from_the_documented_not_present_values() -> None:
    detection = await detect_through(await controller(VAMPAIR))

    assert detection.counts.heating_circuits == 1
    assert detection.counts.boilers == 1
    assert detection.counts.buffers == 1


async def test_a_differential_module_is_never_claimed() -> None:
    """The rule that counts solar would have claimed one on a system with none.

    Its three live channels each repeated a temperature belonging to another
    component, and nothing in the registers separates that from a module wired
    to the same points.
    """
    values = dict(VAMPAIR)
    values.update({(INPUT, 2201): 450, (INPUT, 2202): 300, (INPUT, 2204): 285})
    detection = await detect_through(await controller(values))
    assert detection.counts.differential_modules == 0
    assert detection.evidence["differential_modules"][0] == [450, 300, 285, 0]


async def test_a_solar_circuit_with_an_open_sensor_still_counts() -> None:
    """A configured channel with no sensor reports 270.0 degC; only a flat zero is absent."""
    values = dict(VAMPAIR)
    values[(INPUT, 2100)] = 2700
    detection = await detect_through(await controller(values))
    assert detection.counts.solar == 1


async def test_a_32_bit_register_is_found_despite_refusing_a_read_of_one() -> None:
    """Without the count=2 fallback every 32-bit counter in the map looks absent."""
    values = dict(VAMPAIR)
    values[(INPUT, 2416)] = 0
    values[(INPUT, 2417)] = 5000
    detection = await detect_through(await controller(values))
    assert detection.evidence["biomass_boiler"]["pellet_usage_total"] == 5000


@pytest.mark.parametrize(("version", "kind", "address"), VERSION_MARKERS, ids=str)
async def test_each_version_is_dated_by_its_own_marker(version: ApiVersion, kind: RegisterKind, address: int) -> None:
    """The first marker the controller has wins, so take away every higher one."""
    higher = [(marker_kind, marker_address) for marker_version, marker_kind, marker_address in VERSION_MARKERS if marker_version > version]
    detection = await detect_through(await controller(VAMPAIR, absent=higher))
    assert detection.api_version is version


async def test_a_controller_with_no_markers_at_all_is_the_oldest_firmware() -> None:
    absent = [(kind, address) for _, kind, address in VERSION_MARKERS]
    detection = await detect_through(await controller(VAMPAIR, absent=absent))
    assert detection.api_version is ApiVersion.V_20_110


async def test_counts_are_clamped_to_what_the_detected_firmware_can_address() -> None:
    """A component's registers are mapped whether or not the firmware can read them.

    Handing the count on regardless would only make the configuration refuse to
    build.
    """
    values = dict(VAMPAIR)
    values.update({(INPUT, 900): 400, (INPUT, 700): 3, (INPUT, 2100): 450})
    absent = [(marker_kind, marker_address) for version, marker_kind, marker_address in VERSION_MARKERS if version > ApiVersion.V_23_020]
    detection = await detect_through(await controller(values, absent=absent))

    assert detection.api_version is ApiVersion.V_23_020
    assert detection.counts.circulations == 0
    assert detection.counts.differential_modules == 0
    assert detection.counts.solar <= 1
    assert detection.counts.fresh_water_modules == 1


async def test_an_octoplus_is_told_by_its_buffer() -> None:
    values = {(INPUT, 2400): 650, (INPUT, 2410): 480, (INPUT, 2411): 620, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system is Systems.OCTOPLUS
    assert detection.has_biomass_boiler


async def test_a_live_return_temperature_alone_is_not_told_as_an_octoplus() -> None:
    """2410 is also an ecotop's and a pellet elegance's return flow temperature.

    Regression for home-assistant-solarfocus#237: three real Pellet Elegance
    dumps were detected as an octoplus purely because 2410 read a live
    temperature, which every biomass boiler with a live return flow does.
    """
    values = {(INPUT, 2400): 650, (INPUT, 2410): 480, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system is not Systems.OCTOPLUS


async def test_an_implausibly_hot_buffer_top_is_not_told_as_an_octoplus() -> None:
    """Not one of NO_SENSOR's exact sentinels, but just as far from a real reading.

    A real Pellet Elegance in #237 read 150.0 degC at 2411.
    """
    values = {(INPUT, 2400): 650, (INPUT, 2410): 480, (INPUT, 2411): 1500, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system is not Systems.OCTOPLUS


async def test_a_buffer_top_below_zero_is_not_told_as_an_octoplus() -> None:
    """2411 comes back as the unsigned word, so a negative reading is a large one.

    Nothing an octoplus buffer does, and it must fail the ceiling rather than
    sail past it as a number in the sixty thousands.
    """
    values = {(INPUT, 2400): 650, (INPUT, 2410): 480, (INPUT, 2411): 2**16 - 36, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system is not Systems.OCTOPLUS


@pytest.mark.parametrize("top", [0, 1300, 2700])
async def test_an_octoplus_whose_buffer_top_says_nothing_cannot_be_told_apart(top: int) -> None:
    """A known limitation, recorded rather than fixed.

    2411 is the only register that identifies an octoplus - 2410 is a return
    flow on the other pellet boilers - so an octoplus whose buffer top sensor is
    unconfigured or open has nothing left to identify it and reads as the pellet
    elegance the fall-through guesses. The values it was judged on are in the
    evidence, which is what an owner has to argue with.
    """
    values = {(INPUT, 2400): 650, (INPUT, 2410): 480, (INPUT, 2411): top, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system is Systems.PELLETELEGANCE
    assert detection.evidence["biomass_boiler"]["octoplus_buffer"] == [480, top]


async def test_a_therminator_is_told_by_its_log_wood() -> None:
    values = {(INPUT, 2400): 650, (INPUT, 2412): 1, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system is Systems.THERMINATOR


@pytest.mark.parametrize(("mode", "expected"), [(1, Systems.THERMINATOR), (2, Systems.THERMINATOR), (3, Systems.THERMINATOR)])
async def test_the_boiler_operating_mode_separates_a_therminator_from_the_rest(mode: int, expected: Systems) -> None:
    """Modes 1 to 3 all burn logs, which only a therminator does."""
    values = {(INPUT, 2400): 650, (INPUT, 2409): mode, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system is expected


@pytest.mark.parametrize("mode", [0, 4, 5])
async def test_an_idle_operating_mode_falls_through_to_the_chimney_sweep_check(mode: int) -> None:
    """Mode 0 is logs too, but it is also what an unset register reads.

    Modes 4 and 5 are unassigned. None of the three is taken as therminator
    evidence on their own.
    """
    values = {(INPUT, 2400): 650, (INPUT, 2409): mode, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values))
    assert detection.system in (Systems.ECOTOP, Systems.PELLETELEGANCE)


async def test_the_chimney_sweep_register_separates_an_ecotop_from_a_pellet_elegance() -> None:
    """The chimney sweep function is on every biomass boiler but the ecotop.

    A refused register is the only firm evidence for an ecotop there is. The
    other direction is the tie-break `CHIMNEY_SWEEP_HOLDING` describes rather
    than a reading of the model: a controller that maps the register has not
    said it is a pellet elegance, only that nothing rules one out.
    """
    values = {(INPUT, 2400): 650, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    detection = await detect_through(await controller(values, absent=[(HOLDING, 33410)]))
    assert detection.system is Systems.ECOTOP

    detection = await detect_through(await controller(values))
    assert detection.system is Systems.PELLETELEGANCE


#: The three real Pellet Elegance `detect --evidence` reports from
#: home-assistant-solarfocus#237, transcribed from the biomass_boiler evidence
#: each one printed and named for the owner who posted it - the firmware each
#: one is on is in the issue, and is not what these turn on. Every one of them
#: was detected as an octoplus before this file's fix, purely because 2410 had a
#: live return flow temperature.
_PELLET_ELEGANCE_REPORTS = {
    "RobertoCravallo": {
        (INPUT, 2400): 227,  # temperature
        (INPUT, 2401): 6,  # status
        (INPUT, 2410): 218,  # octoplus_buffer bottom / return temperature
        (INPUT, 2411): 2700,  # octoplus_buffer top
        (INPUT, 2402): 3,
        (INPUT, 2403): 54179,  # operating_minutes = 250787
        (INPUT, 2416): 1,
        (INPUT, 2417): 58872,  # pellet_usage_total = 124408
    },
    "CarlosDerSeher": {
        (INPUT, 2400): 211,
        (INPUT, 2401): 6,
        (INPUT, 2409): 0,  # operating_mode
        (INPUT, 2410): 211,
        (INPUT, 2411): 2700,
        (INPUT, 2402): 1,
        (INPUT, 2403): 17034,  # operating_minutes = 82570
        (INPUT, 2416): 0,
        (INPUT, 2417): 54186,  # pellet_usage_total = 54186
    },
    "Nugman": {
        (INPUT, 2400): 255,
        (INPUT, 2401): 0,
        (INPUT, 2409): 0,
        (INPUT, 2410): 258,
        (INPUT, 2411): 1500,  # implausibly hot for a buffer - see #237
        (INPUT, 2402): 3,
        (INPUT, 2403): 54261,  # operating_minutes = 250869
        (INPUT, 2416): 1,
        (INPUT, 2417): 18638,  # pellet_usage_total = 84174
    },
}


@pytest.mark.parametrize("report", _PELLET_ELEGANCE_REPORTS.values(), ids=_PELLET_ELEGANCE_REPORTS.keys())
async def test_a_real_pellet_elegance_report_from_237_is_no_longer_told_as_an_octoplus(report: dict[tuple[RegisterKind, int], int]) -> None:
    values = {**report, (INPUT, 501): 1, (INPUT, 1904): 1, (INPUT, 1107): 2}
    absent = [(INPUT, 2409)] if (INPUT, 2409) not in report else []
    detection = await detect_through(await controller(values, absent=absent))
    assert detection.system is Systems.PELLETELEGANCE


async def test_ragesofts_real_therminator_report_from_237_no_longer_defaults_to_ecotop() -> None:
    """The real system is a Therminator 2, idling.

    log_wood and operating_mode both read 0, which - per
    `test_an_idle_operating_mode_falls_through_to_the_chimney_sweep_check`
    above - is indistinguishable here from a pellet elegance. That is a known
    limitation, not something this fixes; what the fix changes is that it no
    longer defaults to ecotop, which had lost this installation its chimney
    sweep and pellet-store-reset entities outright.
    """
    values = {
        (INPUT, 2400): 219,  # temperature
        (INPUT, 2401): 301,  # status
        (INPUT, 2409): 0,  # operating_mode
        (INPUT, 2410): 0,  # octoplus_buffer bottom / return temperature - unused on a therminator
        (INPUT, 2411): 0,  # octoplus_buffer top
        (INPUT, 501): 1,
        (INPUT, 1904): 1,
        (INPUT, 1107): 2,
    }
    detection = await detect_through(await controller(values))
    assert detection.system is not Systems.ECOTOP


async def test_lein1013s_real_ecotop_report_is_still_told_as_a_pellet_elegance() -> None:
    """The real system is an Ecotop, from a `detect --evidence` run posted to #237.

    Nothing here separates it from a pellet elegance by the usual signals: 2410
    reads a plausible return flow rather than an octoplus's buffer bottom, 2411
    is 2700 - the open-channel sentinel `_plausible_buffer` already rejects -
    and neither an operating mode nor log_wood says therminator. That leaves
    the chimney-sweep tie-break, and on this real Ecotop holding 33410 reads
    mapped: the first real dump on either side of `CHIMNEY_SWEEP_HOLDING`, and
    it falsifies "mapped means pellet elegance" rather than confirming it.
    Refusal remains unconfirmed in both directions, and there is no other
    register in the document that separates the two, so the guess is still
    wrong here - not fixed, because there is nothing to fix it with, only
    documented the way `CHIMNEY_SWEEP_HOLDING`'s own comment now is.
    """
    values = {
        (INPUT, 2400): 301,  # temperature
        (INPUT, 2401): 0,  # status
        (INPUT, 2409): 0,  # operating_mode
        (INPUT, 2410): 305,  # octoplus_buffer bottom / return temperature
        (INPUT, 2411): 2700,  # octoplus_buffer top - open-channel sentinel
        (INPUT, 2402): 5,
        (INPUT, 2403): 12915,  # operating_minutes = 340595
        (INPUT, 2416): 3,
        (INPUT, 2417): 36629,  # pellet_usage_total = 233237
        (INPUT, 501): 1,
        (INPUT, 1904): 1,
        (INPUT, 1107): 0,
    }
    detection = await detect_through(await controller(values))
    assert detection.system is Systems.PELLETELEGANCE


async def test_a_controller_that_says_nothing_admits_it_is_guessing() -> None:
    """`system` is then a default rather than a finding, and says so."""
    detection = await detect_through(await controller())
    assert not detection.confident
    assert detection.system is Systems.VAMPAIR


async def test_detection_stays_within_a_reasonable_number_of_reads() -> None:
    """Fine once when somebody is setting up; not something to do every refresh."""
    fake = await controller(VAMPAIR)
    detection = await detect_through(fake)
    assert detection.reads < 150
    assert detection.reads == fake.round_trips


async def test_the_evidence_says_what_each_finding_was_read_off() -> None:
    """A heating system this is wrong about is one whose owner has to see why."""
    detection = await detect_through(await controller(VAMPAIR))
    assert detection.evidence["heat_pump"] == {"supply": 285, "return": 240, "state": 1}
    assert detection.evidence["layout"] == {"heating_circuit_state_offset": 7, "buffer_state_offset": 4, "heat_pump": "25.030"}
    assert detection.evidence["api_version_marker"] == "26.020"


async def test_a_detection_hands_back_the_configuration_it_would_have_been_typed_as() -> None:
    detection = await detect_through(await controller(VAMPAIR))
    config = detection.config(host="10.10.10.237")

    assert config.host == "10.10.10.237"
    assert config.system is Systems.VAMPAIR
    assert config.heating_circuits == 1
    client = SolarfocusClient(config, transport=FakeController.for_config(config))
    result = await client.update()
    assert result.ok


async def test_an_override_wins_over_what_was_detected() -> None:
    detection = await detect_through(await controller(VAMPAIR))
    config = detection.config(host="c", port=1502, heating_circuits=3)
    assert (config.port, config.heating_circuits) == (1502, 3)


async def test_a_client_can_detect_over_the_connection_it_already_has() -> None:
    """No second socket for it: the probes go through the transport already open."""
    fake = await controller(VAMPAIR)
    config = SolarfocusConfig(host="c", system=Systems.VAMPAIR, api_version=ApiVersion.V_26_020)
    client = SolarfocusClient(config, transport=fake)

    detection = await client.detect()

    assert detection.system is Systems.VAMPAIR
    assert detection.counts.heating_circuits == 1
