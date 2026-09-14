from dataclasses import replace
from datetime import timedelta

from test_simulator_service import completed, wait_until

from app.dash_app import create_app, history_figure, presentation
from thermocube.acquisition import AcquisitionService
from thermocube.models import FaultProfile, Mode, Snapshot, utc_now
from thermocube.protocol import decode_faults
from thermocube.simulator import Simulator


def test_presented_unknown_commanded_and_reported_state():
    now = utc_now()
    initial = Snapshot(now, "hardware")
    assert presentation(initial)["connection"] == "DISCONNECTED"
    assert presentation(initial)["run"] == "UNKNOWN"
    assert presentation(initial)["stop_disabled"]
    commanded = replace(initial, connected=True, run_intent=True, mode=Mode.CONTROL)
    assert "UNVERIFIED" in presentation(commanded)["run"]
    assert presentation(commanded)["connection"] == "PORT OPEN · UNVERIFIED"
    reported = replace(
        commanded,
        armed=True,
        run_reported=False,
        responding=True,
        quality="good",
        faults=decode_faults(b"\x40", FaultProfile.M5),
        temperature_c=20,
        setpoint_c=20,
        temperature_at=now,
        setpoint_at=now,
        faults_at=now,
    )
    view = presentation(reported)
    assert view["run"] == "STANDBY" and view["fault_class"] == "fault ok"
    assert not view["control_disabled"]
    stale = presentation(reported, now=now + timedelta(seconds=4))
    assert stale["temperature"] == "—" and "UNVERIFIED" in stale["run"]
    assert stale["control_disabled"] and not stale["stop_disabled"]


def test_faults_unknown_bits_and_history_gaps():
    now = utc_now()
    snap = Snapshot(
        now,
        "hardware",
        connected=True,
        responding=True,
        mode=Mode.CONTROL,
        armed=True,
        temperature_c=20,
        setpoint_c=21,
        temperature_at=now,
        setpoint_at=now,
        quality="good",
        faults=decode_faults(b"\x94"),
    )
    view = presentation(snap)
    assert "rtd_open" in view["faults"] and "0x84" in view["faults"]
    assert view["control_disabled"] and not view["stop_disabled"]
    fig = history_figure((snap, replace(snap, quality="stale"), snap))
    assert list(fig.data[0].y) == [20, None, 20]
    assert not fig.data[0].connectgaps


def test_dash_layout_and_real_refresh_callback():
    simulator = Simulator()
    service = AcquisitionService(simulator)
    app = create_app(service)
    assert not service.is_running and not simulator.is_connected
    client = app.server.test_client()
    assert client.get("/").status_code == 200
    layout = client.get("/_dash-layout")
    assert layout.status_code == 200
    text = layout.get_data(as_text=True)
    assert "Confirm device command" in text and "pending-control" in text
    assert "acquisition" not in app.callback_map
    assert "Hardware session permissions" in text
    assert client.get("/assets/style.css").status_code == 200
    key = next(k for k in app.callback_map if "connection.children" in k)
    outputs = [
        {"id": out.component_id, "property": out.component_property}
        for out in app.callback_map[key]["output"]
    ]
    response = client.post(
        "/_dash-update-component",
        json={
            "output": key,
            "outputs": outputs,
            "inputs": [{"id": "refresh", "property": "n_intervals", "value": 1}],
            "state": [{"id": "request-id", "property": "data", "value": None}],
            "changedPropIds": ["refresh.n_intervals"],
        },
    )
    assert response.status_code == 200
    assert response.json["response"]["connection"]["children"] == "DISCONNECTED"
    assert response.json["response"]["start"]["disabled"]


def test_browser_commands_require_review_and_confirmation():
    simulator = Simulator()
    service = AcquisitionService(simulator)
    app = create_app(service)
    client = app.server.test_client()
    key = next(k for k in app.callback_map if "request-id.data" in k)
    callback = app.callback_map[key]

    def click(trigger, pending=None, value=18):
        states = [value, "standby", "", "", "", pending]
        response = client.post(
            "/_dash-update-component",
            json={
                "output": key,
                "outputs": [
                    {"id": out.component_id, "property": out.component_property}
                    for out in callback["output"]
                ],
                "inputs": [{**i, "value": int(i["id"] == trigger)} for i in callback["inputs"]],
                "state": [
                    {**s, "value": v} for s, v in zip(callback["state"], states, strict=True)
                ],
                "changedPropIds": [f"{trigger}.n_clicks"],
            },
        )
        assert response.status_code == 200
        return response.json["response"]

    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        staged = click("start")
        assert staged["confirmation"]["style"]["display"] == "flex"
        assert "request-id" not in staged
        assert not simulator.snapshot().run_reported
        cancelled = click("cancel-command", staged["pending-control"]["data"])
        assert cancelled["pending-control"]["data"] is None
        assert not simulator.snapshot().run_reported
        for trigger, expected_operation in [
            ("apply", "set_setpoint"),
            ("start", "start"),
            ("stop", "stop"),
        ]:
            staged = click(trigger)
            if trigger == "apply":
                assert "18 °C" in staged["confirmation-text"]["children"]
            sent = click("confirm-command", staged["pending-control"]["data"], value=40)
            outcome = completed(service, sent["request-id"]["data"])
            assert outcome.state == "completed" and outcome.operation == expected_operation
        assert simulator.read_setpoint() == 18  # Confirmation uses the reviewed value.
        assert not simulator.snapshot().run_reported
        assert "No command" in click("confirm-command")["action"]["children"]
        sent = click("disconnect")
        assert completed(service, sent["request-id"]["data"]).state == "completed"
        assert not simulator.is_connected
    finally:
        service.close()
    assert "not running" in click("connect")["action"]["children"]
