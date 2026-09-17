# The register document, and where it is wrong

Everything this library knows about addresses, widths, signedness and scale
comes from one vendor PDF:

> **DR-0180-DE / v14-260212** — *Regelung eco<sup>manager-touch</sup>: Modbus TCP
> Registerdaten*, published by SOLARFOCUS.

The PDF is theirs, so it is not redistributed here; ask Solarfocus for it. Every
page reference below is to that revision.

`src/aiosolarfocus/data/registers.csv` is that PDF as data. It is a
**transcription, not a correction**: where the document is wrong, the CSV is
wrong the same way, deliberately. That is what makes
`tests/test_register_table.py` mean anything — it can only ask "does the
document really say this?" if the CSV has not been quietly improved. So a known
error stays in the CSV and the decision about it is recorded in the test suite,
where it has to carry a reason and gets deleted the day it stops being true.

Below is every error found so far, and what the library does about each.

## Heating circuit 7's flow setpoint is printed at circuit 4's address

Page 14 lists circuit 7's block as

| Nr. | Adr. | Bezeichnung |
|---|---|---|
| 7 | **32750** | Vorlaufsolltemperatur Heizen / Kühlen |
| 7 | 32902 | Kühlen E/A |
| 7 | 32903 | Heizkreisbetriebsart |

`32750` is circuit 4's address, four rows up the same page: a copy-paste slip
in the specification, not a firmware that skips one register in one circuit.
The register is at **32900**, where circuit 7's own block starts and where the
stride from every other circuit puts it. Nothing else in the row is wrong:
`int16`, °C, `* 10`, since V20.110, exactly like the other seven.

This one was worth chasing rather than assuming, because of the asymmetry in
[`protocol.md`](protocol.md#2-a-read-that-starts-at-an-unmapped-address-is-refused-outright):
a read that *starts* at an unmapped address is refused outright, so if 32900 had
really been unmapped, circuit 7 would have lost its whole holding block instead
of reading it slightly wrong.

The library reads 32900. The CSV keeps the misprint, which shows up there as
32750 appearing twice, and `MISPRINTED_ADDRESSES` in
`tests/test_register_table.py` records the decision. A test asserts the
duplicate is still there, so a future revision that fixes the typo fails
loudly rather than leaving a stale note behind.

## Registers the document gives no scale factor

- **Holding `32607`**, external room humidity. A vampair on firmware 26.020
  reports 440 for 44.0 % and accepts a write of 44, so it is read in tenths and
  written whole — see home-assistant-solarfocus#150. The controller is the
  authority; recorded in `CORRECTIONS`.
- **Input `2104`**, solar heat meter flow. Here the document is right and the
  predecessor was wrong: it scaled the register by a tenth, and a Therminator 2
  read 23.3 while the eco-manager-touch's own display, at the same moment, read
  233 — see
  [home-assistant-solarfocus#239](https://github.com/LavermanJJ/home-assistant-solarfocus/issues/239).
  The raw register is the reading. Nothing is recorded for it, because there is
  no longer anything to disagree about; settling it emptied `UNRESOLVED`.

## A unit the document understates

- **Input `2104`** again. The document calls it `l`, *aktueller Durchfluss
  Wärmemengenzähler in Liter*, and the controller's display calls it **l/h**. A
  *Durchfluss* is a rate and litres are not, so the table carries `l/h`. Only
  the label differs — no reading changes — and units are the one column of the
  document the cross-check does not compare, so this is written down here
  rather than in a table in the test suite.

## Names that do not say what they look like they say

None of these is a bug in the library; they are the places where "the table's
name does not match the document's" is expected, recorded in `NAME_NOTES`.

- **Holding `32600`** is listed twice on page 13, as
  *Vorlaufsolltemperatur Heizen* and *Vorlaufsolltemperatur Kühlen*. One
  register, two names, two ranges; which one applies depends on `32602`.
- **Holding `32003`** is named *Zirkulation 1 anfordern* — the instance number
  is inside the name, and repeats as *Zirkulation 2* at 32053 and so on.
- **Input `850`** misspells *Zirkulationsmodul* as *Zirkultionsmodul* in the
  name column, while spelling it correctly in the description beside it.
- **Input `2410`** carries three different meanings at one address, split by
  system: the table splits it and the document does not.

## Fresh water module registers a therminator does not implement

Pages 11 and 12 list the fresh water modules (input `700`, `725`, `750`, `775`,
stride 25) and the cascade over them (input `800`) with no system qualifier. For
the **therminator** that is wrong: the controller maps the addresses and answers
reads on them, and the firmware never writes anything into them.

The silence is worth noting, because the document is perfectly willing to speak.
Two pages earlier it writes *Kesselbetriebsart therminator* on `2409`, and
`2410` carries a three-way split whose third branch reads *Therminator: nicht
belegt*. The vocabulary for "this system does not have this register" exists and
is used; the fresh water block simply does not use it.

The owner of a therminator 2 on firmware 26.020 with a fresh water module
physically installed took it to Solarfocus support and was told the therminator
2 lacks the registers in every eco manager-touch version including the newest,
and that implementing them has been filed with their development as a feature
request ([#13](https://github.com/LavermanJJ/aiosolarfocus/issues/13)). So this
is not a document running ahead of a firmware that will catch up on its own
schedule - it is a claim the firmware has never made good.

What it looks like from here is a module reporting nothing rather than a module
that is not there, because the reads succeed. In that owner's `detect
--evidence`:

```
fresh_water_modules: [(0, 0), (0, 0), (0, 0), (0, 0)]
fresh_water_module_cascade: [0, 0]
```

Those are zeros and not `None`, and the difference is the whole point: `None` is
[a refused address](protocol.md#2-a-read-that-starts-at-an-unmapped-address-is-refused-outright),
which is how an absent component normally announces itself. Detection therefore
reached the right count of zero by the ordinary rule that an all-zero block is
not a live one, and would have gone on doing so - but nothing stopped that owner
configuring `fresh_water_modules=1` by hand, which is what they did, and getting
a component whose every register read 0.0 forever.

Both fresh water rows in `components/__init__.py` now carry
`systems=every_system_but(Systems.THERMINATOR)`, so a therminator is refused the
count at configuration time with `therminator has no fresh water module` instead
of reading the block. The cascade is the one step of inference: Solarfocus
confirmed the module registers, and a cascade over modules a system cannot have
is not a thing it can have either.

No version gate goes with the exclusion. Solarfocus has a feature request, not a
release, and a `since` written against a promise would claim a firmware boundary
nobody has seen. If a therminator ever does report a live fresh water module,
`detect` says so in its own output - see `Detection.unsupported` - and that
report is what a version gate should be written from.

The CSV keeps the document's silence about systems, because it is a
transcription. `registers.csv` has no system column at all, so there is nothing
in it to be wrong here; the exclusion lives in the component registry, the same
place the vampair-only heat pump does.

## What the document does not cover at all

The four behaviours in [`protocol.md`](protocol.md) — read compaction, refusal
at an unmapped start address, 32-bit registers refusing a one-register read, and
the map tracking firmware rather than installation — appear nowhere in it. They
were measured against hardware, and they are the reason the read planner exists.
