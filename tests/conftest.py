"""All tests deny real serial discovery/open, including application imports."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
import serial
from serial.tools import list_ports

_session_guard = pytest.MonkeyPatch()


def forbidden(*args, **kwargs):
    raise AssertionError("A hardware-free test attempted real serial access")


def pytest_sessionstart(session):
    # Active before test collection imports application modules.
    _session_guard.setattr(serial.Serial, "open", forbidden)
    _session_guard.setattr(list_ports, "comports", forbidden)


def pytest_sessionfinish(session, exitstatus):
    _session_guard.undo()


@pytest.fixture(autouse=True)
def deny_hardware(monkeypatch):
    monkeypatch.setattr(serial.Serial, "open", forbidden)
    monkeypatch.setattr(list_ports, "comports", forbidden)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.origin = datetime.now(UTC)
        self.lock = threading.RLock()

    def __call__(self):
        with self.lock:
            return self.t

    def sleep(self, duration):
        with self.lock:
            self.t += duration

    def now(self):
        return self.origin + timedelta(seconds=self())


class FakeSerial:
    """Independent binary endpoint: it does not call the production codec."""

    def __init__(self, clock):
        self.clock = clock
        self.is_open = True
        self.timeout = 0.5
        self.rx = b""
        self.writes = []
        self.starts = []
        self.setpoint_raw = 680  # 68 F = 20 C
        self.temperature_raw = 770  # 77 F = 25 C
        self.faults = 0
        self.run = False
        self.m5 = False
        self.chunk = 100
        self.override = None
        self.drop_reply = False
        self.fail_write = False
        self.fail_read = False
        self.short_write = False
        self.ignore_setpoint = False
        self.write_delay = 0

    @property
    def in_waiting(self):
        return len(self.rx)

    def write(self, payload):
        if self.fail_write:
            raise OSError("USB lost")
        self.starts.append(self.clock())
        self.writes.append(payload)
        self.clock.sleep(self.write_delay)
        command = payload[0]
        self.run = bool(command & 64)
        parameter = command & 31
        if command & 32:
            if parameter == 1 and not self.ignore_setpoint:
                self.setpoint_raw = payload[1] + 256 * payload[2]
        elif parameter in (1, 9):
            raw = self.setpoint_raw if parameter == 1 else self.temperature_raw
            self.rx = bytes([raw % 256, raw // 256])
        elif parameter == 8:
            self.rx = bytes([self.faults | (64 if self.m5 and not self.run else 0)])
        if self.override is not None:
            self.rx, self.override = self.override, None
        if self.drop_reply:
            self.rx = b""
        return len(payload) - 1 if self.short_write else len(payload)

    def read(self, size):
        if self.fail_read:
            raise OSError("USB read lost")
        if not self.rx:
            self.clock.sleep(self.timeout)
            return b""
        count = min(size, self.chunk)
        result, self.rx = self.rx[:count], self.rx[count:]
        return result

    def close(self):
        self.is_open = False


@pytest.fixture
def rig():
    from thermocube.driver import ThermoCube
    from thermocube.models import TemperatureLimits
    from thermocube.transport import SerialConfig, SerialTransport

    clock = Clock()
    endpoint = FakeSerial(clock)

    def factory(config):
        endpoint.is_open = True
        return endpoint

    transport = SerialTransport(
        SerialConfig("FAKE"), serial_factory=factory, clock=clock, sleep=clock.sleep
    )
    device = ThermoCube(transport, limits=TemperatureLimits(-5, 50), now=clock.now)
    return device, endpoint, clock, transport


def arm(device, *, control=False, run=False):
    from thermocube.driver import CONTROL_ACK, QUERY_ACK

    device.connect()
    device.arm_queries(run=run, acknowledgement=QUERY_ACK)
    if control:
        device.enable_control(CONTROL_ACK)
