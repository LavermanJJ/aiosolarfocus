"""Firmware versions, systems, protocol limits, and the values that mean nothing."""

from __future__ import annotations

from enum import IntEnum, StrEnum

DEFAULT_PORT = 502
DEFAULT_DEVICE_ID = 1
DEFAULT_TIMEOUT = 3.0

#: Modbus allows 125 registers in one read. Stay under it with headroom - the
#: longest contiguous run in the map today is 18, so this is a guard with a test
#: rather than a limit anything currently reaches.
MAX_REGISTERS_PER_READ = 120

#: What an unconfigured or open sensor channel reports instead of a measurement,
#: in the tenths a temperature register is read in: 130.0 degC and 270.0 degC. A
#: channel that is wired to something never gets there.
#:
#: 65535 (-1) is deliberately *not* here. -0.1 degC is a legitimate outdoor
#: reading, and treating it as absence would blank a sensor every frosty night.
#: Detection is where -1 counts as evidence that a channel is not configured.
OPEN_CHANNEL = frozenset({1300, 2700})

#: OPEN_CHANNEL's counterpart for a percent register. -0.1% has no equivalent
#: to -0.1 degC on a frosty night - a percentage is never legitimately negative
#: - so unlike OPEN_CHANNEL, 65535 belongs in this one. Found leaking through as
#: -0.1% indoor humidity and -1% boiler cleaning on real controllers in
#: home-assistant-solarfocus#237.
NOT_WIRED_PERCENT = frozenset({65535})


class Systems(StrEnum):
    """The heating systems this library knows how to address.

    The values are the strings the Home Assistant config entry has always
    stored, so an entry written by the old integration still resolves.
    """

    VAMPAIR = "Vampair"
    THERMINATOR = "Therminator"
    ECOTOP = "Ecotop"
    PELLETELEGANCE = "Pellet Elegance"
    OCTOPLUS = "Octoplus"


class RegisterKind(StrEnum):
    """Which Modbus table a register lives in."""

    INPUT = "input"
    HOLDING = "holding"


class Access(StrEnum):
    """Whether the controller lets us write a register, not just read it."""

    READ = "read"
    READ_WRITE = "read_write"


#: One write on the wire: which table, which address, and the words to put there.
#: A sequence of these is what goes out under one hold of the transport lock.
type Write = tuple["RegisterKind", int, tuple[int, ...]]


class ApiVersion(IntEnum):
    """A Solarfocus firmware version, ordered by construction.

    The value is the printed version with the dot taken out, so `>=` is the
    comparison the register table needs and there is nothing to parse at
    runtime. This replaces `ApiVersions.greater_or_equal(other.value)`, which
    read backwards, took a string, and pulled in `packaging` for a comparison
    an ordered enum gives free.
    """

    V_20_110 = 20110
    V_21_140 = 21140
    V_22_090 = 22090
    V_23_010 = 23010
    V_23_020 = 23020
    V_23_040 = 23040
    V_23_080 = 23080
    V_25_020 = 25020
    V_25_030 = 25030
    V_25_100 = 25100
    V_26_020 = 26020

    @property
    def label(self) -> str:
        """The version as the controller prints it on its own screen."""
        return f"{self // 1000}.{self % 1000:03d}"

    @classmethod
    def parse(cls, value: str | int | ApiVersion, *, clamp: bool = True) -> ApiVersion:
        """Read a version off a config entry, a command line, or a controller.

        Accepts the printed form ("26.020"), the enum name ("V_26_020") and the
        bare integer. With `clamp`, a firmware newer than any we know resolves
        to the newest we do rather than failing: Solarfocus will ship 26.030
        one day, and an installation should keep working when they do.
        """
        if isinstance(value, ApiVersion):
            return value
        if isinstance(value, str):
            text = value.strip()
            if text.upper().startswith("V_"):
                try:
                    return cls[text.upper()]
                except KeyError:
                    raise ValueError(f"unknown api version {value!r}") from None
            text = text.lstrip("vV")
            major, _, minor = text.partition(".")
            if not major.isdigit() or not minor.isdigit():
                raise ValueError(f"unknown api version {value!r}")
            number = int(major) * 1000 + int(minor)
        else:
            number = int(value)

        try:
            return cls(number)
        except ValueError:
            pass
        if not clamp:
            raise ValueError(f"unsupported api version {value!r}")
        known = sorted(cls)
        if number < known[0]:
            raise ValueError(f"api version {value!r} is older than the oldest supported, {known[0].label}")
        return max(version for version in known if version <= number)


def every_system_but(*excluded: Systems) -> frozenset[Systems]:
    """Every system except these, for a register one or two models do not have."""
    return frozenset(Systems) - frozenset(excluded)
