# Critical pre-hardware engineering review

**Disposition: NO-GO for write-enabled hardware testing.**
Candidate **0.1.1**, 2026-09-14, for ThermoCube
**10-400-1D-1-CP-R2-LT-AR-267**. No physical port discovery, open, query, write,
power operation or other hardware access was performed. Stage 5+ remains closed
pending explicit human approval and the physical criteria below.

## Findings and corrections

The 0.1.0 baseline passed 618 tests, but targeted review reproduced eleven failing
safety/data-integrity cases. Those statistics had overstated the breadth of
failure-path assurance. The following defects are corrected in 0.1.1.

| Finding | Consequence before correction | Correction / regression evidence |
| --- | --- | --- |
| Queued stop could be cancelled by later disconnect/disarm | The requested standby never reached the device before link release | Preserve stop ahead of later shutdown requests; deduplicate pending shutdown; reject new work once close begins; test in-flight setpoint → stop → disconnect |
| Faults were polled last; rearming bypassed the setpoint fault lock | Two extra state-asserting queries occurred before detecting faults; a setpoint could be written while a known fault persisted | Query faults first; end faulty/mismatched snapshot immediately; fault preflight before every setpoint write and start |
| Complete replies/writes returned after deadlines were accepted | Late data could be treated as current; delivery timing remained ambiguous | Check actual completion time, normalize pyserial timeout exception, quarantine and never replay |
| Quarantine belonged only to one transport instance | Replacing the object could bypass recovery requirements | Quarantine shares the per-port state with pacing/ownership; replacement remains locked |
| GUI freshness used a shared snapshot timestamp | Fresh fault data made old RTD/setpoint values appear fresh | Check each observation timestamp; missing, old or future values cannot enable start/setpoint |
| Logging failure left active polling running; cancellation was absent from CSV | Device state continued to be asserted without audit evidence; cancelled requests disappeared from logs | Pause polling/reopen on audit failure; bounded worker audit queue records queued/cancelled outcomes while preserving explicitly permitted shutdown |
| Metadata/configuration were not stable | Caller metadata could replace session identity; a second file read could log a different config than the one used | Protect reserved identity fields; parse configuration once; immutable approval/profile/limits; copy mutable run-state lists |
| Import isolation and headless exit were weak | Fixtures did not protect pre-test imports; failed acquisition could exit successfully | Install test guards before collection, add fresh-process import check; failed headless runs exit with failure and close resources |

The relevant regressions are in
[test_review_regressions.py](../tests/test_review_regressions.py), with revised
literal transaction expectations in the driver tests. These are software proofs
for stated scenarios, not physical guarantees.

## What was simplified

Removed redundant PROJECT.md, STATE.md, docs/PLAN.md, the accumulated agent ledger,
initialization-only checks and duplicate test README. The latest review now
contains the disposition; one Stage 0–7 document owns the physical gate.
AGENTS.md is concise contributor guidance. Prompt history and manual provenance
remain intact.

Removed repeated version literals using one package version source, eliminated
duplicate configuration file reads, reused the shutdown-operation allowlist, and
made artifact verification select the actual candidate rather than a fixed old
version. No feature expansion, plugin registry, database, task scheduler, device
manager or new service framework was added.

Publication cleanup consolidates this review under docs/, removes session-specific
contributor instructions and keeps generated reports/builds out of Git. Artifact
verification now runs from tools/ in self-cleaning temporary directories and is
part of CI. Source archives include the referenced review, manual, prompt history,
verification script and concise evidence. Text uses consistent LF line endings.

The small Device and SerialLike typing protocols remain: they separate consumers
from device semantics and fake/real serial I/O. The seven backend modules plus
Dash/CLI composition are appropriate boundaries, not speculative abstractions.
Serial validation at both semantic and transport boundaries is retained deliberately.

## Protocol review

The supplied M5 manual was re-extracted and printed pp. 22–23 visually inspected.
Tables 2–5 and note 2 agree with the codec's bit layout, LOW-before-HIGH order,
0.1 F representation and M5 fault profile. Command examples are derived from
the bit structure, not scattered opcodes.

A query is **not guaranteed non-mutating**. Bit 5=0 requests chiller-to-host
data, but bit 7 still selects remote/local and bit 6 still selects run/standby.
There is no documented preserve-state encoding. In particular:

| Query state | Fault / RTD / setpoint bytes | Active request |
| --- | --- | --- |
| Remote, standby | 88 / 89 / 81 | Select REMOTE and assert STANDBY |
| Remote, run | C8 / C9 / C1 | Select REMOTE and assert RUN |
| Local-bit alternatives | Bit 7 cleared | Transfer/select LOCAL; no neutrality guarantee |

A0 followed by stale C9 can request RUN again. Query-only approval must explicitly
cover the chosen state assertion, even though setpoint/start/stop methods are
locked. A response cannot establish the pre-query state or demonstrate that
physical cooling/flow is safe. A running load must not be put into standby just
to follow a convenient test script.

