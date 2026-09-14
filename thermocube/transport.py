"""Bounded binary serial transactions with one paced stream per physical port."""

from __future__ import annotations

import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from thermocube.models import (
    CommunicationTimeout,
    ConnectionError,
    Event,
    FaultProfile,
    ProtocolError,
    SafetyError,
    TemperatureLimits,
    utc_now,
)


@dataclass(frozen=True)
class HardwareApproval:
    """Explicit local record of a later human-approved candidate, not self-approval."""

    record: str
    profile: FaultProfile
    binary_framing_confirmed: bool = False
    allow_queries: bool = False
    allow_control: bool = False
    allowed_run_states: tuple[bool, ...] = (False,)
    setpoint_limits: TemperatureLimits | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.record, str)
            or not self.record.strip()
            or not isinstance(self.profile, FaultProfile)
        ):
            raise ValueError("Approval requires a record reference and fault profile")
        if not self.allowed_run_states or any(type(v) is not bool for v in self.allowed_run_states):
            raise ValueError("Specify allowed run states explicitly")
        object.__setattr__(self, "allowed_run_states", tuple(self.allowed_run_states))
        for flag in (self.binary_framing_confirmed, self.allow_queries, self.allow_control):
            if type(flag) is not bool:
                raise ValueError("Approval flags must be booleans")


@dataclass(frozen=True)
class SerialConfig:
    port: str
    timeout: float = 0.5
    write_timeout: float = 0.5
    command_interval: float = 0.35
    open_attempts: int = 2
    retry_delay: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip() or "://" in self.port:
            raise ValueError("Select a local serial port; URL transports are not supported")
        object.__setattr__(self, "port", self.port.strip())
        for value in (self.timeout, self.write_timeout, self.command_interval, self.retry_delay):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeouts and pacing must be finite and positive")
        if self.command_interval < 0.35:
            raise ValueError("Command interval must be at least 350 ms")
        if type(self.open_attempts) is not int or not 1 <= self.open_attempts <= 5:
            raise ValueError("Open attempts must be in 1..5")


class SerialLike(Protocol):
    timeout: float | None

    @property
    def is_open(self) -> bool: ...
    @property
    def in_waiting(self) -> int: ...
    def read(self, size: int = 1) -> bytes: ...
    def write(self, data: bytes) -> int | None: ...
    def close(self) -> None: ...


@dataclass
class _PortState:
    lock: threading.RLock = field(default_factory=threading.RLock)
    owner: object | None = None
    next_tx: float = 0.0
    needs_recovery: bool = False


_ports_lock = threading.Lock()
_ports: dict[str, _PortState] = {}


