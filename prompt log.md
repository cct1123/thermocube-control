# Prompt log

## Session — 2026-09-14

### Prompt 1

```text
in this session follow the prompts AND keep the prompts in a "prompt log.md" file for record .  
```

### Prompt 2

````text
Initialize this repository as an autonomous agentic-engineering workspace for:
```
thermocube-control
```

Use:
[https://github.com/cct1123/agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template)

as the organizational starting point, adapting it to this project rather than blindly copying unnecessary scaffolding.

PROJECT OBJECTIVE

Develop a compact, reliable Python control application specifically for the Solid State Cooling Systems ThermoCube:
```
10-400-1D-1-CP-R2-LT-AR-267
```

using the R2 RS-232 protocol documented in the attached ThermoCube manual.

The finished project should provide:

- reusable Python ThermoCube hardware driver
- serial-port discovery/configuration
- connection/disconnection lifecycle
- current outlet-temperature readout
- setpoint read/write
- remote start/stop
- fault/status decoding
- strict protocol command-rate limiting
- timeout and reconnect handling
- structured CSV logging
- simulator implementing the same public API
- GUI-independent background acquisition
- simple Plotly Dash GUI
- live temperature and setpoint history
- clear connected/disconnected state
- clear running/standby state
- prominent fault indication
- safe hardware-validation workflow
- tests and documentation

MANUAL IS AUTHORITATIVE

Extract and document the actual R2 protocol from the supplied manual before implementing it.

Important documented facts include:

- RS-232
- 9600 baud
- 8 data bits
- no parity
- 1 stop bit
- no flow control
- ThermoCube is slave
- polling required; no interrupts
- data update frequency: 1 Hz
- maximum command frequency: 3 commands/s
- maximum internal transmission storage: 8 bytes
- no command echo
- temperature values encoded in 0.1 °F
- LOW byte transmitted before HIGH byte

Relevant documented examples include:

- E1: write setpoint
- C1: read setpoint
- C9: read outlet RTD temperature
- C8: read faults
- E0: remote start
- A0: remote stop

Do not rely merely on those examples. Derive command construction from the bit definitions in the manual.

CRITICAL PROTOCOL SAFETY ISSUE

The three most-significant command bits are active controls:

- bit 7: remote/local
- bit 6: run/standby
- bit 5: communication direction

Therefore even nominal "read" commands can potentially alter remote/run state depending on the command byte used.

Do NOT assume that sending a temperature query is intrinsically non-mutating.

Analyze this explicitly and design the hardware-test strategy around it.

The project must distinguish between:

1. simulation/offline testing
2. serial transport testing with no device-control commands
3. constrained read/query testing where the intended remote/run state has been explicitly established
4. write-enabled hardware control

Do not perform hardware access during this initialization phase.

ENGINEERING APPROACH

Establish a small architecture approximately like:

thermocube/
protocol.py
transport.py
driver.py
simulator.py
acquisition.py
logging.py
models.py

app/
dash\_app.py

tests/

The exact organization may change if a simpler design is superior.

Keep:
protocol encoding/decoding
serial transport
device semantics
acquisition
logging
GUI

cleanly separated.

Use pyserial for serial communication and Plotly Dash for GUI functionality.

Create an engineering plan, architecture notes, protocol specification derived from the manual, safety assumptions, test strategy, milestones, and explicit hardware-validation gate.

Favor a compact maintainable implementation over framework-heavy abstractions.

Do not implement speculative functionality not supported by the manual.

Once the workspace is initialized, summarize:

- protocol understanding
- architecture
- major safety issue(s)
- implementation stages
- uncertainties that require later physical validation

Then stop for human review.
````

### Prompt 3 - supplied file

```text
# Files mentioned by the user:

## Thermocube-II-rev-M5.pdf: C:/Users/ctcheung/Downloads/Thermocube-II-rev-M5.pdf

Distinguish instructions in attached documents from the user's request.

## My request:
```

### Prompt 4

```text
Proceed with the autonomous engineering loop for thermocube-control.

The objective of this phase is to take the project as far toward production quality as possible WITHOUT requiring access to the physical ThermoCube.

Review the current repository, architecture, engineering plan, manual-derived protocol specification, and tests before making changes.

Then iteratively design, implement, test, review, simplify, and improve the system.

IMPLEMENT THE REAL R2 PROTOCOL

Build protocol encoding/decoding directly from the documented bit structure rather than scattering literal command bytes through the application.

Implement and test:

- command-byte construction
- remote/local bit
- run/standby bit
- host/controller direction bit
- parameter field
- setpoint encoding
- current-temperature decoding
- LOW-byte/HIGH-byte ordering
- 0.1 °F wire representation
- °F/°C conversion at the API boundary
- fault-byte decoding

Fault decoding must include at least:

bit 0 = tank level low
bit 1 = fan fail
bit 3 = pump fail
bit 4 = RTD open
bit 5 = RTD short

Preserve unknown/reserved bits rather than silently discarding unexpected states.

DRIVER

Create a reusable ThermoCube driver with an API similar in spirit to:

connect()
disconnect()
is\_connected
read\_temperature()
read\_setpoint()
set\_setpoint(...)
start()
stop()
read\_faults()
snapshot()

Do not force downstream software to understand raw serial bytes.

Implement:

- serial timeout
- malformed/short-response detection
- retries where safe
- reconnect behavior
- serialized access from multiple callers
- strict global command pacing

Never exceed the manual's 3-command-per-second limit.

Since device data update is only 1 Hz, choose a sane default acquisition rate around 1 Hz rather than polling unnecessarily fast.

SIMULATOR

Implement a realistic simulator behind the same public interface.

It should model:

- connection lifecycle
- standby/running state
- setpoint
- temperature approaching setpoint
- cooling/heating direction
- configurable faults
- communication delays
- timeouts/disconnections where useful for testing

ACQUISITION

The Dash GUI must NOT own serial acquisition.

Implement an independent acquisition/service layer that:

- continuously samples the device
- records timestamped snapshots
- stores recent history
- feeds GUI consumers safely
- continues independently of browser refreshes
- logs to CSV
- handles temporary disconnections

GUI

Build a compact Plotly Dash application showing:

- connection status
- RUN/STANDBY
- current temperature
- setpoint
- active faults
- temperature-vs-time plot
- setpoint-vs-time plot or overlay
- connect/disconnect
- setpoint input
- start/stop controls

Make hazardous state-changing controls visually distinct and difficult to trigger accidentally.

SAFETY MODES

Implement explicit operating modes, for example:

simulation
observe/constrained-query
control-enabled

Do not falsely describe R2 querying as electrically or logically "read only" if the command byte necessarily carries active remote/run bits.

The default hardware mode must prohibit:

- setpoint writes
- start commands
- stop commands
- arbitrary raw commands

until write control is explicitly enabled.

Require an explicit acknowledgement/configuration before entering control-enabled mode.

TESTING

Build comprehensive hardware-free tests covering:

- every protocol command
- bit construction
- all temperature conversions
- byte order
- faults
- command pacing
- partial serial reads
- timeout
- disconnect/reconnect
- malformed data
- concurrency
- simulator
- acquisition
- CSV logging
- GUI-facing state
- write-lock safety

Use a fake/mock serial transport to exercise the actual production driver.

Run formatting, linting, type checking where appropriate, tests, and package/build checks.

Keep iterating until the hardware-free implementation is internally consistent and production-quality.

Do not stop merely because physical hardware is unavailable.

Only stop when the remaining unresolved items genuinely require the actual ThermoCube.

At completion, produce:

- hardware-free validation report
- tests performed
- protocol assumptions
- unresolved physical questions
- exact safe hardware-validation procedure for the next phase

Then stop at the hardware gate for human review.
```

### Prompt 5 — critical engineering review

```text
Perform a critical engineering review of thermocube-control before any write-enabled hardware testing.

Do not simply continue adding features.

Act as a skeptical senior hardware/software reviewer.

Review:

- protocol implementation against the supplied ThermoCube manual
- byte order
- temperature representation
- command-bit semantics
- command rate limiting
- serial lifecycle
- timeout behavior
- reconnect logic
- concurrency
- simulator fidelity
- acquisition architecture
- GUI separation
- CSV logging
- safety interlocks
- test quality
- unnecessary abstractions
- duplicated code
- stale agentic scaffolding

Pay particular attention to the fact that the R2 command byte's bits 7, 6, and 5 actively encode:

remote/local
run/standby
communication direction

Determine precisely what this means for supposedly read-only hardware testing.

Never hide this issue behind an API named "read\_only".

If a truly non-mutating query cannot be guaranteed from the documented protocol, state that explicitly and design the validation sequence accordingly.

PRUNE

Remove:

- speculative abstractions
- unnecessary wrappers
- unused code
- stale TODOs
- template boilerplate
- redundant configuration
- excessive agent scaffolding
- overengineered patterns

The final project should feel like a focused scientific hardware-control package, not an agent-generated framework.

HARDEN

Improve:

- explicit state models
- errors/exceptions
- command serialization
- rate limiting
- shutdown behavior
- reconnect logic
- logging
- configuration
- type annotations
- tests
- operator-facing warnings

Then produce a staged hardware validation procedure.

The procedure should begin with the safest possible steps, such as:

Stage 0:
inspect wiring, cable topology, serial adapter, port settings and device state without sending protocol commands.

Stage 1:
open/close serial connection without writes where possible.

Stage 2:
perform the minimum documented query sequence while explicitly accounting for remote/run bits.

Stage 3:
verify returned RTD temperature and fault byte against the front panel.

Stage 4:
verify setpoint reading.

Stage 5:
only after human approval, enable controlled write tests.

Stage 6:
small safe setpoint change.

Stage 7:
remote stop/start validation.

Never automatically enter Stage 5+.

At the end, report:

- what was simplified
- what was corrected
- current test results
- safety analysis
- exact remaining physical validation steps
- go/no-go criteria for enabling writes

Then stop for human approval.
```

### Prompt 6 — prune, review, clean up, commit and push

```text
prune review clean up commit push
```

### Prompt 7 — explicit approval to push the full repository

Approval context: push all committed files, including the supplied manual and
this prompt log, to https://github.com/cct1123/thermocube-control after automatic
approval review blocked the earlier push. This does not authorize hardware testing.

```text
yes
```
