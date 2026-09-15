import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
import serial
from conftest import arm

from thermocube import SafetyError, ThermoCube
from thermocube.controller import (
    CONTROL_ACK,
    QUERY_ACK,
    RECOVERY_ACK,
    decode_faults,
    decode_temperature,
    encode_temperature,
    setpoint_payload,
    to_celsius,
    validate_limits,
)


def test_connect_close_are_zero_tx_and_explicit(hardware):
    device, wire, _ = hardware
    assert not device.is_connected
    with device:
        assert device.is_connected and not device.queries_enabled and not device.control_enabled
        device.connect()
        assert wire.opens == 1
        for operation in (
            device.read_temperature,
            device.start,
            device.stop,
            lambda: device.set_setpoint(10),
        ):
            with pytest.raises(SafetyError):
                operation()
        assert wire.settings["baudrate"] == 9600
        assert wire.settings["bytesize"] == 8 and wire.settings["parity"] == "N"
        assert wire.settings["stopbits"] == 1
        assert not any(wire.settings[k] for k in ("xonxoff", "rtscts", "dsrdtr"))
        assert wire.rts is False and wire.dtr is False
        if os.name == "posix":
            assert wire.exclusive is True
    assert not device.is_connected and wire.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(port=""),
        dict(port="loop://"),
        dict(port=None),
        dict(profile="guess"),
        dict(binary_framing_confirmed=1),
        dict(timeout=0),
        dict(timeout=float("nan")),
        dict(timeout=True),
        dict(command_interval=0.333),
        dict(limits_c=(False, 40)),
    ],
)
def test_invalid_configuration_never_opens(hardware, kwargs):
    _, wire, _ = hardware
    with pytest.raises(ValueError):
        ThermoCube(**{"port": "TEST-PORT", **kwargs})
    assert wire.opens == 0


def test_query_context_requires_profile_framing_and_deliberate_ack(hardware):
    device, wire, _ = hardware
    with pytest.raises(ConnectionError):
        device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    for kwargs in ({}, {"profile": "legacy-r2"}, {"binary_framing_confirmed": True}):
        with ThermoCube("TEST-PORT", **kwargs) as locked:
            with pytest.raises(SafetyError):
                locked.arm_queries(run=False, acknowledgement=QUERY_ACK)
    device.connect()
    for run, ack in ((False, "yes"), (1, QUERY_ACK)):
        with pytest.raises(SafetyError):
            device.arm_queries(run=run, acknowledgement=ack)
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    with pytest.raises(SafetyError):
        device.arm_queries(run=True, acknowledgement=QUERY_ACK)
    with pytest.raises(SafetyError):
        device.enable_control("yes")
    assert wire.calls == []


def test_real_driver_public_operations_use_exact_state_bits(hardware):
    device, wire, clock = hardware
    arm(device, control=True)
    assert device.read_faults().raw == 64
    assert device.read_temperature() == 25
    assert device.read_setpoint("F") == 68
    assert device.set_setpoint(10) == 10
    device.start()
    assert device.read_temperature("F") == 77
    device.stop()
    assert device.read_temperature() == 25
    expected = [
        b"\x88",
        b"\x89",
        b"\x81",
        b"\x88",
        b"\xa1\xf4\x01",
        b"\x81",
        b"\x88",
        b"\xe0",
        b"\xc9",
        b"\xa0",
        b"\x89",
    ]
    assert [data for _, data in wire.calls] == expected
    assert wire.calls[0][0] >= 0.35
    stamps = [stamp for stamp, _ in wire.calls]
    assert all(b - a >= 0.35 - 1e-9 for a, b in zip(stamps, stamps[1:], strict=False))
    assert all(sum(t <= other < t + 1 for other in stamps) <= 3 for t in stamps)
    assert clock.monotonic() >= 0.35 * len(expected) - 1e-9


