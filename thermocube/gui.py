"""Optional monitoring, CSV and local GUI. Only create_app imports Dash."""

from __future__ import annotations

import argparse
import csv
import math
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from thermocube.controller import SafetyError, Status, ThermoCube
from thermocube.simulator import Simulator

if TYPE_CHECKING:
    from dash import Dash


class Monitor:
    def __init__(
        self,
        device: ThermoCube | Simulator,
        *,
        interval: float = 1.05,
        history_size: int = 3600,
        csv_path: str | Path | None = None,
    ) -> None:
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 1:
            raise ValueError("Monitoring interval must be at least one second")
        if type(history_size) is not int or history_size < 1:
            raise ValueError("History size must be a positive integer")
        self.device = device
        self._interval, self._csv_path = interval, csv_path
        self._history: deque[tuple[datetime, Status | None]] = deque(maxlen=history_size)
        self._latest: Status | None = None
        self._error: str | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._file: TextIO | None = None

    @property
    def latest(self) -> Status | None:
        with self._lock:
            return self._latest

    @property
    def history(self) -> tuple[tuple[datetime, Status | None], ...]:
        with self._lock:
            return tuple(self._history)

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start one explicitly owned worker. This monitor is single-use."""
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                raise RuntimeError("Create a new monitor after stopping")
            if self._csv_path is not None:
                self._file = Path(self._csv_path).open("x", newline="", encoding="utf-8")
            self._thread = threading.Thread(target=self._run, name="thermocube-monitor")
            try:
                self._thread.start()
            except Exception:
                if self._file:
                    self._file.close()
                raise

    def _run(self) -> None:
        try:
            writer = csv.writer(self._file) if self._file else None
            if writer:
                writer.writerow(
                    (
                        "timestamp_utc",
                        "connected",
                        "temperature_c",
                        "setpoint_c",
                        "fault_profile",
                        "faults_hex",
                        "unknown_hex",
                        "active_faults",
                        "requested_run",
                        "reported_run",
                        "error",
                    )
                )
                assert self._file is not None
                self._file.flush()
            while not self._stop.is_set():
                sample, error = None, None
                started = datetime.now(UTC)
                started_tick = time.monotonic()
                try:
                    if self.device.queries_enabled:
                        sample = self.device.status()
                    else:
                        error = "Queries disarmed" if self.device.is_connected else "Disconnected"
                except (OSError, SafetyError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                stamp = sample.timestamp if sample else started
                with self._lock:
                    # Keep the last fault visible while queries are inhibited; its age never resets.
                    if sample or not self.device.is_connected or self.device.queries_enabled:
                        self._latest = sample
                    self._error = error
                    self._history.append((stamp, sample))
                if writer:
                    faults = sample.faults if sample else None
                    writer.writerow(
                        (
                            stamp.isoformat(),
                            self.device.is_connected,
                            sample.temperature_c if sample else None,
                            sample.setpoint_c if sample else None,
                            faults.profile if faults else None,
                            f"{faults.raw:02X}" if faults else None,
                            f"{faults.unknown_mask:02X}" if faults else None,
                            "|".join(faults.active) if faults else None,
                            sample.requested_run if sample else None,
                            sample.reported_run if sample else None,
                            error,
                        )
                    )
                    assert self._file is not None
                    self._file.flush()
                # No catch-up polling after a slow sample.
                elapsed = time.monotonic() - started_tick
                self._stop.wait(max(0, self._interval - elapsed))
        except Exception as exc:
            with self._lock:
                self._latest = None
                self._error = f"Monitor stopped: {type(exc).__name__}: {exc}"
            self._stop.set()  # Includes CSV failure: no further state-asserting queries.
        finally:
            if self._file:
                try:
                    self._file.close()
                except OSError as exc:
                    with self._lock:
                        self._error = f"CSV close failed: {exc}"

    def stop(self, timeout: float = 5) -> None:
        """Join the worker; does not stop, disarm or disconnect the device."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Shutdown timeout must be finite and positive")
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("Monitor I/O is still in flight; device ownership is retained")

    def __enter__(self) -> Monitor:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


