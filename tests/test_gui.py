import csv
import io
import sys
import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import arm

from thermocube import Simulator
from thermocube.gui import Monitor, create_app, presentation


def request_callback(app, client, target, trigger, values=None):
    values = values or {}
    key = next(key for key in app.callback_map if target in key)
    callback = app.callback_map[key]
    outputs = [
        {"id": item.component_id, "property": item.component_property}
        for item in callback["output"]
    ]

    def arguments(items):
        return [
            dict(item, value=values.get(item["id"] + "." + item["property"], 0)) for item in items
        ]

    result = client.post(
        "/_dash-update-component",
        json={
            "output": key,
            "outputs": outputs,
            "inputs": arguments(callback["inputs"]),
            "state": arguments(callback["state"]),
            "changedPropIds": [trigger],
        },
    )
    assert result.status_code == 200, result.data
    return result.get_json()["response"]


def test_layout_refresh_and_creation_do_not_connect_or_start_monitor():
    device = Simulator()
    monitor = Monitor(device)
    app = create_app(monitor)
    client = app.server.test_client()
    for path in ("/", "/_dash-layout", "/assets/style.css"):
        assert client.get(path).status_code == 200
    result = request_callback(app, client, "connection.children", "refresh.n_intervals")
    assert result["connection"]["children"] == "DISCONNECTED"
    assert result["start"]["disabled"] and result["stop"]["disabled"]
    assert not device.is_connected and not monitor.is_running


def test_gui_confirmation_calls_public_api_and_cancel_does_nothing():
    with Simulator() as device, Monitor(device) as monitor:
        deadline = time.monotonic() + 2
        while monitor.latest is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        app = create_app(monitor)
        client = app.server.test_client()
        request_callback(app, client, "pending.data", "connect.n_clicks")
        staged = request_callback(app, client, "pending.data", "start.n_clicks")
        assert staged["confirmation"]["displayed"] and not device.status().reported_run
        cancelled = request_callback(
            app,
            client,
            "pending.data",
            "confirmation.cancel_n_clicks",
            {"pending.data": staged["pending"]["data"]},
        )
        assert cancelled["pending"]["data"] is None and not device.status().reported_run
        started = request_callback(
            app,
            client,
            "pending.data",
            "confirmation.submit_n_clicks",
            {"pending.data": {"operation": "start"}},
        )
        assert "completed" in started["action"]["children"] and device.status().reported_run
        request_callback(
            app,
            client,
            "pending.data",
            "confirmation.submit_n_clicks",
            {"pending.data": {"operation": "apply", "value": 18}},
        )
        assert device.read_setpoint() == 18
        request_callback(
            app,
            client,
            "pending.data",
            "confirmation.submit_n_clicks",
            {"pending.data": {"operation": "stop"}},
        )
        assert not device.status().reported_run
        invalid = request_callback(
            app,
            client,
            "pending.data",
            "confirmation.submit_n_clicks",
            {"pending.data": {"operation": "apply", "value": None}},
        )
        assert "ValueError" in invalid["action"]["children"]
        request_callback(app, client, "pending.data", "disconnect.n_clicks")
        assert not device.is_connected


def test_gui_hardware_write_lock_is_enforced_by_controller(hardware):
    device, wire, _ = hardware
    device.connect()
    app = create_app(Monitor(device))
    result = request_callback(
        app,
        app.server.test_client(),
        "pending.data",
        "confirmation.submit_n_clicks",
        {"pending.data": {"operation": "start"}},
    )
    assert "SafetyError" in result["action"]["children"] and wire.calls == []


def test_presentation_stale_future_fault_and_legacy_states():
    with Simulator() as device:
        sample = device.status()
        for seconds in (-5, 5):
            view = presentation(
                replace(sample, timestamp=sample.timestamp + timedelta(seconds=seconds)),
                connected=True,
                queries=True,
                control=True,
                now=sample.timestamp,
            )
            assert view["disabled"] and view["temperature"] == "—"
        legacy = presentation(
            replace(sample, reported_run=None),
            connected=True,
            queries=True,
            control=True,
            now=sample.timestamp,
        )
        assert "UNVERIFIED" in legacy["run"]
        device.inject_faults(128)
        view = presentation(device.status(), connected=True, queries=False, control=True)
        assert "unknown bits" in view["faults"] and view["fault_class"] == "fault alarm"
        assert view["disabled"] and not view["stop_disabled"]


def test_cli_headless_csv_and_shutdown(monkeypatch, tmp_path):
    from thermocube.gui import main

    path = tmp_path / "simulation.csv"
    monkeypatch.setattr(
        sys, "argv", ["thermocube", "--headless", "--duration", ".1", "--csv", str(path)]
    )
    main()
    assert list(csv.DictReader(path.open(encoding="utf-8")))[0]["temperature_c"] == "25.0"


