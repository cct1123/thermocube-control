from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import Clock, FakeSerial, arm

from thermocube.driver import QUERY_ACK, ThermoCube
from thermocube.models import (
    CommunicationTimeout,
    ConnectionError,
    FaultProfile,
    Mode,
    ProtocolError,
    SafetyError,
    SetpointRejected,
)
from thermocube.transport import HardwareApproval, SerialConfig, SerialTransport, discover_ports


def test_defaults_and_locked_writes(rig):
    device, port, _, _ = rig
    device.connect()
    assert device.is_connected and port.writes == []
    with pytest.raises(SafetyError):
        device.read_temperature()
    for action in (device.start, device.stop, lambda: device.set_setpoint(20)):
        with pytest.raises(SafetyError):
            action()
    assert port.writes == []
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    assert device.read_temperature() == 25
    assert device.read_setpoint("F") == 68
    assert not device.read_faults().has_fault
    assert port.writes == [b"\x89", b"\x81", b"\x88"]
    with pytest.raises(SafetyError):
        device.enable_control("yes")
    device.disconnect()
    assert len(port.writes) == 3


def test_control_readback_and_stop_queries(rig):
    device, port, _, _ = rig
    arm(device, control=True)
    assert device.set_setpoint(50, "F") == 50
    assert port.writes == [b"\x88", b"\xa1\xf4\x01", b"\x81"]
    device.start()
    assert port.writes[-2:] == [b"\x88", b"\xe0"]
    assert device.read_temperature() == 25
    assert port.writes[-1] == b"\xc9"
    device.stop()
    snapshot = device.snapshot()
    assert port.writes[-4:] == [b"\xa0", b"\x88", b"\x89", b"\x81"]
    assert snapshot.run_intent is False and snapshot.run_reported is None
    assert snapshot.quality == "good"
    assert any(e.outcome == "confirmed" for e in device.drain_events())


def test_quantized_bounds_and_ignored_setpoint(rig):
    device, port, _, _ = rig
    arm(device, control=True)
    for value in (-5.1, 50.1, float("nan")):
        with pytest.raises(ValueError):
            device.set_setpoint(value)
    assert port.writes == []
    port.ignore_setpoint = True
    with pytest.raises(SetpointRejected):
        device.set_setpoint(10)
    assert port.writes == [b"\x88", b"\xa1\xf4\x01", b"\x81"]
    assert not device.cached_snapshot.armed


def test_partial_reads_and_pacing(rig):
    device, port, clock, _ = rig
    arm(device, control=True)
    port.chunk = 1
    device.snapshot()
    device.set_setpoint(22)
    device.stop()
    assert all(b - a >= 0.35 - 1e-12 for a, b in zip(port.starts, port.starts[1:], strict=False))
    for t in port.starts:
        assert sum(t <= stamp < t + 1 for stamp in port.starts) <= 3
    clock.sleep(100)
    before = len(port.starts)
    device.snapshot()
    assert port.starts[before + 1] - port.starts[before] >= 0.35 - 1e-12


def test_concurrent_queries_and_stop(rig):
    device, port, _, _ = rig
    arm(device, control=True, run=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(device.read_temperature) for _ in range(15)]
        futures.insert(3, pool.submit(device.stop))
        for future in futures:
            future.result(timeout=5)
    stop_index = port.writes.index(b"\xa0")
    assert all(not payload[0] & 64 for payload in port.writes[stop_index:])
    assert all(b - a >= 0.35 - 1e-12 for a, b in zip(port.starts, port.starts[1:], strict=False))


@pytest.mark.parametrize(
    "failure,error",
    [
        ("drop_reply", CommunicationTimeout),
        ("short_write", ProtocolError),
        ("fail_write", ConnectionError),
        ("fail_read", ConnectionError),
    ],
)
def test_uncertain_failure_disarms_no_replay(rig, failure, error):
    device, port, clock, transport = rig
    arm(device, control=True)
    setattr(port, failure, True)
    with pytest.raises(error):
        device.read_temperature()
    assert not device.is_connected and not device.cached_snapshot.armed
    assert transport.needs_recovery
    writes = len(port.writes)
    setattr(port, failure, False)
    port.rx = b""  # Operator-side stream reset, not driver automatic draining.
    device.connect()
    assert len(port.writes) == writes
    with pytest.raises(SafetyError):
        device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    with pytest.raises(SafetyError):
        device.confirm_recovery("yes")
    device.confirm_recovery("STREAM RESET AND DEVICE STATE VERIFIED")
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    assert device.read_temperature() == 25
    assert device.cached_snapshot.mode == Mode.OBSERVE


