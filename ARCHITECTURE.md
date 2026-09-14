# Application architecture

Implemented as seven small backend modules and two app modules. No backend module
imports Dash; the app factory does not open a port or create a worker.

~~~mermaid
flowchart LR
    GUI[Dash callbacks] -->|semantic requests| QUEUE[Bounded command queue]
    QUEUE --> WORKER[One acquisition worker]
    WORKER --> DRIVER[ThermoCube driver]
    WORKER --> SIM[Simulator: same Device API]
    DRIVER --> CODEC[Pure codec]
    DRIVER --> SERIAL[Serial transport + global pacing]
    WORKER --> HISTORY[Immutable snapshots / bounded history]
    HISTORY --> GUI
    WORKER --> CSV[CSV samples + events + manifest]
~~~

## Responsibilities

| Module | Responsibility |
| --- | --- |
| thermocube/protocol.py | Bit construction, allowlisted messages, LOW/HIGH words, units, explicit fault profiles; no I/O |
| thermocube/transport.py | pyserial lifecycle, physical approval boundary, exact-length reads, stream quarantine, per-port ownership and pacing |
| thermocube/driver.py | Semantic API, query intent, write locks, setpoint limits/readback, fault inhibition, disarm and recovery |
| thermocube/simulator.py | Same API with thermal evolution, lifecycle, faults, delay, timeout and disconnect injection |
| thermocube/acquisition.py | Single worker, request outcomes, reconnect backoff, history, events and CSV ownership |
| thermocube/logging.py | Exclusive session files, stable schemas, quoting, flush and close |
| thermocube/models.py | Device protocol, immutable snapshots/events/faults, enums and exceptions |
| app/dash_app.py | Cached-state presentation, history, explicit command review and queued actions |
| app/cli.py | Composition, JSON configuration, simulation default, local Waitress launch and orderly shutdown |

Standard-library locks, threads, deques and dataclasses are sufficient. Runtime
dependencies are pyserial; optional GUI dependencies are Dash, Plotly and Waitress.

## Ownership and timing

Direct driver calls share an RLock across full semantic operations. A snapshot's
three queries and a setpoint write/readback cannot interleave with another call.
The transport also serializes every exchange; physical instances share a port
registry and only one owner can open a given normalized port in this process.
Operate one application process and do not use another terminal/control program
on the port. OS/adapter behavior still requires physical validation.

Every attempted write consumes a pacing slot. The next write starts at least
350 ms after the prior write completes, including failed writes and reconnects.
A physical open also establishes an initial quiet interval. No rate credits,
catch-up bursts, or stop exception exist. Monotonic time controls deadlines;
UTC and monotonic TX-start timestamps are retained in serial events.

The service samples nominally every 1.05 s, querying faults first, then
temperature and setpoint only when the fault/state check passes. Each field has its own actual observation time; these are not a
simultaneous measurement. Commands take available worker time and delay polling.
Hardware data updates only once per second. A fault can arise between samples;
this application is not an independent safety interlock.

Actual return times are checked against read/write deadlines; a late complete
reply is still uncertain. Quarantine and pacing persist across replacement
transports for the same physical port within the process. Approval, selected
configuration, driver profile and limits are immutable public properties.

Defaults: read/write timeout 0.5 s each; two open attempts, 0.5 s apart; reconnect
backoff 0.5..10 s; command queue 32; history 3600; completed results bounded by
queue capacity plus 128. Driver/transport event queues hold 1024 each; direct
API users should drain them regularly. CSV rows flush individually.

## Lifecycle and authority

Construction and imports are inert. Hardware connect opens only an approved
port, sends no probe and leaves queries unarmed. Port-open, device-responding,
query-armed, control-enabled and reported run state are distinct fields.

Constrained queries require an exact acknowledgement and a selected, physically
established remote/run context. Control additionally needs an acknowledgement
and approved temperature bounds. Each message is built from current intent
under the driver lock. No raw command is exposed through the service or GUI.

Stop/disconnect/disarm cancel obsolete pending control commands. A queued stop
is retained before a later disconnect or disarm; duplicate shutdown requests
reuse the pending request. No new work is accepted once close begins.
In-flight I/O finishes or reaches its deadline first. A failed operation cancels
pending control work while preserving queued shutdown requests. Standby does not
imply power-off, pump-off, or return to local mode.

An ambiguous transfer closes/quarantines the stream and disarms control. Reopen
can retry port opening only; it cannot retry a transaction, resume queries,
replay setpoints or start the unit. Empty RX alone cannot prove recovery: the
operator must validate the stream reset and physical state, acknowledge recovery,
then separately arm queries/control within the approved scope.

An observed fault, unknown bit, or reported-state mismatch inhibits further
snapshot queries and start/setpoint operations. Both start and setpoint write
perform a paced fault preflight, so rearming cannot bypass the fault interlock. Existing control permission permits an explicit STOP
if the link remains usable. This does not clear faults or automatically restart.
A communication failure locks that stop path too; use independent physical
shutdown when serial delivery is unavailable or uncertain.

## Data and UI

Snapshot values are frozen; GUI callbacks read cached snapshots/history and queue
semantic requests. Refreshing or adding browsers never creates acquisition.
The launcher runs one local Waitress process, four HTTP threads, one worker, no
development reloader. This is a single-operator localhost application without
authentication or a supported multi-user/network deployment.

Legacy fault bytes cannot report RUN/STANDBY; the UI says REQUESTED · UNVERIFIED.
The M5 profile can expose fresh bit-6 status. Simulation reports its own modeled
state. Each value uses its own observation timestamp; values older than 3.5 s, future
timestamps or incomplete snapshots display unavailable, plots
have gaps, and start/setpoint controls disable. Standby remains available when
existing control authority allows it. Fault text preserves unknown masks.

The GUI stages hazardous actions, displays the exact reviewed request, and requires
a separate Confirm command click. Cancelling queues nothing. The backend remains
the authority even if a client bypasses presentation controls.

CSV schema version 1 is defined by SAMPLE_FIELDS and EVENT_FIELDS in logging.py.
Samples include sequence, UTC/per-signal times, raw tenths-F values, Celsius,
source, mode, permission, intent/reported state, raw/profile/unknown faults,
quality and error. Events include operation/outcome/detail, exact TX/RX,
UTC TX start and monotonic TX seconds. The JSON manifest records the same single configuration read used to construct
the device. Reserved session/schema fields cannot be overwritten by caller metadata.

Files use exclusive creation, UTF-8, ordinary CSV quoting and blanks for absent
numbers. Sample and event rows flush when the worker writes them; queued/cancelled
request events briefly reside in a bounded audit queue, so GUI submissions never
wait for disk I/O. Flush is not a power-loss durability guarantee. Disk errors
stay visible and pause all polling, automatic reopen and new control work, since
queries also assert active state. Existing explicitly permitted
stop/disconnect/disarm remain available. Audit overflow also inhibits controls
and polling. Rotate sessions and manage retention explicitly.
