# The register document, and where it is wrong

Everything this library knows about addresses, widths, signedness and scale
comes from one vendor PDF:

> **DR-0180-DE / v14-260212** — *Regelung eco<sup>manager-touch</sup>: Modbus TCP
> Registerdaten*, kept here as
> [`ecomanager-touch_modbus-tcp_registerdaten_anleitung1-2_v14-260212.pdf`](ecomanager-touch_modbus-tcp_registerdaten_anleitung1-2_v14-260212.pdf).

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
- **Input `2104`**, solar heat meter flow. The predecessor scaled it by a tenth
  and this library keeps doing so, but the unit is litres and both readings are
  plausible. **Unresolved** — recorded in `UNRESOLVED`, and waiting on a dump
  from a system that has solar.

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

## What the document does not cover at all

The four behaviours in [`protocol.md`](protocol.md) — read compaction, refusal
at an unmapped start address, 32-bit registers refusing a one-register read, and
the map tracking firmware rather than installation — appear nowhere in it. They
were measured against hardware, and they are the reason the read planner exists.
