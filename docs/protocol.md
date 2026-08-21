# How the controller actually behaves

Four things the eco<sup>manager-touch</sup> does that a well-behaved Modbus
server does not. All were measured against a vampair on firmware 26.020. They
are written down here because they are the whole reason this library is shaped
the way it is, and because they are not in the register document.

## 1. A read that spans an unmapped address is compacted, not padded

This is the dangerous one.

Ask for four registers starting at 100 when 100, 101, 103 and 104 are mapped and
102 is not, and the controller answers with **four registers**: the values of
100, 101, 103 and 104, packed together. The hole is not padded. Every value
after the gap has silently shifted into the wrong name, the answer is the right
length, and **nothing in the protocol says this happened**.

There is no way to detect it after the fact. The only defence is a read plan
that never spans an address the firmware does not map, which is what
`planner.plan` is for and what `testing.FakeController` reproduces so the
planner's tests check readings rather than comments.

It is also why this library will not bridge a gap with filler registers to make
its plan shorter. A controller that turned out not to have one filler would
silently shift every reading after it.

## 2. A read that *starts* at an unmapped address is refused outright

Illegal data address, however long the read. So a slice beginning one register
early does not shift — it fails, loudly. That asymmetry is why the missing
`32900` row in the register document matters: if heating circuit 7's holding
block really does start at an address the firmware does not map, that circuit
loses its whole block rather than reading it wrong.

## 3. A 32-bit register refuses a read of one register

A 32-bit counter is only handed out whole. Asking for one of its two registers
is refused with illegal data address — **exactly the way a missing address is**.

So probing whether an address exists needs a `count=2` fallback, or every 32-bit
counter in the map looks absent. `detect._Prober._exists` does that, and the
planner never cuts a wide register in half.

## 4. The register map tracks the firmware, not the installation

Of the 352 registers in the document, 349 were mapped on the reference
controller — including every component its owner does not have. The only absent
ones were the X35 sensors of buffers 2 to 4.

So an illegal-data-address reply identifies the **api version**, and says next
to nothing about which components are installed. Component presence has to come
from what the registers *say*: the documented "nicht vorhanden" and "nicht
freigeschaltet" values, and the out-of-range temperatures an unconfigured sensor
channel reports (130.0 °C, 270.0 °C, or -1). That split is why `detect.py`
probes for the version and reads values for the counts.

## Sentinel readings

An unconfigured or open sensor channel reports `1300`, `2700` or `65535` rather
than a measurement. The read path decodes `1300` and `2700` to `None` but
deliberately **not** `65535`: -0.1 °C is a perfectly good outdoor reading on a
frosty night. Detection uses the wider set, because there a -1 is evidence that
a channel is not configured.
