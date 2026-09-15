import pytest
from conftest import Clock, arm

from thermocube import SafetyError, Simulator


def test_simulator_heat_cool_standby_and_connection_persistence(monkeypatch):
    from thermocube import simulator

    clock = Clock()
    monkeypatch.setattr(simulator, "time", clock)
    with Simulator(time_constant=10) as device:
        device.start()
        clock.sleep(10)
        assert 20 < device.read_temperature() < 25
        device.set_setpoint(30)
        clock.sleep(10)
        warmer = device.read_temperature()
        assert 25 < warmer < 30
        device.stop()
        clock.sleep(10)
        assert 25 < device.read_temperature() < warmer
        device.start()
        device.disconnect()
        clock.sleep(10)
        device.connect()
        assert device.status().reported_run is True
    assert not device.is_connected


@pytest.mark.parametrize("profile", ["legacy-r2", "thermocube-ii-m5"])
def test_simulator_faults_delays_and_injected_link_failures(profile):
    with Simulator(profile=profile) as device:
        assert device.read_setpoint("F") == 68
        device.inject_faults(128)
        assert device.status().faults.unknown_mask == 128
        assert device.status().temperature_c is None
        for operation in (device.start, lambda: device.set_setpoint(10)):
            with pytest.raises(SafetyError):
                operation()
        device.stop()
        device.inject_faults(0)
        device.inject_timeout()
        with pytest.raises(TimeoutError):
            device.read_temperature()
        assert not device.is_connected
        device.connect()
        device.inject_disconnect()
        with pytest.raises(ConnectionError):
            device.read_setpoint()
    with Simulator(delay=0.01, timeout=0.005) as slow:
        with pytest.raises(TimeoutError):
            slow.status()


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(time_constant=0),
        dict(delay=-1),
        dict(timeout=True),
        dict(profile="guess"),
        dict(limits_c=None),
        dict(initial_c=float("inf")),
    ],
)
def test_invalid_simulation_configuration(kwargs):
    with pytest.raises(ValueError):
        Simulator(**kwargs)


@pytest.mark.parametrize("backend", ["hardware", "simulation"])
def test_external_experiment_uses_the_same_device_operations(hardware, backend):
    device = hardware[0] if backend == "hardware" else Simulator()
    with device:
        if backend == "hardware":
            arm(device, control=True)
        assert device.set_setpoint(18) == 18
        device.start()
        state = device.status()
        assert state.setpoint_c == 18 and state.reported_run is True
        assert device.read_faults().has_fault is False
        assert isinstance(device.read_temperature(), float)
        device.stop()
    assert not device.is_connected