@pytest.mark.parametrize(
    "reply,error",
    [(b"x", CommunicationTimeout), (b"xyz", ProtocolError), (b"\xff\xff", ProtocolError)],
)
def test_malformed_replies(rig, reply, error):
    device, port, _, _ = rig
    arm(device)
    port.override = reply
    with pytest.raises(error):
        device.read_temperature()
    assert not device.cached_snapshot.armed


def test_late_bytes_not_used(rig):
    device, port, _, _ = rig
    arm(device)
    port.rx = b"\xf4\x01"
    with pytest.raises(ProtocolError, match="late"):
        device.read_temperature()
    assert port.writes == []


def test_fault_inhibits_polling_but_explicit_stop_remains(rig):
    device, port, _, _ = rig
    arm(device, control=True, run=True)
    port.faults = 16
    assert device.read_faults().active == ("rtd_open",)
    with pytest.raises(SafetyError):
        device.read_temperature()
    device.stop()
    assert port.writes[-1] == b"\xa0"


def test_m5_profile_standby_status():
    clock = Clock()
    port = FakeSerial(clock)
    port.m5 = True
    driver = ThermoCube(
        SerialTransport(
            SerialConfig("FAKE-M5"), serial_factory=lambda _: port, clock=clock, sleep=clock.sleep
        ),
        profile=FaultProfile.M5,
    )
    arm(driver)
    snapshot = driver.snapshot()
    assert snapshot.run_reported is False
    assert snapshot.faults.standby and not snapshot.faults.has_fault


def test_open_retry_only_before_commands():
    clock, attempts = Clock(), []
    port = FakeSerial(clock)

    def factory(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("temporarily unavailable")
        return port

    transport = SerialTransport(
        SerialConfig("OPEN"), serial_factory=factory, clock=clock, sleep=clock.sleep
    )
    transport.connect()
    assert len(attempts) == 2 and clock() == 0.5 and not port.writes


def test_hardware_gate_no_enumeration_or_open():
    with pytest.raises(SafetyError):
        discover_ports()
    transport = SerialTransport(SerialConfig("UNAPPROVED"))
    with pytest.raises(SafetyError):
        transport.connect()
    with pytest.raises(SafetyError):
        ThermoCube(transport, mode=Mode.CONTROL)


def test_real_factory_serial_configuration(monkeypatch):
    import serial

    clock = Clock()
    port = FakeSerial(clock)
    port.is_open = False
    port.open = lambda: setattr(port, "is_open", True)
    monkeypatch.setattr(serial, "Serial", lambda port=None: endpoint)
    endpoint = port
    approval = HardwareApproval("offline-test-record", FaultProfile.LEGACY)
    transport = SerialTransport(SerialConfig("CONFIG-FAKE"), approval=approval)
    transport.connect()
    assert (port.baudrate, port.bytesize, port.parity, port.stopbits) == (9600, 8, "N", 1)
    assert not any((port.xonxoff, port.rtscts, port.dsrdtr, port.rts, port.dtr))
    with pytest.raises(SafetyError, match="framing"):
        transport.exchange(b"\x89", 2)
    assert not port.writes
    other = SerialTransport(SerialConfig("CONFIG-FAKE"), approval=approval)
    with pytest.raises(SafetyError, match="owned"):
        other.connect()
    transport.disconnect()


def test_transport_only_and_single_owner(rig):
    _, _, _, transport = rig
    with pytest.raises(SafetyError):
        ThermoCube(transport)
    clock = Clock()
    port = FakeSerial(clock)
    driver = ThermoCube(
        SerialTransport(SerialConfig("T"), serial_factory=lambda _: port), mode=Mode.TRANSPORT
    )
    driver.connect()
    with pytest.raises(SafetyError):
        driver.arm_queries(run=False, acknowledgement=QUERY_ACK)
    assert not port.writes


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout": 0},
        {"write_timeout": float("inf")},
        {"command_interval": 0.333},
        {"open_attempts": 10},
        {"retry_delay": -1},
    ],
)
def test_invalid_transport_settings(kwargs):
    with pytest.raises(ValueError):
        SerialConfig("FAKE", **kwargs)
