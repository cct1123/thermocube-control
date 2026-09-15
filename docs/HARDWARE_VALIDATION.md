# Staged hardware validation — candidate 0.2.0

**NO-GO for writes. No physical stage is currently approved or executed.**
This procedure is a reviewable proposal, not permission. Never enter Stage 5+
automatically. Use one operator, one port owner and the direct driver, without
Dash, Monitor, terminal auto-probes or another serial application.

## Why query testing is a control test

M5 Table 2 and p. 23 note 2 make bits 7, 6 and 5 active on every command.
Direction=0 selects a returned parameter; it does not disable remote/local or
run/standby. There is no documented “leave those bits unchanged” encoding.

88, 89 and 81 each assert REMOTE and STANDBY. C8, C9 and C1 each assert REMOTE and
RUN. Local-bit queries can transfer control to the front panel; they are not
neutral alternatives. A response cannot prove what the state was before the query.
Never describe this as electrically or logically read-only testing.

This candidate uses raw binary bytes, no CR/LF. The supplied M5 HEX newline note
conflicts with its examples. Resolve this for the actual firmware before Stage 2.
If terminators are required, this candidate is NO-GO: revise the implementation
and repeat offline tests first. Do not trial framing, alternate profiles or baud
rates against the unit. CR/LF may themselves decode as active commands.

## Record before any physical work

Record candidate manifest/hash; actual approval, approver and scope; unit model,
serial number, controller/firmware; applicable manual and fault profile; host,
adapter, selected port and pinout; initial physical state; exact permitted bytes,
count and duration; log location; operator observations; independent abort method.
API acknowledgement strings document deliberate choices, not authority
or proof of physical conditions.

Before Stage 2 also resolve framing, standby-query applicability and known fault
mapping. Before Stage 5 record actual coolant/load/device limits, initial
setpoint S0, proposed small target S1, permitted run states and run duration.
The meanings of -AR/-267 and restart/pump/keypad behavior must be established.

Use 9600 baud, 8 data bits, no parity, one stop bit, all flow control off. Initial
tests use timeout=0.5 s (both reading and writing) and command_interval=0.35 s.
connect() attempts opening once. Changing these values changes the reviewed configuration.
All TX uses the same limiter. Successful host writes do not establish device ACKs.

## Stage 0 — inspection, zero protocol commands

Inspect the nameplate/controller and applicable manual. Check cable continuity,
adapter identity, genuine RS-232 levels (not TTL), connector and cable topology.
M5's DTE/DCE wording conflicts; its DTE-host table shows host RX2 to chiller TX2,
host TX3 to chiller RX3 and ground5 to ground5. Verify the actual unit/adapter;
do not infer topology from connector gender or attach unknown option/handshake pins.

Inspect the fluid circuit, fill/priming, leaks, ventilation, coolant suitability
and attached load. The load must safely tolerate standby; do not stop an operating
cooling process merely to fit this procedure. If standby cannot be safely
established, hold and review a different explicitly authorized sequence.
Establish a safe independent physical shutdown method. Confirm
whether standby keeps the pump running and whether remote mode locks the keypad.
Record front-panel state without operating through the protocol. Availability
of a COM port is not device identification or approval.

**Pass:** electrical topology and physical readiness documented; unresolved
items are explicitly held. **Fail/hold:** unknown wiring, controller or abort method.

## Stage 1 — connection lifecycle, zero writes

After explicit Stage 1 approval, test the adapter isolated from the chiller first.
OS metadata discovery is allowed only within that scope. Observe RTS/DTR behavior
during one open/close: requested false values do not guarantee absence of glitches.
A pyserial open can affect lines even though the application transmits zero bytes.

Construct the controller with only the port; call connect() and disconnect()
without arming queries. Opening is attempted once. No serial
test string, echo check, break, newline, flush-as-probe or implicit stop is permitted.
Only after isolated behavior and wiring pass may a separately approved attached
open/close occur. Record any unsolicited received bytes; do not respond to them.

**Pass:** zero TX, correct settings, acceptable measured line behavior, orderly
close. This does not validate device identity or protocol framing.

## Stage 2 — one constrained fault query

Only after compatibility/framing resolution and Stage 2 approval: physically
establish STANDBY using the approved procedure. Explicitly authorize the query
to select REMOTE and reassert STANDBY. Query approval does not authorize E0/A0
to create the initial state.

Construct ThermoCube with the confirmed profile, binary_framing_confirmed=True
and no limits_c. Do not call enable_control(). The approved query context is run=False.
Open once, acknowledge the already established context with run=False, then call
read_faults() **once**. TX **88**, RX **one raw byte**, no echo/ACK or suffix.
The first TX is at least 350 ms after open.

Healthy expected byte: **00** for a confirmed legacy profile; **40** for M5
standby. Preserve the whole byte. Legacy bit 6 is unknown, not run telemetry.
Observe panel and physical behavior before/after; do not infer preservation merely
from the response. Halt on any fault, unknown bit, transition or uncertain exchange.

The operator can use the following one-query lifecycle after actual approval:

~~~python
from thermocube import ThermoCube
from thermocube.controller import QUERY_ACK

# approved_port and confirmed_profile come from the operator's Stage 0–2 record.
with ThermoCube(approved_port, profile=confirmed_profile,
               binary_framing_confirmed=True) as device:
    device.arm_queries(run=False, acknowledgement=QUERY_ACK)
    print(device.read_faults())  # Only TX: 88; disconnect adds zero bytes.
~~~

This deliberately stops after one query. No background acquisition is created.
Configure an independent trace or the standard thermocube.controller DEBUG logger
before opening; it records exact TX/RX and uncertain exchanges. Record the returned
raw fault byte and panel observations. Logging is optional in the reusable core
and is not an interlock: the operator must verify the required audit evidence
after each step before authorizing any further traffic.

