# Safety assumptions and boundaries

This is an implemented offline candidate, not authorization to operate equipment.
No physical stage is authorized in the current review.
Future operation is subject to [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md).

## Principal hazards

| Hazard | Why it matters | Required design / workflow |
| --- | --- | --- |
| Queries command remote/run state | C9/C1/C8 all assert remote/run; an observation may start temperature control | Explicit control context on every operation; no neutral-read claims, probes, or polling on connect |
| Stale queued query after stop | A0 followed by old C9 can request run again | Invalidate obsolete requests; bind bits at dispatch under one lock; test the stop/poll race |
| Repeated query overrides state | Polling reasserts intent and may prevent observation of prior manual changes | Constrained query approval explicitly covers that state assertion; start physical tests one transaction at a time; independent operator shutdown available |
| Model/firmware mismatch | M5 describes ThermoCube II, while supplied part number differs | Confirm target controller and fault map before enabling the hardware profile |
| Ambiguous terminators | M5's HEX note requests OS-dependent newline bytes that can themselves decode as commands | Block hardware framing selection pending clarification; no automatic trial of framing variants |
| Fault status is not device safety | M5 bit 6 is standby, while bit 7 is unassigned; different generations may map flags differently | Separate standby, real faults, unknown flags, and stale data; no automatic alarm clearing |
| Timeout leaves ambiguous state | A write can take effect even if the host sees an error; late replies lack transaction IDs | Disarm, show unknown/stale, no replay, and validate resynchronization before resuming |
| Loss of communication is not stop | No fail-to-standby or communications watchdog guarantee was found for this target | Never promise automatic safe shutdown on unplug/process exit; provide an independent shutdown procedure |
| Stop is limited | M5 p. 15 says standby removes temperature control but the pump can still run depending on settings | Label stop as temperature-control standby, not power-off or pump-off; physically confirm configured behavior |
| Remote control restricts keypad | M5 p. 15 describes keypad lockout in remote mode | Do not rely on front-panel START/STOP without confirming its availability; agree an independent means of shutdown |
| Electrical/connector mismatch | DTE/DCE wording conflicts; DB9 may contain other option signals | Confirm RS-232 adapter and pins 2/3/5; do not connect TTL serial or unknown handshake/option pins |
| Incorrect temperature limits | LT operation, coolant freezing point, and connected load all constrain safety | Require approved bounds; validate requested and quantized values; do not use the full manual range as a default |
| Command overload | Up to 3 commands/s, 1 Hz updates, and user-specified 8-byte storage | One transaction at a time and >=350 ms command-start spacing; no catch-up or emergency-rate bypass |

## Source-backed assumptions and open limits

M5 pp. 6, 20, and 33 describe standard 5..50 C and LT operation down to -5 C
(-10 C for T26). These are source facts, not an approved range for the user's
exact unit. M5 pp. 4 and 33 require avoiding operation within 5 C of the coolant's
freezing point. The operating envelope must also account for the attached load
and condensation conditions. No executable hardware setpoint range is selected.

M5 p. 13 warns that operating without fluid can damage the pump. Before any run
assertion, an operator must confirm coolant, fill/priming, connected loop, leak
checks, ventilation, and suitability of the load using the applicable manual.
No physical setup or actuation has been performed.

`-AR` and `-267` are not decoded by the supplied manual's model table. Do not
interpret AR as a verified auto-restart option. Power-loss/restart behavior and
remote/local persistence need target-specific evidence. The manual's advice to
cycle power after communication problems is source content, not permission for
an automated power cycle. Alarm-clear/restart commands are outside project scope.

## Implemented enforcement and limits

Modes are explicit backend policy, not merely GUI button states. Offline/simulator
code has no hardware discovery or opening side effects. A transport-only session
has no capability to send chiller commands. Query mode has fixed authorized
remote/run intent and only the required query allowlist. Control mode adds bounded
setpoint writes and explicit start/stop, retaining rate limits and all other gates.

An application error cannot grant more authority. Runtime approval/configuration
cannot be widened in place; a new approved session is required. Stop/error/disconnect cancels obsolete pending controls; a queued stop survives
later disconnect/disarm requests and failures. Audit failure pauses active queries. Ambiguous
I/O invalidates observed state. Any software stop remains paced
and may be delayed by an in-flight timeout; the candidate report must measure and
state its limits. A conservative configured budget is 3 × (0.35 + 0.5 + 0.5) +
(0.35 + 0.5) = 4.9 s for a complete in-flight snapshot followed by a stop write.
This is an I/O scheduling budget, not a measured worst-case guarantee: OS opening,
scheduling, logging and adapter delivery are not bounded by this software. A timeout instead disarms
and refuses uncertain serial control. It is not a hard real-time interlock.

The driver must not automatically restart on fault disappearance, resend a setpoint
after reconnect, or restore a previous run flag just because the saved configuration
contains one. Reuse recorded approval within its scope, while requiring explicit
re-establishment of live state after an uncertain connection.
