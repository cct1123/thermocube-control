import json
import threading
from dataclasses import replace
from uuid import uuid4

import pytest
from conftest import Clock, FakeSerial, arm
from test_simulator_service import completed, wait_until

from app import cli
from thermocube.acquisition import AcquisitionService
from thermocube.driver import CONTROL_ACK, QUERY_ACK, ThermoCube
from thermocube.models import ConnectionError, FaultProfile, Mode, SafetyError, TemperatureLimits
from thermocube.simulator import Simulator
from thermocube.transport import HardwareApproval, SerialConfig, SerialTransport, discover_ports


def fake_physical_path(monkeypatch, approval, *, profile=FaultProfile.LEGACY):
    """Exercise all real-hardware permission checks, replacing ONLY the serial opening."""
    endpoint = FakeSerial(Clock())
    endpoint.m5 = profile == FaultProfile.M5
    transport = SerialTransport(SerialConfig("TEST-" + uuid4().hex), approval=approval)
    monkeypatch.setattr(transport, "_open", lambda: endpoint)
    driver = ThermoCube(transport, profile=profile, limits=TemperatureLimits(5, 40))
    return driver, transport, endpoint


def test_real_path_query_permission_and_control_lock(monkeypatch):
    approval = HardwareApproval(
        "test only", FaultProfile.LEGACY, binary_framing_confirmed=True, allow_queries=True
    )
    driver, transport, endpoint = fake_physical_path(monkeypatch, approval)
    driver.connect()
    try:
        with pytest.raises(SafetyError):
            driver.arm_queries(run=True, acknowledgement=QUERY_ACK)
        driver.arm_queries(run=False, acknowledgement=QUERY_ACK)
        assert driver.read_temperature() == 25
        with pytest.raises(SafetyError):
            driver.enable_control(CONTROL_ACK)
        for data, length in [(b"\xa0", 0), (b"\xe0", 0), (b"\xfe", 0), (b"\x09", 2), (b"\x89", 1)]:
            with pytest.raises(SafetyError):
                transport.exchange(data, length)
        assert endpoint.writes == [b"\x89"]
    finally:
        driver.disconnect()


def test_real_path_control_limits_and_states(monkeypatch):
    approval = HardwareApproval(
        "test only",
        FaultProfile.LEGACY,
        binary_framing_confirmed=True,
        allow_queries=True,
        allow_control=True,
        setpoint_limits=TemperatureLimits(5, 40),
    )
    driver, transport, endpoint = fake_physical_path(monkeypatch, approval)
    arm(driver, control=True)
    try:
        with pytest.raises(SafetyError):
            driver.start()  # Approval permits only standby; even the preflight read is withheld.
        assert endpoint.writes == [] and driver.cached_snapshot.run_intent is False
        assert driver.set_setpoint(10) == 10
        with pytest.raises(ValueError):
            transport.exchange(b"\xa1\xff\xff", 0)
        with pytest.raises(AttributeError):
            transport.approval = replace(approval, setpoint_limits=None)
        assert endpoint.writes == [b"\x88", b"\xa1\xf4\x01", b"\x81"]
    finally:
        driver.disconnect()

    driver, transport, endpoint = fake_physical_path(
        monkeypatch, replace(approval, setpoint_limits=None)
    )
    driver.connect()
    try:
        with pytest.raises(SafetyError):
            transport.exchange(b"\xa1\xf4\x01", 0)
        assert not endpoint.writes
    finally:
        driver.disconnect()


def test_profile_and_framing_not_silently_chosen(monkeypatch):
    approval = HardwareApproval("test only", FaultProfile.M5)
    driver, _, _ = fake_physical_path(monkeypatch, approval)
    with pytest.raises(SafetyError, match="profile"):
        driver.connect()
    driver, _, endpoint = fake_physical_path(monkeypatch, approval, profile=FaultProfile.M5)
    driver.connect()
    try:
        with pytest.raises(SafetyError):
            driver.arm_queries(run=False, acknowledgement=QUERY_ACK)
        assert not endpoint.writes
    finally:
        driver.disconnect()


