"""Reusable R2 driver. Queries actively assert the explicitly selected run state."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from thermocube.models import (
    ConnectionError,
    Event,
    FaultProfile,
    Faults,
    Mode,
    ProtocolError,
    SafetyError,
    SetpointRejected,
    Snapshot,
    TemperatureLimits,
    utc_now,
)
from thermocube.protocol import (
    Parameter,
    decode_faults,
    decode_temperature,
    encode_temperature,
    from_celsius,
    message,
    to_celsius,
)
from thermocube.transport import SerialTransport

QUERY_ACK = "QUERIES ASSERT REMOTE AND RUN STATE"
CONTROL_ACK = "ENABLE SETPOINT START AND STOP CONTROL"
DEFAULT_MEASUREMENT_LIMITS = TemperatureLimits(-20, 100)


class ThermoCube:
    def __init__(
        self,
        transport: SerialTransport,
        *,
        profile: FaultProfile = FaultProfile.LEGACY,
        limits: TemperatureLimits | None = None,
        mode: Mode = Mode.OBSERVE,
        measurement_limits: TemperatureLimits = DEFAULT_MEASUREMENT_LIMITS,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        if mode not in (Mode.OBSERVE, Mode.TRANSPORT):
            raise SafetyError("Construct in constrained-query or transport-only mode")
        if not isinstance(profile, FaultProfile):
            raise ValueError("Unknown fault profile")
        transport.bind_driver()
        self._transport, self._profile, self._limits = transport, profile, limits
        self._measurement_limits, self._now = measurement_limits, now
        self._lock = threading.RLock()
        self._mode: Mode = mode
        self._armed = False
        self._run: bool | None = None
        self._events: deque[Event] = deque(maxlen=1024)
        self._snapshot = Snapshot(
            now(), "hardware" if transport.is_hardware else "fake-serial", mode=mode
        )

    @property
    def profile(self) -> FaultProfile:
        return self._profile

    @property
    def limits(self) -> TemperatureLimits | None:
        return self._limits

    @property
    def is_connected(self) -> bool:
        return self._transport.is_connected

    @property
    def cached_snapshot(self) -> Snapshot:
        with self._lock:
            return replace(
                self._snapshot, connected=self.is_connected, mode=self._mode, armed=self._armed
            )

    def _event(self, operation: str, outcome: str, detail: str = "") -> None:
        self._events.append(Event(self._now(), operation, outcome, detail))

    def connect(self) -> None:
        with self._lock:
            if self.is_connected:
                return
            approval = self._transport.approval
            if (
                self._transport.is_hardware
                and approval is not None
                and approval.profile != self.profile
            ):
                raise SafetyError("Selected fault profile differs from approved candidate")
            self._transport.connect()
            self._armed = False
            self._run = None
            if self._mode != Mode.TRANSPORT:
                self._mode = Mode.OBSERVE
            self._snapshot = replace(
                self._snapshot,
                timestamp=self._now(),
                connected=True,
                responding=False,
                run_intent=None,
                run_reported=None,
                quality="unavailable",
                error=None,
            )
            self._event("connect", "port-open-unverified")

    def disconnect(self) -> None:
        with self._lock:
            try:
                self._transport.disconnect()
            finally:
                self._disarm()
                self._snapshot = replace(
                    self._snapshot,
                    timestamp=self._now(),
                    connected=False,
                    responding=False,
                    run_reported=None,
                    quality="stale",
                )
                self._event("disconnect", "closed", "No implicit device stop")

    def _disarm(self) -> None:
        self._armed = False
        self._run = None
        if self._mode != Mode.TRANSPORT:
            self._mode = Mode.OBSERVE

    def arm_queries(self, *, run: bool, acknowledgement: str) -> None:
        with self._lock:
            if self._mode == Mode.TRANSPORT:
                raise SafetyError("Transport-only mode cannot send device queries")
            if type(run) is not bool or acknowledgement != QUERY_ACK:
                raise SafetyError("Acknowledge the established remote/run state before querying")
            if not self.is_connected:
                raise ConnectionError("Connect before arming queries")
            if self._transport.needs_recovery:
                raise SafetyError("Uncertain serial stream requires explicit recovery")
            approval = self._transport.approval
            if self._transport.is_hardware and (
                approval is None
                or not approval.allow_queries
                or not approval.binary_framing_confirmed
                or run not in approval.allowed_run_states
            ):
                raise SafetyError("Query state/framing is not approved")
            if self._armed and self._run != run:
                raise SafetyError("Disarm before establishing a different query context")
            self._run, self._armed = run, True
            self._snapshot = replace(self._snapshot, run_intent=run, error=None)
            self._event("arm_queries", "armed", f"remote=True, run={run}")

    def disarm(self) -> None:
        with self._lock:
            self._disarm()
            self._snapshot = replace(self._snapshot, run_intent=None, quality="stale")
            self._event("disarm", "locked")

    def confirm_recovery(self, acknowledgement: str) -> None:
        with self._lock:
            self._transport.confirm_recovery(acknowledgement)
            self._event("recovery", "operator-confirmed")

    def enable_control(self, acknowledgement: str) -> None:
        with self._lock:
            self._require_query()
            if acknowledgement != CONTROL_ACK or self.limits is None:
                raise SafetyError("Control requires explicit acknowledgement and setpoint limits")
            approval = self._transport.approval
            if self._transport.is_hardware:
                if (
                    approval is None
                    or not approval.allow_control
                    or approval.setpoint_limits is None
                ):
                    raise SafetyError("Write-enabled hardware control is not approved")
                approval.setpoint_limits.check(self.limits.minimum_c)
                approval.setpoint_limits.check(self.limits.maximum_c)
            self._mode = Mode.CONTROL
            self._event("enable_control", "enabled")

    def _require_query(self) -> None:
        if not self.is_connected:
            raise ConnectionError("Device is disconnected")
        if not self._armed or self._run is None or self._mode == Mode.TRANSPORT:
            raise SafetyError("Queries require an explicit remote/run context")

    def _require_control(self) -> None:
        if self._mode != Mode.CONTROL:
            raise SafetyError("Setpoint/start/stop controls are locked")
        if not self.is_connected:
            raise ConnectionError("Device is disconnected")

    def _transaction(
        self, parameter: Parameter, *, write: bool = False, data: bytes = b""
    ) -> bytes:
        assert self._run is not None
        tx, length = message(parameter, remote=True, run=self._run, write=write, data=data)
        try:
            return self._transport.exchange(tx, length)
        except (ConnectionError, ProtocolError) as exc:
            self._disarm()
            self._snapshot = replace(
                self._snapshot,
                timestamp=self._now(),
                connected=False,
                responding=False,
                run_intent=None,
                run_reported=None,
                quality="stale",
                error=str(exc),
            )
            raise

    def _temperature(self, parameter: Parameter, unit: str) -> float:
        if unit not in ("C", "F"):
            raise ValueError("Unit must be C or F")
        self._require_query()
        data = self._transaction(parameter)
        value_c = decode_temperature(data)
        try:
            self._measurement_limits.check(value_c)
        except ValueError as exc:
            self._disarm()
            self._transport.quarantine()
            self._snapshot = replace(
                self._snapshot,
                connected=False,
                responding=False,
                run_reported=None,
                quality="invalid",
                error=f"Implausible temperature: {value_c} C",
            )
            raise ProtocolError(self._snapshot.error) from exc
        self._snapshot = replace(self._snapshot, responding=True, timestamp=self._now(), error=None)
        if parameter == Parameter.TEMPERATURE:
            self._snapshot = replace(
                self._snapshot,
                temperature_c=value_c,
                temperature_raw=int.from_bytes(data, "little"),
                temperature_at=self._now(),
            )
        else:
            self._snapshot = replace(
                self._snapshot,
                setpoint_c=value_c,
                setpoint_raw=int.from_bytes(data, "little"),
                setpoint_at=self._now(),
            )
        return decode_temperature(data, unit)

    def read_temperature(self, unit: str = "C") -> float:
        with self._lock:
            return self._temperature(Parameter.TEMPERATURE, unit)

    def read_setpoint(self, unit: str = "C") -> float:
        with self._lock:
            return self._temperature(Parameter.SETPOINT, unit)

    def read_faults(self) -> Faults:
        with self._lock:
            self._require_query()
            faults = decode_faults(self._transaction(Parameter.FAULTS), self.profile)
            reported = None if faults.standby is None else not faults.standby
            mismatch = reported is not None and reported != self._run
            error = "Run state differs from intent" if mismatch else None
            self._snapshot = replace(
                self._snapshot,
                timestamp=self._now(),
                faults=faults,
                faults_at=self._now(),
                run_reported=reported,
                responding=True,
                error=error,
            )
            if faults.has_fault or mismatch:
                # Stop polling/reasserting run; retain explicit control permission for STOP only.
                self._armed = False
                self._snapshot = replace(self._snapshot, quality="faulted")
            return faults

    def set_setpoint(self, value: float, unit: str = "C") -> float:
        with self._lock:
            self._require_control()
            self._require_query()
            assert self.limits is not None
            self.limits.check(to_celsius(value, unit))
            data = encode_temperature(value, unit)
            effective_c = decode_temperature(data)
            self.limits.check(effective_c)
            if self.read_faults().has_fault or not self._armed:
                raise SafetyError("Setpoint refused because faults or a state mismatch are present")
            self._event("set_setpoint", "requested", f"{value} {unit}; effective {effective_c} C")
            self._transaction(Parameter.SETPOINT, write=True, data=data)
            self.read_setpoint()
            if self._snapshot.setpoint_raw != int.from_bytes(data, "little"):
                self._armed = False
                self._event("set_setpoint", "rejected", "Readback differs; no retry")
                raise SetpointRejected("Setpoint readback differs from requested wire value")
            self._event("set_setpoint", "confirmed", f"{effective_c} C")
            return from_celsius(effective_c, unit)

    def start(self) -> None:
        with self._lock:
            self._require_control()
            self._require_query()
            self._check_run_permission(True)
            if self.read_faults().has_fault or not self._armed:
                raise SafetyError("Start refused because faults or a state mismatch are present")
            self._run = True
            self._transaction(Parameter.CONTROL, write=True)
            self._snapshot = replace(self._snapshot, run_intent=True, run_reported=None)
            self._event("start", "sent-unconfirmed")

    def stop(self) -> None:
        with self._lock:
            self._require_control()
            self._check_run_permission(False)
            self._run = False
            self._transaction(Parameter.CONTROL, write=True)
            # Explicit re-arming is necessary if a fault had previously inhibited polling.
            self._snapshot = replace(self._snapshot, run_intent=False, run_reported=None)
            self._event("stop", "sent-unconfirmed", "Temperature-control standby, not power-off")

    def _check_run_permission(self, run: bool) -> None:
        approval = self._transport.approval
        if self._transport.is_hardware and (
            approval is None or run not in approval.allowed_run_states
        ):
            raise SafetyError("Requested run state is outside approval")

    def snapshot(self) -> Snapshot:
        with self._lock:
            self.read_faults()
            if self._armed:
                self.read_temperature()
                self.read_setpoint()
            self._snapshot = replace(
                self._snapshot,
                timestamp=self._now(),
                connected=True,
                mode=self._mode,
                armed=self._armed,
                run_intent=self._run,
                quality="good" if self._armed else "faulted",
            )
            return self._snapshot

    def drain_events(self) -> tuple[Event, ...]:
        with self._lock:
            result = tuple(self._events) + self._transport.drain_events()
            self._events.clear()
            return tuple(sorted(result, key=lambda event: event.timestamp))

    def __enter__(self) -> ThermoCube:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.disconnect()