def test_status_is_fault_first_without_merging_old_values(hardware):
    device, wire, _ = hardware
    arm(device)
    healthy = device.status()
    assert (healthy.temperature_c, healthy.setpoint_c, healthy.reported_run) == (25, 20, False)
    assert [data for _, data in wire.calls] == [b"\x88", b"\x89", b"\x81"]
    wire.faults = 1
    faulted = device.status()
    assert faulted.temperature_c is None and faulted.setpoint_c is None
    assert faulted.faults.has_fault and not device.queries_enabled
    assert len(wire.calls) == 4


@pytest.mark.parametrize("run", [False, True])
def test_manual_query_and_setpoint_vectors_through_public_api(hardware, run):
    device, wire, _ = hardware
    arm(device, control=True, run=run)
    state = device.status()
    assert state.reported_run is run
    assert device.set_setpoint(50, "F") == 50
    expected = (
        [b"\xc8", b"\xc9", b"\xc1", b"\xc8", b"\xe1\xf4\x01", b"\xc1"]
        if run
        else [b"\x88", b"\x89", b"\x81", b"\x88", b"\xa1\xf4\x01", b"\x81"]
    )
    assert [data for _, data in wire.calls] == expected


def test_faults_and_mismatch_block_start_setpoint_but_allow_explicit_stop(hardware):
    device, wire, _ = hardware
    arm(device, control=True)
    wire.faults = 128
    with pytest.raises(SafetyError):
        device.set_setpoint(10)
    assert [data for _, data in wire.calls] == [b"\x88"]
    device.stop()
    assert wire.calls[-1][1] == b"\xa0"
    arm(device, control=True)
    with pytest.raises(SafetyError):
        device.start()
    wire.faults, wire.running, wire.ignore_run = 0, True, True
    arm(device, control=True)
    with pytest.raises(SafetyError):
        device.start()
    assert all(data != b"\xe0" for _, data in wire.calls)


def test_rearming_cannot_bypass_fault_preflight(hardware):
    device, wire, _ = hardware
    arm(device, control=True)
    wire.faults = 1
    device.read_faults()
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    assert not device.control_enabled
    device.enable_control(CONTROL_ACK)
    with pytest.raises(SafetyError):
        device.set_setpoint(10)
    assert [data for _, data in wire.calls] == [b"\x88", b"\x88"]


def test_invalid_setpoints_do_not_transmit_and_ignored_write_is_not_replayed(hardware):
    device, wire, _ = hardware
    arm(device, control=True)
    for value in (-1, 41, True, None, "10", float("nan")):
        with pytest.raises(ValueError):
            device.set_setpoint(value)
    assert wire.calls == []
    wire.ignore_setpoint = True
    with pytest.raises(SafetyError, match="readback"):
        device.set_setpoint(10)
    assert [data for _, data in wire.calls] == [b"\x88", b"\xa1\xf4\x01", b"\x81"]
    assert not device.queries_enabled


@pytest.mark.parametrize(
    "failure,error",
    [
        ("timeout", TimeoutError),
        ("short", TimeoutError),
        ("extra", ConnectionError),
        ("malformed", ConnectionError),
        ("late-read", TimeoutError),
        ("late-write", TimeoutError),
        ("short-write", ConnectionError),
        ("disconnect", ConnectionError),
        ("serial-timeout", TimeoutError),
        ("implausible", ConnectionError),
    ],
)
def test_uncertain_io_closes_locks_and_never_retries(hardware, failure, error):
    device, wire, _ = hardware
    arm(device)
    if failure == "timeout":
        wire.replies.append(b"")
    elif failure == "short":
        wire.replies.append(b"\x02")
    elif failure == "extra":
        wire.replies.append(b"\x02\x03\n")
    elif failure == "malformed":
        wire.bad_read = "xx"
    elif failure == "late-read":
        wire.read_delay = 0.6
    elif failure == "late-write":
        wire.write_delay = 0.6
    elif failure == "short-write":
        wire.short_write = True
    elif failure == "disconnect":
        wire.write_error = OSError("link lost")
    elif failure == "serial-timeout":
        wire.write_error = serial.SerialTimeoutException("late")
    elif failure == "implausible":
        wire.temperature = 65535
    with pytest.raises(error):
        device.read_temperature()
    assert not device.is_connected and not device.control_enabled and device.needs_recovery
    assert len(wire.calls) == 1
    device.connect()
    with pytest.raises(SafetyError, match="recovery"):
        device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    assert len(wire.calls) == 1