def test_discovery_uses_metadata_only(monkeypatch):
    from types import SimpleNamespace

    from serial.tools import list_ports

    monkeypatch.setattr(
        list_ports, "comports", lambda: [SimpleNamespace(device="FAKE", description="test")]
    )
    assert discover_ports(approval=HardwareApproval("test only", FaultProfile.LEGACY)) == (
        ("FAKE", "test"),
    )


def test_delay_pacing_survives_reconnect_and_write_exception(rig):
    driver, port, clock, transport = rig
    arm(driver)
    port.write_delay = 0.4
    driver.read_temperature()
    driver.disconnect()
    arm(driver)
    driver.read_temperature()
    assert port.starts[1] - port.starts[0] >= 0.75
    port.fail_write = True
    with pytest.raises(ConnectionError):
        driver.read_temperature()
    failed_at = clock()
    port.fail_write = False
    arm_connection_only = driver.connect
    arm_connection_only()
    driver.confirm_recovery("STREAM RESET AND DEVICE STATE VERIFIED")
    driver.arm_queries(run=False, acknowledgement=QUERY_ACK)
    driver.read_temperature()
    assert port.starts[-1] >= failed_at + 0.35 - 1e-12
    assert not transport.needs_recovery
    serial_events = [e for e in driver.drain_events() if e.operation == "serial"]
    assert all(e.tx_started_at is not None for e in serial_events)
    assert all(e.tx_monotonic_s is not None for e in serial_events)
    assert all(
        b.tx_monotonic_s - a.tx_monotonic_s >= 0.35 - 1e-12
        for a, b in zip(serial_events, serial_events[1:], strict=False)
    )


def test_service_preserves_stop_after_inflight_failure():
    sim = Simulator()
    entered, release = threading.Event(), threading.Event()

    def fail_set(*args):
        entered.set()
        release.wait(3)
        raise ValueError("invalid setpoint")

    sim.set_setpoint = fail_set
    service = AcquisitionService(sim)
    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        bad = service.submit("set_setpoint", value=200)
        assert entered.wait(2)
        stop = service.submit("stop")
        release.set()
        assert completed(service, bad).state == "failed"
        assert completed(service, stop).state == "completed"
    finally:
        release.set()
        service.close()


def test_service_hardware_session_operations(rig):
    device, _, _, _ = rig
    service = AcquisitionService(device)
    service.start()
    try:
        assert completed(service, service.submit("connect")).state == "completed"
        assert (
            completed(
                service, service.submit("arm_queries", run=False, acknowledgement=QUERY_ACK)
            ).state
            == "completed"
        )
        assert (
            completed(service, service.submit("enable_control", acknowledgement=CONTROL_ACK)).state
            == "completed"
        )
        assert completed(service, service.submit("disarm")).state == "completed"
        assert device.cached_snapshot.mode == Mode.OBSERVE
        assert (
            completed(service, service.submit("confirm_recovery", acknowledgement="wrong")).state
            == "failed"
        )
    finally:
        service.close()


def test_cli_headless_runs_and_closes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["thermocube", "--headless", "--duration", "0.1", "--log-dir", str(tmp_path)]
    )
    cli.main()
    assert "simulation" in capsys.readouterr().out
    assert len(list(tmp_path.glob("*-samples.csv"))) == 1
    assert not any(t.name == "thermocube-acquisition" for t in threading.enumerate())


def test_cli_hardware_config_stays_inert_and_closed(tmp_path):
    path = tmp_path / "hardware.json"
    config = {"serial": {"port": "DO-NOT-OPEN"}, "profile": "legacy-r2", "approval": None}
    path.write_text(json.dumps(config))
    device = cli.hardware_from_config(json.loads(path.read_text()))
    assert not device.is_connected
    with pytest.raises(SafetyError):
        device.connect()
    config["unexpected"] = True
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        cli.hardware_from_config(json.loads(path.read_text()))


@pytest.mark.parametrize("duration", ["-1", "nan", "inf"])
def test_cli_invalid_duration(monkeypatch, duration):
    monkeypatch.setattr("sys.argv", ["thermocube", "--duration", duration])
    with pytest.raises(SystemExit):
        cli.main()
