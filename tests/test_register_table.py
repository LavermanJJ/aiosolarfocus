"""Every register table, checked against the register document.

This is what stops `registers.csv` and the code drifting apart, which in the
predecessor they silently did in both directions. Two rules make it work:

* Addresses, widths, signedness and scale come from the document. Where the
  table disagrees, it says so here, with a reason - the disagreement becomes a
  decision rather than an accident.
* `since` comes from the predecessor's gating, not from the document's Version
  column. That column carries entries like "V25.020/V25.010" that name two
  firmwares at once, and the predecessor's gating is what has actually been run
  against controllers in the field.

The document describes the *newest* firmware, so only the newest layout can be
checked against it. Older offsets - the pre-25.030 heating circuit block, for
instance - are simply not in the file, and their tests live in test_layout.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from aiosolarfocus.components import COMPONENTS, ComponentSpec
from aiosolarfocus.const import ApiVersion, RegisterKind, Systems
from aiosolarfocus.layout import Layout, ResolvedRegister
from aiosolarfocus.testing import load_spec

NEWEST = ApiVersion.V_26_020


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One place the table knowingly does not match the document."""

    reason: str


@dataclass(frozen=True, slots=True)
class Misprint(Disagreement):
    """A register the document lists, under an address that cannot be its own.

    `printed_as` is the address it is printed at, which is another register's,
    so the misprint shows up in the transcription as a duplicated address.
    """

    printed_as: int


#: The document is wrong here and the controller is the authority.
CORRECTIONS: dict[tuple[RegisterKind, int], Disagreement] = {
    (RegisterKind.HOLDING, 32607): Disagreement(
        "The document gives 32607 no scale factor. A vampair on 26.020 reports 440 for "
        "44.0 % room humidity and accepts a write of 44, so it is read in tenths and "
        "written whole. See home-assistant-solarfocus issue #150."
    ),
}

#: The table and the document differ and nobody has settled it on hardware. The
#: predecessor's behaviour is kept so that a reading can be compared against the
#: old library register for register without this standing in the way.
UNRESOLVED: dict[tuple[RegisterKind, int], Disagreement] = {
    (RegisterKind.INPUT, 2104): Disagreement(
        "The document gives the solar heat meter flow no scale factor; the predecessor "
        "scaled it by a tenth. Unit is litres, so both are plausible. Verify against a "
        "system that has solar before changing either - asked for in "
        "home-assistant-solarfocus#239."
    ),
}

#: Addresses the document prints wrongly, so the transcription has no row at
#: the address the register is really at. Not edited into registers.csv: that
#: file is a transcription of the vendor document, and correcting it there would
#: leave the cross-check asking whether the table agrees with itself. See
#: docs/register-document.md.
MISPRINTED_ADDRESSES: dict[tuple[RegisterKind, int], Misprint] = {
    (RegisterKind.HOLDING, 32900): Misprint(
        reason=(
            "Heating circuit 7's flow setpoint, printed at 32750 - circuit 4's address, "
            "four rows up the same page of the PDF. A copy-paste slip in the "
            "specification rather than a firmware that skips one register in one "
            "circuit: 32902 to 32908 are all listed, circuit 8's 32950 is too, and every "
            "other cell of the row matches the seven circuits around it."
        ),
        printed_as=32750,
    ),
}

#: The document's own name for a register, where the table's differs for a reason.
NAME_NOTES: dict[tuple[RegisterKind, int], str] = {
    (RegisterKind.HOLDING, 32600): "listed twice, as the heating and the cooling flow setpoint; one register, two names",
    (RegisterKind.HOLDING, 32003): "the document numbers the instance in the name ('Zirkulation 1 anfordern')",
    (RegisterKind.INPUT, 850): "the document misspells 'Zirkulationsmodul'",
    (RegisterKind.INPUT, 2410): "one address, three meanings; the table splits it by system and the document does not",
}


def resolved_registers() -> list[tuple[Systems, ComponentSpec, ResolvedRegister]]:
    """Every register of every component a controller could have on the newest firmware."""
    found: list[tuple[Systems, ComponentSpec, ResolvedRegister]] = []
    for system in Systems:
        for spec in COMPONENTS:
            if not spec.available(NEWEST, system):
                continue
            input_base, holding_base = spec.bases(0, NEWEST)
            layout = Layout.resolve(spec.component, NEWEST, system, input_base, holding_base)
            found.extend((system, spec, resolved) for resolved in layout.registers)
    return found


ALL_REGISTERS = resolved_registers()
IDS = [f"{system.value}-{spec.id.value}-{resolved.name}" for system, spec, resolved in ALL_REGISTERS]


