"""Small first-order thermal model implementing the controller's device operations."""

from __future__ import annotations

import math
import threading
import time
from datetime import UTC, datetime

from thermocube.controller import (
    FAULT_NAMES,
    Faults,
    SafetyError,
    Status,
    decode_temperature,
    encode_temperature,
    setpoint_payload,
    to_celsius,
    validate_limits,
)


class Simulator:
    def __init__(
        self,
        *,
        initial_c: float = 25,
        setpoint_c: float = 20,
        ambient_c: float = 25,
        time_constant: float = 15,
        delay: float = 0,
        timeout: float = 0.5,
        limits_c: tuple[float, float] = (-5, 50),
        profile: str = "thermocube-ii-m5",
    ) -> None:
        for value in (time_constant, timeout, delay):
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Simulation timing must be finite")
        if time_constant <= 0 or timeout <= 0 or delay < 0:
            raise ValueError("Invalid simulation timing")
        if profile not in FAULT_NAMES:
            raise ValueError("Unknown fault profile")
        limits = validate_limits(limits_c)
        self._limits, self._profile = limits, profile
        self._temperature, self._ambient = to_celsius(initial_c), to_celsius(ambient_c)
        encode_temperature(initial_c)
        encode_temperature(ambient_c)
        self._setpoint = decode_temperature(setpoint_payload(setpoint_c, "C", limits))
        self._tau, self._delay, self._timeout = time_constant, delay, timeout
        self._connected = self._running = False
        self._fault_byte = 0
        self._timeout_once = self._disconnect_once = False
        self._last = time.monotonic()
        self._lock = threading.RLock()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def queries_enabled(self) -> bool:
        return self._connected

    @property
    def control_enabled(self) -> bool:
        return self._connected

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False  # Device thermal/run state persists across link loss.

    def inject_faults(self, raw: int) -> None:
        Faults(raw, self._profile)
        with self._lock:
            self._advance()
            self._fault_byte = raw

    def inject_timeout(self) -> None:
        with self._lock:
            self._timeout_once = True

    def inject_disconnect(self) -> None:
        with self._lock:
            self._disconnect_once = True

    def _faults(self) -> Faults:
        raw = self._fault_byte
        if self._profile == "thermocube-ii-m5":
            raw = (raw & ~64) | (0 if self._running else 64)
        return Faults(raw, self._profile)

    def _advance(self) -> None:
        now = time.monotonic()
        elapsed, self._last = max(0, now - self._last), now
        active = self._running and not self._faults().has_fault
        target = self._setpoint if active else self._ambient
        tau = self._tau if active else self._tau * 4
        self._temperature += (target - self._temperature) * -math.expm1(-elapsed / tau)

    def _before(self) -> None:
        if not self._connected:
            raise ConnectionError("Simulator disconnected")
        if self._disconnect_once:
            self._disconnect_once = False
            self._connected = False
            raise ConnectionError("Injected connection loss")
        time.sleep(min(self._delay, self._timeout))
        if self._timeout_once or self._delay > self._timeout:
            self._timeout_once = False
            self._connected = False
            raise TimeoutError("Simulated timeout; reconnect explicitly")
        self._advance()

    def read_temperature(self, unit: str = "C") -> float:
        with self._lock:
            if unit not in ("C", "F"):
                raise ValueError("Unit must be C or F")
            self._before()
            return decode_temperature(encode_temperature(self._temperature), unit)

    def read_setpoint(self, unit: str = "C") -> float:
        with self._lock:
            if unit not in ("C", "F"):
                raise ValueError("Unit must be C or F")
            self._before()
            return decode_temperature(encode_temperature(self._setpoint), unit)

    def read_faults(self) -> Faults:
        with self._lock:
            self._before()
            return self._faults()

    def set_setpoint(self, value: float, unit: str = "C") -> float:
        with self._lock:
            data = setpoint_payload(value, unit, self._limits)
            self._before()
            if self._faults().has_fault:
                raise SafetyError("Setpoint refused while faults are active")
            self._setpoint = decode_temperature(data)
            return decode_temperature(data, unit)

    def start(self) -> None:
        with self._lock:
            self._before()
            if self._faults().has_fault:
                raise SafetyError("Start refused while faults are active")
            self._running = True

    def stop(self) -> None:
        with self._lock:
            self._before()
            self._running = False

    def status(self) -> Status:
        with self._lock:
            stamp = datetime.now(UTC)
            self._before()
            faults = self._faults()
            temperature = (
                None
                if faults.has_fault
                else decode_temperature(encode_temperature(self._temperature))
            )
            return Status(
                stamp,
                temperature,
                None if faults.has_fault else self._setpoint,
                faults,
                self._running,
            )

    def __enter__(self) -> Simulator:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()
