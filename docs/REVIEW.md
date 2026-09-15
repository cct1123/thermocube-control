# Simplification review — 0.2.0

**Offline candidate; NO-GO for write-enabled hardware testing.** No physical port
discovery, opening, commands or power operations were performed.

## Current implementation

| Measure | Before the original simplification | Current |
| --- | --- | --- |
| Runtime Python files | 11 | 4: three implementation modules plus package exports |
| Runtime lines | 2,255 | 1,067 |
| Classes | 25 | 6, including two immutable result records |

`controller` owns synchronous serial I/O and value encoding; `simulator` provides
hardware-free device operations; `gui` contains optional monitoring, CSV, Dash
and the simulator launcher. The controller imports neither simulator nor GUI.
Nothing connects or starts a worker on import or construction.

The package uses `src/`, `pyproject.toml`, `uv.lock` and `uv_build`. pyserial is the
only core dependency; Dash is optional and imports only when creating the UI.
There are no transport factories, policy/configuration objects, global port
registries, command queues, automatic reconnect or separate launcher modules.

## Latest review and pruning

- **Recovery-check cancellation:** Ctrl+C or SystemExit while checking buffered
  bytes could leave the port open and permissions enabled. Two regression cases
  reproduced this; the OSError control case already passed. The check now closes,
  disarms, retains the recovery gate and propagates cancellation without sending bytes.
- **Derived observations:** `Faults` stores only the raw byte and profile; active
  faults, unknown bits and standby are properties. `Status.reported_run` derives
  from those faults. Stored result fields fall from 11 to 7, preventing contradictory
  copies of the same observation. The decoder wrapper and per-reply dictionary
  construction/sorting are removed; one shared table defines fault names/profiles.
- **Direct ownership and inputs:** the monitor passes its CSV handle directly to
  its worker instead of storing another object field. The GUI reads permissions
  from the device, replacing repeated flag plumbing. Bounds validation always
  returns bounds; only the hardware constructor handles absent limits. Repeated
  test wait loops reuse the existing helper.
- **Documentation:** retained the expanded GUI showcase and hardware workflow,
  documented the result-constructor migration, and replaced accumulated review
  history with this current summary. Runtime is 1,067 lines versus 1,070 before
  this pass; module/class/dependency counts are unchanged.

Earlier fixes remain covered: interrupted serial opening/read/write/close and
failed-open cleanup, no replay or implicit STOP after uncertain RUN delivery,
monitor worker-startup cleanup, and legacy simulation without invented run telemetry.

## Validation

**86 tests pass; 96.54% statement coverage (641/664 statements)** on Windows,
Python 3.12.14, uv 0.11.2. Ruff lint/format, mypy, lockfile and installed-dependency
checks pass. Build and independent wheel/source checks pass; all 86 tests also
pass from the extracted source archive. See [TESTING.md](TESTING.md) for commands
and [software-checks.json](../records/software-checks.json) for check records.

Coverage retains every unsigned temperature word in both units and all 256 fault
bytes in both profiles. Public operations use a literal-byte fake serial peer:
framing/response lengths, state gates, fault preflight, whole-operation locking,
pacing, uncertainty/recovery, simulator, CSV/history and Dash callbacks remain covered.
Physical port opening and discovery are blocked before collection. Test counts
are scenario counts, not evidence of hardware safety.

The existing [GUI screenshot](images/simulator.jpg) was captured during the
preceding README validation: initial standby, confirmed 18 °C target, START,
cooling trace and return to standby. The server/browser were closed afterward.
This pass checks HTTP/callback behavior; it does not claim a new browser capture.
The source archive includes the README and JPEG; wheel metadata includes the README.
All local documentation links/anchors resolve, excluding quoted prompt history.

The earlier hardware-guide check ran ten cases against fake serial: one-query
stages, setpoint preflight/write/readback, fault refusal, exact bytes, pacing and
zero-transmit disconnect. Hardware examples are unchanged; these are software
checks, not approved physical sessions.

## Retained protections and limits

One caller-owned controller per physical port serializes and paces every command
by at least 350 ms. There is no global ownership registry. Queries require an
explicit profile, confirmed framing and acknowledged physical run state; controls
also require approved bounds and a separate enable step. Faults and state mismatch
inhibit further queries/start/setpoint; existing control permission permits STOP.

Uncertain I/O closes/disarms without retries, implicit STOP or automatic restart.
Reopening does not rearm; physical stream/state recovery is required. Replacing
an object or process cannot prove recovery. Observations never merge stale values.
Optional CSV failure stops monitoring; the owning application remains responsible
for its audit/abort requirements. Python/OS/USB timing is not an emergency-stop guarantee.

Queries actively assert REMOTE and RUN/STANDBY. The supplied M5 manual may not
apply to the target controller; fault mapping, framing/terminators, signed HEX,
buffer capacity, wiring, thermal limits and pump/restart behavior remain unresolved.
See [PROTOCOL.md](PROTOCOL.md) for the source analysis and uncertainty register.

[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) retains separate Stage 0–7 hold
points. No stage is approved or executed. Writes remain NO-GO until the exact unit,
protocol and safe operating envelope are established, Stage 0–4 evidence passes,
and a human explicitly approves a finite Stage 5–7 scope.
