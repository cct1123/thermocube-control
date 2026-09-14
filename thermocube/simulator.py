"""Deterministic first-order thermal simulator. No serial dependency or discovery."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from thermocube.models import (
    CommunicationTimeout,
    ConnectionError,
    Event,
    FaultProfile,
    Faults,
    Mode,
    SafetyError,
    Snapshot,
    TemperatureLimits,
    utc_now,
)
from thermocube.protocol import (
    decode_faults,
    decode_temperature,
    encode_temperature,
    from_celsius,
    to_celsius,
)

DEFAULT_SIMULATOR_LIMITS = TemperatureLimits(-5, 50)


class Simulator:
    def __init__(
        self,
        *,
        initial_c: float = 25,
        setpoint_c: float = 20,
        ambient_c: float = 25,
        time_constant: float = 15,
        max_rate_c_s: float = 2,
        delay: float = 0,
        timeout: float = 0.5,
        limits: TemperatureLimits = DEFAULT_SIMULATOR_LIMITS,
        profile: FaultProfile = FaultProfile.LEGACY,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        for value in (time_constant, max_rate_c_s, timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Simulation time constants/rates/timeouts must be positive")
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Simulation delay must be finite and nonnegative")
        if not isinstance(profile, FaultProfile):
            raise ValueError("Select a known fault profile")
        encode_temperature(initial_c)
        encode_temperature(ambient_c)
        limits.check(setpoint_c)
        self._setpoint = decode_temperature(encode_temperature(setpoint_c))
        limits.check(self._setpoint)
        self._temperature, self._ambient = float(initial_c), float(ambient_c)
        self._tau, self._max_rate = time_constant, max_rate_c_s
        self.delay, self.timeout, self.limits, self.profile = delay, timeout, limits, profile
        self._clock, self._sleep, self._now = clock, sleep, now
        self._last = clock()
        self._connected = self._running = False
        self._fault_byte = 0
        self._timeouts = self._disconnects = 0
        self._lock = threading.RLock()
        self._events: deque[Event] = deque(maxlen=1024)
        self._snapshot = Snapshot(now(), "simulation", mode=Mode.SIMULATION)

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def cached_snapshot(self) -> Snapshot:
        with self._lock:
            return replace(self._snapshot, connected=self._connected)

    def _event(self, operation: str, outcome: str, detail: str = "") -> None:
        self._events.append(Event(self._now(), operation, outcome, detail))

    def connect(self) -> None:
        with self._lock:
            self._connected = True
            self._snapshot = replace(self._snapshot, connected=True, armed=True, error=None)
            self._event("connect", "simulated")

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._snapshot = replace(
                self._snapshot,
                connected=False,
                armed=False,
                responding=False,
                quality="stale",
                run_reported=None,
            )
            self._event("disconnect", "simulated", "Device run state retained")

    def inject_faults(self, raw: int) -> None:
        if type(raw) is not int or not 0 <= raw <= 255:
            raise ValueError("Fault mask must be a byte")
        with self._lock:
            self._advance()
            self._fault_byte = raw

    def inject_timeout(self, count: int = 1) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("Timeout count must be nonnegative")
        with self._lock:
            self._timeouts = count

    def inject_disconnect(self) -> None:
        with self._lock:
            self._disconnects += 1

    def _faults(self) -> Faults:
        raw = self._fault_byte
        if self.profile == FaultProfile.M5:
            raw = (raw & ~64) | (0 if self._running else 64)
        return decode_faults(bytes([raw]), self.profile)

    def _advance(self) -> None:
        current = self._clock()
        dt = max(0.0, current - self._last)
        self._last = current
        active = self._running and not self._faults().has_fault
        target = self._setpoint if active else self._ambient
        tau = self._tau if active else self._tau * 4
        change = (target - self._temperature) * (-math.expm1(-dt / tau))
        self._temperature += max(-self._max_rate * dt, min(self._max_rate * dt, change))

    def _before(self) -> None:
        if not self._connected:
            raise ConnectionError("Simulator disconnected")
        if self._disconnects:
            self._disconnects -= 1
            self.disconnect()
            raise ConnectionError("Injected connection loss")
        self._sleep(min(self.delay, self.timeout))
        if self._timeouts or self.delay > self.timeout:
            self._timeouts = max(0, self._timeouts - 1)
            self._snapshot = replace(self._snapshot, quality="stale", error="Simulated timeout")
            raise CommunicationTimeout("Simulated timeout")
        self._advance()

    def snapshot(self) -> Snapshot:
        with self._lock:
            self._before()
            temperature = encode_temperature(self._temperature)
            setpoint = encode_temperature(self._setpoint)
            active = self._running and not self._faults().has_fault
            delta = self._setpoint - self._temperature
            direction = (
                "idle"
                if not active or abs(delta) < 0.03
                else ("heating" if delta > 0 else "cooling")
            )
            stamp = self._now()
            self._snapshot = Snapshot(
                stamp,
                "simulation",
                connected=True,
                mode=Mode.SIMULATION,
                armed=True,
                responding=True,
                run_intent=self._running,
                run_reported=self._running,
                temperature_c=decode_temperature(temperature),
                setpoint_c=self._setpoint,
                temperature_raw=int.from_bytes(temperature, "little"),
                setpoint_raw=int.from_bytes(setpoint, "little"),
                temperature_at=stamp,
                setpoint_at=stamp,
                faults_at=stamp,
                faults=self._faults(),
                thermal_direction=direction,
                quality="good",
            )
            return self._snapshot

    def read_temperature(self, unit: str = "C") -> float:
        if unit not in ("C", "F"):
            raise ValueError("Unit must be C or F")
        value = self.snapshot().temperature_c
        assert value is not None
        return from_celsius(value, unit)

    def read_setpoint(self, unit: str = "C") -> float:
        if unit not in ("C", "F"):
            raise ValueError("Unit must be C or F")
        value = self.snapshot().setpoint_c
        assert value is not None
        return from_celsius(value, unit)

    def read_faults(self) -> Faults:
        faults = self.snapshot().faults
        assert faults is not None
        return faults

    def set_setpoint(self, value: float, unit: str = "C") -> float:
        with self._lock:
            self.limits.check(to_celsius(value, unit))
            effective = decode_temperature(encode_temperature(value, unit))
            self.limits.check(effective)
            self._before()
            if self._faults().has_fault:
                raise SafetyError("Simulated setpoint refused while faults are active")
            self._setpoint = effective
            self._event("set_setpoint", "simulated", f"{value} {unit}; effective {effective} C")
            return from_celsius(effective, unit)

    def start(self) -> None:
        with self._lock:
            self._before()
            if self._faults().has_fault:
                raise SafetyError("Simulated start refused while faults are active")
            self._running = True
            self._event("start", "simulated")

    def stop(self) -> None:
        with self._lock:
            self._before()
            self._running = False
            self._event("stop", "simulated")

    def drain_events(self) -> tuple[Event, ...]:
        with self._lock:
            result = tuple(self._events)
            self._events.clear()
            return result
