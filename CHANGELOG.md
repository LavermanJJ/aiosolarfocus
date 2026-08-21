# Changelog

## Unreleased

First cut. Nothing has been released yet.

- Async client over pymodbus, one socket per controller, typed exceptions.
- The register map is data: a `Register` is a frozen dataclass and a descriptor
  at once, with per-version offsets instead of `if api_version >=` branches.
- `registers.csv` ships as package data and every table is checked against it.
- Reads are planned once across every component, so interleaved blocks become
  one request and no read spans an unmapped address.
- `update()` raises only on connection failure; everything else is reported per
  component.
- Optional detection, ported from `pysolarfocus`'s `detect-configuration` branch.
- A blocking facade, a `python -m aiosolarfocus` command line, and a fake
  controller shipped in the wheel for downstream tests.
