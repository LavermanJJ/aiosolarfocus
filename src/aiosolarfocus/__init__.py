"""Async client for Solarfocus eco manager-touch heating systems.

Everything a caller needs is re-exported here; nothing else in the package is
public API. See `docs/porting-from-pysolarfocus.md` if you are coming from the
synchronous `pysolarfocus`.
"""

from importlib.metadata import PackageNotFoundError, version

from .const import DEFAULT_DEVICE_ID, DEFAULT_PORT, Access, ApiVersion, RegisterKind, Systems
from .enums import (
    BOILER_STATE,
    BUFFER_STATE,
    HEATING_CIRCUIT_STATE,
    VAMPAIR_STATE,
    BufferMode,
    DomesticHotWaterMode,
    HeatingCircuitCooling,
    HeatingCircuitHeatingMode,
    HeatingCircuitMode,
    HeatPumpSgReadyMode,
    describe,
)
from .exceptions import (
    DeviceFailureError,
    IllegalAddressError,
    IllegalValueError,
    ReadOnlyRegisterError,
    SolarfocusConfigError,
    SolarfocusConnectionError,
    SolarfocusError,
    SolarfocusProtocolError,
    SolarfocusRegisterError,
    SolarfocusTimeoutError,
    SolarfocusValueError,
    UnsupportedRegisterError,
    ValueOutOfRangeError,
)
from .registers import RegisterInfo

try:
    __version__ = version("aiosolarfocus")
except PackageNotFoundError:  # pragma: no cover - only when running from a source tree
    __version__ = "0.0.0"

__all__ = [
    "BOILER_STATE",
    "BUFFER_STATE",
    "DEFAULT_DEVICE_ID",
    "DEFAULT_PORT",
    "HEATING_CIRCUIT_STATE",
    "VAMPAIR_STATE",
    "Access",
    "ApiVersion",
    "BufferMode",
    "DeviceFailureError",
    "DomesticHotWaterMode",
    "HeatPumpSgReadyMode",
    "HeatingCircuitCooling",
    "HeatingCircuitHeatingMode",
    "HeatingCircuitMode",
    "IllegalAddressError",
    "IllegalValueError",
    "ReadOnlyRegisterError",
    "RegisterInfo",
    "RegisterKind",
    "SolarfocusConfigError",
    "SolarfocusConnectionError",
    "SolarfocusError",
    "SolarfocusProtocolError",
    "SolarfocusRegisterError",
    "SolarfocusTimeoutError",
    "SolarfocusValueError",
    "Systems",
    "UnsupportedRegisterError",
    "ValueOutOfRangeError",
    "__version__",
    "describe",
]
