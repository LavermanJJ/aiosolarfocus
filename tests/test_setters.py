"""Every writable register can actually be written, through a method with a real name.

Written by calling the setters rather than by reading their names, so it catches
a setter that writes the wrong register as well as one that has gone missing.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from enum import IntEnum
from typing import Any, get_type_hints

import pytest

from aiosolarfocus.components import COMPONENTS, ComponentSpec
from aiosolarfocus.components.base import Component
from aiosolarfocus.const import ApiVersion, Systems
from aiosolarfocus.exceptions import UnsupportedRegisterError
from aiosolarfocus.layout import Layout
from aiosolarfocus.registers import Register

from .conftest import RecordingWriter

pytestmark = pytest.mark.asyncio

#: Every system that has each component, so a system-specific register is
#: exercised on a system that has it.
CASES = [(spec, system) for spec in COMPONENTS for system in Systems if spec.available(ApiVersion.V_26_020, system)]
IDS = [f"{spec.id.value}-{system.value}" for spec, system in CASES]


def _argument_for(annotation: Any, register: Register[Any] | None) -> Any:
    if isinstance(annotation, type) and issubclass(annotation, IntEnum):
        return next(iter(annotation))
    if annotation is bool:
        return True
    if annotation is int:
        return 0
    if register is not None and register.bounds is not None:
        low, high = register.bounds
        return round((low + high) / 2, 1)
    return 20.0


def _setters(component: type[Component]) -> list[tuple[str, Callable[..., Any]]]:
    return [(name, member) for name, member in inspect.getmembers(component, inspect.iscoroutinefunction) if not name.startswith("_") and name not in {"write", "write_many"}]


@pytest.mark.parametrize(("spec", "system"), CASES, ids=IDS)
async def test_every_writable_register_is_reachable_through_a_setter(spec: ComponentSpec, system: Systems) -> None:
    api_version = ApiVersion.V_26_020
    input_base, holding_base = spec.bases(0, api_version)
    layout = Layout.resolve(spec.component, api_version, system, input_base, holding_base)
    writable = {resolved.name for resolved in layout.registers if resolved.register.writable}
    if not writable:
        pytest.skip(f"a {spec.label} has nothing to write")

    written: set[int] = set()
    hints = {name: get_type_hints(member) for name, member in _setters(spec.component)}
    for name, member in _setters(spec.component):
        writer = RecordingWriter()
        component = spec.component(layout, index=1, writer=writer)
        arguments = {}
        for parameter in inspect.signature(member).parameters.values():
            if parameter.name == "self":
                continue
            register = getattr(spec.component, parameter.name, None)
            arguments[parameter.name] = _argument_for(hints[name].get(parameter.name), register if isinstance(register, Register) else None)
        try:
            await member(component, **arguments)
        except UnsupportedRegisterError:
            # The right answer for a register this system does not have - an
            # ecotop has no chimney sweep function - and the setter says so
            # rather than writing somewhere harmless.
            continue
        written.update(address for _, address, _ in writer.writes)

    reachable = {resolved.name for resolved in layout.registers if resolved.address in written}
    assert writable <= reachable, f"a {spec.label} cannot write {sorted(writable - reachable)}"


@pytest.mark.parametrize(("spec", "system"), CASES, ids=IDS)
async def test_a_setter_for_a_register_this_system_lacks_refuses_it(spec: ComponentSpec, system: Systems) -> None:
    """An ecotop has no chimney sweep function, and asking for one should say so."""
    api_version = ApiVersion.V_26_020
    input_base, holding_base = spec.bases(0, api_version)
    full = Layout.resolve(spec.component, api_version, Systems.THERMINATOR, input_base, holding_base)
    here = Layout.resolve(spec.component, api_version, system, input_base, holding_base)
    missing = {resolved.name for resolved in full.registers if resolved.register.writable} - set(here.by_name)
    if not missing:
        pytest.skip(f"a {system.value} {spec.label} has every writable register")

    component = spec.component(here, index=1, writer=RecordingWriter())
    for name in missing:
        with pytest.raises(UnsupportedRegisterError):
            await component.write(getattr(spec.component, name), 0)


@pytest.mark.parametrize(("spec", "system"), CASES, ids=IDS)
async def test_no_setter_writes_a_register_that_is_not_this_components(spec: ComponentSpec, system: Systems) -> None:
    api_version = ApiVersion.V_26_020
    input_base, holding_base = spec.bases(0, api_version)
    layout = Layout.resolve(spec.component, api_version, system, input_base, holding_base)
    mine = {resolved.address for resolved in layout.registers}

    hints = {name: get_type_hints(member) for name, member in _setters(spec.component)}
    for name, member in _setters(spec.component):
        writer = RecordingWriter()
        component = spec.component(layout, index=1, writer=writer)
        arguments = {}
        for parameter in inspect.signature(member).parameters.values():
            if parameter.name == "self":
                continue
            register = getattr(spec.component, parameter.name, None)
            arguments[parameter.name] = _argument_for(hints[name].get(parameter.name), register if isinstance(register, Register) else None)
        try:
            await member(component, **arguments)
        except UnsupportedRegisterError:
            continue
        for _, address, _ in writer.writes:
            assert address in mine, f"{spec.component.__name__}.{name} writes {address}, which is not one of its registers"


@pytest.mark.parametrize(("spec", "system"), CASES, ids=IDS)
async def test_every_derived_value_a_component_names_actually_exists(spec: ComponentSpec, system: Systems) -> None:
    """`snapshot` reads these by name, so a rename must not leave one dangling."""
    for name in spec.component.derived:
        assert isinstance(getattr(spec.component, name, None), property), f"{spec.component.__name__}.{name} is named in `derived` but is not a property"
