# thermocube-control

A focused Python R2 driver, simulator, acquisition/CSV service and local Plotly
Dash interface for ThermoCube **10-400-1D-1-CP-R2-LT-AR-267**.

**Candidate 0.1.1: reviewed and tested offline; no hardware testing authorized.**
See the [critical review](docs/REVIEW.md) and
[Stage 0–7 validation procedure](docs/HARDWARE_VALIDATION.md).

**A truly non-mutating R2 query is not guaranteed.** Every command carries active
remote/local, run/standby and direction bits. Queries can change device state;
disconnect does not stop the chiller. The exact controller, framing, fault map,
wiring and operating limits must be established before hardware use.

## Simulation

Python 3.11+ is required; local validation uses Windows and Python 3.12.14.

~~~powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -c requirements-lock.txt -e '.[gui,dev]'
.\.venv\Scripts\thermocube
~~~

Open http://127.0.0.1:8050 and click Connect. Setpoint, START and STANDBY require
on-page review and confirmation. Acquisition runs in one independent worker;
browser reload does not restart it. Ctrl+C closes the application.

For a browser-free simulation:

~~~powershell
.\.venv\Scripts\thermocube --headless --duration 10 --log-dir logs
~~~

Each session produces samples CSV, events CSV and a JSON configuration record.
A failed headless acquisition exits unsuccessfully. Hardware is never selected
automatically. Run one local operator/process; network deployment is unsupported.

Backend-only installation is `pip install .`. Linux/macOS use
`.venv/bin/python` and `.venv/bin/thermocube`.

## Python API

~~~python
from thermocube import Simulator

device = Simulator()
device.connect()
device.set_setpoint(18, unit="C")
device.start()
print(device.snapshot())
device.stop()
device.disconnect()
~~~

ThermoCube and Simulator provide connect/disconnect, is_connected,
read_temperature, read_setpoint, set_setpoint, start, stop, read_faults and
snapshot. Temperature calls accept "C" or "F". The physical driver additionally
requires explicit query arming and write permission; connect sends no commands.

## Documentation and checks

- [Protocol and source uncertainties](docs/PROTOCOL.md)
- [Architecture](ARCHITECTURE.md), [API/operation](docs/OPERATIONS.md),
  [safety boundaries](docs/SAFETY.md)
- [Test strategy and commands](docs/TESTING.md)
- [Manual provenance](inputs/README.md)
- [Review findings and evidence](docs/REVIEW.md)

Contributor guidance is in [AGENTS.md](AGENTS.md). Session prompts remain in
[prompt log.md](prompt%20log.md).
