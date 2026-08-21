"""The errors this library raises.

Every member of this hierarchy is raised somewhere, and a test asserts as much.
That is the deliberate answer to the predecessor's `exceptions.py`, where three
of seven classes were imported into the Modbus wrapper and never raised, because
the wrapper reported failure by returning `False` instead.
"""

from __future__ import annotations


class SolarfocusError(Exception):
    """Base class for everything this library raises.

    `context` says what was being attempted - "reading input 2300-2304",
    "writing heating circuit 1 mode" - because a Modbus error on its own tells
    the reader nothing about which part of the heating system went quiet.
    """

    def __init__(self, message: str, *, context: str | None = None) -> None:
        super().__init__(f"{context}: {message}" if context else message)
        self.message = message
        self.context = context


class SolarfocusConfigError(SolarfocusError):
    """The configuration cannot describe a real controller.

    A component count above what the firmware supports, an unknown version, a
    blocking client built inside a running event loop.
    """


class SolarfocusConnectionError(SolarfocusError):
    """The socket is the problem: refused, dropped, or never opened.

    This is the one failure that is not a statement about any component, which
    is why `SolarfocusClient.update` raises it rather than reporting it as every
    component having gone quiet.
    """


class SolarfocusTimeoutError(SolarfocusConnectionError):
    """The controller did not answer in time."""


class SolarfocusProtocolError(SolarfocusError):
    """The answer was not one we can read: malformed, or the wrong length."""


class SolarfocusRegisterError(SolarfocusError):
    """The controller answered with a Modbus exception response."""


class IllegalAddressError(SolarfocusRegisterError):
    """Exception code 2: this firmware does not have that register.

    Almost always a configuration fault the owner can fix - an api version set
    higher than the controller actually runs - so it is reported against the
    components whose registers were in the failed read, not as a lost
    connection.
    """


class IllegalValueError(SolarfocusRegisterError):
    """Exception code 3: the controller rejected the value we wrote."""


class DeviceFailureError(SolarfocusRegisterError):
    """Exception code 4: the controller failed while carrying out the request."""


class SolarfocusValueError(SolarfocusError):
    """A write was rejected here, before anything went out on the wire."""


class UnsupportedRegisterError(SolarfocusValueError):
    """This firmware, or this system, does not have that register."""


class ReadOnlyRegisterError(SolarfocusValueError):
    """The register exists, but the controller does not accept writes to it."""


class ValueOutOfRangeError(SolarfocusValueError):
    """The value is outside what the register accepts.

    Carries the bounds so a caller - or a Home Assistant number entity - can say
    what would have been allowed instead of just refusing.
    """

    def __init__(self, message: str, *, bounds: tuple[float, float] | None = None, context: str | None = None) -> None:
        super().__init__(message, context=context)
        self.bounds = bounds