## Stage 3 — RTD and fault comparison

After Stage 2 passes, compare its fault byte against the panel and the confirmed
profile. A zero-fault result alone does not verify the meanings of nonzero bits.
Use manufacturer evidence or later approved non-damaging fault demonstrations;
never run dry, block fans/flow, short an RTD or create a leak.

Re-establish/confirm the same physical standby context and approve one
read_temperature(): TX **89**, RX **LOW HIGH**, each temperature word in 0.1 F.
Compare the decoded RTD value with the panel. Choose the tolerance beforehand:
panel half-display-increment plus 0.05 F (0.0278 C) wire rounding, with recorded
allowance for observation-time variation. This is communication agreement, not
temperature calibration.

**Pass:** fault interpretation and temperature agree with the panel within the
predeclared tolerance, with no unexpected state change. Unstable data is inconclusive.

## Stage 4 — setpoint reading

After Stage 3 passes and the standby context is confirmed again, approve one
read_setpoint(): TX **81**, RX **LOW HIGH**. Compare to the front-panel setpoint
using recorded display/quantization tolerance. Record S0 and raw word.

Stages 2–4 use a maximum of **three queries total**, one per stage, within a
recorded maximum session duration (proposed: five minutes). Each stage is a hold
point. Expiry, an uncertain response or changed physical state ends the sequence;
it never grants extra retries. Disconnect adds **zero cleanup bytes**, and does
not restore local mode or guarantee the physical state.

## Stage 5 — human write-approval gate, no automatic transition

Stop and obtain explicit human approval tied to Stage 0–4 results and this
candidate. Record approval externally and construct a controller with the reviewed
profile/framing and limits_c. Start a fresh, explicitly established standby
session. Set approved Celsius bounds to the intersection of the actual
device, coolant and load limits. A generic manual range is not a safe default.

Enable control only with the exact acknowledgement:
ENABLE SETPOINT START AND STOP CONTROL. Call enable_control() only after actual
human approval. The core has no approval/policy object and does not enforce stage
numbers: the owning application/operator must enforce the approved finite scope.

**GO requires every item:**
- Stage 0–4 evidence passes for the exact unit, cable, firmware, profile and framing.
- No active/unknown faults, stale or implausible data, state mismatch, audit failure
  or unresolved stream quarantine.
- Human-approved S1, bounds, maximum change, command/time budget and restoration plan.
- Verified independent shutdown; agreed response to loss of link, keypad lockout,
  retained pump operation and any restart option.
- Offline checks pass for the current manifest; no unreviewed changes.
- Stage 6 and Stage 7 authority is explicit; run authority includes an approved
  final standby state and observed time limit.

Any missing item is **NO-GO**. Plausible data, a config file or a successful serial
write is insufficient. Current project status remains NO-GO.

## Stage 6 — one small approved setpoint change

Remain in the confirmed standby context. Choose S1 only after knowing S0 and the
physical envelope; no universal safe increment is specified. Calculate and record
the quantized word before sending. Reject both requested and quantized values
outside bounds; never clamp or test the device's limit by trial writes.

One set_setpoint(S1) now sends exactly:
**88** fault preflight; **A1 LOW(S1) HIGH(S1)**; **81** setpoint readback.
All three commands are paced. A fault/mismatch prevents the write. A readback
difference or ambiguous delivery stops the procedure without replay.
Verify the panel and physical standby state as well as the exact returned word.

Restoration to S0 is an additional approved three-command operation; it is not
automatic cleanup and must still be physically appropriate.

## Stage 7 — bounded remote start/stop validation

Only with explicit Stage 7 approval, no background polls, a safe load, a recorded
maximum run time and independent shutdown available:

1. With established STANDBY, start() sends **88** preflight then **E0**.
   Observe the actual transition. A successful TX is not a confirmed start.
2. Only while the operator confirms the approved RUN context, one read_faults()
   sends **C8**. M5 bit 6 should be clear; legacy run state remains unverified by
   protocol and must be judged physically. This query reasserts RUN—it cannot
   prove the previous state and may counteract a manual standby change.
3. Request stop() once: **A0**. Observe standby and separately check pump behavior.
4. After physical standby is confirmed, one read_faults() sends **88**, never C8.
   Any subsequent approved query must retain run=0.

Budget: **five commands**, no retries or extra snapshots. Choose the run-duration
limit before testing; the software cannot guarantee an emergency stop deadline.
Abort immediately on an unexpected transition, fault, timeout, stale state,
missing log or operator stop request. Do not send another RUN query after any
manual intervention that may have put the unit into standby.

## Abort and later recovery

Stop further traffic on failed/inconclusive criteria. Use the agreed independent
physical method when necessary; do not repair state with speculative bytes,
alarm reset, profile changes or an unapproved power cycle. Disconnect alone is
not an abort mechanism for a running chiller.

Ctrl+C during serial I/O is also an uncertain outcome. The driver attempts to
close/disarm and requires recovery, then propagates the interruption. It sends
no STOP or other cleanup command; use the approved physical method when needed.

Late/ambiguous I/O quarantines that controller's stream. Reopening cannot rearm
or replay commands. There is no global registry: replacing an object or process
does not establish safety. Every new owner must verify the stream and physical
state before arming. Recovery needs an
actual manufacturer-supported stream reset and physical-state verification,
then a recovery acknowledgement and separate query arming. An empty buffer
alone is insufficient. Revalidate after application restart as well.

Extended acquisition, link-loss-in-run, power-loss/-AR behavior, recovery and
non-damaging fault demonstrations need separate finite approval after these stages.
No such physical tests have been run.
