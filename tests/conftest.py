"""Block real serial I/O before collection; test the production serial code path."""

from collections import deque

import pytest
import serial
from serial.tools import list_ports


def forbidden(*args, **kwargs):
    raise AssertionError("Physical serial access is forbidden in offline tests")


def pytest_sessionstart(session):
    session.serial_guard = pytest.MonkeyPatch()
    session.serial_guard.setattr(serial.Serial, "open", forbidden)
    session.serial_guard.setattr(list_ports, "comports", forbidden)


def pytest_sessionfinish(session, exitstatus):
    session.serial_guard.undo()


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, duration):
        self.value += max(0, duration)


class FakePort:
    """Literal-byte R2 peer independent of the production codec and API simulator."""

    def __init__(self, clock):
        self.clock = clock
        self.is_open = False
        self.calls = []
        self.opens = self.closes = 0
        self.rx = bytearray()
        self.replies = deque()
        self.running = False
        self.temperature, self.setpoint = 770, 680  # 25 C and 20 C, in tenths F.
        self.faults = 0
        self.m5 = True
        self.ignore_setpoint = self.ignore_run = False
        self.chunk_size = 2
        self.read_delay = self.write_delay = 0
        self.write_error = self.open_error = self.close_error = None
        self.bad_read = None
        self.short_write = False
        self.on_write = None

    def configure(self, **kwargs):
        self.settings = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)
        return self

    def open(self):
        self.opens += 1
        if self.open_error:
            raise self.open_error
        self.is_open = True

    def close(self):
        self.closes += 1
        self.is_open = False
        if self.close_error:
            raise self.close_error

    @property
    def in_waiting(self):
        return len(self.rx)

    def write(self, data):
        self.calls.append((self.clock.monotonic(), data))
        if self.on_write:
            self.on_write(data)
        self.clock.sleep(self.write_delay)
        if self.write_error:
            raise self.write_error
        if self.short_write:
            return 0
        command = data[0]
        assert command & 128, "Driver must deliberately select REMOTE"
        if not self.ignore_run:
            self.running = bool(command & 64)
        parameter, write = command & 31, bool(command & 32)
        if write:
            if parameter == 1 and not self.ignore_setpoint:
                self.setpoint = data[1] + 256 * data[2]
            return len(data)
        if self.replies:
            reply = self.replies.popleft()
        elif parameter == 8:
            reply = bytes([self.faults | (64 if self.m5 and not self.running else 0)])
        elif parameter in (1, 9):
            raw = self.setpoint if parameter == 1 else self.temperature
            reply = bytes([raw % 256, raw // 256])
        else:
            raise AssertionError(f"Unexpected parameter: {parameter}")
        self.rx.extend(reply)
        return len(data)

    def read(self, size):
        self.clock.sleep(self.read_delay)
        if self.bad_read is not None:
            return self.bad_read
        if not self.rx:
            self.clock.sleep(self.timeout)
            return b""
        count = min(size, self.chunk_size)
        reply = bytes(self.rx[:count])
        del self.rx[:count]
        return reply


@pytest.fixture
def hardware(monkeypatch):
    from thermocube import ThermoCube, controller

    clock = Clock()
    wire = FakePort(clock)
    monkeypatch.setattr(controller, "time", clock)
    monkeypatch.setattr(serial, "Serial", wire.configure)
    device = ThermoCube(
        "TEST-PORT", profile="thermocube-ii-m5", binary_framing_confirmed=True, limits_c=(0, 40)
    )
    yield device, wire, clock
    wire.close_error = None
    device.disconnect()


def arm(device, *, control=False, run=False):
    from thermocube.controller import CONTROL_ACK, QUERY_ACK

    device.connect()
    device.arm_queries(run=run, acknowledgement=QUERY_ACK)
    if control:
        device.enable_control(CONTROL_ACK)
