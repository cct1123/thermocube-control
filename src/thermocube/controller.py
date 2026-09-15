"""One synchronous controller, one serial handle, one lock. No background work."""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

import serial

CONTROL, SETPOINT, FAULTS, TEMPERATURE = 0, 1, 8, 9
PROFILES = ("legacy-r2", "thermocube-ii-m5")


@dataclass(frozen=True)
class Faults:
    raw: int
    profile: str
    active: tuple[str, ...]
    unknown_mask: int
    standby: bool | None

    @property
    def has_fault(self) -> bool:
        return bool(self.active or self.unknown_mask)


def _number(value: float | int | Decimal) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Temperature must be a finite number")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Temperature must be finite")
    return result


def to_celsius(value: float | int | Decimal, unit: str = "C") -> float:
    number = _number(value)
    if unit not in ("C", "F"):
        raise ValueError("Unit must be C or F")
    result = float(number if unit == "C" else (number - 32) * 5 / 9)
    if not math.isfinite(result):
        raise ValueError("Temperature is too large")
    return result


def encode_temperature(value: float | int | Decimal, unit: str = "C") -> bytes:
    number = _number(value)
    if unit == "C":
        number = number * 9 / 5 + 32
    elif unit != "F":
        raise ValueError("Unit must be C or F")
    # Quantization tolerance permits exact unsigned-word roundtrips. Not a safe operating range.
    if not Decimal("-0.05") < number < Decimal("6553.55"):
        raise ValueError("Outside unsigned tenths-F representation; signed HEX is unverified")
    raw = int((number * 10).to_integral_value(rounding=ROUND_HALF_UP))
    return raw.to_bytes(2, "little")


def decode_temperature(data: bytes, unit: str = "C") -> float:
    if type(data) is not bytes or len(data) != 2:
        raise ConnectionError("Temperature reply must contain exactly two bytes")
    number = Decimal(int.from_bytes(data, "little")) / 10
    if unit == "F":
        return float(number)
    if unit == "C":
        return float((number - 32) * 5 / 9)
    raise ValueError("Unit must be C or F")


def validate_limits(bounds: tuple[float, float] | None) -> tuple[float, float] | None:
    if bounds is None:
        return None
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
        raise ValueError("limits_c must contain a minimum and maximum")
    low, high = (to_celsius(v) for v in bounds)
    if low >= high:
        raise ValueError("Minimum must be less than maximum")
    return low, high


def setpoint_payload(value: float, unit: str, limits: tuple[float, float]) -> bytes:
    requested = to_celsius(value, unit)
    data = encode_temperature(value, unit)
    if not all(limits[0] <= v <= limits[1] for v in (requested, decode_temperature(data))):
        raise ValueError(f"Requested and quantized setpoint must be within {limits} C")
    return data


def decode_faults(data: bytes, profile: str) -> Faults:
    if type(data) is not bytes or len(data) != 1:
        raise ConnectionError("Fault reply must contain exactly one byte")
    if profile not in PROFILES:
        raise ValueError("Select a known fault profile explicitly")
    mapping = {0: "tank_level_low", 1: "fan_fail", 3: "pump_fail"}
    if profile == "legacy-r2":
        mapping.update({4: "rtd_open", 5: "rtd_short"})
        standby, known = None, 0
    else:
        mapping.update({2: "flow_fault", 4: "leak_detected", 5: "rtd_fault"})
        standby, known = bool(data[0] & 64), 64
    known |= sum(1 << bit for bit in mapping)
    return Faults(
        data[0],
        profile,
        tuple(name for bit, name in sorted(mapping.items()) if data[0] & (1 << bit)),
        data[0] & (~known & 255),
        standby,
    )


QUERY_ACK = "QUERIES ASSERT REMOTE AND RUN STATE"
CONTROL_ACK = "ENABLE SETPOINT START AND STOP CONTROL"
RECOVERY_ACK = "STREAM RESET AND DEVICE STATE VERIFIED"


class SafetyError(RuntimeError):
    """The requested operation is locked or conflicts with observed device state."""


@dataclass(frozen=True)
class Status:
    """One observation; timestamp precedes its first query, so age is conservative."""

    timestamp: datetime
    temperature_c: float | None
    setpoint_c: float | None
    faults: Faults
    requested_run: bool | None
    reported_run: bool | None


