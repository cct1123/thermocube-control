# thermocube-control

A small synchronous Python controller for ThermoCube R2 RS-232 devices, targeting
**10-400-1D-1-CP-R2-LT-AR-267**. The core requires only **pyserial**. Monitoring,
CSV and the local Dash GUI are optional. The package has three implementation
modules: `controller`, `simulator` and `gui`.

**0.2.0 is an offline-tested candidate, not a physically validated driver.**
R2 queries carry active REMOTE/LOCAL and RUN/STANDBY bits. A non-mutating query
cannot be guaranteed. Hardware use requires the [staged validation procedure](docs/HARDWARE_VALIDATION.md).

## Install and use

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once, then:

~~~powershell
uv sync --locked
uv run --locked python examples/experiment.py
~~~

uv creates `.venv` and installs the package from `src/` in editable mode. Python
3.11+ is supported; `.python-version` selects 3.12 for local development. Runtime
requirements, the optional `gui` extra and the `dev` dependency group live in
`pyproject.toml`; `uv.lock` records resolved versions and distribution hashes.

~~~text
src/thermocube/    controller.py, simulator.py, gui.py, assets, package exports
tests/            hardware-free behavior tests
examples/         runnable simulation integration
docs/             protocol, review and hardware validation
inputs/           reference manual and provenance
records/          source manifest and validation evidence
tools/            distribution verification
pyproject.toml    package metadata, dependencies, build and tool configuration
uv.lock           reproducible dependency resolution
.python-version   local development Python version
~~~

The simulator follows the same device-operation API:

~~~python
from thermocube import Simulator

with Simulator() as device:  # connect on entry, disconnect on exit
    device.set_setpoint(18)   # Celsius by default; returns the quantized value
    device.start()
    try:
        print(device.status())
    finally:
        device.stop()        # explicit action; disconnect never implies STOP
~~~

Use `ThermoCube` in an approved hardware session:

~~~python
from thermocube import ThermoCube
from thermocube.controller import QUERY_ACK

device = ThermoCube(
    "APPROVED_PORT",
    profile="thermocube-ii-m5",           # must match the actual controller
    binary_framing_confirmed=True,       # only after resolving the manual's ambiguity
)
with device:
    # Only after the operator has established and approved this physical state:
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    print(device.read_faults())          # 88: selects REMOTE and asserts STANDBY
~~~

Constructing a controller does nothing to hardware. `connect()` opens the named
port once, with no commands or retries. Without a confirmed profile/framing and
explicit query arming, no query is sent. Setpoint/start/stop remain locked until
the caller supplies safe `limits_c=(minimum, maximum)` at construction and calls
`enable_control(CONTROL_ACK)` after separate human approval. Acknowledgement
strings document intent; they do not constitute approval or verify physical state.

Public device operations: `connect()`, `disconnect()`, `read_temperature()`,
`read_setpoint()`, `read_faults()`, `set_setpoint()`, `start()`, `stop()`, `status()`.
Temperature calls accept `unit="C"` or `"F"`; `Status` always uses Celsius.
`status()` performs up to three queries, faults first. Faults/mismatch return an
observation with absent temperatures; communication failures raise instead of
combining new and stale readings.

Use **one controller per physical port** and share it between callers. It owns
one serial handle and one lock; every transaction is paced at least 350 ms apart.
Concurrent operations do not interleave, but the experiment application must
sequence conflicting intents and shutdown. Do not create a controller per poll.

## Optional monitoring and GUI

~~~python
from thermocube import Simulator
from thermocube.gui import Monitor

with Simulator() as device, Monitor(device, csv_path="new-session.csv") as monitor:
    # Your experiment calls the same device directly.
    device.set_setpoint(18)
    # monitor.latest holds the latest observation; monitor.history is bounded.
    # On exit, the monitor joins its worker before the device disconnects.
~~~

`Monitor` and the headless launcher work without Dash installed.
`Monitor.start()` explicitly creates one thread. Its default interval is 1.05 s.
It never connects, reconnects, arms, writes setpoints or stops the device. CSV
uses exclusive creation and flushes each row. CSV failure stops monitoring;
the owning application decides how to abort its experiment. Core operations
remain independent of optional logging. Standard Python logging on
`thermocube.controller` at DEBUG records exact TX/RX bytes and uncertain exchanges.

Install the GUI only when needed:

~~~powershell
uv run --locked --extra gui thermocube
# Optional browser-free simulation with CSV:
uv run --locked thermocube --headless --duration 10 --csv simulation.csv
~~~

The launcher always uses simulation. To embed the GUI in approved laboratory
software, pass your explicitly started `Monitor` to `thermocube.gui.create_app()`.
Creating the GUI starts no worker and opens no device. Refresh callbacks read
cached observations; confirmed actions call the same public device methods.
Hardware arming, recovery and write enabling belong to the caller, not the GUI.
The supplied server is a local, single-operator development UI with its reloader
disabled, not an authenticated network service or a hardware-validation harness.

## Failures and recovery

`ValueError` indicates invalid input, `SafetyError` a locked/refused operation,
`TimeoutError` an expired exchange, and `ConnectionError` a failed connection or
malformed/unexpected reply. Catch `OSError` for communication failures together.

An uncertain exchange closes the port and clears permissions. Nothing is replayed.
After an actual stream reset and physical-state verification, reopen, call
`confirm_recovery(RECOVERY_ACK)`, then explicitly arm again. Replacing a controller
does not prove recovery; state verification is required for every new owner.
Neither STOP delivery, process exit nor disconnect is an emergency-stop guarantee.

## Project guide

- [Architecture and module cleanup](ARCHITECTURE.md)
- [Manual-derived protocol and unresolved facts](docs/PROTOCOL.md)
- [Hardware validation and write gate](docs/HARDWARE_VALIDATION.md)
- [Review and validation results](docs/REVIEW.md), [test commands](docs/TESTING.md)
- [Runnable simulation integration example](examples/experiment.py)
- [Manual provenance](inputs/README.md), [contributor notes](AGENTS.md)

Import devices from `thermocube`, optional monitoring/UI from `thermocube.gui`,
and use `status()` for observations. User requests remain in [prompt log.md](prompt%20log.md).

## Development

~~~powershell
uv sync --locked --extra gui
uv run --locked --extra gui pytest
uv run --locked ruff check src tests tools examples
uv run --locked mypy
uv build
~~~

Use `uv add PACKAGE` for runtime dependencies, `uv add --optional gui PACKAGE`
for GUI dependencies, and `uv add --dev PACKAGE` for development tools. Commit
both `pyproject.toml` and `uv.lock` when dependencies change. `uv lock --upgrade-package PACKAGE` intentionally upgrades one dependency. See [the complete validation
commands](docs/TESTING.md) for format, coverage and installed-artifact checks.
