from decimal import Decimal

import pytest

from thermocube.models import FaultProfile, ProtocolError, TemperatureLimits
from thermocube.protocol import (
    Parameter,
    command_byte,
    decode_faults,
    decode_temperature,
    encode_temperature,
    from_celsius,
    message,
    split_command,
    to_celsius,
)


@pytest.mark.parametrize("raw", range(256))
def test_all_bit_combinations(raw):
    bits = split_command(raw)
    assert (
        command_byte(
            remote=bits.remote,
            run=bits.run,
            host_to_controller=bits.host_to_controller,
            parameter=bits.parameter,
        )
        == raw
    )
    assert bits.parameter == raw % 32
    assert bits.remote == (raw >= 128)


@pytest.mark.parametrize(
    "parameter,write,run,payload,expected,length",
    [
        (Parameter.SETPOINT, True, True, b"\xf4\x01", b"\xe1\xf4\x01", 0),
        (Parameter.SETPOINT, False, True, b"", b"\xc1", 2),
        (Parameter.TEMPERATURE, False, True, b"", b"\xc9", 2),
        (Parameter.FAULTS, False, True, b"", b"\xc8", 1),
        (Parameter.CONTROL, True, True, b"", b"\xe0", 0),
        (Parameter.CONTROL, True, False, b"", b"\xa0", 0),
        (Parameter.SETPOINT, True, False, b"\xf4\x01", b"\xa1\xf4\x01", 0),
        (Parameter.SETPOINT, False, False, b"", b"\x81", 2),
        (Parameter.TEMPERATURE, False, False, b"", b"\x89", 2),
        (Parameter.FAULTS, False, False, b"", b"\x88", 1),
    ],
)
def test_manual_and_derived_commands(parameter, write, run, payload, expected, length):
    assert message(parameter, remote=True, run=run, write=write, data=payload) == (expected, length)
    assert (
        message(parameter, remote=False, run=run, write=write, data=payload)[0][0]
        == expected[0] - 128
    )


@pytest.mark.parametrize("bad", [-1, 32, True, 1.5, "1"])
def test_invalid_parameter(bad):
    with pytest.raises(ValueError):
        command_byte(remote=True, run=False, host_to_controller=False, parameter=bad)


@pytest.mark.parametrize("field", ["remote", "run", "host_to_controller"])
def test_explicit_bits(field):
    args = dict(remote=True, run=False, host_to_controller=False, parameter=1)
    args[field] = 1
    with pytest.raises(ValueError):
        command_byte(**args)


@pytest.mark.parametrize(
    "parameter,write,data",
    [
        (Parameter.CONTROL, False, b""),
        (Parameter.TEMPERATURE, True, b""),
        (Parameter.FAULTS, True, b""),
        (Parameter.SETPOINT, True, b"x"),
        (Parameter.SETPOINT, False, b"xx"),
        (30, True, b""),
    ],
)
def test_unsupported_messages(parameter, write, data):
    with pytest.raises(ValueError):
        message(parameter, remote=True, run=False, write=write, data=data)


@pytest.mark.parametrize(
    "fahrenheit,data",
    [
        (0.1, b"\x01\x00"),
        (1, b"\x0a\x00"),
        (10, b"\x64\x00"),
        (20, b"\xc8\x00"),
        (30, b"\x2c\x01"),
        (40, b"\x90\x01"),
        (50, b"\xf4\x01"),
        (60, b"\x58\x02"),
        (70, b"\xbc\x02"),
    ],
)
def test_temperature_vectors(fahrenheit, data):
    assert encode_temperature(fahrenheit, "F") == data
    assert decode_temperature(data, "F") == fahrenheit
    assert decode_temperature(data) == pytest.approx((fahrenheit - 32) * 5 / 9)


def test_every_wire_word_roundtrip():
    for raw in range(65536):
        data = raw.to_bytes(2, "little")
        assert encode_temperature(decode_temperature(data, "F"), "F") == data
        assert encode_temperature(decode_temperature(data), "C") == data


def test_quantization_and_units():
    assert encode_temperature(10) == b"\xf4\x01"
    assert encode_temperature(-5) == b"\xe6\x00"
    assert encode_temperature(Decimal("50.05"), "F") == b"\xf5\x01"
    assert to_celsius(32, "F") == 0
    assert from_celsius(0, "F") == 32
    assert from_celsius(100, "F") == 212
    assert to_celsius(100) == from_celsius(100) == 100


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, -1, 6554])
def test_invalid_temperature(value):
    with pytest.raises(ValueError):
        encode_temperature(value, "F")


@pytest.mark.parametrize("data", [b"", b"a", b"abc", "ab"])
def test_bad_temperature_reply(data):
    with pytest.raises(ProtocolError):
        decode_temperature(data)


@pytest.mark.parametrize("raw", range(256))
def test_all_fault_bytes(raw):
    legacy = decode_faults(bytes([raw]))
    assert legacy.unknown_mask == raw & 0xC4
    assert legacy.standby is None
    assert ("rtd_open" in legacy.active) == bool(raw & 16)
    assert ("rtd_short" in legacy.active) == bool(raw & 32)
    for mask, name in [(1, "tank_level_low"), (2, "fan_fail"), (8, "pump_fail")]:
        assert (name in legacy.active) == bool(raw & mask)
    m5 = decode_faults(bytes([raw]), FaultProfile.M5)
    assert m5.unknown_mask == raw & 128
    assert m5.standby == bool(raw & 64)
    assert ("leak_detected" in m5.active) == bool(raw & 16)


def test_fault_status_distinction():
    assert not decode_faults(b"\x40", FaultProfile.M5).has_fault
    assert decode_faults(b"\x40").has_fault
    assert decode_faults(b"\x49", FaultProfile.M5).active == ("tank_level_low", "pump_fail")
    for data in (b"", b"xx"):
        with pytest.raises(ProtocolError):
            decode_faults(data)


def test_invalid_units_and_limits():
    for fn, arg in [
        (to_celsius, 2),
        (from_celsius, 2),
        (encode_temperature, 2),
        (decode_temperature, b"xx"),
    ]:
        with pytest.raises(ValueError):
            fn(arg, "K")
    for low, high in [(1, 1), (2, 1), (float("nan"), 2)]:
        with pytest.raises(ValueError):
            TemperatureLimits(low, high)
