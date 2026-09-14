"""Small immutable API values shared by hardware, simulation, service, and UI."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol


def utc_now() -> datetime:
    return datetime.now(UTC)


class ThermoCubeError(Exception):
    """Base class for actionable device/application errors."""


class SafetyError(ThermoCubeError):
    pass


class ConnectionError(ThermoCubeError):
    pass


class ProtocolError(ThermoCubeError):
    pass


class CommunicationTimeout(ConnectionError):
    pass


class SetpointRejected(ThermoCubeError):
    pass


class Mode(StrEnum):
    SIMULATION = "simulation"
    TRANSPORT = "transport-only"
    OBSERVE = "constrained-query"
    CONTROL = "control-enabled"


class FaultProfile(StrEnum):
    LEGACY = "legacy-r2"
    M5 = "thermocube-ii-m5"


@dataclass(frozen=True)
class TemperatureLimits:
    minimum_c: float
    maximum_c: float

    def __post_init__(self) -> None:
        if not all(
            type(v) in (int, float) and math.isfinite(v) for v in (self.minimum_c, self.maximum_c)
        ):
            raise ValueError("Temperature limits must be finite")
        if self.minimum_c >= self.maximum_c:
            raise ValueError("Minimum temperature must be less than maximum")

    def check(self, value_c: float) -> None:
        if not math.isfinite(value_c) or not (self.minimum_c <= value_c <= self.maximum_c):
            raise ValueError(f"Temperature must be within {self.minimum_c}..{self.maximum_c} C")


@dataclass(frozen=True)
class Faults:
    raw: int
    profile: FaultProfile
    active: tuple[str, ...]
    unknown_mask: int
    standby: bool | None

    @property
    def has_fault(self) -> bool:
        return bool(self.active or self.unknown_mask)


@dataclass(frozen=True)
class Snapshot:
    timestamp: datetime
    source: str
    connected: bool = False
    mode: Mode = Mode.OBSERVE
    armed: bool = False
    responding: bool = False
    run_intent: bool | None = None
    run_reported: bool | None = None
    temperature_c: float | None = None
    setpoint_c: float | None = None
    temperature_raw: int | None = None
    setpoint_raw: int | None = None
    temperature_at: datetime | None = None
    setpoint_at: datetime | None = None
    faults_at: datetime | None = None
    faults: Faults | None = None
    thermal_direction: str = "unknown"
    quality: Literal[
        "unavailable", "good", "faulted", "stale", "invalid", "disconnected", "paused"
    ] = "unavailable"
    error: str | None = None


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    operation: str
    outcome: str
    detail: str = ""
    tx_hex: str = ""
    rx_hex: str = ""
    tx_started_at: datetime | None = None
    tx_monotonic_s: float | None = None


class Device(Protocol):
    @property
    def is_connected(self) -> bool: ...
    @property
    def cached_snapshot(self) -> Snapshot: ...
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def read_temperature(self, unit: str = "C") -> float: ...
    def read_setpoint(self, unit: str = "C") -> float: ...
    def set_setpoint(self, value: float, unit: str = "C") -> float: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def snapshot(self) -> Snapshot: ...
    def read_faults(self) -> Faults: ...
    def drain_events(self) -> tuple[Event, ...]: ...