def presentation(
    sample: Status | None,
    *,
    connected: bool,
    queries: bool,
    control: bool,
    error: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    fresh = bool(sample and connected and 0 <= (current - sample.timestamp).total_seconds() <= 3.5)
    names = list(sample.faults.active) if sample else []
    if sample and sample.faults.unknown_mask:
        names.append(f"unknown bits 0x{sample.faults.unknown_mask:02X}")
    mismatch = bool(
        sample and sample.reported_run is not None and sample.reported_run != sample.requested_run
    )
    if mismatch:
        names.append("reported state differs from requested state")
    state = "UNKNOWN"
    if sample and fresh:
        run = sample.reported_run if sample.reported_run is not None else sample.requested_run
        if run is not None:
            state = "RUN" if run else "STANDBY"
            if sample.reported_run is None:
                state += " REQUESTED · UNVERIFIED"
    complete = bool(
        fresh and sample and sample.temperature_c is not None and sample.setpoint_c is not None
    )
    return {
        "connection": "DISCONNECTED"
        if not connected
        else ("CONNECTED" if fresh else "PORT OPEN · DATA UNAVAILABLE"),
        "run": state,
        "temperature": f"{sample.temperature_c:.2f} °C" if complete and sample else "—",
        "setpoint": f"{sample.setpoint_c:.2f} °C" if complete and sample else "—",
        "faults": "; ".join(names) + (" · last observation is stale" if not fresh else "")
        if names
        else ("No reported faults" if fresh else "Fault state unknown / stale"),
        "fault_class": "fault alarm" if names else "fault",
        "disabled": not (complete and queries and control and not names and not error),
        "stop_disabled": not (connected and control),
    }


def create_app(monitor: Monitor) -> Dash:
    """Compose the UI without connecting a device or starting a worker."""
    from dash import Dash, Input, Output, State, ctx, dcc, html

    device = monitor.device
    app = Dash(__name__, assets_folder=str(Path(__file__).parent / "assets"), title="ThermoCube")
    app.layout = html.Main(
        [
            html.Header(
                [html.H1("ThermoCube"), html.Span(type(device).__name__, className="badge")]
            ),
            html.Div(
                [
                    html.Span(id="connection", className="badge"),
                    html.Span(id="run", className="badge"),
                ]
            ),
            html.P(
                "R2 queries assert REMOTE and RUN/STANDBY. Disconnecting or closing a browser does not stop the chiller.",
                className="warning",
            ),
            html.Div(id="faults", className="fault", role="alert"),
            html.Div(
                [
                    html.Section([html.H2("Outlet temperature"), html.Strong(id="temperature")]),
                    html.Section([html.H2("Setpoint"), html.Strong(id="setpoint")]),
                ],
                className="readings",
            ),
            html.Div(
                [
                    html.Button("Connect", id="connect"),
                    html.Button("Disconnect", id="disconnect"),
                    dcc.Input(id="setpoint-input", type="number", placeholder="Setpoint (°C)"),
                    html.Button("Apply setpoint…", id="apply", className="danger"),
                    html.Button("START…", id="start", className="danger"),
                    html.Button("STANDBY…", id="stop", className="danger"),
                ],
                className="controls",
            ),
            html.P(
                "Hardware query context and write permission are configured explicitly by the calling application. The GUI cannot enable them."
            ),
            html.Div(id="action", role="status"),
            html.Div(id="error", role="alert"),
            dcc.Graph(id="history"),
            dcc.ConfirmDialog(id="confirmation"),
            dcc.Store(id="pending"),
            dcc.Interval(id="refresh", interval=1000),
            html.Footer(
                "Monitoring belongs to the calling application and continues independently of browser refreshes."
            ),
        ],
        className="shell",
    )

    @app.callback(
        Output("connection", "children"),
        Output("run", "children"),
        Output("temperature", "children"),
        Output("setpoint", "children"),
        Output("faults", "children"),
        Output("faults", "className"),
        Output("apply", "disabled"),
        Output("start", "disabled"),
        Output("stop", "disabled"),
        Output("error", "children"),
        Output("history", "figure"),
        Input("refresh", "n_intervals"),
    )
    def refresh(_: int) -> tuple[Any, ...]:
        view = presentation(
            monitor.latest,
            connected=device.is_connected,
            queries=device.queries_enabled,
            control=device.control_enabled,
            error=monitor.error,
        )
        history = monitor.history
        figure = {
            "data": [
                {
                    "x": [stamp for stamp, _ in history],
                    "y": [getattr(sample, field) if sample else None for _, sample in history],
                    "name": name,
                    "mode": "lines",
                    "connectgaps": False,
                    "line": {"color": color},
                }
                for name, field, color in (
                    ("Outlet", "temperature_c", "#38bdf8"),
                    ("Setpoint", "setpoint_c", "#fbbf24"),
                )
            ],
            "layout": {
                "paper_bgcolor": "#0f172a",
                "plot_bgcolor": "#0f172a",
                "font": {"color": "#e2e8f0"},
                "height": 380,
                "uirevision": "history",
                "margin": {"t": 25, "b": 40},
                "yaxis": {"title": {"text": "Temperature (°C)"}},
                "xaxis": {"title": {"text": "Observation time (UTC)"}},
            },
        }
        return (
            view["connection"],
            view["run"],
            view["temperature"],
            view["setpoint"],
            view["faults"],
            view["fault_class"],
            view["disabled"],
            view["disabled"],
            view["stop_disabled"],
            monitor.error or "",
            figure,
        )

    @app.callback(
        Output("pending", "data"),
        Output("confirmation", "displayed"),
        Output("confirmation", "message"),
        Output("action", "children"),
        Input("connect", "n_clicks"),
        Input("disconnect", "n_clicks"),
        Input("apply", "n_clicks"),
        Input("start", "n_clicks"),
        Input("stop", "n_clicks"),
        Input("confirmation", "submit_n_clicks"),
        Input("confirmation", "cancel_n_clicks"),
        State("setpoint-input", "value"),
        State("pending", "data"),
        prevent_initial_call=True,
    )
    def action(*values: Any) -> tuple[Any, ...]:
        value, pending = values[-2:]
        trigger = ctx.triggered[0]["prop_id"]
        operation = trigger.split(".")[0]
        if operation in ("apply", "start", "stop"):
            descriptions = {
                "apply": f"Apply {value} °C? The target will be quantized and read back.",
                "start": "Start temperature control? Confirm coolant and load readiness.",
                "stop": "Request standby? The pump may keep running; this is not an emergency stop.",
            }
            return (
                {"operation": operation, "value": value},
                True,
                descriptions[operation],
                "Review the command.",
            )
        if trigger == "confirmation.cancel_n_clicks":
            return None, False, "", "Cancelled."
        try:
            if operation == "connect":
                device.connect()
            elif operation == "disconnect":
                device.disconnect()
            elif trigger == "confirmation.submit_n_clicks" and isinstance(pending, dict):
                operation = pending.get("operation")
                if operation in ("apply", "start"):
                    view = presentation(
                        monitor.latest,
                        connected=device.is_connected,
                        queries=device.queries_enabled,
                        control=device.control_enabled,
                        error=monitor.error,
                    )
                    if view["disabled"]:
                        raise SafetyError(
                            "Fresh fault-free monitoring and write permission are required"
                        )
                if operation == "apply":
                    target = pending.get("value")
                    if isinstance(target, bool) or not isinstance(target, (int, float)):
                        raise ValueError("Enter a numeric setpoint")
                    device.set_setpoint(target)
                elif operation == "start":
                    device.start()
                elif operation == "stop":
                    device.stop()
                else:
                    raise ValueError("No valid command awaiting confirmation")
            else:
                raise ValueError("No command awaiting confirmation")
            return None, False, "", f"{operation}: completed; verify device state."
        except (OSError, SafetyError, ValueError, TypeError) as exc:
            return None, False, "", f"{type(exc).__name__}: {exc}"

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="ThermoCube simulation; never opens a serial port")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--csv", type=Path, help="Optional new CSV file; never overwritten")
    parser.add_argument("--port", type=int, default=8050, help="Local HTTP port")
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0 or not 1 <= args.port <= 65535:
        parser.error("Duration must be finite and positive; HTTP port must be in 1..65535")
    try:
        with Simulator() as device, Monitor(device, csv_path=args.csv) as monitor:
            if args.headless:
                time.sleep(args.duration)
                if monitor.error or monitor.latest is None:
                    raise RuntimeError(monitor.error or "No observation received")
                print(monitor.latest)
            else:
                create_app(monitor).run(
                    host="127.0.0.1", port=args.port, debug=False, use_reloader=False
                )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
