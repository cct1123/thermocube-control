# Simplification review — 0.2.0

**Disposition: offline candidate; NO-GO for write-enabled hardware testing.**
No physical port discovery, opening, commands or power operations were performed.

## Changes

The runtime is reduced from **11 Python files / 2,255 lines / 25 classes** to
**4 Python files / 1,057 lines / 6 classes**: `controller`, `simulator`, `gui`,
plus the package export file. This leaves three implementation modules and two
immutable result records; no standalone protocol, monitoring or launcher module.

All serial and value-codec logic lives in controller.py. Supported device methods
construct commands directly; the generic command builder and custom protocol
exception are gone. Monitoring, CSV and the launcher live with the GUI and import
Dash only when creating the UI. Unused run-state properties and the direct Plotly
API/dependency were removed. Plotly remains a dependency of Dash itself.

Removed global ownership/port registries, configuration and approval objects,
typing-interface wrappers, mode enums, event buffers, command/result queues,
automatic reconnect, cached-snapshot merging, the separate app package and JSON
hardware configuration. Operation/safety notes are consolidated into the README,
architecture and hardware procedure instead of duplicating them across documents.
Waitress and its type-stub dependency are removed from the optional local GUI.

The GUI calls the same public methods as an experiment script. Monitoring, CSV,
plotting and the application entry point are not imported by the core controller.
No worker starts on import, construction, connection or GUI creation. The monitor
never connects/reconnects or owns device shutdown. The caller sequences operations.

## Package layout and uv migration

The package lives under `src/thermocube`. `pyproject.toml` is the version and
metadata source; the package exposes its installed distribution version.
`uv_build` replaces setuptools configuration and MANIFEST.in. `uv.lock` replaces
the former requirements constraints, preserving the previously tested versions.
Development tools use the standard `dev` dependency group; the GUI remains an
optional extra. Build/wheel frontend dependencies were removed.

`.python-version` selects Python 3.12 locally. The Windows/Ubuntu 3.11/3.12 CI
matrix uses uv with a checked lockfile. Wheel and source checks now create
separate uv environments and use real installations, including for subprocess
import tests. They no longer insert checkout paths into `sys.path` or inherit
installed dependencies from the development environment. Hardware behavior and
its three-module API were not changed by this packaging migration.

## First-time-user guide

The README now leads with hardware use: uv installation and offline checks,
serial/physical preparation, one approved fault query, separately approved
temperature/setpoint checks, and a bounded setpoint-write example. An operation
table explains reads, start/stop and disconnect. A connection diagram shows the
experiment-to-chiller path. Simulation and its existing screenshot are supporting
material near the bottom, followed by developer details. Runtime code is unchanged.

## Retained hardware protections and tradeoffs

- Build commands from documented bit fields; LOW before HIGH, unsigned tenths F.
- Validate inputs and both requested/quantized setpoints against caller-supplied bounds.
- Require explicit profile/framing and query-state acknowledgement before querying.
- Keep writes locked until separately enabled; fault preflight before setpoint/start.
- Serialize whole operations and pace every attempted write by at least 350 ms.
- Reject short/extra/late/implausible data; close/disarm uncertain streams and never replay.
- Reopening never rearms. Recovery requires actual physical stream/state verification.
- Return fresh observations or errors; do not refresh stale temperatures with a fault timestamp.
- Preserve raw/unknown faults; distinguish reported M5 state from unverified legacy intent.

The global registry was deliberately removed. Simultaneous ownership relies on
OS serial exclusivity and cooperating callers; the laboratory application must own
one controller per port. Replacement objects do not inherit uncertainty latches:
every new owner must re-establish the stream and physical state. No hidden process
policy pretends to prove these conditions. Approval records belong to the operator.

Standard Python DEBUG logging provides optional wire traces. Optional CSV failure
stops the monitor; it cannot disable unrelated calls made by an experiment owner.
The owner must implement its audit/abort requirements. Logging is not mandatory
for importing or using the controller. Python, the OS and USB adapters cannot
provide a hard emergency-stop deadline; an independent physical method is required.

## Validation

The rewritten suite covers protocol vectors and exhaustive word/fault cases,
actual controller I/O through fake serial, state locks, fault preflight, uncertain
delivery, concurrency, recovery, simulator, monitoring/CSV and Dash/CLI behavior.
Local results: **65 tests pass; 94.98% statement coverage (624/657 statements)**.
Ruff lint/format, mypy and dependency checks pass. The wheel and source archive
build successfully. Isolated wheel imports, packaged CSS, Dash endpoints and
headless CSV/shutdown pass; all 65 tests also pass from the extracted source
archive. Reproduction commands are in [TESTING.md](TESTING.md).

The relocated package passes HTTP layout/action/history and packaged-asset tests.
The README browser check confirmed initial CONNECTED/STANDBY, an 18 C setpoint,
START, a cooling trace and return to STANDBY; the running display was captured in
docs/images/simulator.png. Physical serial opening and discovery were blocked.
The temporary server and browser were closed. The earlier browser check also
covered retained state/history after reload and the disconnected/disabled display.

The hardware README examples were executed through the real controller against
the existing literal-byte fake serial peer, with physical access blocked. Ten
cases across both profiles verified the fault/temperature/setpoint queries,
setpoint write/readback, fault refusal, exact bytes, pacing and zero-TX disconnect.
The unedited profile placeholder fails before opening. These are software checks,
not approved physical sessions. All 14 local README links/anchors resolve.

The earlier simulator-example and 10-second headless check produced 10 standby
CSV rows, printed final status and refused overwrite. The HTTPS clone URL was
verified. The source archive contains the exact README and screenshot, and wheel
metadata contains the README. No physical evidence is claimed.

The 0.1.1 count of 640 tests is not carried forward: hundreds of bit permutations
are now grouped into exhaustive tests, and internal-architecture tests were removed.
The final pass also replaces generic-builder cases with literal vectors executed
through the public API in both RUN and STANDBY. Exhaustive temperature/fault tests
remain. Fresh-process tests forbid GUI imports while running headless monitoring.
Test count alone is not evidence of hardware safety.

## Physical gate

A query is **not guaranteed non-mutating**. Bits 7 and 6 remain active when bit 5
selects a reply. Standby queries 88/89/81 select REMOTE/STANDBY; C8/C9/C1 assert
REMOTE/RUN. A stale RUN query after A0 may request RUN again. Local-bit alternatives
also change control semantics and cannot be described as neutral.

The supplied ThermoCube II M5 manual may not describe the exact 10-400/-AR/-267
controller. Its fault mapping conflicts with the requested legacy map; both remain
explicit profiles. The HEX newline note conflicts with binary examples. Signed HEX,
the requested eight-byte buffer limit, actual wiring, thermal limits, pump/keypad
and restart behavior remain unvalidated. No framing trials or automatic profile
selection were added. See the unchanged source analysis in [PROTOCOL.md](PROTOCOL.md).

[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) retains Stage 0–7 hold points:
inspection → zero-TX opening → one standby fault query → RTD comparison → setpoint
read → separate human write approval → small approved setpoint change → bounded
remote start/stop test. Use direct calls, not the monitor or GUI, during these stages.
The previous software architecture is not physical evidence for the refactor.

Writes remain NO-GO until the actual controller/profile/framing/wiring is confirmed,
Stage 0–4 evidence passes, safe bounds and an independent abort method are established,
and the human explicitly approves the finite Stage 5–7 scope.
