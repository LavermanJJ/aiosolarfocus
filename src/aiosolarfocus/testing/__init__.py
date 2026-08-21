"""Test doubles for aiosolarfocus, shipped so downstream test suites can use them.

The fake controller reproduces the ways the real eco manager-touch misbehaves -
refusing unmapped addresses, refusing a one-register read of a 32-bit register,
and compacting a block read that spans a gap - because those are what the read
planner exists to avoid.
"""

from .spec import SpecRow, documented_addresses, load_spec, names_at

__all__ = ["SpecRow", "documented_addresses", "load_spec", "names_at"]