@pytest.mark.parametrize(("system", "spec", "resolved"), ALL_REGISTERS, ids=IDS)
def test_every_register_is_where_the_document_says(system: Systems, spec: ComponentSpec, resolved: ResolvedRegister) -> None:
    """Address, width, signedness and scale, against the register document."""
    key = (resolved.kind, resolved.address)
    if key in MISPRINTED_ADDRESSES:
        pytest.skip(MISPRINTED_ADDRESSES[key].reason)
    rows = load_spec().get(key)
    assert rows is not None, f"{spec.id.value}.{resolved.name} claims {resolved.kind.value} {resolved.address}, which the document does not list"
    row = rows[0]

    assert resolved.width == row.count, f"{resolved.name}: table says {resolved.width} registers, document says {row.count}"
    assert resolved.register.signed == row.signed, f"{resolved.name}: table says signed={resolved.register.signed}, document says {row.data_type}"

    if key not in CORRECTIONS and key not in UNRESOLVED:
        assert resolved.register.scale == row.scale, f"{resolved.name}: table scales by {resolved.register.scale}, document by {row.scale}"


@pytest.mark.parametrize(("system", "spec", "resolved"), ALL_REGISTERS, ids=IDS)
def test_every_register_is_named_as_the_document_names_it(system: Systems, spec: ComponentSpec, resolved: ResolvedRegister) -> None:
    """The strongest check that a register is at the address we think it is.

    A transcription that put a register one address out would almost always
    land on a differently named one.
    """
    key = (resolved.kind, resolved.address)
    if key in NAME_NOTES:
        pytest.skip(NAME_NOTES[key])
    if key in MISPRINTED_ADDRESSES:
        pytest.skip(MISPRINTED_ADDRESSES[key].reason)
    names = [row.name for row in load_spec()[key]]
    assert resolved.register.doc in names, f"{resolved.name} at {resolved.kind.value} {resolved.address}: table calls it {resolved.register.doc!r}, document calls it {names}"


@pytest.mark.parametrize("spec", [spec for spec in COMPONENTS if spec.multiple], ids=lambda spec: spec.id.value)
def test_every_instance_of_a_repeated_component_is_documented(spec: ComponentSpec) -> None:
    """The stride is right: instance 4 lands on documented addresses, not past the end.

    A buffer's holding block strides by 50 while its input block strides by 20,
    and nothing but this test would notice if the two were swapped.
    """
    documented = load_spec()
    for index in range(spec.limit(NEWEST)):
        input_base, holding_base = spec.bases(index, NEWEST)
        if input_base is not None:
            assert (RegisterKind.INPUT, input_base) in documented, f"{spec.label} {index + 1} starts at input {input_base}, which the document does not list"
        if holding_base is not None and (RegisterKind.HOLDING, holding_base) not in MISPRINTED_ADDRESSES:
            assert (RegisterKind.HOLDING, holding_base) in documented, f"{spec.label} {index + 1} starts at holding {holding_base}, which the document does not list"


def test_the_recorded_disagreements_are_all_still_real() -> None:
    """A disagreement that has been fixed should be deleted, not left as a licence."""
    # Across every instance, not just the first: the one misprinted address is
    # in heating circuit 7, and only circuit 7 claims that address.
    claimed: set[tuple[RegisterKind, int]] = set()
    for system in Systems:
        for spec in COMPONENTS:
            if not spec.available(NEWEST, system):
                continue
            for index in range(spec.limit(NEWEST)):
                input_base, holding_base = spec.bases(index, NEWEST)
                layout = Layout.resolve(spec.component, NEWEST, system, input_base, holding_base)
                claimed.update((resolved.kind, resolved.address) for resolved in layout.registers)
    for key in list(CORRECTIONS) + list(UNRESOLVED) + list(NAME_NOTES) + list(MISPRINTED_ADDRESSES):
        assert key in claimed, f"{key} is recorded as a disagreement but no register claims that address any more"


def test_the_misprinted_addresses_are_still_misprinted() -> None:
    """A document revision that fixes the address should retire the entry, not outlive it.

    A misprinted address is another register's, so the transcription lists that
    other address twice. When the duplicate goes, the row is where it belongs and
    the ordinary cross-check above can have it back.
    """
    for (kind, address), misprint in MISPRINTED_ADDRESSES.items():
        assert (kind, address) not in load_spec(), f"the document now lists {kind.value} {address}; delete its entry and let the cross-check run"
        printed = load_spec().get((kind, misprint.printed_as), ())
        assert len(printed) > 1, (
            f"{kind.value} {address} is recorded as misprinted at {misprint.printed_as}, but the transcription lists {misprint.printed_as} {len(printed)} time(s), not twice"
        )
