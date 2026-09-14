"""Failure scenarios identified by the pre-hardware engineering review."""

import csv
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import arm
from test_simulator_service import completed, wait_until

from app.dash_app import presentation
from thermocube.acquisition import AcquisitionService
from thermocube.driver import QUERY_ACK
from thermocube.logging import CsvLogger
from thermocube.models import CommunicationTimeout, FaultProfile, SafetyError
from thermocube.simulator import Simulator
from thermocube.transport import HardwareApproval


@pytest.mark.parametrize("operation", ["read", "write"])
def test_completed_io_after_deadline_is_uncertain(rig, operation):
    device, port, clock, transport = rig
    arm(device)
    original = getattr(port, operation)

    def delayed(*args):
        clock.sleep(0.51)
        return original(*args)

    setattr(port, operation, delayed)
    with pytest.raises(CommunicationTimeout):
        device.read_temperature()
    assert transport.needs_recovery and not device.is_connected
    assert not device.cached_snapshot.armed


def test_snapshot_stops_at_first_fault_query(rig):
    device, port, _, _ = rig
    arm(device, run=True)
    port.faults = 1
    snapshot = device.snapshot()
    assert port.writes == [b"\xc8"]
    assert snapshot.faults.has_fault and not snapshot.armed
    assert snapshot.quality == "faulted"


def test_rearming_does_not_bypass_setpoint_fault_interlock(rig):
    device, port, _, _ = rig
    arm(device, control=True)
    port.faults = 1
    device.read_faults()
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    with pytest.raises(SafetyError):
        device.set_setpoint(21)
    assert all(not tx[0] & 32 for tx in port.writes)


def test_fresh_fault_read_does_not_refresh_old_measurements():
    sim = Simulator()
    sim.connect()
    sample = sim.snapshot()
    old = sample.timestamp - timedelta(seconds=10)
    view = presentation(replace(sample, temperature_at=old, setpoint_at=old))
    assert view["temperature"] == view["setpoint"] == "—"
    assert view["control_disabled"] and not view["stop_disabled"]


def test_future_timestamp_is_not_fresh():
    sim = Simulator()
    sim.connect()
    sample = sim.snapshot()
    assert presentation(sample, now=sample.timestamp - timedelta(seconds=1))["control_disabled"]


def test_pending_stop_survives_following_disconnect(tmp_path):
    sim = Simulator()
    entered, release = threading.Event(), threading.Event()
    original = sim.set_setpoint

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    sim.set_setpoint = blocked
    service = AcquisitionService(sim, logger=CsvLogger(tmp_path, "shutdown"))
    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        assert completed(service, service.submit("start")).state == "completed"
        active = service.submit("set_setpoint", value=19)
        assert entered.wait(2)
        cancelled = service.submit("start")
        stop = service.submit("stop")
        disconnected = service.submit("disconnect")
        release.set()
        assert completed(service, active).state == "completed"
        assert completed(service, stop).state == "completed"
        assert completed(service, disconnected).state == "completed"
        assert service.result(cancelled).state == "cancelled"
    finally:
        release.set()
        service.close()
    sim.connect()
    assert sim.snapshot().run_reported is False
    with (tmp_path / "shutdown-events.csv").open(encoding="utf-8", newline="") as file:
        events = list(csv.DictReader(file))
    assert any(e["outcome"] == "cancelled" and cancelled in e["detail"] for e in events)


def test_audit_failure_stops_active_query_traffic(rig, tmp_path, monkeypatch):
    device, port, _, _ = rig
    arm(device, control=True)
    logger = CsvLogger(tmp_path, "audit-failure")

    def disk_full(*args):
        raise OSError("disk full")

    monkeypatch.setattr(logger, "sample", disk_full)
    service = AcquisitionService(device, logger=logger, interval=1)
    service.start(connect=True)
    try:
        wait_until(lambda: service.error is not None)
        sent = len(port.writes)
        wait_until(lambda: len(service.history) >= 2)
        assert len(port.writes) == sent
        assert completed(service, service.submit("stop")).state == "completed"
        assert port.writes[-1] == b"\xa0"
    finally:
        service.close()


def test_approval_copies_mutable_run_states():
    states = [False]
    approval = HardwareApproval("test", FaultProfile.LEGACY, allowed_run_states=states)
    states.append(True)
    assert approval.allowed_run_states == (False,)


@pytest.mark.parametrize("key", ["schema_version", "session_id"])
def test_log_metadata_cannot_override_identity(tmp_path, key):
    with pytest.raises(ValueError):
        CsvLogger(tmp_path, "identity", metadata={key: "incorrect"})
    assert not list(tmp_path.iterdir())


def test_imports_cannot_touch_serial_in_a_fresh_process():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import serial
from serial.tools import list_ports
def forbidden(*args, **kwargs):
    raise AssertionError('Import attempted hardware access')
serial.Serial.open = forbidden
list_ports.comports = forbidden
import thermocube, app.cli, app.dash_app
""",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_close_rejects_new_work_while_io_is_still_in_flight():
    sim = Simulator()
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(3)

    sim.set_setpoint = blocked
    service = AcquisitionService(sim)
    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        service.submit("set_setpoint", value=20)
        assert entered.wait(2)
        with pytest.raises(TimeoutError):
            service.close(timeout=0.01)
        with pytest.raises(RuntimeError):
            service.submit("start")
    finally:
        release.set()
        service.close()


def test_close_before_start_releases_device():
    sim = Simulator()
    sim.connect()
    AcquisitionService(sim).close()
    assert not sim.is_connected


def test_quarantine_survives_replacing_the_transport(monkeypatch):
    from test_safety_integration import fake_physical_path

    from thermocube.driver import ThermoCube
    from thermocube.transport import SerialTransport

    approval = HardwareApproval(
        "test", FaultProfile.LEGACY, binary_framing_confirmed=True, allow_queries=True
    )
    driver, first, endpoint = fake_physical_path(monkeypatch, approval)
    driver.connect()
    first.quarantine()
    second = SerialTransport(first.config, approval=approval)
    endpoint.is_open = True
    monkeypatch.setattr(second, "_open", lambda: endpoint)
    replacement = ThermoCube(second)
    replacement.connect()
    try:
        assert second.needs_recovery
        with pytest.raises(SafetyError):
            replacement.arm_queries(run=False, acknowledgement=QUERY_ACK)
        assert not endpoint.writes
    finally:
        replacement.disconnect()


def test_pyserial_timeout_has_the_public_timeout_type(rig):
    from serial import SerialTimeoutException

    device, port, _, transport = rig
    arm(device)

    def timed_out(data):
        raise SerialTimeoutException("driver timeout")

    port.write = timed_out
    with pytest.raises(CommunicationTimeout):
        device.read_temperature()
    assert transport.needs_recovery


def test_failed_headless_run_has_failure_exit(tmp_path, monkeypatch):
    from app import cli

    sim = Simulator()
    sim.inject_disconnect()
    monkeypatch.setattr(cli, "Simulator", lambda: sim)
    monkeypatch.setattr(
        sys, "argv", ["thermocube", "--headless", "--duration", "0.05", "--log-dir", str(tmp_path)]
    )
    with pytest.raises(RuntimeError):
        cli.main()
    assert not sim.is_connected


@pytest.mark.parametrize("bounds", [[], [1], [1, 2, 3], [False, 40], "5,40"])
def test_invalid_config_bounds_fail_before_connection(bounds):
    from app.cli import hardware_from_config

    with pytest.raises(ValueError):
        hardware_from_config(
            {"serial": {"port": "TEST"}, "profile": "legacy-r2", "limits_c": bounds}
        )
