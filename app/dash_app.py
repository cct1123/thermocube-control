"""Dash presentation over service snapshots; callbacks never perform serial I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dcc, html, no_update

from thermocube.acquisition import AcquisitionService
from thermocube.driver import CONTROL_ACK, QUERY_ACK
from thermocube.models import Mode, SafetyError, Snapshot


def presentation(snapshot: Snapshot, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    age = max(0, (current - snapshot.timestamp).total_seconds())

    def recent(stamp: datetime | None) -> bool:
        return (
            stamp is not None
            and snapshot.connected
            and 0 <= (current - stamp).total_seconds() <= 3.5
        )

    temperature_fresh = snapshot.quality == "good" and recent(snapshot.temperature_at)
    setpoint_fresh = snapshot.quality == "good" and recent(snapshot.setpoint_at)
    faults_fresh = snapshot.quality in {"good", "faulted"} and recent(snapshot.faults_at)
    fresh = temperature_fresh and setpoint_fresh and faults_fresh
    if not snapshot.connected:
        connection = "DISCONNECTED"
    elif snapshot.responding:
        connection = "CONNECTED" if fresh else "CONNECTED · STALE / PAUSED"
    else:
        connection = "PORT OPEN · UNVERIFIED"
    run = "UNKNOWN"
    if faults_fresh and snapshot.run_reported is not None:
        run = "RUN" if snapshot.run_reported else "STANDBY"
    elif snapshot.run_intent is not None:
        run = ("RUN" if snapshot.run_intent else "STANDBY") + " REQUESTED · UNVERIFIED"
    faults = snapshot.faults
    names = list(faults.active) if faults else []
    if faults and faults.unknown_mask:
        names.append(f"unknown/reserved bits 0x{faults.unknown_mask:02X}")
    fault_text = (
        "; ".join(names)
        if names
        else ("No reported faults" if faults_fresh and faults else "Fault state unknown / stale")
    )
    control = snapshot.connected and (
        snapshot.mode == Mode.SIMULATION or snapshot.mode == Mode.CONTROL
    )
    return {
        "connection": connection,
        "run": run,
        "faults": fault_text,
        "fault_class": "fault alarm" if names else ("fault ok" if fresh else "fault stale"),
        "temperature": f"{snapshot.temperature_c:.2f} °C"
        if temperature_fresh and snapshot.temperature_c is not None
        else "—",
        "setpoint": f"{snapshot.setpoint_c:.2f} °C"
        if setpoint_fresh and snapshot.setpoint_c is not None
        else "—",
        "age": f"Last snapshot {age:.1f} s ago · {snapshot.quality}",
        "control_disabled": not control or not snapshot.armed or not fresh or bool(names),
        "stop_disabled": not control,
        "error": snapshot.error or "",
    }


def history_figure(history: tuple[Snapshot, ...]) -> go.Figure:
    figure = go.Figure()
    for label, value_field, time_field, color in (
        ("Outlet", "temperature_c", "temperature_at", "#38bdf8"),
        ("Read-back setpoint", "setpoint_c", "setpoint_at", "#fbbf24"),
    ):
        figure.add_trace(
            go.Scatter(
                x=[getattr(s, time_field) if s.quality == "good" else s.timestamp for s in history],
                y=[
                    getattr(s, value_field) if s.quality == "good" and s.connected else None
                    for s in history
                ],
                name=label,
                mode="lines",
                line={"color": color, "width": 2},
                connectgaps=False,
            )
        )
    figure.update_layout(
        template="plotly_dark",
        height=390,
        margin={"l": 55, "r": 20, "t": 25, "b": 45},
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        yaxis_title="Temperature (°C)",
        xaxis_title="Observation time (UTC)",
        uirevision="thermocube-history",
        legend={"orientation": "h", "y": 1.12},
    )
    return figure


def create_app(service: AcquisitionService) -> Dash:
    app = Dash(
        __name__, assets_folder=str(Path(__file__).parent / "assets"), title="ThermoCube Control"
    )
    source = service.latest.source
    dialog_accessibility: dict[str, Any] = {
        "aria-modal": "true",
        "aria-label": "Confirm device command",
    }
    app.layout = html.Main(
        [
            html.Header(
                [
                    html.Div(
                        [
                            html.P("THERMOCUBE / R2", className="eyebrow"),
                            html.H1("Temperature control"),
                        ]
                    ),
                    html.Span(source.upper(), className="source"),
                ]
            ),
            html.Div(
                [
                    html.Span(id="connection", className="badge"),
                    html.Span(id="run", className="badge"),
                    html.Span(id="mode", className="badge"),
                ],
                className="status",
            ),
            html.Div(id="faults", role="alert", className="fault stale"),
            html.P(
                "Every hardware query asserts REMOTE and the selected RUN/STANDBY state. "
                "A non-mutating query is not guaranteed. Closing this page does not stop acquisition or the device.",
                className="fault stale",
                style={"display": "none"} if source == "simulation" else {},
            ),
            html.Div(
                [
                    html.Section([html.Label("OUTLET TEMPERATURE"), html.Strong(id="temperature")]),
                    html.Section([html.Label("READ-BACK SETPOINT"), html.Strong(id="setpoint")]),
                ],
                className="metrics",
            ),
            dcc.Graph(id="history", figure=history_figure(()), config={"displayModeBar": False}),
            html.Div(id="age", className="muted"),
            html.Div(
                [
                    html.Button("Connect", id="connect"),
                    html.Button("Disconnect", id="disconnect"),
                    html.Span(
                        "Disconnect releases the link; it does not stop the device.",
                        className="muted",
                    ),
                ],
                className="controls",
            ),
            html.Section(
                [
                    html.H2("Temperature-control commands"),
                    html.P(
                        "Start, standby, and setpoint changes require confirmation. Standby may leave the pump running.",
                        className="muted",
                    ),
                    dcc.Input(
                        id="setpoint-input",
                        type="number",
                        value=20,
                        step=0.1,
                        debounce=True,
                        placeholder="Setpoint (C)",
                    ),
                    html.Span(" °C "),
                    html.Div(
                        [
                            html.Button("Apply setpoint", id="apply", className="warning"),
                            html.Button("START", id="start", className="danger"),
                            html.Button("STANDBY", id="stop", className="warning"),
                        ],
                        className="controls",
                    ),
                ],
                className="panel",
            ),
            html.Details(
                [
                    html.Summary("Hardware session permissions"),
                    html.P(
                        "R2 queries actively assert REMOTE and RUN/STANDBY. Establish the physical state before arming; this form cannot grant hardware-phase approval."
                    ),
                    dcc.Dropdown(
                        id="query-run",
                        options=[
                            {"label": "Established STANDBY", "value": "standby"},
                            {"label": "Established RUN", "value": "run"},
                        ],
                        value="standby",
                        clearable=False,
                    ),
                    dcc.Input(id="query-ack", placeholder=QUERY_ACK, type="text", className="ack"),
                    html.Button("Arm constrained queries", id="arm"),
                    dcc.Input(
                        id="control-ack", placeholder=CONTROL_ACK, type="text", className="ack"
                    ),
                    html.Button("Enable write control", id="enable", className="danger"),
                    html.Button("Disarm and lock writes", id="disarm"),
                    html.P(
                        "After uncertain communication, verify the stream reset and device state using the approved procedure before recovery.",
                        className="muted",
                    ),
                    dcc.Input(
                        id="recovery-ack",
                        placeholder="STREAM RESET AND DEVICE STATE VERIFIED",
                        type="text",
                        className="ack",
                    ),
                    html.Button("Confirm recovery", id="recover"),
                ],
                className="panel",
                style={"display": "none"} if source == "simulation" else {},
            ),
            html.Div(id="action", role="status"),
            html.Div(id="action-result", role="status"),
            html.Div(id="error", role="alert", className="error"),
            html.Section(
                [
                    html.Div(
                        [
                            html.H2("Confirm device command"),
                            html.P(id="confirmation-text"),
                            html.Button("Cancel", id="cancel-command"),
                            html.Button(
                                "Confirm command", id="confirm-command", className="danger"
                            ),
                        ],
                        className="confirmation-card",
                    ),
                ],
                id="confirmation",
                role="dialog",
                **dialog_accessibility,
                style={"display": "none"},
            ),
            dcc.Store(id="pending-control"),
            dcc.Store(id="request-id"),
            dcc.Interval(id="refresh", interval=1000),
            html.Footer(
                "Acquisition runs independently of this browser. Hardware state remains unverified until physically validated."
            ),
        ],
        className="shell",
    )

    @app.callback(
        Output("connection", "children"),
        Output("run", "children"),
        Output("mode", "children"),
        Output("faults", "children"),
        Output("faults", "className"),
        Output("temperature", "children"),
        Output("setpoint", "children"),
        Output("history", "figure"),
        Output("age", "children"),
        Output("apply", "disabled"),
        Output("start", "disabled"),
        Output("stop", "disabled"),
        Output("error", "children"),
        Output("action-result", "children"),
        Input("refresh", "n_intervals"),
        State("request-id", "data"),
    )
    def refresh(_: int, request_id: str | None) -> tuple[Any, ...]:
        snapshot = service.latest
        view = presentation(snapshot)
        result = service.result(request_id) if request_id else None
        status = f"{result.operation}: {result.state} {result.detail}" if result else ""
        return (
            view["connection"],
            view["run"],
            snapshot.mode.value,
            view["faults"],
            view["fault_class"],
            view["temperature"],
            view["setpoint"],
            history_figure(service.history),
            view["age"],
            view["control_disabled"],
            view["control_disabled"],
            view["stop_disabled"],
            service.error or view["error"],
            status,
        )

    @app.callback(
        Output("request-id", "data"),
        Output("action", "children"),
        Output("pending-control", "data"),
        Output("confirmation", "style"),
        Output("confirmation-text", "children"),
        Input("connect", "n_clicks"),
        Input("disconnect", "n_clicks"),
        Input("apply", "n_clicks"),
        Input("start", "n_clicks"),
        Input("stop", "n_clicks"),
        Input("arm", "n_clicks"),
        Input("enable", "n_clicks"),
        Input("disarm", "n_clicks"),
        Input("recover", "n_clicks"),
        Input("confirm-command", "n_clicks"),
        Input("cancel-command", "n_clicks"),
        State("setpoint-input", "value"),
        State("query-run", "value"),
        State("query-ack", "value"),
        State("control-ack", "value"),
        State("recovery-ack", "value"),
        State("pending-control", "data"),
        prevent_initial_call=True,
    )
    def action(*values: Any) -> tuple[Any, ...]:
        setpoint, run, query_ack, control_ack, recovery_ack, pending = values[-6:]
        trigger = str(ctx.triggered_id)
        hidden = {"display": "none"}
        if trigger in {"apply", "start", "stop"}:
            description = {
                "apply": f"Apply setpoint {setpoint} °C? The target will be quantized to 0.1 °F and read back.",
                "start": "Start temperature control? Confirm the coolant and connected load are ready.",
                "stop": "Request standby? This is not a power-off or emergency stop; the pump may continue running.",
            }[trigger]
            pending = {"operation": trigger, "value": setpoint}
            return (
                no_update,
                "Review the proposed command.",
                pending,
                {"display": "flex"},
                description,
            )
        if trigger == "cancel-command":
            return no_update, "Command cancelled; nothing queued.", None, hidden, ""
        operations = {
            "connect": ("connect", {}),
            "disconnect": ("disconnect", {}),
            "arm": ("arm_queries", {"run": run == "run", "acknowledgement": query_ack}),
            "enable": ("enable_control", {"acknowledgement": control_ack}),
            "disarm": ("disarm", {}),
            "recover": ("confirm_recovery", {"acknowledgement": recovery_ack}),
        }
        if trigger == "confirm-command":
            if not isinstance(pending, dict) or pending.get("operation") not in {
                "apply",
                "start",
                "stop",
            }:
                return no_update, "No command awaiting confirmation.", None, hidden, ""
            operation = "set_setpoint" if pending["operation"] == "apply" else pending["operation"]
            kwargs = {"value": pending.get("value")} if operation == "set_setpoint" else {}
        else:
            operation, kwargs = operations[trigger]
        try:
            return service.submit(operation, **kwargs), f"Queued {operation}.", None, hidden, ""
        except (ValueError, RuntimeError, SafetyError) as exc:
            return None, str(exc), None, hidden, ""

    return app
