"""The aiosolarfocus command line: `python -m aiosolarfocus <command>`.

The commands that need no controller - `registers` and, later, `plan` - are the
ones that get used, because they turn a table change into something reviewable
before anyone goes near a heating system.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Iterator, Sequence
from datetime import datetime
from enum import IntEnum
from typing import Any

from . import __version__
from .client import SolarfocusClient
from .components import COMPONENTS, ComponentId, ComponentSpec
from .config import SolarfocusConfig
from .const import DEFAULT_DEVICE_ID, DEFAULT_PORT, ApiVersion, Systems
from .detect import detect
from .exceptions import SolarfocusError
from .layout import Layout
from .planner import plan
from .registers import DerivedInfo, RegisterInfo

#: `component.number.register` has three parts; `component.register` has two.
_TARGET_WITHOUT_NUMBER = 2


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", required=True, help="the controller's address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Modbus TCP port (default: %(default)s)")
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID, help="Modbus device id (default: %(default)s)")


def _add_controller_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--system", type=Systems, choices=list(Systems), default=Systems.VAMPAIR, help="which heating system (default: %(default)s)")
    parser.add_argument("--api-version", type=ApiVersion.parse, default=ApiVersion.V_26_020, help="controller firmware, as it prints it (default: 26.020)")


def _add_count_arguments(parser: argparse.ArgumentParser) -> None:
    """One flag per component, named as the configuration field is."""
    for spec in COMPONENTS:
        flag = "--" + spec.id.value.replace("_", "-")
        if spec.max_count == 1:
            parser.add_argument(flag, action="store_true", help=f"the installation has a {spec.label}")
        else:
            parser.add_argument(
                flag, type=int, default=1 if spec.id.value in {"heating_circuits", "buffers", "boilers"} else 0, metavar="N", help=f"how many {spec.label}s (default: %(default)s)"
            )


def _config_from(args: argparse.Namespace) -> SolarfocusConfig:
    counts: dict[str, Any] = {spec.id.value: getattr(args, spec.id.value) for spec in COMPONENTS}
    # A heat pump and a biomass boiler are decided by the system unless the
    # caller says otherwise, and `--heat-pump` not being given is not the caller
    # saying otherwise.
    for name in ("heat_pump", "biomass_boiler"):
        if not counts[name]:
            counts[name] = None
    return SolarfocusConfig(
        host=getattr(args, "host", "") or "offline",
        port=getattr(args, "port", DEFAULT_PORT),
        device_id=getattr(args, "device_id", DEFAULT_DEVICE_ID),
        system=args.system,
        api_version=args.api_version,
        **counts,
    )


def build_parser() -> argparse.ArgumentParser:
    """The whole command line."""
    parser = argparse.ArgumentParser(prog="python -m aiosolarfocus", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    registers = subcommands.add_parser("registers", help="print the register map for a system and firmware, without connecting")
    _add_controller_arguments(registers)
    registers.add_argument("--component", help="only this component, by id")
    registers.add_argument("--markdown", action="store_true", help="emit the generated docs/registers.md")
    registers.set_defaults(run=_run_registers)

    plan_ = subcommands.add_parser("plan", help="print the reads one refresh would make, without connecting")
    _add_controller_arguments(plan_)
    _add_count_arguments(plan_)
    plan_.add_argument("--verbose", "-v", action="store_true", help="name the components each read serves")
    plan_.set_defaults(run=_run_plan)

    detect_ = subcommands.add_parser("detect", help="ask the controller what it is and what is wired to it")
    _add_connection_arguments(detect_)
    detect_.add_argument("--evidence", action="store_true", help="show what each finding was read off")
    detect_.add_argument("--json", action="store_true", help="print the configuration ready to paste")
    detect_.set_defaults(run=_run_detect)

    dump = subcommands.add_parser("dump", help="connect, read everything once, and print it")
    _add_connection_arguments(dump)
    _add_controller_arguments(dump)
    _add_count_arguments(dump)
    dump.add_argument("--json", action="store_true", help="machine-readable, and the shape a test fixture wants")
    dump.set_defaults(run=_run_dump)

    watch = subcommands.add_parser("watch", help="poll, and print only what changed")
    _add_connection_arguments(watch)
    _add_controller_arguments(watch)
    _add_count_arguments(watch)
    watch.add_argument("--interval", type=float, default=10.0, help="seconds between polls (default: %(default)s)")
    watch.add_argument("--component", help="only this component, by id")
    watch.set_defaults(run=_run_watch)

    write = subcommands.add_parser("set", help="write one register, after showing what it holds now")
    _add_connection_arguments(write)
    _add_controller_arguments(write)
    _add_count_arguments(write)
    write.add_argument("target", help="which register, as component.number.register - for instance hc.1.mode or heat_pump.evu_lock")
    write.add_argument("value", help="the value to write, in the unit the register reports")
    write.add_argument("--yes", "-y", action="store_true", help="do not ask before writing")
    write.set_defaults(run=_run_set)

    parser.add_argument("--verbose", "-v", action="count", default=0, help="-v for detail, -vv for the Modbus traffic")
    parser.add_argument("--version", action="version", version=f"aiosolarfocus {__version__}")
    return parser


def _layouts(system: Systems, api_version: ApiVersion, only: str | None) -> Iterator[tuple[ComponentSpec, Layout]]:
    for spec in COMPONENTS:
        if only is not None and spec.id.value != only:
            continue
        if not spec.available(api_version, system):
            continue
        input_base, holding_base = spec.bases(0, api_version)
        yield spec, Layout.resolve(spec.component, api_version, system, input_base, holding_base)


def _row(info: RegisterInfo) -> tuple[str, ...]:
    width = "32-bit" if info.width > 1 else ""
    sign = "signed" if info.signed else "unsigned"
    scale = "" if info.scale == 1.0 else f"x{info.scale:g}"
    bounds = "" if info.bounds is None else f"{info.bounds[0]:g}..{info.bounds[1]:g}"
    return (info.name, info.kind.value, str(info.address), width, sign, scale, info.unit or "", info.access.value, info.since.label, bounds, info.doc)


def _derived_row(info: DerivedInfo) -> tuple[str, ...]:
    """A computed value has no address, no width and no sign; say so plainly."""
    return (info.name, "derived", "", "", "", "", info.unit or "", "read", "", "", info.doc)


_HEADINGS = ("register", "table", "address", "width", "sign", "scale", "unit", "access", "since", "range", "document name")


def _run_registers(args: argparse.Namespace) -> int:
    system: Systems = args.system
    api_version: ApiVersion = args.api_version
    sections = []
    for spec, layout in _layouts(system, api_version, args.component):
        rows = [_row(resolved.info()) for resolved in layout.registers]
        rows.extend(_derived_row(computed.info()) for computed in spec.component.derived() if all(name in layout.by_name for name in computed.depends_on))
        sections.append((spec, rows))

    if args.markdown:
        print(
            f"# Register map\n\nGenerated by `python -m aiosolarfocus registers --markdown`. Do not edit by hand.\n\nSystem **{system.value}**, firmware **{api_version.label}**.\n"
        )
        for spec, rows in sections:
            print(f"## {spec.label.title()} (`{spec.id.value}`)\n")
            print("| " + " | ".join(_HEADINGS) + " |")
            print("|" + "|".join(["---"] * len(_HEADINGS)) + "|")
            for row in rows:
                print("| " + " | ".join(row) + " |")
            print()
        return 0

    print(f"{system.value}, firmware {api_version.label}")
    for spec, rows in sections:
        count = spec.limit(api_version)
        instances = f" (up to {count})" if count > 1 else ""
        print(f"\n{spec.label}{instances} - {len(rows)} registers")
        _print_table(_HEADINGS, rows)
    return 0


def _run_plan(args: argparse.Namespace) -> int:
    config = _config_from(args)
    read_plan = plan(config.layouts())

    print(f"{config.system.value}, firmware {config.api_version.label}")
    for key in config.component_keys():
        print(f"  {key}")
    print()
    for read in read_plan.slices:
        if args.verbose:
            served = ", ".join(sorted(str(key) for key in read.components))
            print(f"{read!s:26}  {served}")
        else:
            print(read)
    print(f"\n{read_plan.round_trips} round trips, {read_plan.registers_read} registers, {len(config.component_keys())} components")
    return 0


def _run_detect(args: argparse.Namespace) -> int:
    return asyncio.run(_detect(args))


async def _detect(args: argparse.Namespace) -> int:
    detection = await detect(args.host, args.port, args.device_id)
    config = detection.config(host=args.host, port=args.port, device_id=args.device_id)

    if args.json:
        print(f"aiosolarfocus {__version__}", file=sys.stderr)
        print(json.dumps({field: _jsonable(getattr(config, field)) for field in _config_fields()}, indent=2))
    else:
        print(f"{args.host}:{args.port}  aiosolarfocus {__version__}  read in {detection.reads} probes\n")
        rows = [
            ("system", detection.system.value, "" if detection.confident else "  a default: no heat generator reported anything"),
            # fklein1980's controller prints 26.030 on its own screen and reads
            # as 26.020 here, which looks like a misdetection and is not: a
            # firmware newer than the newest register set we know reads as that
            # set, which is every register of it the controller has.
            ("firmware", detection.api_version.label, "  the newest register set this library knows" if detection.api_version is max(ApiVersion) else ""),
        ]
        for spec in COMPONENTS:
            count = config.count_of(spec.id)
            rows.append((spec.id.value, str(count), "  never counted; raise it yourself if you have one" if spec.id.value == "differential_modules" else ""))
        _print_table(("", "detected", ""), rows)

    if args.evidence:
        print("\nevidence")
        for name, value in detection.evidence.items():
            print(f"  {name}: {value}")
    if not detection.confident:
        print("\nNo heat generator reported anything alive, so the system is a default rather than a finding.", file=sys.stderr)
    return 0


def _config_fields() -> list[str]:
    return ["host", "port", "device_id", "system", "api_version", *(spec.id.value for spec in COMPONENTS)]


def _jsonable(value: Any) -> Any:
    if isinstance(value, ApiVersion):
        return value.label
    if isinstance(value, Systems):
        return value.value
    return value


def _run_dump(args: argparse.Namespace) -> int:
    return asyncio.run(_dump(args))


async def _dump(args: argparse.Namespace) -> int:
    config = _config_from(args)
    async with SolarfocusClient(config) as client:
        result = await client.update()

    if args.json:
        print(
            json.dumps(
                {"meta": {"system": config.system.value, "api_version": config.api_version.label, "aiosolarfocus": __version__}, "components": client.snapshot()},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"{config.address}  {config.system.value}  firmware {config.api_version.label}  aiosolarfocus {__version__}")
        for key, component in client.components.items():
            print(f"\n{key}{'' if component.available else '  (not read: ' + str(component.last_error) + ')'}")
            rows = []
            for name, reading in component.snapshot().items():
                note = "  no sensor" if reading["value"] is None and reading["raw"] is not None else ""
                address = "" if reading["address"] is None else str(reading["address"])
                raw = "-" if reading["raw"] is None else str(reading["raw"])
                rows.append((name, address, raw, _render(reading["value"]), reading["unit"] or "", note))
            _print_table(("register", "address", "raw", "value", "unit", ""), rows)
    print(f"\n{result}", file=sys.stderr)
    return 0 if result.ok else 1


def _run_watch(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_watch(args))
    except KeyboardInterrupt:
        return 0


async def _watch(args: argparse.Namespace) -> int:
    """Poll, and print what moved.

    The tool for chasing a register whose meaning is not obvious, and for
    watching a write land - a full dump every ten seconds tells you nothing,
    whereas three lines that changed tells you what you came for.
    """
    config = _config_from(args)
    only = [ComponentId(args.component)] if args.component else None
    previous: dict[str, object] = {}
    first = True

    async with SolarfocusClient(config) as client:
        header = f"{config.address}  {config.system.value}  firmware {config.api_version.label}  aiosolarfocus {__version__}"
        print(f"{header}  every {args.interval:g}s; ctrl-c to stop", file=sys.stderr)
        while True:
            result = await client.update(components=only)
            stamp = datetime.now().strftime("%H:%M:%S")
            changes = 0
            for key, component in client.components.items():
                if only is not None and key.id not in only:
                    continue
                for name, reading in component.snapshot().items():
                    label = f"{key}.{name}"
                    value = reading["value"]
                    if not first and previous.get(label) != value:
                        # Flushed: stdout to a pipe is block-buffered, so piping
                        # this into `tee` would otherwise show nothing for
                        # minutes at a time - the one thing a watch must not do.
                        print(f"{stamp}  {label:52} {_render(previous[label]):>18} -> {_render(value)}", flush=True)
                        changes += 1
                    previous[label] = value
            if not result.ok:
                print(f"{stamp}  not read: " + ", ".join(f"{key} ({error.message})" for key, error in result.failed.items()), file=sys.stderr)
            if first:
                # Nothing can have changed on the first poll; say what is being
                # watched, then stay quiet until something moves.
                print(f"{stamp}  watching {len(previous)} registers", file=sys.stderr)
                first = False
            await asyncio.sleep(args.interval)


def _render(value: object) -> str:
    """Show a value the way a person reads it.

    An `IntEnum` prints as its number since Python 3.11, which is exactly the
    thing a mode column should not be showing.
    """
    if value is None:
        return "-"
    if isinstance(value, IntEnum):
        return f"{value.name.lower()} ({value.value})"
    return str(value)


def _run_set(args: argparse.Namespace) -> int:
    return asyncio.run(_set(args))


async def _set(args: argparse.Namespace) -> int:
    config = _config_from(args)
    client = SolarfocusClient(config)
    try:
        key, component, register_name = _resolve_target(client, args.target)
    except KeyError as error:
        print(error.args[0], file=sys.stderr)
        return 2

    register = getattr(type(component), register_name)
    info = component.info(register)
    if not info.writable:
        print(f"{args.target} is read only", file=sys.stderr)
        return 2
    value = _parse_value(info, args.value)

    async with client:
        # Read the component first, so the caller sees what they are changing.
        await client.update(components=[key.id])
        print(f"{key}.{register_name}  ({info.kind.value} {info.address})")
        print(f"  now:      {_with_unit(component.value_of(register), info)}")
        print(f"  writing:  {_with_unit(value, info)}")
        if not args.yes and not await _confirm():
            print("  nothing written")
            return 1
        await component.write(register, value)
        print(f"  after:    {_with_unit(component.value_of(register), info)}")
    return 0


async def _confirm() -> bool:
    """Ask, without blocking the event loop on a terminal that may never answer."""
    answer = await asyncio.to_thread(input, "  go ahead? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def _with_unit(value: object, info: RegisterInfo) -> str:
    if value is None:
        return "-"
    return f"{_render(value)}{' ' + info.unit if info.unit else ''}"


def _parse_value(info: RegisterInfo, text: str) -> Any:
    """Read a value the way the register reports it, not the way the wire carries it."""
    if info.enum is not None:
        return _parse_enum(info.enum, text)
    lowered = text.lower()
    if lowered in {"true", "false", "on", "off", "yes", "no"}:
        return lowered in {"true", "on", "yes"}
    if info.scale == 1.0 and info.step is None:
        return int(text)
    return float(text)


def _resolve_target(client: SolarfocusClient, target: str) -> tuple[Any, Any, str]:
    """Turn `heating_circuits.1.mode` or `heat_pump.evu_lock` into what to write."""
    parts = target.split(".")
    example = "heating_circuits.1.mode or heat_pump.evu_lock"
    if len(parts) < _TARGET_WITHOUT_NUMBER:
        raise KeyError(f"{target!r} should look like {example}")
    register_name = parts[-1]
    name, number = (parts[0], int(parts[1])) if len(parts) > _TARGET_WITHOUT_NUMBER else (parts[0], 1)
    for key, component in client.components.items():
        if key.id.value == name and key.number == number:
            if not component.supports(register_name):
                raise KeyError(f"a {name} has no {register_name!r} on this controller")
            return key, component, register_name
    known = ", ".join(str(key) for key in client.components)
    raise KeyError(f"this configuration has no {name} {number}; it has {known}")


def _parse_enum(enum: Any, text: str) -> Any:
    try:
        return enum[text.upper()]
    except KeyError:
        pass
    try:
        return enum(int(text))
    except (ValueError, KeyError):
        names = ", ".join(member.name.lower() for member in enum)
        raise SystemExit(f"{text!r} is not one of: {names}") from None


def _print_table(headings: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    widths = [max(len(headings[column]), *(len(row[column]) for row in rows)) if rows else len(headings[column]) for column in range(len(headings))]
    print("  " + "  ".join(heading.ljust(width) for heading, width in zip(headings, widths, strict=True)).rstrip())
    for row in rows:
        print("  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip())


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level={0: logging.WARNING, 1: logging.INFO}.get(args.verbose, logging.DEBUG))
    try:
        exit_code: int = args.run(args)
    except SolarfocusError as error:
        print(error, file=sys.stderr)
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
