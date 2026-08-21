# aiosolarfocus

Async Python client for [Solarfocus](https://www.solarfocus.com) **eco<sup>manager-touch</sup>**
heating systems, over Modbus TCP.

> [!WARNING]
> This is an unofficial library, developed without support from Solarfocus.
> Writing to a heating system can damage the system or the building. Check with
> Solarfocus or your installer before changing anything you do not understand.

Status: **in development.** Nothing below works yet — see `docs/` and the
milestones in the implementation plan.

## Install

```sh
pip install aiosolarfocus
```

## Use

```python
import asyncio

from aiosolarfocus import ApiVersion, SolarfocusClient, SolarfocusConfig, Systems


async def main() -> None:
    config = SolarfocusConfig(
        host="10.0.0.5",
        system=Systems.VAMPAIR,
        api_version=ApiVersion.V_26_020,
    )
    async with SolarfocusClient(config) as client:
        await client.update()
        print(client.heat_pump.supply_temperature)  # float | None


asyncio.run(main())
```

## Command line

```sh
python -m aiosolarfocus detect    --host 10.0.0.5
python -m aiosolarfocus dump      --host 10.0.0.5
python -m aiosolarfocus watch     --host 10.0.0.5 --interval 10
python -m aiosolarfocus registers --system Vampair --api-version 26.020   # offline
python -m aiosolarfocus plan      --system Vampair --api-version 26.020   # offline
```

## About

Successor to [pysolarfocus](https://github.com/lavermanjj/pysolarfocus), rewritten
async-native with the register map as validated data. Portions of this work derive
from that project; see `NOTICE`.

Register reference: the Solarfocus Modbus TCP register specification, transcribed
into `src/aiosolarfocus/data/registers.csv` and checked against the code by the
test suite.