def test_partial_reads_and_explicit_recovery(hardware):
    device, wire, _ = hardware
    arm(device)
    wire.chunk_size = 1
    wire.read_delay = 0.2
    assert device.read_temperature() == 25
    wire.read_delay = 0.3
    with pytest.raises(TimeoutError):
        device.read_temperature()
    device.connect()
    wire.rx.extend(b"late")
    with pytest.raises(SafetyError):
        device.confirm_recovery(RECOVERY_ACK)
    wire.rx.clear()  # Fake operator stream reset; the controller never flushes.
    with pytest.raises(SafetyError):
        device.confirm_recovery("yes")
    device.confirm_recovery(RECOVERY_ACK)
    assert not device.queries_enabled
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    wire.read_delay = 0
    assert device.read_temperature() == 25


def test_late_buffered_bytes_prevent_any_new_command(hardware):
    device, wire, _ = hardware
    arm(device)
    wire.rx.extend(b"late")
    with pytest.raises(ConnectionError):
        device.read_temperature()
    assert wire.calls == [] and device.needs_recovery


def test_reconnect_requires_rearming_and_keeps_pacing(hardware):
    device, wire, _ = hardware
    arm(device, control=True)
    device.stop()
    device.disconnect()
    device.connect()
    assert not device.queries_enabled and not device.control_enabled
    arm(device)
    device.read_temperature()
    assert wire.calls[-1][0] - wire.calls[-2][0] >= 0.35 - 1e-9


def test_open_is_one_attempt_and_close_failure_is_explicit(hardware):
    device, wire, _ = hardware
    wire.open_error = OSError("busy")
    with pytest.raises(ConnectionError):
        device.connect()
    assert wire.opens == 1 and wire.calls == []
    wire.open_error = None
    device.connect()
    wire.close_error = OSError("close failed")
    with pytest.raises(ConnectionError):
        device.disconnect()
    assert not device.is_connected and device.needs_recovery


def test_concurrent_setpoint_operations_are_not_interleaved(hardware):
    device, wire, _ = hardware
    arm(device, control=True)
    barrier = threading.Barrier(8)
    wire.on_write = lambda _: time.sleep(0.001)

    def write(value):
        barrier.wait()
        return device.set_setpoint(value)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(write, range(10, 18))) == list(range(10, 18))
    assert len(wire.calls) == 24
    for index in range(0, 24, 3):
        group = [data for _, data in wire.calls[index : index + 3]]
        assert group[0] == b"\x88" and group[1][0] == 0xA1 and group[2] == b"\x81"