class ThermoCube:
    def __init__(
        self,
        port: str,
        *,
        profile: str | None = None,
        binary_framing_confirmed: bool = False,
        limits_c: tuple[float, float] | None = None,
        timeout: float = 0.5,
        command_interval: float = 0.35,
    ) -> None:
        if not isinstance(port, str) or not port.strip() or "://" in port:
            raise ValueError("Specify a local serial port, not a URL")
        if profile is not None and profile not in PROFILES:
            raise ValueError("Unknown fault profile")
        if type(binary_framing_confirmed) is not bool:
            raise ValueError("Framing confirmation must be a boolean")
        for value in (timeout, command_interval):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeout and command interval must be finite and positive")
        if command_interval < 0.35:
            raise ValueError("R2 command interval must be at least 350 ms")
        self._port_name, self._profile = port.strip(), profile
        self._framing = binary_framing_confirmed
        self._limits = validate_limits(limits_c)
        self._timeout, self._interval = timeout, command_interval
        self._serial: serial.Serial | None = None
        self._lock = threading.RLock()
        self._armed = self._control = self._uncertain = False
        self._run: bool | None = None
        self._next_tx = 0.0
        self._log = logging.getLogger(__name__)

    @property
    def is_connected(self) -> bool:
        port = self._serial
        return port is not None and port.is_open

    @property
    def queries_enabled(self) -> bool:
        return self._armed and self.is_connected

    @property
    def control_enabled(self) -> bool:
        return self._control and self.is_connected

    @property
    def needs_recovery(self) -> bool:
        return self._uncertain

    def connect(self) -> None:
        """Open once at 9600/8N1; no discovery, queries, retries or stream flushing."""
        with self._lock:
            if self.is_connected:
                return
            self.disarm()
            port = serial.Serial(
                port=None,
                baudrate=9600,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self._timeout,
                write_timeout=self._timeout,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
            try:
                port.port = self._port_name
                port.rts = port.dtr = False  # Adapter/OS opening glitches still need measurement.
                if os.name == "posix":
                    port.exclusive = True
                port.open()
                if not port.is_open:
                    raise OSError("Port did not open")
            except BaseException as exc:
                if not isinstance(exc, OSError):
                    self._uncertain = True
                try:
                    port.close()
                except BaseException as close_error:
                    self._uncertain = True
                    if not isinstance(close_error, OSError):
                        raise
                if isinstance(exc, OSError):
                    raise ConnectionError(f"Cannot open {self._port_name}: {exc}") from exc
                raise
            self._serial = port
            self._next_tx = max(self._next_tx, time.monotonic() + self._interval)

    def disconnect(self) -> None:
        """Release the port and permissions. Does not stop or restore LOCAL mode."""
        with self._lock:
            port, self._serial = self._serial, None
            self.disarm()
            if port is not None:
                try:
                    port.close()
                except BaseException as exc:
                    self._uncertain = True
                    if isinstance(exc, OSError):
                        raise ConnectionError(f"Port close failed: {exc}") from exc
                    raise

    def arm_queries(self, *, run: bool, acknowledgement: str) -> None:
        """Acknowledge an independently established state; every query reasserts it."""
        with self._lock:
            if not self.is_connected:
                raise ConnectionError("Connect before arming queries")
            if type(run) is not bool or acknowledgement != QUERY_ACK:
                raise SafetyError("Acknowledge that queries actively select REMOTE and RUN/STANDBY")
            if not self._framing or self._profile is None:
                raise SafetyError("Confirm binary framing and fault profile for this controller")
            if self._uncertain:
                raise SafetyError("Uncertain stream: explicit physical recovery is required")
            if self._armed and run != self._run:
                raise SafetyError("Disarm before establishing a different physical query state")
            self._run, self._armed, self._control = run, True, False

    def enable_control(self, acknowledgement: str) -> None:
        with self._lock:
            self._require_query()
            if acknowledgement != CONTROL_ACK or self._limits is None:
                raise SafetyError("Control needs explicit acknowledgement and safe setpoint bounds")
            self._control = True

    def disarm(self) -> None:
        with self._lock:
            self._armed = self._control = False
            self._run = None

    def confirm_recovery(self, acknowledgement: str) -> None:
        """Send no bytes; acknowledge actual stream reset/state verification, not just an empty buffer."""
        with self._lock:
            if not self.is_connected:
                raise ConnectionError("Reopen before confirming recovery")
            if acknowledgement != RECOVERY_ACK:
                raise SafetyError("Physical stream reset and state verification are required")
            assert self._serial is not None
            try:
                buffered = self._serial.in_waiting
            except OSError as exc:
                self._quarantine()
                raise ConnectionError(f"Cannot verify the reopened stream: {exc}") from exc
            if buffered:
                raise SafetyError("Unexpected buffered bytes; stream remains uncertain")
            self._uncertain = False

    def _require_query(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Device is disconnected")
        if not self._armed:
            raise SafetyError("Queries are locked; establish and acknowledge the physical state")

    def _require_control(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Device is disconnected")
        if not self._control:
            raise SafetyError("Setpoint/start/stop controls are locked")

    def _quarantine(self) -> None:
        self._uncertain = True
        try:
            self.disconnect()
        except OSError:
            pass  # Preserve the original transaction failure.

    def _exchange(self, parameter: int, data: bytes | None = None) -> bytes:
        # All callers hold the one controller lock over their complete semantic operation.
        assert self._serial is not None and self._run is not None
        # Only the four supported operations reach this private method.
        command = 0x80 | (0x40 if self._run else 0) | (0x20 if data is not None else 0) | parameter
        tx = bytes([command]) + (data or b"")
        size = 0 if data is not None else (1 if parameter == FAULTS else 2)
        port, rx = self._serial, bytearray()
        try:
            while time.monotonic() < self._next_tx:
                time.sleep(max(0, self._next_tx - time.monotonic()))
            if port.in_waiting:
                raise ConnectionError("Unsolicited/late bytes before transaction")
            started = time.monotonic()
            self._log.debug("port=%s TX=%s monotonic=%.6f", self._port_name, tx.hex(), started)
            try:
                count = port.write(tx)
            finally:
                self._next_tx = time.monotonic() + self._interval
            if time.monotonic() - started > self._timeout:
                raise TimeoutError("Write completed after deadline; delivery uncertain")
            if count != len(tx):
                raise ConnectionError("Short serial write; delivery uncertain")
            deadline = time.monotonic() + self._timeout
            while len(rx) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Short reply: {len(rx)}/{size} bytes")
                port.timeout = remaining
                chunk = port.read(size - len(rx))
                if type(chunk) is not bytes or len(chunk) > size - len(rx):
                    raise ConnectionError("Malformed serial response")
                rx.extend(chunk)
                if time.monotonic() > deadline:
                    raise TimeoutError("Read completed after deadline")
                if not chunk:
                    time.sleep(min(0.001, max(0, deadline - time.monotonic())))
            if port.in_waiting:
                raise ConnectionError("Unexpected bytes after response")
        except BaseException as exc:
            # Cancellation can leave a delivered command or a reply in flight.
            self._quarantine()
            self._log.debug("port=%s RX=%s uncertain=%s", self._port_name, rx.hex(), exc)
            if isinstance(exc, serial.SerialTimeoutException):
                raise TimeoutError(f"Serial write timed out: {exc}") from exc
            if isinstance(exc, (TimeoutError, ConnectionError)):
                raise
            if isinstance(exc, OSError):
                raise ConnectionError(f"Serial exchange failed: {exc}") from exc
            raise
        self._log.debug(
            "port=%s RX=%s outcome=%s",
            self._port_name,
            rx.hex(),
            "received" if size else "sent-unconfirmed",
        )
        return bytes(rx)

    def _temperature(self, parameter: int, unit: str) -> float:
        if unit not in ("C", "F"):
            raise ValueError("Unit must be C or F")
        self._require_query()
        data = self._exchange(parameter)
        # Provisional read plausibility guard, not the unit's operating envelope.
        if not -20 <= decode_temperature(data) <= 100:
            self._quarantine()
            raise ConnectionError(f"Implausible temperature word: {data.hex()}")
        return decode_temperature(data, unit)

    def read_temperature(self, unit: str = "C") -> float:
        with self._lock:
            return self._temperature(TEMPERATURE, unit)

    def read_setpoint(self, unit: str = "C") -> float:
        with self._lock:
            return self._temperature(SETPOINT, unit)

    def read_faults(self) -> Faults:
        with self._lock:
            self._require_query()
            assert self._profile is not None
            faults = decode_faults(self._exchange(FAULTS), self._profile)
            mismatch = faults.standby is not None and faults.standby == self._run
            if faults.has_fault or mismatch:
                self._armed = False  # Existing write permission still permits explicit STOP.
            return faults

    def set_setpoint(self, value: float, unit: str = "C") -> float:
        with self._lock:
            self._require_control()
            self._require_query()
            assert self._limits is not None
            data = setpoint_payload(value, unit, self._limits)
            self.read_faults()
            self._require_query()
            self._exchange(SETPOINT, data)
            readback = self.read_setpoint()
            if encode_temperature(readback) != data:
                self._armed = False
                raise SafetyError("Setpoint readback differs; no retry")
            return decode_temperature(data, unit)

    def start(self) -> None:
        with self._lock:
            self._require_control()
            self._require_query()
            self.read_faults()
            self._require_query()
            self._run = True
            self._exchange(CONTROL, b"")

    def stop(self) -> None:
        with self._lock:
            self._require_control()
            self._run = False
            self._exchange(CONTROL, b"")

    def status(self) -> Status:
        """Fault-first observation (up to three paced queries); no stale value merging."""
        with self._lock:
            stamp = datetime.now(UTC)
            faults = self.read_faults()
            temperature = self.read_temperature() if self._armed else None
            setpoint = self.read_setpoint() if self._armed else None
            reported = None if faults.standby is None else not faults.standby
            return Status(stamp, temperature, setpoint, faults, self._run, reported)

    def __enter__(self) -> ThermoCube:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()
