import csv
import json
import threading
import time
from dataclasses import FrozenInstanceError, replace

import pytest
from conftest import Clock, arm

from thermocube.acquisition import AcquisitionService
from thermocube.logging import EVENT_FIELDS, SAMPLE_FIELDS, CsvLogger
from thermocube.models import (
    CommunicationTimeout,
    ConnectionError,
    Event,
    FaultProfile,
    SafetyError,
    utc_now,
)
from thermocube.simulator import Simulator


def wait_until(predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for service state")


def completed(service, key):
    wait_until(lambda: service.result(key).state not in {"pending", "running"})
    return service.result(key)


@pytest.mark.parametrize("profile", list(FaultProfile))
def test_simulator_lifecycle_and_heat_flow(profile):
    clock = Clock()
    sim = Simulator(clock=clock, sleep=clock.sleep, now=clock.now, profile=profile)
    with pytest.raises(ConnectionError):
        sim.snapshot()
    sim.connect()
    assert sim.snapshot().run_reported is False
    assert sim.read_temperature("F") == 77
    sim.start()
    clock.sleep(10)
    value = sim.snapshot()
    assert 20 < value.temperature_c < 25 and value.thermal_direction == "cooling"
    sim.set_setpoint(35)
    clock.sleep(10)
    assert sim.snapshot().thermal_direction == "heating"
    sim.stop()
    assert sim.snapshot().thermal_direction == "idle"
    sim.disconnect()
    assert not sim.is_connected
    sim.connect()
    assert sim.snapshot().run_reported is False


def test_simulator_long_run_and_faults():
    clock = Clock()
    sim = Simulator(clock=clock, sleep=clock.sleep)
    sim.connect()
    sim.start()
    clock.sleep(100000)
    assert sim.read_temperature() == pytest.approx(20)
    sim.inject_faults(0x30)
    assert sim.read_faults().active == ("rtd_open", "rtd_short")
    with pytest.raises(SafetyError):
        sim.start()
    sim.stop()
    sim.inject_faults(0)
    sim.set_setpoint(50, "F")
    assert sim.read_setpoint("F") == 50


def test_simulated_fault_injection_and_delay():
    clock = Clock()
    sim = Simulator(clock=clock, sleep=clock.sleep, delay=0.2)
    sim.connect()
    sim.snapshot()
    assert clock() == 0.2
    sim.inject_timeout()
    with pytest.raises(CommunicationTimeout):
        sim.snapshot()
    assert sim.snapshot().quality == "good"
    sim.inject_disconnect()
    with pytest.raises(ConnectionError):
        sim.snapshot()
    assert not sim.is_connected
    delayed = Simulator(clock=clock, sleep=clock.sleep, delay=1, timeout=0.1)
    delayed.connect()
    with pytest.raises(CommunicationTimeout):
        delayed.snapshot()


@pytest.mark.parametrize("backend", ["simulator", "driver"])
def test_shared_public_api(backend, rig):
    device = Simulator() if backend == "simulator" else rig[0]
    if backend == "driver":
        arm(device, control=True)
    else:
        device.connect()
    assert device.is_connected
    assert device.read_setpoint() == 20
    assert device.set_setpoint(10) == 10
    device.start()
    assert device.snapshot().run_intent is True
    device.stop()
    assert device.snapshot().run_intent is False
    device.disconnect()
    assert not device.is_connected


def test_csv_roundtrip_and_no_overwrite(tmp_path):
    sim = Simulator()
    sim.connect()
    snapshot = replace(sim.snapshot(), error='commas, quotes "here"\nand newlines')
    logger = CsvLogger(tmp_path, "test-session", metadata={"source": "simulation"})
    logger.sample(snapshot)
    logger.sample(replace(snapshot, temperature_c=None, quality="stale"))
    logger.event(Event(utc_now(), "set_setpoint", "requested", '"x",\ny'), "simulation")
    logger.close()
    with (tmp_path / "test-session-samples.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames) == SAMPLE_FIELDS
        rows = list(reader)
    assert rows[0]["error"] == snapshot.error
    assert rows[1]["temperature_c"] == "" and rows[1]["quality"] == "stale"
    assert rows[0]["source"] == "simulation" and rows[0]["timestamp_utc"].endswith("+00:00")
    with (tmp_path / "test-session-events.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames) == EVENT_FIELDS
        assert list(reader)[0]["detail"] == '"x",\ny'
    assert json.loads((tmp_path / "test-session-session.json").read_text())["schema_version"] == 1
    with pytest.raises(FileExistsError):
        CsvLogger(tmp_path, "test-session")
    with pytest.raises(ValueError):
        CsvLogger(tmp_path, "../escape")


def test_worker_runs_without_browser_and_bounded_history(tmp_path):
    logger = CsvLogger(tmp_path, "headless")
    service = AcquisitionService(Simulator(), history_size=2, interval=1, logger=logger)
    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        service.start()  # Idempotent: no second worker.
        assert completed(service, service.submit("set_setpoint", value=23)).state == "completed"
        assert completed(service, service.submit("start")).state == "completed"
        wait_until(lambda: service.latest.run_reported is True)
        wait_until(lambda: len(service.history) == 2)
        assert (
            service.history.maxlen
            if hasattr(service.history, "maxlen")
            else isinstance(service.history, tuple)
        )
        with pytest.raises(FrozenInstanceError):
            service.latest.connected = False
    finally:
        service.close()
    assert not service.is_running
    assert not service.latest.connected
    with (tmp_path / "headless-samples.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) >= 2
    with pytest.raises(RuntimeError):
        service.start()


def test_service_recovers_temporary_simulator_disconnect():
    sim = Simulator()
    service = AcquisitionService(sim, interval=1, reconnect_initial=0.05, reconnect_max=0.1)
    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        sim.inject_disconnect()
        wait_until(lambda: any(s.quality == "stale" for s in service.history))
        wait_until(lambda: service.latest.quality == "good")
        assert completed(service, service.submit("disconnect")).state == "completed"
        time.sleep(0.15)
        assert not sim.is_connected  # User disconnect disables automatic reopen.
    finally:
        service.close()


def test_intentional_disconnect_is_a_normal_state():
    service = AcquisitionService(Simulator(), interval=1)
    service.start()
    try:
        wait_until(lambda: len(service.history) > 0)
        assert service.latest.quality == "disconnected"
        assert service.latest.error is None and service.error is None
        assert not service.latest.connected
        assert completed(service, service.submit("connect")).state == "completed"
        assert completed(service, service.submit("disconnect")).state == "completed"
        wait_until(lambda: service.latest.quality == "disconnected")
        assert service.latest.error is None
    finally:
        service.close()


def test_service_driver_reconnect_does_not_rearm(rig):
    device, port, _, _ = rig
    arm(device)
    service = AcquisitionService(device, interval=1, reconnect_initial=0.05, reconnect_max=0.1)
    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        port.drop_reply = True
        wait_until(lambda: service.latest.quality == "stale")
        count = len(port.writes)
        port.drop_reply = False
        wait_until(lambda: device.is_connected)
        assert not device.cached_snapshot.armed
        time.sleep(0.1)
        assert len(port.writes) == count
    finally:
        service.close()


def test_stop_cancels_queued_start_and_queue_is_bounded():
    sim = Simulator()
    sim.connect()
    service = AcquisitionService(sim, queue_size=2)
    entered, release = threading.Event(), threading.Event()
    original = sim.set_setpoint

    def slow_set(value, unit="C"):
        entered.set()
        release.wait(3)
        return original(value, unit)

    sim.set_setpoint = slow_set
    service.start()
    try:
        wait_until(lambda: service.latest.quality == "good")
        key = service.submit("set_setpoint", value=22)
        assert entered.wait(2)
        start = service.submit("start")
        service.submit("set_setpoint", value=24)
        with pytest.raises(RuntimeError, match="full"):
            service.submit("start")
        stop = service.submit("stop")
        assert service.result(start).state == "cancelled"
        release.set()
        assert completed(service, key).state == "completed"
        assert completed(service, stop).state == "completed"
        assert sim.snapshot().run_reported is False
    finally:
        release.set()
        service.close()


def test_logging_failure_inhibits_controls_but_allows_stop(tmp_path, monkeypatch):
    logger = CsvLogger(tmp_path, "failure")
    service = AcquisitionService(Simulator(), logger=logger)

    def disk_full(*args):
        raise OSError("disk full")

    monkeypatch.setattr(logger, "sample", disk_full)
    service.start(connect=True)
    try:
        wait_until(lambda: service.error is not None)
        with pytest.raises(SafetyError, match="CSV"):
            service.submit("start")
        assert completed(service, service.submit("stop")).state == "completed"
    finally:
        service.close()


def test_failed_command_visible_and_service_keeps_running():
    service = AcquisitionService(Simulator())
    service.start(connect=True)
    try:
        wait_until(lambda: service.latest.quality == "good")
        result = completed(service, service.submit("set_setpoint", value=200))
        assert result.state == "failed" and "ValueError" in result.detail
        assert service.is_running
        with pytest.raises(ValueError):
            service.submit("raw_command", value="E0")
    finally:
        service.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval": 0.5},
        {"interval": float("nan")},
        {"history_size": 0},
        {"queue_size": 0},
        {"reconnect_initial": 0},
    ],
)
def test_service_configuration_validation(kwargs):
    with pytest.raises(ValueError):
        AcquisitionService(Simulator(), **kwargs)
