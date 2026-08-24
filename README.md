<p align="center">
  <a href="https://github.com/lavermanjj/home-assistant-solarfocus">
    <img src="https://brands.home-assistant.io/solarfocus/logo.png" alt="Logo" height="80">
  </a>
</p>

<h3 align="center">aiosolarfocus</h3>

<p align="center">
  Async Python client for <a href="https://www.solarfocus.com/">Solarfocus</a> eco<sup>manager-touch</sup> heating systems, over Modbus TCP. The successor of <a href="https://github.com/LavermanJJ/pysolarfocus">pysolarfocus</a>.
</p>




> [!WARNING]
> Unofficial, and developed without support from Solarfocus. Writing to a
> heating system can damage the system or the building. Check with Solarfocus or
> your installer before changing anything you do not understand.

## Install

```sh
pip install aiosolarfocus
```

## Read

```python
import asyncio

from aiosolarfocus import ApiVersion, SolarfocusClient, SolarfocusConfig, Systems


async def main() -> None:
    config = SolarfocusConfig(
        host="10.0.0.5",
        system=Systems.VAMPAIR,
        api_version=ApiVersion.V_26_020,
        heating_circuits=1,
        buffers=1,
        boilers=1,
    )
    async with SolarfocusClient(config) as client:
        result = await client.update()

        print(client.heat_pump.supply_temperature)  # float | None
        print(client.heating_circuits[0].mode)  # HeatingCircuitMode | None
        print(client.heat_pump.cop_heating)  # float | None

        if not result.ok:
            print("not read:", *result.failed)


asyncio.run(main())
```

Reading a value is not asynchronous — it is the last reading, already decoded.
Only the calls that talk to the controller are awaited.

`None` means one of three things, and callers almost always want the same
behaviour for all of them: this firmware or system does not have the register,
it has not been read yet or its last read failed, or the channel is reporting
one of the values an open sensor gives instead of a measurement.
`component.raw()` and `component.info()` tell them apart.

Values the library works out from registers — the heat pump's coefficients of
performance, its seasonal figures — are declared beside the registers they come
from, so `component.available_values()` offers them alongside everything else
and `component.info()` describes them. An idle heat pump reports `None` for a
coefficient of performance rather than zero.

## Write

```python
from aiosolarfocus import HeatingCircuitMode

await client.heating_circuits[0].set_mode(HeatingCircuitMode.AUTOMATIC)
await client.heating_circuits[0].set_target_supply_temperature(45.0)
await client.photovoltaic.set_smart_meter(-1500)  # the library does the two's complement

# Registers the controller wants written together go out together.
await client.heating_circuits[0].set_operating_state(
    mode=HeatingCircuitMode.AUTOMATIC,
    target_supply_temperature=18.0,
)
```

A successful write updates the component's own cache, so the new value is
readable straight away without re-reading anything.

## Errors

`update()` raises only when the connection itself is the problem, because that
is the one failure that says nothing about any particular component. Everything
else — a range this firmware refuses, a controller answering an exception — is
attributed to the components whose registers were in that read:

```python
try:
    result = await client.update()
except SolarfocusConnectionError:
    ...  # the whole controller is unreachable
for key, error in result.failed.items():
    ...  # just these components went quiet
```

## Without asyncio

```python
from aiosolarfocus import SolarfocusConfig, SolarfocusSync

with SolarfocusSync(SolarfocusConfig(host="10.0.0.5")) as sync:
    sync.update()
    print(sync.client.heat_pump.supply_temperature)
```

## Command line

```sh
python -m aiosolarfocus detect    --host 10.0.0.5 --evidence
python -m aiosolarfocus dump      --host 10.0.0.5
python -m aiosolarfocus watch     --host 10.0.0.5 --interval 10   # prints only what changed
python -m aiosolarfocus set       --host 10.0.0.5 heating_circuits.1.mode automatic

python -m aiosolarfocus registers --system Vampair --api-version 26.020   # no controller needed
python -m aiosolarfocus plan      --system Vampair --api-version 26.020   # no controller needed
```

## Supported

Systems: vampair, therminator, ecotop, Pellet Elegance, octoplus.
Firmware: 20.110 through 26.020. Give the version the controller prints on its
own screen; a newer one clamps to the newest this library knows.

Only the vampair has been tested against real hardware. The other four are
reasoned from the register specification — a
[register dump](CONTRIBUTING.md#producing-a-register-dump) from one of them is
the most useful thing you can contribute.

## Documentation

- [`docs/registers.md`](docs/registers.md) — the register map, generated from the tables.
- [`docs/protocol.md`](docs/protocol.md) — how the controller actually behaves, including the ways it misbehaves.
- [`docs/porting-from-pysolarfocus.md`](docs/porting-from-pysolarfocus.md) — coming from the synchronous library.

## Testing against it

`aiosolarfocus.testing` ships in the wheel, so a downstream test suite can drive
a real client against a fake controller that reproduces the real one's
behaviour — including refusing unmapped addresses and compacting reads that span
a gap.

```python
from aiosolarfocus.testing import FakeController

config = SolarfocusConfig(host="test")
client = SolarfocusClient(config, transport=FakeController.for_config(config))
```

## About

Successor to [pysolarfocus](https://github.com/lavermanjj/pysolarfocus),
rewritten async-native with the register map as validated data. Portions of this
work derive from that project; see [`NOTICE`](NOTICE).

Not affiliated with, endorsed by, or supported by Solarfocus GmbH.
