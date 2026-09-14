# Operation and API notes

## Simulation

The CLI defaults to simulation with no serial discovery or opening. Connect,
read, set a target, start, stop and disconnect use the same API as the hardware
driver. The simulation defaults to 25 C ambient/initial temperature, 20 C setpoint,
standby, -5..50 C software bounds, 15 s response constant and 2 C/s maximum slew.

Temperature approaches the target exponentially while running without faults,
and ambient in standby/fault inhibition. This is a test model, not a prediction
of the chiller's thermal performance. Link disconnection does not reset its
modeled run state. Its reported state is simulated evidence only.

For deterministic tests, inject clock/sleep/now callables. Constructor options
include ambient_c, initial_c, setpoint_c, time_constant, max_rate_c_s, delay,
timeout and limits. `inject_faults(byte)`, `inject_timeout()` and
`inject_disconnect()` exercise consumers without hardware.

## Device API

| API | Result / meaning |
| --- | --- |
| connect(), disconnect(), is_connected | Explicit link lifecycle; no implicit hardware query/start/stop |
| read_temperature(unit="C") | Current outlet RTD value; "F" also supported |
| read_setpoint(unit="C") | Device setpoint readback |
| set_setpoint(value, unit="C") | Validates requested and quantized bounds; returns the confirmed effective value; uncertain writes are never replayed |
| start(), stop() | Explicit temperature-control run/standby; successful send is not a device ACK |
| read_faults() | Immutable Faults with raw/profile/active/unknown_mask/optional standby |
| snapshot() | Full timestamped snapshot; actual per-field times, raw words and quality |
| cached_snapshot | No I/O; last state with current connection/authority |
| drain_events() | Bounded event buffer, including uncertain deliveries |

Use `try/finally` or the hardware driver's context manager to release resources.
When using AcquisitionService, let the worker own the device and use
`submit(operation, **arguments)`. Retain its returned request ID and inspect
`result(id)` for pending/running/completed/failed/cancelled. Do not equate queued
or completed START with physically verified operation.

## Hardware configuration (gate closed)

[hardware.example.json](../examples/hardware.example.json) is deliberately inert:
placeholder port, no approval and no operating bounds. Parsing it with hardware_from_config performs no I/O.
Connecting it fails closed. Do not edit approval fields until the scoped review in
[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) has occurred.

Later, a reviewed JSON file uses serial.port, timeout, write_timeout,
command_interval, open_attempts and retry_delay; profile is explicitly
"legacy-r2" or "thermocube-ii-m5". Mode must initially be "transport-only" or
"constrained-query"; constructing in "control-enabled" is rejected. limits_c is
a confirmed [minimum, maximum], not the broad wire representable range.

Approval fields: record (actual human approval/evidence reference),
binary_framing_confirmed (false until U02 resolved), allow_queries, allow_control,
and allowed_run_states (booleans). The parser ties the selected profile and limits to this immutable approval.
The CLI reads the file once and logs that exact configuration. Runtime profile,
limits and approval cannot be widened in place. These local settings are a deliberate operator safety
gate, not an authentication system or protection from hostile Python code.

The driver requires separate per-session actions:
1. connect() within port-open approval.
2. After physically establishing the intended state, arm_queries(run=...,
   acknowledgement="QUERIES ASSERT REMOTE AND RUN STATE").
3. Only in approved control scope, enable_control(
   "ENABLE SETPOINT START AND STOP CONTROL").

No step automatically executes the next. Stage 5+ always requires separate
human authority under the staged procedure. Arming sends no bytes, but the next
query will assert REMOTE and the selected RUN/STANDBY state. In the GUI worker,
arming starts ongoing polling, so use the direct driver for finite first-query
validation. The normal GUI is not the Stage 2 single-query harness.

discover_ports(approval=...) returns selected OS metadata without probing.
Never use enumeration as device identification. No other baud rate, ASCII
fallback, terminator trial, alarm reset, or remote/local restore is implemented.

## Failure and shutdown

CommunicationTimeout/ConnectionError/ProtocolError invalidate freshness and
quarantine uncertain streams. SetpointRejected means readback differs from
the requested wire word. SafetyError means the operation is outside the current
authority. Treat reported text and event outcomes as evidence, not as successful
hardware actuation.

After communication uncertainty, follow the approved physical recovery procedure.
Reopening sends no bytes. confirm_recovery requires
"STREAM RESET AND DEVICE STATE VERIFIED" and empty RX, then the session still
needs explicit arming. There is no automatic receive-buffer flush or transaction
retry. A new empty buffer does not establish that a late response is impossible.
Quarantine also persists across a replacement transport on the same port in this
process; a process restart still requires the approved physical-state checks.

Ctrl+C joins the worker with a bounded wait and closes logging/transport. If
shutdown times out it reports failure; an I/O operation may still be active.
Closing a browser does not stop acquisition. Disconnect, disarm and process exit
do not command physical standby. Remote keypad availability, pump behavior and
independent shutdown must be verified for the actual configuration.
