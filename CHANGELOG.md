# Changelog

## Unreleased

- **Heating circuit 7's `32900` is settled**, and is no longer a known
  limitation. The vendor document does carry the row; it prints the address as
  `32750`, which is circuit 4's, four rows up the same page. The library already
  read `32900` and goes on doing so. Every known error in the vendor document is
  now written up in
  [`docs/register-document.md`](docs/register-document.md).

- **Detection's ecotop/pellet-elegance tie-break rests on evidence now, and
  the evidence is against it.** A real Ecotop `detect --evidence` report
  (home-assistant-solarfocus#237, lein1013) reads the chimney-sweep holding
  register (`33410`) mapped, the direction detection has read as a pellet
  elegance since 0.2.0. That was always the weaker of the tie-break's two
  directions, guessed from firmware behaviour rather than measured; this is
  the first dump to measure it, and it says the guess is wrong for a real
  installation. There is no other register in the document that separates the
  two, so this is recorded as a known limitation rather than fixed: detection
  still guesses pellet elegance when the register is mapped, because guessing
  ecotop instead costs a real pellet elegance two working entities, where this
  costs a real ecotop three that read back nothing.

## 0.2.0

Real register dumps from three Pellet Elegance controllers and a Therminator
(home-assistant-solarfocus#237) turned up sentinel values and a detection tie
the specification alone hadn't covered.

### Bugs fixed rather than carried over

- **A biomass boiler's cleaning or ash container reading of -1% or -999%
  was published as a real percentage**, and a heating circuit's humidity the
  same at -0.1%. Two Pellet Elegance controllers marked an absent cleaning
  sensor with -1 and read the ash container fine; a Therminator marked an
  absent ash container with -999 and read the cleaning fine — so `cleaning`,
  `ash_container` and `humidity` now decode both markers to `None`.
- **`outdoor_temperature_external` published -999.9 °C as a reading**, the
  value all three controllers send before anyone has written one — twenty
  degrees past the -50 °C floor the same register refuses to be written
  past. It decodes to `None`, alongside the existing open-channel sentinel.
- **A flag register read as `True` when the controller had nothing to put
  there.** 0xFFFF decoded through `bool()` reports every flag as set; two
  controllers read it from holding 32003 and had it decode into a
  circulation request nobody had made. Every flag now decodes 0xFFFF to
  `None` instead.
- **Detection called an octoplus off register 2410 alone**, the same
  register a Pellet Elegance and an EcoTop read their return flow
  temperature from — all three Pellet Elegance dumps in #237 had a live
  reading there and were detected as an octoplus for exactly that reason.
  Only 2411 counts now, and only within a range a buffer sensor could
  plausibly report.
- **EcoTop and Pellet Elegance were indistinguishable, and detection always
  guessed EcoTop.** It now probes the chimney-sweep holding register
  (33410) as a tie-break: a refusal reads as EcoTop, a mapped register as
  Pellet Elegance — the guess that costs fewer entities when it is wrong.

## 0.1.0

First release. `aiosolarfocus` is an async rewrite of
[pysolarfocus](https://github.com/lavermanjj/pysolarfocus), with the register
map as validated data rather than branches in component constructors. It is not
a drop-in replacement — see
[the porting guide](docs/porting-from-pysolarfocus.md).

12 components, 121 registers and 5 computed values, across firmware 20.110 to
26.020 and all five systems. Python 3.13 and 3.14, `pymodbus>=3.10,<4`.

### Async, and only where it is

Every call that talks to the controller is awaited; nothing else is. Reading a
value is a decoded reading already in hand, so `client.heat_pump.supply_temperature`
is a plain attribute, and `SolarfocusSync` needs to wrap only five coroutines
for callers who want no event loop at all.

One socket per controller, one lock, and no polling from a thread pool. For
Home Assistant this removes a thread hop per component per poll, and removes
blocking Modbus writes from the event loop entirely — a climate service call was
four writes and four whole-component re-reads, all blocking.

### Failures say what failed

`update()` raises `SolarfocusConnectionError` only when the connection itself is
the problem, because that is the one failure that says nothing about any
component. Everything else — a range this firmware refuses, an exception
response — is attributed to the components whose registers were in that read and
returned in `UpdateResult.failed`. One refused range greys out one component
instead of the arbitrary tail of the poll that came after it, and a caller no
longer has to consult a connection flag afterwards to guess what a `False` meant.

### The register map is data

A `Register` is a frozen dataclass and a descriptor at once: `HeatPump.supply_temperature`
is the specification, `heat_pump.supply_temperature` is the reading, and both
narrow under mypy strict with no cast and no plugin. Firmware differences are
fields rather than `if` statements — `since=`, `systems=`, and a per-version
offset mapping for the blocks 25.030 renumbered.

`registers.csv` ships as package data, and every table is checked against it:
address, width, signedness, scale, and the document's own name for each
register. Where table and document disagree, the disagreement is recorded with a
reason, so it is a decision rather than an accident.

### Reads are planned across the whole system

Slices are computed once for every component together rather than per component.
The throughput saving is small and worth stating plainly: one or two requests out
of eighteen on a typical installation, where the photovoltaic holding block abuts
the heat pump's or the biomass boiler's and the two fold into one read.

Correctness is the actual point. No read spans an address the firmware does not
map, and none cuts a 32-bit register in half. The controller answers a read
across an unmapped address by packing the next mapped register into the hole
rather than padding it, so every value after the gap silently shifts into the
wrong name, with nothing in the protocol to say so — the misreading below is
exactly that, and there is no way to detect it after the fact. See
[docs/protocol.md](docs/protocol.md).

### Bugs fixed rather than carried over

- **A pellet boiler misread its return flow temperature as 270.0 °C** where the
  sensor said 22.1 °C, and its buffer reported the status register as the pump.
  Registers the document gives to one system — `2409`, `2411`, `2412`, buffer
  `1902` — were read on every system, and a Pellet Elegance maps neither `2409`
  nor `2413`, so the read spanned them and came back compacted.
  (home-assistant-solarfocus#217)
- **The fresh water module cascade and the circulation module were never built**,
  so reading either raised `AttributeError` on any controller from 23.040 on.
- **An idle heat pump reported a coefficient of performance of 0.0**, which Home
  Assistant records as a measurement. It is `None`.
- **A Therminator or EcoTop heating circuit resolved every register at a default
  firmware version**, because the subclass dropped its `api_version` argument.
  On 21.140 that declared a register the firmware does not have and shifted the
  three before it.
- **Negative values were the caller's problem.** `await pv.set_smart_meter(-1500)`
  now encodes the two's complement; the caller need not know the register is signed.
- **An open sensor channel read as 130.0 °C or 270.0 °C.** It decodes to `None`.
  `-1` deliberately does not: −0.1 °C is a real outdoor reading.
- **A single failing slice failed a whole component**, and a failing instance of
  a repeated component hid the ones after it.
- **Readings carried floating-point noise** — `47.400000000000006` — into
  whatever recorded them.

### Also here

- **Optional detection.** `await detect(host)` works out the system, firmware and
  component counts and hands back the configuration you would otherwise have
  typed, with the evidence for each finding. Explicit configuration remains the
  primary path.
- **A command line.** `detect`, `dump`, `watch` and `set` against a controller;
  `registers` and `plan` need no hardware, so a table change is reviewable as a
  read-plan diff.
- **`aiosolarfocus.testing` ships in the wheel**, so downstream tests can drive a
  real client against a fake controller that reproduces the real one's
  behaviour — including the compaction.

### Known limitations

- **Only the vampair has been tested against hardware.** Therminator, EcoTop,
  Pellet Elegance and Octoplus are reasoned from the register specification. A
  [register dump](CONTRIBUTING.md#producing-a-register-dump) from any of them is
  the most useful thing you can contribute.
- **Detection never claims a differential module.** On the one system available
  the rule that counts solar circuits would have claimed one that did not exist.
  Set the count yourself if you have one.
- **Solar `2104`** is scaled by a tenth, as the predecessor scaled it; the
  document gives it no scale factor. Unresolved, and recorded as such.
- **Heating circuit 7's `32900`** is missing from the register document, while
  the seven other circuits have theirs. Assumed present. If it really is not
  mapped, that circuit loses its holding block, since a read starting at an
  unmapped address is refused outright.
- **Buffer external temperatures are not offered on Therminator or EcoTop**,
  matching both the predecessor and the Home Assistant integration. Neither
  source explains why, and no hardware was available to check.