def test_core_import_needs_no_gui_and_starts_no_threads():
    script = """
import builtins, threading, serial
from serial.tools import list_ports
def forbidden(*args, **kwargs): raise AssertionError('Physical access forbidden')
serial.Serial.open = forbidden
list_ports.comports = forbidden
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'dash', 'plotly', 'flask', 'waitress'}:
        raise AssertionError('Core imported GUI')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
before = threading.enumerate()
from thermocube import ThermoCube, Simulator
ThermoCube('TEST-PORT')
Simulator()
assert threading.enumerate() == before
from thermocube.gui import Monitor, main
import sys
sys.argv = ['thermocube', '--headless', '--duration', '.1']
main()
assert threading.enumerate() == before
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_pacing_with_real_monotonic_clock_and_fake_serial(hardware, monkeypatch):
    from thermocube import controller

    device, wire, _ = hardware
    monkeypatch.setattr(controller, "time", time)
    wire.clock = time
    arm(device)
    device.status()
    stamps = [stamp for stamp, _ in wire.calls]
    assert len(stamps) == 3
    assert all(b - a >= 0.35 for a, b in zip(stamps, stamps[1:], strict=False))


def test_uncertain_start_does_not_trigger_retry_or_implicit_stop(hardware):
    device, wire, _ = hardware
    arm(device, control=True)

    def unexpected_ack(packet):
        if packet == b"\xe0":
            wire.rx.extend(b"\r")

    wire.on_write = unexpected_ack
    with pytest.raises(ConnectionError):
        device.start()
    assert wire.running and not device.is_connected
    assert [data for _, data in wire.calls] == [b"\x88", b"\xe0"]
    with pytest.raises(ConnectionError):
        device.stop()


def test_explicit_stop_then_disconnect_waits_for_inflight_operation(hardware):
    device, wire, _ = hardware
    arm(device, control=True)
    entered, release = threading.Event(), threading.Event()

    def block_start(packet):
        if packet == b"\xe0":
            entered.set()
            assert release.wait(3)

    def shutdown():
        device.stop()
        device.disconnect()

    wire.on_write = block_start
    with ThreadPoolExecutor(max_workers=2) as pool:
        starting = pool.submit(device.start)
        assert entered.wait(2)
        stopping = pool.submit(shutdown)
        release.set()
        starting.result(timeout=3)
        stopping.result(timeout=3)
    assert [data for _, data in wire.calls] == [b"\x88", b"\xe0", b"\xa0"]
    assert not wire.running and not device.is_connected


def test_manual_temperatures_and_complete_wire_roundtrip():
    for value, expected in (
        (0.1, b"\x01\x00"),
        (30, b"\x2c\x01"),
        (50, b"\xf4\x01"),
        (70, b"\xbc\x02"),
    ):
        assert encode_temperature(value, "F") == expected
        assert decode_temperature(expected, "F") == value
    for word in range(65536):
        data = word.to_bytes(2, "little")
        for unit in ("C", "F"):
            assert encode_temperature(decode_temperature(data, unit), unit) == data
    assert encode_temperature(10) == b"\xf4\x01"
    assert encode_temperature(-5) == b"\xe6\x00"
    assert encode_temperature(Decimal("50.05"), "F") == b"\xf5\x01"
    assert to_celsius(32, "F") == 0


def test_input_range_and_quantized_bounds():
    for value in (None, "10", True, float("nan"), float("inf"), -1, 6554):
        with pytest.raises(ValueError):
            encode_temperature(value, "F")
    for bounds in ((1, 1), (2, 1), (True, 3), (0, float("inf")), (1,), "1,2"):
        with pytest.raises(ValueError):
            validate_limits(bounds)
    assert validate_limits([0, 40]) == (0, 40)
    assert validate_limits(None) is None
    with pytest.raises(ValueError):
        setpoint_payload(10.03, "C", (0, 10.04))
    for unit in ("K", "c", ""):
        with pytest.raises(ValueError):
            decode_temperature(b"xx", unit)
    for data in (b"", b"x", b"xxx", "xx"):
        with pytest.raises(ConnectionError):
            decode_temperature(data)


def test_every_fault_byte_preserves_unknown_bits_and_profile_meanings():
    for raw in range(256):
        legacy = decode_faults(bytes([raw]), "legacy-r2")
        assert legacy.raw == raw and legacy.unknown_mask == raw & 0xC4
        assert legacy.standby is None
        for bit, name in (
            (0, "tank_level_low"),
            (1, "fan_fail"),
            (3, "pump_fail"),
            (4, "rtd_open"),
            (5, "rtd_short"),
        ):
            assert (name in legacy.active) == bool(raw & (1 << bit))
        m5 = decode_faults(bytes([raw]), "thermocube-ii-m5")
        assert m5.raw == raw and m5.unknown_mask == raw & 128 and m5.standby == bool(raw & 64)
        for bit, name in ((2, "flow_fault"), (4, "leak_detected"), (5, "rtd_fault")):
            assert (name in m5.active) == bool(raw & (1 << bit))
    assert not decode_faults(b"\x40", "thermocube-ii-m5").has_fault
    assert decode_faults(b"\x40", "legacy-r2").has_fault
    with pytest.raises(ValueError):
        decode_faults(b"\x00", "guessed")
    with pytest.raises(ConnectionError):
        decode_faults(b"\x00\x00", "legacy-r2")
