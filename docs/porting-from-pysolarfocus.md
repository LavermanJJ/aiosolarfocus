# Porting from pysolarfocus

`aiosolarfocus` is not a drop-in replacement. The value API is deliberately
different; this is what maps to what.

## Reading

| pysolarfocus | aiosolarfocus |
|---|---|
| `api.heating_circuits[0].supply_temperature.scaled_value` | `client.heating_circuits[0].supply_temperature` |
| `api.heatpump` | `client.heat_pump` |
| `api.biomassboiler` | `client.biomass_boiler` |
| `getattr(component, key).scaled_value` | `component.value_of(Register)` or plain attribute access |
| `getattr(component, key).value` | `component.raw(Register)` |
| `{n: p.scaled_value for n, p in vars(component).items() if isinstance(p, Part)}` | `component.snapshot()` or `client.snapshot()` |
| `api.update()` → `bool` | `await client.update()` → `UpdateResult`, raising on connection failure |
| `api.update_heating()` | `await client.update(components=[ComponentId.HEATING_CIRCUITS])` |
| `api.is_connected` | `client.connected` |
| `api.api_version.greater_or_equal("22.090")` | `config.api_version >= ApiVersion.V_22_090` |
| `heatpump.performance_overall` | `heat_pump.seasonal_performance` |
| `heatpump.performance_overall_heating` | `heat_pump.seasonal_performance_heating` |
| `heatpump.performance_overall_drinking_water` | `heat_pump.seasonal_performance_drinking_water` |

The heat pump's derived figures kept their meaning and changed their names: a
"performance overall" that is a seasonal figure reads better as one. They are
rounded to two decimals — `pysolarfocus` reported `3.7369392844083906`.

They are still first-class. In `pysolarfocus` they were `PerformanceCalculator`
objects, indistinguishable from registers to anything reading the component;
here they are declared with `@derived` beside the registers they are worked out
from, and they appear in `component.available_values()`, `component.info()`,
`component.supports()`, `component.snapshot()` and the command line's
`registers` and `dump` output.

**Build entities from `available_values()`, not `available_registers()`.** The
latter is registers only, and would drop all five coefficients of performance —
which the Home Assistant integration has a sensor for apiece.

## Writing

```python
# pysolarfocus
value = api.heating_circuits[0].mode
value.set_unscaled_value(HeatingCircuitMode.AUTOMATIC)
value.commit()
component.update()  # to see the change

# aiosolarfocus
await client.heating_circuits[0].set_mode(HeatingCircuitMode.AUTOMATIC)
```

The re-read is gone: a successful write updates the component's own cache, so
the value is readable immediately.

`SolarfocusAPI.set_heating_circuit_mode(index, mode)` and its siblings have no
equivalent, because the integration that was meant to use them never called one.
The setters live on the component.

## What the library now does that the caller used to

- **Two's complement.** `await pv.set_smart_meter(-1500)` puts `0xFA24` on the
  wire. The caller no longer needs to know the register is signed.
- **Version and system gating.** `component.available_registers()` is exactly
  what this controller has, so a consumer need not carry a
  `min_required_version` and an `unsupported_systems` list per entity.
- **Bounds, step and unit.** `component.info(Register)` reports them, instead of
  each consumer copying them out of the register document.
- **Sentinel readings.** An open sensor channel decodes to `None` rather than
  130.0 °C.
- **Rounding.** A reading is rounded to the precision its scale carries.
  `304 * 0.1` is `30.400000000000002` in binary floating point, and the old
  library passed that through for Home Assistant to record.

## Semantic changes to watch for

- **Scaling runs one direction only**: engineering value = raw × scale.
  `pysolarfocus` multiplied for input registers and divided for holding ones.
  Values agree; the declaration does not.
- **`None` is a real answer.** It means the register is absent on this firmware
  or system, has not been read, or is reporting an open channel. `raw()` and
  `info()` tell those apart.
- **Errors raise.** `SolarfocusConnectionError` for the connection;
  everything else lands in `UpdateResult.failed`, per component.
- **Enumerations decode to `IntEnum` members** for closed sets like `mode`, and
  stay `int` for open ones like `state`, where firmware keeps adding codes.

## Bugs fixed rather than carried over

- The fresh water module cascade and the circulation module were never built by
  `ComponentManager`, so `update_fresh_water_module_cascade()` raised
  `AttributeError` on every controller running 23.040 or newer.
- `TherminatorHeatingCircuit.__init__` dropped `api_version`, so those systems
  resolved every register against a default version — which on 21.140 declared
  `heating_mode` at 32608, a register that firmware does not have, and so
  shifted the three registers before it (see `protocol.md`).
- `PerformanceCalculator` returned `0.0` when the denominator was zero, so an
  idle heat pump reported a coefficient of performance of zero all night. Now
  `None`.
- A single failing read failed a whole component; and a failing instance of a
  multi-instance component hid the ones after it.
- Negative writes were the caller's problem.
- **Registers belonging to one system were read on all of them.** `2409`
  (Kesselbetriebsart, therminator), `2411` (Speichertemperatur oben, octoplus),
  `2412` (Stückholz, therminator) and buffer `1902` (X35, therminator) were read
  on systems the document does not give them to. A Pellet Elegance maps neither
  `2409` nor `2413`, so the boiler read spanned them and came back compacted:
  the return flow temperature reported 270.0 °C where the sensor said 22.1 °C,
  and the buffer's pump reported the status register. See
  home-assistant-solarfocus issue #217. A test now holds every register against
  the system the document names in its own description.

## Deliberately *not* changed

`TherminatorBuffer` never read the external X44/X36/X35 holding registers. That
looked like the same class of bug, but the Home Assistant integration
independently marks those three entities `unsupported_systems=[THERMINATOR,
ECOTOP]`. Two sources agreeing was enough to carry the behaviour over rather
than "fix" it against hardware nobody could test. See `Buffer._external`.
