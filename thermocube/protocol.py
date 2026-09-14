"""R2 logical binary messages. No I/O, framing guesses, or implicit control bits."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import IntEnum

from thermocube.models import FaultProfile, Faults, ProtocolError


class Parameter(IntEnum):
    CONTROL = 0
    SETPOINT = 1
    FAULTS = 8
    TEMPERATURE = 9


@dataclass(frozen=True)
class CommandBits:
    remote: bool
    run: bool
    host_to_controller: bool
    parameter: int


def command_byte(*, remote: bool, run: bool, host_to_controller: bool, parameter: int) -> int:
    """Compose Table 2 bits; this low-level function does not grant TX authority."""
    if any(type(v) is not bool for v in (remote, run, host_to_controller)):
        raise ValueError("Control bits must be explicit booleans")
    if isinstance(parameter, bool) or not isinstance(parameter, int) or not 0 <= parameter <= 31:
        raise ValueError("Parameter must be an integer in 0..31")
    return (int(remote) << 7) | (int(run) << 6) | (int(host_to_controller) << 5) | parameter


def split_command(value: int) -> CommandBits:
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError("Command must be a byte")
    return CommandBits(bool(value & 128), bool(value & 64), bool(value & 32), value & 31)


def message(
    parameter: Parameter, *, remote: bool, run: bool, write: bool = False, data: bytes = b""
) -> tuple[bytes, int]:
    """Return a supported logical request and its expected reply length."""
    if not isinstance(parameter, Parameter) or type(write) is not bool:
        raise ValueError("Unsupported operation")
    if type(data) is not bytes:
        raise ValueError("Payload must be bytes")
    if write:
        length = {Parameter.CONTROL: 0, Parameter.SETPOINT: 2}.get(parameter)
        if length is None or len(data) != length:
            raise ValueError("Unsupported write or payload length")
        reply = 0
    else:
        if data or parameter == Parameter.CONTROL:
            raise ValueError("Unsupported query or unexpected payload")
        reply = 1 if parameter == Parameter.FAULTS else 2
    command = command_byte(remote=remote, run=run, host_to_controller=write, parameter=parameter)
    return bytes([command]) + data, reply


def decimal_value(value: float | int | Decimal) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a temperature")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Temperature must be a finite number") from exc
    if not result.is_finite():
        raise ValueError("Temperature must be finite")
    return result


def to_celsius(value: float | int | Decimal, unit: str = "C") -> float:
    number = decimal_value(value)
    if unit == "C":
        return float(number)
    if unit == "F":
        return float((number - 32) * 5 / 9)
    raise ValueError("Unit must be C or F")


def from_celsius(value: float, unit: str = "C") -> float:
    number = decimal_value(value)
    if unit == "C":
        return float(number)
    if unit == "F":
        return float(number * 9 / 5 + 32)
    raise ValueError("Unit must be C or F")


def encode_temperature(value: float | int | Decimal, unit: str = "C") -> bytes:
    number = decimal_value(value)
    if unit == "C":
        number = number * 9 / 5 + 32
    elif unit != "F":
        raise ValueError("Unit must be C or F")
    # Signed HEX values are not specified. Do not wrap or guess two's complement.
    if not Decimal("-0.05") < number < Decimal("6553.55"):
        raise ValueError("Temperature outside documented unsigned wire representation")
    raw = int((number * 10).to_integral_value(rounding=ROUND_HALF_UP))
    return raw.to_bytes(2, "little")


def decode_temperature(data: bytes, unit: str = "C") -> float:
    if type(data) is not bytes or len(data) != 2:
        raise ProtocolError("Temperature reply must contain exactly two bytes")
    raw = int.from_bytes(data, "little")
    if unit == "F":
        return raw / 10
    if unit == "C":
        return float((Decimal(raw) / 10 - 32) * 5 / 9)
    raise ValueError("Unit must be C or F")


def decode_faults(data: bytes, profile: FaultProfile = FaultProfile.LEGACY) -> Faults:
    if type(data) is not bytes or len(data) != 1:
        raise ProtocolError("Fault reply must contain exactly one byte")
    if not isinstance(profile, FaultProfile):
        raise ValueError("Select an explicit known fault profile")
    mapping = {0: "tank_level_low", 1: "fan_fail", 3: "pump_fail"}
    if profile == FaultProfile.LEGACY:
        mapping.update({4: "rtd_open", 5: "rtd_short"})
        status_mask = 0
        standby = None
    else:
        mapping.update({2: "flow_fault", 4: "leak_detected", 5: "rtd_fault"})
        status_mask = 1 << 6
        standby = bool(data[0] & status_mask)
    known = sum(1 << bit for bit in mapping) | status_mask
    return Faults(
        raw=data[0],
        profile=profile,
        active=tuple(name for bit, name in sorted(mapping.items()) if data[0] & (1 << bit)),
        unknown_mask=data[0] & (~known & 255),
        standby=standby,
    )
