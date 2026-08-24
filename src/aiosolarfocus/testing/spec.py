"""The register document, as data.

`registers.csv` is a transcription of the Solarfocus Modbus TCP register
specification. In the predecessor it was documentation that nothing loaded, so
it drifted from the code silently and in both directions. Here it is package
data with two jobs: the test suite checks every table against it, and the fake
controller uses it to know which addresses a real controller answers.

It transcribes the document's errors along with the rest of it, so that the
cross-check is asking about the document and not about itself. Those errors are
written up in docs/register-document.md and decided in tests/test_register_table.py.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files

from ..const import RegisterKind

#: How the document writes a scale factor, and what it means in the one
#: direction this library scales: engineering value = raw * scale.
#:
#: The document says "1/10" of an input register and "*10" of the holding
#: register that mirrors it - the same relationship, phrased from either end.
#: The predecessor took that literally and multiplied for input registers while
#: dividing for holding ones, so every new register was a chance to get the
#: direction wrong.
_SCALES: Mapping[str, float] = {"-": 1.0, "1/10": 0.1, "*10": 0.1, "1/1000": 0.001}


@dataclass(frozen=True, slots=True)
class SpecRow:
    """One row of the register document."""

    kind: RegisterKind
    address: int
    count: int
    name: str
    data_type: str
    unit: str | None
    scale: float
    version: str | None

    @property
    def signed(self) -> bool:
        """Whether the document calls this a signed register."""
        return self.data_type.startswith("int")

    @property
    def addresses(self) -> range:
        """Every address this row occupies."""
        return range(self.address, self.address + self.count)


@cache
def load_spec() -> Mapping[tuple[RegisterKind, int], tuple[SpecRow, ...]]:
    """Read the register document, keyed by table and address.

    The value is a tuple because the document lists some addresses twice under
    two names - 32600 is both the heating and the cooling flow setpoint - and
    losing one of them would make the cross-check look stricter than it is.
    """
    text = (files("aiosolarfocus.data") / "registers.csv").read_text(encoding="utf-8")
    rows: dict[tuple[RegisterKind, int], list[SpecRow]] = {}
    for raw in csv.DictReader(text.splitlines()):
        kind = RegisterKind.INPUT if raw["Register Type"] == "Input" else RegisterKind.HOLDING
        unit = raw["Unit"].strip()
        row = SpecRow(
            kind=kind,
            address=int(raw["Register Address"]),
            # The count column carries the instance number in some repeated
            # blocks - "2" for the second differential module - rather than a
            # register count. Only 32-bit rows are meaningfully wider than one,
            # and the data type says so, so take the width from there.
            count=2 if raw["Data Type"].endswith("32") else 1,
            name=raw["Name"].strip(),
            data_type=raw["Data Type"].strip(),
            unit=None if unit in {"-", ""} else unit,
            scale=_SCALES[raw["Scale Factor"].strip()],
            version=raw["Version"].strip() or None,
        )
        rows.setdefault((kind, row.address), []).append(row)
    return {key: tuple(value) for key, value in rows.items()}


def documented_addresses() -> frozenset[tuple[RegisterKind, int]]:
    """Every address the document says a controller answers, 32-bit halves included."""
    return frozenset((row.kind, address) for rows in load_spec().values() for row in rows for address in row.addresses)


def names_at(kind: RegisterKind, address: int) -> Sequence[str]:
    """What the document calls the register at one address, or nothing."""
    return [row.name for row in load_spec().get((kind, address), ())]
