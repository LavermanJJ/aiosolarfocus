# Contributing

```sh
uv sync
make check     # ruff check, ruff format --check, mypy strict
make test      # pytest
make docs      # regenerate docs/registers.md
```

## Adding a register

Add one line to the component's class body. Take the address, the width, the
signedness and the scale from `src/aiosolarfocus/data/registers.csv`; the test
suite checks all four, and the `doc=` string against the document's own name for
it. If they disagree, record the disagreement in
`tests/test_register_table.py` with a reason rather than editing either quietly:
the CSV transcribes the vendor document errors and all, and
[`docs/register-document.md`](docs/register-document.md) explains which errors
are known.

Then look at the read plan, which no register changes without affecting:

```sh
python -m aiosolarfocus plan --system Vampair --api-version 26.020 -v
```

## Adding a component

One row in `src/aiosolarfocus/components/__init__.py` and one file beside it.
There is one list; it cannot drift from itself.

## Producing a register dump

The most useful thing an owner can contribute, especially for a system nobody
here has: therminator, ecotop, Pellet Elegance and octoplus are all reasoned
from the specification rather than measured.

```sh
python -m aiosolarfocus detect --host <your controller> --evidence
python -m aiosolarfocus dump --host <your controller> --json > my-system.json
```

Both are read-only. Paste them into an issue along with what the installation
actually has, so the counts can be checked against the truth.

> [!WARNING]
> `python -m aiosolarfocus set` writes to your heating system. Do not run it
> against a register you do not understand.