class SerialTransport:
    def __init__(
        self,
        config: SerialConfig,
        *,
        approval: HardwareApproval | None = None,
        serial_factory: Callable[[SerialConfig], SerialLike] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config, self._approval = config, approval
        self._factory = serial_factory
        self._clock, self._sleep = clock, sleep
        self._serial: SerialLike | None = None
        self._bound = False
        self._events: deque[Event] = deque(maxlen=1024)
        if self.is_hardware:
            # Physical pacing always uses real monotonic time, never a caller's fake clock.
            self._clock, self._sleep = time.monotonic, time.sleep
            key = (
                config.port.upper().removeprefix("\\\\.\\")
                if os.name == "nt"
                else os.path.realpath(config.port)
            )
            with _ports_lock:
                self._state = _ports.setdefault(key, _PortState())
        else:
            self._state = _PortState()

    @property
    def config(self) -> SerialConfig:
        return self._config

    @property
    def approval(self) -> HardwareApproval | None:
        return self._approval

    @property
    def is_hardware(self) -> bool:
        return self._factory is None

    @property
    def needs_recovery(self) -> bool:
        with self._state.lock:
            return self._state.needs_recovery

    def bind_driver(self) -> None:
        with self._state.lock:
            if self._bound:
                raise SafetyError("A transport may have only one device owner")
            self._bound = True

    @property
    def is_connected(self) -> bool:
        with self._state.lock:
            return self._serial is not None and self._serial.is_open

    def connect(self) -> None:
        with self._state.lock:
            if self.is_connected:
                return
            if self.is_hardware and self.approval is None:
                raise SafetyError("Hardware gate closed: explicit candidate approval required")
            if self._state.owner not in (None, self):
                raise SafetyError("Serial port already owned by another transport")
            self._close()
            for attempt in range(self.config.open_attempts):
                try:
                    self._serial = self._open()
                    if not self._serial.is_open:
                        raise OSError("Port did not open")
                    self._state.owner = self
                    if self.is_hardware:
                        # A new process/owner also waits after opening before its first TX.
                        self._state.next_tx = max(
                            self._state.next_tx, self._clock() + self.config.command_interval
                        )
                    return
                except OSError as exc:
                    self._close()
                    if attempt + 1 == self.config.open_attempts:
                        raise ConnectionError(f"Cannot open {self.config.port}: {exc}") from exc
                    # Only port opening, with no command transmission, is retried automatically.
                    self._sleep(self.config.retry_delay)

    def _open(self) -> SerialLike:
        if self._factory is not None:
            return self._factory(self.config)
        import serial

        port = serial.Serial(port=None)
        try:
            port.port = self.config.port
            port.baudrate, port.bytesize, port.parity, port.stopbits = 9600, 8, "N", 1
            port.xonxoff = port.rtscts = port.dsrdtr = False
            port.rts = port.dtr = False
            if os.name == "posix":
                port.exclusive = True
            port.timeout, port.write_timeout = self.config.timeout, self.config.write_timeout
            port.open()
            return port
        except Exception:
            port.close()
            raise

    def _close(self) -> None:
        port, self._serial = self._serial, None
        try:
            if port is not None:
                port.close()
        finally:
            if self._state.owner is self:
                self._state.owner = None

    def disconnect(self) -> None:
        with self._state.lock:
            try:
                self._close()
            except OSError as exc:
                raise ConnectionError(f"Port close failed: {exc}") from exc

    def confirm_recovery(self, acknowledgement: str) -> None:
        """Operator confirms stream reset/state after an uncertain exchange; sends no bytes."""
        with self._state.lock:
            if acknowledgement != "STREAM RESET AND DEVICE STATE VERIFIED":
                raise SafetyError("Recovery requires stream and physical-state verification")
            if not self.is_connected:
                raise ConnectionError("Open the approved port before confirming recovery")
            assert self._serial is not None
            if self._serial.in_waiting:
                raise ProtocolError("Unexpected buffered bytes; stream has not been reset")
            self._state.needs_recovery = False

    def quarantine(self) -> None:
        """Invalidate a complete but semantically malformed response; never retry it."""
        with self._state.lock:
            self._state.needs_recovery = True
            self.disconnect()

    def exchange(self, request: bytes, reply_length: int) -> bytes:
        """Low-level transport API; device users should use ThermoCube, never raw TX."""
        if (
            type(request) is not bytes
            or not 1 <= len(request) <= 3
            or type(reply_length) is not int
            or reply_length not in (0, 1, 2)
        ):
            raise ValueError("Invalid R2 transaction size")
        with self._state.lock:
            if not self.is_connected:
                raise ConnectionError("Serial port is disconnected")
            if self.needs_recovery:
                raise SafetyError("Stream uncertain; explicit recovery is required")
            if self.is_hardware:
                approval = self.approval
                if approval is None or not approval.binary_framing_confirmed:
                    raise SafetyError("Binary framing is not confirmed for this candidate")
                # Defense at the transport boundary as well as in the semantic driver.
                from thermocube.protocol import Parameter, message, split_command

                bits = split_command(request[0])
                try:
                    valid, expected = message(
                        Parameter(bits.parameter),
                        remote=bits.remote,
                        run=bits.run,
                        write=bits.host_to_controller,
                        data=request[1:],
                    )
                except ValueError as exc:
                    raise SafetyError(
                        "Raw or unsupported hardware commands are prohibited"
                    ) from exc
                if valid != request or expected != reply_length or not bits.remote:
                    raise SafetyError("Hardware request violates the reviewed R2 subset")
                allowed = (
                    approval.allow_control if bits.host_to_controller else approval.allow_queries
                )
                if not allowed or bits.run not in approval.allowed_run_states:
                    raise SafetyError("Transaction exceeds hardware approval")
                if bits.host_to_controller and bits.parameter == Parameter.SETPOINT:
                    from thermocube.protocol import decode_temperature

                    if approval.setpoint_limits is None:
                        raise SafetyError("Approved setpoint limits are missing")
                    approval.setpoint_limits.check(decode_temperature(request[1:]))
            assert self._serial is not None
            port = self._serial
            result = bytearray()
            started_at, started_monotonic = None, None
            try:
                while self._clock() < self._state.next_tx:
                    self._sleep(self._state.next_tx - self._clock())
                if port.in_waiting:
                    raise ProtocolError("Unsolicited/late bytes before transaction")
                started_at, started_monotonic = utc_now(), self._clock()
                self._state.next_tx = self._clock() + self.config.command_interval
                try:
                    count = port.write(request)
                finally:
                    # Includes uncertain or delayed writes; prevents USB/OS queue catch-up.
                    self._state.next_tx = self._clock() + self.config.command_interval
                if self._clock() - started_monotonic > self.config.write_timeout:
                    raise CommunicationTimeout("Write completed after deadline; delivery uncertain")
                if count != len(request):
                    raise ProtocolError(
                        f"Short serial write: {count}/{len(request)}; delivery uncertain"
                    )
                deadline = self._clock() + self.config.timeout
                while len(result) < reply_length:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise CommunicationTimeout(
                            f"Short reply: {len(result)}/{reply_length} bytes"
                        )
                    port.timeout = remaining
                    chunk = port.read(reply_length - len(result))
                    if type(chunk) is not bytes or len(chunk) > reply_length - len(result):
                        raise ProtocolError("Malformed serial response")
                    result.extend(chunk)
                    if self._clock() > deadline:
                        raise CommunicationTimeout(
                            "Read completed after deadline; response uncertain"
                        )
                    if not chunk:
                        self._sleep(min(0.001, max(0, deadline - self._clock())))
                if port.in_waiting:
                    raise ProtocolError("Unexpected bytes after response")
            except (OSError, ConnectionError, ProtocolError) as exc:
                self._state.needs_recovery = True
                self._events.append(
                    Event(
                        utc_now(),
                        "serial",
                        "uncertain",
                        str(exc),
                        request.hex(),
                        result.hex(),
                        started_at,
                        started_monotonic,
                    )
                )
                try:
                    self._close()
                except OSError:
                    pass  # Preserve the original I/O error; the handle is already detached.
                if isinstance(exc, (ConnectionError, ProtocolError)):
                    raise
                from serial import SerialTimeoutException

                if isinstance(exc, SerialTimeoutException):
                    raise CommunicationTimeout(f"Serial write timed out: {exc}") from exc
                raise ConnectionError(f"Serial transaction failed: {exc}") from exc
            self._events.append(
                Event(
                    utc_now(),
                    "serial",
                    "received" if reply_length else "sent-unconfirmed",
                    "",
                    request.hex(),
                    result.hex(),
                    started_at,
                    started_monotonic,
                )
            )
            return bytes(result)

    def drain_events(self) -> tuple[Event, ...]:
        with self._state.lock:
            result = tuple(self._events)
            self._events.clear()
            return result


def discover_ports(*, approval: HardwareApproval | None = None) -> tuple[tuple[str, str], ...]:
    """Enumerate metadata only. Never opens or probes a port."""
    if approval is None:
        raise SafetyError("Port discovery requires hardware-phase approval")
    from serial.tools import list_ports

    return tuple(sorted((p.device, p.description) for p in list_ports.comports()))