def test_cli_reports_failed_acquisition_and_closes(monkeypatch):
    from thermocube import gui

    device = Simulator()
    device.inject_timeout()
    monkeypatch.setattr(gui, "Simulator", lambda: device)
    monkeypatch.setattr(sys, "argv", ["thermocube", "--headless", "--duration", ".1"])
    with pytest.raises(RuntimeError):
        gui.main()
    assert not device.is_connected


def test_gui_refresh_gets_background_history_without_polling_device():
    with Simulator() as device, Monitor(device) as monitor:
        deadline = time.monotonic() + 2
        while monitor.latest is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        app = create_app(monitor)
        client = app.server.test_client()
        before = monitor.latest
        result = request_callback(app, client, "connection.children", "refresh.n_intervals")
        assert result["temperature"]["children"] == "25.00 °C"
        assert len(result["history"]["figure"]["data"]) == 2
        assert monitor.latest is before


def wait_for(condition, timeout=4):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "Timed out waiting for monitor"
        time.sleep(0.01)


def test_monitor_is_optional_and_does_not_own_connection(hardware):
    device, wire, _ = hardware
    monitor = Monitor(device)
    assert not monitor.is_running and not device.is_connected and wire.opens == 0
    with monitor:
        wait_for(lambda: monitor.error == "Disconnected")
        assert wire.opens == 0
        arm(device)
        wait_for(lambda: monitor.latest is not None)
        assert monitor.latest.temperature_c == 25
    assert device.is_connected and device.queries_enabled
    assert [data for _, data in wire.calls] == [b"\x88", b"\x89", b"\x81"]
    with pytest.raises(RuntimeError):
        monitor.start()


def test_csv_and_history_are_bounded_and_errors_make_gaps(tmp_path):
    path = tmp_path / "measurements.csv"
    with (
        Simulator() as device,
        Monitor(device, interval=1, history_size=2, csv_path=path) as monitor,
    ):
        wait_for(lambda: monitor.latest is not None)
        device.inject_disconnect()
        wait_for(lambda: monitor.error is not None)
        assert not device.is_connected and monitor.latest is None
        device.connect()  # The monitor never reconnects for the caller.
        wait_for(lambda: monitor.error is None)
        assert len(monitor.history) == 2
        assert monitor.history[0][1] is None and monitor.history[1][1] is not None
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert rows[0]["temperature_c"] == "25.0" and rows[0]["faults_hex"] == "40"
    assert rows[1]["temperature_c"] == "" and "ConnectionError" in rows[1]["error"]
    assert rows[2]["temperature_c"] == "25.0"
    with pytest.raises(FileExistsError):
        Monitor(Simulator(), csv_path=path).start()


def test_csv_failure_stops_queries_and_allows_caller_stop(hardware, monkeypatch, tmp_path):
    device, wire, _ = hardware
    arm(device, control=True)

    class FullDisk(io.StringIO):
        def flush(self):
            if len(self.getvalue().splitlines()) > 1:
                raise OSError("disk full")

    output = FullDisk()
    original = Path.open
    target = tmp_path / "full.csv"
    monkeypatch.setattr(
        Path, "open", lambda path, *a, **k: output if path == target else original(path, *a, **k)
    )
    monitor = Monitor(device, csv_path=target)
    monitor.start()
    wait_for(lambda: monitor.error is not None)
    monitor.stop()
    assert "disk full" in monitor.error and monitor.latest is None
    assert len(wire.calls) == 3 and device.is_connected
    device.stop()
    assert wire.calls[-1][1] == b"\xa0"


def test_fault_indication_is_retained_without_more_queries(hardware):
    device, wire, _ = hardware
    arm(device)
    wire.faults = 1
    with Monitor(device, interval=1) as monitor:
        wait_for(lambda: monitor.latest is not None)
        original = monitor.latest
        wait_for(lambda: monitor.error == "Queries disarmed")
        assert monitor.latest is original and len(wire.calls) == 1


def test_monitor_shutdown_waits_for_inflight_io_without_disconnecting(hardware):
    device, wire, _ = hardware
    arm(device)
    entered, release = threading.Event(), threading.Event()

    def block(_):
        entered.set()
        assert release.wait(3)

    wire.on_write = block
    monitor = Monitor(device)
    monitor.start()
    assert entered.wait(2)
    try:
        with pytest.raises(TimeoutError):
            monitor.stop(timeout=0.01)
        assert device.is_connected
    finally:
        release.set()
        monitor.stop(timeout=3)
    assert device.is_connected and not monitor.is_running
    assert len(wire.calls) == 3


def test_monitor_configuration_and_stop_before_start():
    for kwargs in (dict(interval=0.5), dict(interval=float("nan")), dict(history_size=0)):
        with pytest.raises(ValueError):
            Monitor(Simulator(), **kwargs)
    monitor = Monitor(Simulator())
    for timeout in (0, True, float("inf")):
        with pytest.raises(ValueError):
            monitor.stop(timeout)
    monitor.stop()
    with pytest.raises(RuntimeError):
        monitor.start()