Temperature roundtrips exercise all unsigned words but do not establish that the
hardware supports that numerical range or signed HEX encoding. Requested and
quantized setpoints are checked against approved bounds. The broad read plausibility
window is a malformed-data guard, not a safety envelope or overtemperature interlock.

The user-required legacy fault mapping (0 tank, 1 fan, 3 pump, 4 RTD open, 5 RTD
short; C4 unknown) conflicts with M5 (2 flow, 4 leak, 5 RTD, 6 standby; 80 unknown).
Both explicit profiles are retained. Legacy has no documented run feedback in
this contract. Model/firmware identification must resolve which is applicable.

M5's HEX newline note still conflicts with its binary examples. No terminator
trial or OS-dependent assumption is permitted; this candidate supports raw binary
only. The requested 8-byte capacity is not substantiated by M5 and remains a
conservative single-outstanding-request constraint.

## Architecture, limitations and verification

The driver locks complete semantic operations; transport pacing/ownership is
global per normalized physical port within this process. POSIX also requests
exclusive opening; Windows serial handles exclude another ordinary opener.
Do not rely on this against a second uncontrolled program or alternate hardware
path. The limiter uses actual monotonic time for physical paths and at least
350 ms after each attempted write completes. Stop shares that limit.

Read/write return deadlines are enforced, but Python cannot forcibly bound an
OS call or USB delivery. A conservative snapshot-plus-stop I/O budget is 4.9 s,
excluding OS scheduling, opening, logging and adapter delays. This is not an
emergency-stop guarantee. Independent physical shutdown remains necessary.
Opening can glitch RTS/DTR despite requested values; this is why the first
connection test uses an isolated adapter. [pyserial API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html).

One independent worker owns acquisition, semantic requests, CSV and bounded
history. GUI callbacks render cached values and enqueue work. CSV flushes do not
guarantee survival of power failure. A stalled filesystem or OS call can still
delay the worker; it is not a safety controller. Audit failure stops polling
because polling itself commands state.

Simulation is a useful API test model of heat/cool approach, standby relaxation,
faults/delays and link lifecycle. It is not evidence of real thermal dynamics,
firmware retries, pump behavior or electrical timing. Independent byte fakes
exercise the production driver separately.

Offline checkout results: **640 passed; 93% statement coverage**
(1,198/1,284 statements). Ruff lint/format and mypy pass. Counts include many
parameterized cases; they do not substitute for the review or physical tests.
The 0.1.1 wheel and source archive build successfully. The isolated installed
wheel passes import, Dash/CSS, simulated headless CSV and shutdown checks;
the extracted source archive independently passes **640 tests**.
Dependency checks pass. Serial opening and enumeration are blocked in these tests.
Source hashes are in the [candidate manifest](../records/candidate-manifest.json);
the [test map](TESTING.md) describes scope, evidence and reproduction.

At the time of the local review, the configured CI matrix had not run remotely;
local results are Windows/Python
3.12.14. Current GUI evidence is HTTP callbacks/presentation/assets; previous
interactive browser testing applied to 0.1.0. No physical test or long hardware
soak is claimed. No current application worker or automation is left running.

## Remaining physical sequence and write criteria

[HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) is the exact procedure:

0. Inspect unit/controller, wiring, cable topology, adapter/settings, fluid/load
   state and independent abort method; zero protocol commands.
1. Approved isolated adapter open/close, then attached lifecycle only if safe;
   zero application writes and observed control-line behavior.
2. After framing/profile confirmation and safe physical standby establishment,
   send 88 once, expecting one fault byte.
3. Compare that fault byte with the panel, then send 89 once for LOW/HIGH RTD
   data and compare within a predeclared display/quantization tolerance.
4. Send 81 once for setpoint readback; record the initial setpoint and raw word.
5. Hold for human approval tied to passing evidence, exact candidate and actual
   limits/target/run-duration/abort plan. Never advance here automatically.
6. One small approved standby setpoint change: 88 preflight, A1 LOW HIGH, 81 readback.
7. Separately approved bounded start/stop: 88, E0, approved RUN-context
   C8 as specified, A0, then standby-context 88; observe the real behavior.

Stages 2–4 allow three queries total, no background acquisition, no retries or
cleanup TX. Stage 7's complete proposed sequence has five commands. Every stage
is a hold point; unexplained data or state ends further traffic.

**GO for writes only when** the exact controller/profile/framing/pinout is
confirmed, Stage 0–4 evidence passes, no faults/unknown bits or stale/uncertain
stream remains, audit logging works, the approved target and bounds are safe
for the actual unit/fluid/load, independent shutdown and restart/pump/keypad
behavior are established, and the human approves Stage 5–7 scope.

**Current result: NO-GO.** Those physical facts and approvals remain outstanding.
Do not clear alarms, power-cycle, alter profiles to fit a reply or widen bounds
to obtain a passing result. Link-loss, power-loss/-AR, extended acquisition and
recovery tests require separate finite scope after this sequence.

The review stops here for human approval. All user requests are preserved in
[prompt log.md](../prompt%20log.md).
