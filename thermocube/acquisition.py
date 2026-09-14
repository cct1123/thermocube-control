"""Browser-independent device ownership, command queue, history, logging and recovery."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from uuid import uuid4

from thermocube.driver import ThermoCube
from thermocube.logging import CsvLogger
from thermocube.models import Device, Event, SafetyError, Snapshot, utc_now


@dataclass(frozen=True)
class CommandResult:
    request_id: str
    operation: str
    state: str
    detail: str = ""


class AcquisitionService:
    SHUTDOWN_OPERATIONS = frozenset({"stop", "disconnect", "disarm"})
    OPERATIONS = frozenset(
        {
            "connect",
            "disconnect",
            "set_setpoint",
            "start",
            "stop",
            "arm_queries",
            "enable_control",
            "disarm",
            "confirm_recovery",
        }
    )

    def __init__(
        self,
        device: Device,
        *,
        interval: float = 1.05,
        history_size: int = 3600,
        queue_size: int = 32,
        logger: CsvLogger | None = None,
        reconnect_initial: float = 0.5,
        reconnect_max: float = 10,
    ) -> None:
        if not math.isfinite(interval) or interval < 1:
            raise ValueError("Acquisition interval must be at least one second")
        if any(type(v) is not int or v < 1 for v in (history_size, queue_size)):
            raise ValueError("History and queue sizes must be positive integers")
        if not 0 < reconnect_initial <= reconnect_max or not math.isfinite(reconnect_max):
            raise ValueError("Reconnect backoff must be finite and positive")
        self.device, self.interval, self.logger = device, interval, logger
        self._history: deque[Snapshot] = deque(maxlen=history_size)
        self._pending: deque[tuple[str, str, dict[str, object]]] = deque()
        self._results: OrderedDict[str, CommandResult] = OrderedDict()
        self._queue_size = queue_size
        self._audit: deque[Event] = deque(maxlen=4 * queue_size + 128)
        self._lock = threading.RLock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._wanted_connection = False
        self._latest = device.cached_snapshot
        self._logging_error: str | None = None
        self._service_error: str | None = None
        self._reconnect_initial, self._reconnect_max = reconnect_initial, reconnect_max
        self._backoff, self._next_reconnect = reconnect_initial, 0.0

    @property
    def latest(self) -> Snapshot:
        with self._lock:
            return self._latest

    @property
    def history(self) -> tuple[Snapshot, ...]:
        with self._lock:
            return tuple(self._history)

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._logging_error or self._service_error

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, *, connect: bool = False) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Service is closed")
            if self.is_running:
                return
            self._wanted_connection = connect
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="thermocube-acquisition", daemon=False
            )
            self._thread.start()

    def submit(self, operation: str, **kwargs: object) -> str:
        if operation not in self.OPERATIONS:
            raise ValueError("Unsupported service operation")
        with self._lock:
            if self._closed or self._stop.is_set() or not self.is_running:
                raise RuntimeError("Acquisition service is not running")
            if self._logging_error and operation not in self.SHUTDOWN_OPERATIONS:
                raise SafetyError("Required CSV logging failed; new control work is inhibited")
            if operation in self.SHUTDOWN_OPERATIONS:
                self._cancel_pending("Cancelled by shutdown request", preserve_shutdown=True)
                for key, pending_operation, _ in self._pending:
                    if pending_operation == operation:
                        return key  # Repeated shutdown clicks do not grow the queue.
            elif len(self._pending) >= self._queue_size:
                raise RuntimeError("Command queue is full")
            request_id = uuid4().hex
            self._results[request_id] = CommandResult(request_id, operation, "pending")
            item = (request_id, operation, kwargs)
            if operation == "stop":
                self._pending.appendleft(item)
            else:
                self._pending.append(item)
            self._trim_results()
            self._queue_event(Event(utc_now(), operation, "queued", request_id))
            self._wake.set()
            return request_id

    def result(self, request_id: str) -> CommandResult | None:
        with self._lock:
            return self._results.get(request_id)

    def _trim_results(self) -> None:
        for key in list(self._results):
            if len(self._results) <= self._queue_size + 128:
                break
            if self._results[key].state not in {"pending", "running"}:
                del self._results[key]

    def _cancel_pending(self, detail: str, *, preserve_shutdown: bool = False) -> None:
        retained: deque[tuple[str, str, dict[str, object]]] = deque()
        while self._pending:
            item = self._pending.popleft()
            key, operation, _ = item
            if preserve_shutdown and operation in self.SHUTDOWN_OPERATIONS:
                retained.append(item)
                continue
            self._results[key] = CommandResult(key, operation, "cancelled", detail)
            self._queue_event(Event(utc_now(), operation, "cancelled", f"{key}: {detail}"))
        self._pending.extend(retained)

    def _execute(self, operation: str, args: dict[str, object]) -> object:
        if operation == "connect":
            self._wanted_connection = True
            self.device.connect()
            return None
        if operation == "disconnect":
            self._wanted_connection = False
            self.device.disconnect()
            self._service_error = None
            return None
        if operation == "set_setpoint":
            value = args.get("value")
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError("Enter a numeric setpoint")
            return self.device.set_setpoint(value, str(args.get("unit", "C")))
        if operation == "start":
            self.device.start()
            return None
        if operation == "stop":
            self.device.stop()
            return None
        if not isinstance(self.device, ThermoCube):
            raise SafetyError("Hardware arming is not needed in simulation")
        if operation == "arm_queries":
            run = args.get("run")
            if type(run) is not bool:
                raise ValueError("Select the explicitly established run state")
            self.device.arm_queries(run=run, acknowledgement=str(args.get("acknowledgement", "")))
            return None
        if operation == "enable_control":
            self.device.enable_control(str(args.get("acknowledgement", "")))
            return None
        if operation == "confirm_recovery":
            self.device.confirm_recovery(str(args.get("acknowledgement", "")))
            return None
        self.device.disarm()
        return None

    def _log_event(self, event: Event) -> None:
        if self.logger and not self._logging_error:
            try:
                self.logger.event(event, self.device.cached_snapshot.source)
            except (OSError, ValueError) as exc:
                self._logging_error = f"CSV logging failed: {exc}"

    def _queue_event(self, event: Event) -> None:
        # Called under the service lock; GUI submissions never wait on disk I/O.
        if self.logger is None:
            return
        if len(self._audit) == self._audit.maxlen:
            self._logging_error = "Audit queue full; controls and polling inhibited"
            return
        self._audit.append(event)

    def _flush_audit(self) -> None:
        with self._lock:
            events = tuple(self._audit)
            self._audit.clear()
        for event in events:
            self._log_event(event)

    def _commands(self) -> None:
        with self._lock:
            if self._stop.is_set():
                self._cancel_pending("Service closing")
                return
            if not self._pending:
                return
            key, operation, args = self._pending.popleft()
            self._results[key] = CommandResult(key, operation, "running")
        self._log_event(Event(utc_now(), operation, "requested", key))
        try:
            if self._logging_error and operation not in self.SHUTDOWN_OPERATIONS:
                raise SafetyError(self._logging_error)
            result = self._execute(operation, args)
            outcome, detail = "completed", "" if result is None else str(result)
        except Exception as exc:
            outcome, detail = "failed", f"{type(exc).__name__}: {exc}"
            with self._lock:
                self._cancel_pending("Cancelled after a failed operation", preserve_shutdown=True)
        with self._lock:
            self._results[key] = CommandResult(key, operation, outcome, detail)
            self._latest = self.device.cached_snapshot
        self._log_event(Event(utc_now(), operation, outcome, f"{key}: {detail}"))

    def _sample(self) -> None:
        try:
            if self._logging_error:
                snapshot = replace(
                    self.device.cached_snapshot,
                    timestamp=utc_now(),
                    quality="paused",
                    error=self._logging_error,
                )
            elif not self._wanted_connection and not self.device.is_connected:
                snapshot = replace(
                    self.device.cached_snapshot,
                    timestamp=utc_now(),
                    quality="disconnected",
                    error=None,
                )
            else:
                snapshot = self.device.snapshot()
        except Exception as exc:
            snapshot = replace(
                self.device.cached_snapshot,
                timestamp=utc_now(),
                connected=self.device.is_connected,
                quality="stale",
                error=f"{type(exc).__name__}: {exc}",
            )
            with self._lock:
                self._cancel_pending("Cancelled after acquisition failure", preserve_shutdown=True)
        with self._lock:
            self._latest = snapshot
            self._history.append(snapshot)
        if self.logger and not self._logging_error:
            try:
                self.logger.sample(snapshot)
            except (OSError, ValueError) as exc:
                self._logging_error = f"CSV logging failed: {exc}"

    def _run(self) -> None:
        next_sample = time.monotonic()
        try:
            while not self._stop.is_set():
                self._wake.clear()
                self._flush_audit()
                self._commands()
                current = time.monotonic()
                if (
                    self._wanted_connection
                    and not self._logging_error
                    and not self.device.is_connected
                    and current >= self._next_reconnect
                ):
                    try:
                        self.device.connect()  # Opens only; hardware queries remain disarmed.
                        self._backoff = self._reconnect_initial
                    except Exception as exc:
                        self._service_error = f"Reconnect failed: {exc}"
                    else:
                        self._service_error = None
                    self._next_reconnect = time.monotonic() + self._backoff
                    self._backoff = min(self._backoff * 2, self._reconnect_max)
                with self._lock:
                    queued = bool(self._pending)
                if current >= next_sample and not queued:
                    self._sample()
                    next_sample = max(current + self.interval, time.monotonic())
                for event in self.device.drain_events():
                    self._log_event(event)
                with self._lock:
                    if self._pending:
                        continue
                self._wake.wait(min(0.25, max(0, next_sample - time.monotonic())))
        except Exception as exc:
            self._service_error = f"Acquisition worker stopped: {type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._cancel_pending("Service stopped")
            self._flush_audit()
            try:
                self.device.disconnect()
                for event in self.device.drain_events():
                    self._log_event(event)
            except Exception as exc:
                self._service_error = f"Disconnect failed: {exc}"
            if self.logger:
                try:
                    self.logger.close()
                except OSError as exc:
                    self._logging_error = f"CSV close failed: {exc}"
            with self._lock:
                self._latest = self.device.cached_snapshot
                self._closed = True

    def close(self, timeout: float = 5) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Shutdown timeout must be positive")
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("Worker has not stopped; device I/O may still be in flight")
        else:
            try:
                self.device.disconnect()
            finally:
                if self.logger:
                    self.logger.close()
                self._closed = True
