# R2 protocol specification

Status: **implemented and tested offline against the documented logical bytes;
hardware applicability and framing unresolved; no physical validation**.

Source M5: [Thermocube-II-rev-M5.pdf](../inputs/Thermocube-II-rev-M5.pdf), document
52-14790-1, version M5. Page references below are printed page numbers; add one
for the PDF viewer's 1-based page number. Source digest and inspection method are
in [inputs/README.md](../inputs/README.md).

This specification distinguishes **M5 facts**, **derived bytes**, **project design
choices**, and **unresolved issues**. The user's target remains
`10-400-1D-1-CP-R2-LT-AR-267`. M5 is not evidence that this exact variant has a
ThermoCube II controller. The follow-up user request explicitly requires a legacy
fault map that differs from M5. Both named profiles are implemented; M5 findings
remain intact rather than being relabeled to match the requested map.

## Link and timing facts

M5 section 7.1, p. 20, defines R2 as RS-232 at 9600 baud, defaulting to HEX.
Section 7.2.1, p. 21, specifies:

| Property | Required interpretation |
| --- | --- |
| Encoding | Raw command/data bytes represented in the manual as hexadecimal; not ASCII text such as `C9` |
| Serial setup | 9600 baud, 8 data bits, no parity, 1 stop bit; no software or hardware flow control |
| Initiation | Host initiates; chiller is slave; no interrupts, so status requires polling |
| Data refresh | Once per second |
| Command rate | Maximum 3 commands/s; p. 23 note 3 additionally requires even spacing, described as 333 ms |
| Transmission | One command byte with zero, one, or two data bytes depending on operation |
| Echo | No command echo (p. 23 note 4) |
| Wiring | DTE host RX pin 2 to chiller TX pin 2; host TX pin 3 to chiller RX pin 3; ground pin 5 to pin 5 (Table 1A) |
| Cable length | At most 15 m in the rendered p. 21 table |

The p. 21 figure calls the chiller DCE, while its master/slave text calls it DTE.
Use the signal table as the documented wiring basis; verify the actual adapter,
connector, and option-specific pins before connection. Do not infer cable wiring
from connector gender. Other pins may carry option signals, not modem control.

**Storage discrepancy:** the request specifies 8 bytes of internal transmission
storage. M5's HEX section does not state that limit; a full-text search found no
8-byte storage statement. Retain it as a conservative project constraint with
unverified provenance. It never justifies batching commands or multiple outstanding
responses.

## Command construction from Table 2

M5 section 7.2.2, Table 2, p. 22:

| Bits | Meaning | Value 0 | Value 1 |
| --- | --- | --- | --- |
| 7 | Control source | Local | Remote |
| 6 | Temperature-control state | Standby | On/run |
| 5 | Data direction | Chiller to host | Host to chiller |
| 4 through 0 | Parameter selector | Five-bit parameter number | Five-bit parameter number |

For explicitly selected booleans `remote`, `run`, `host_to_chiller` and a validated
parameter `p` in 0..31, derive the command as:

```text
command = (remote << 7) | (run << 6) | (host_to_chiller << 5) | p
```

The controller exposes only the supported device operations. It constructs these
bits inside its private exchange method; there is no generic command builder or
public raw-write API. Mathematical encodability is not permission to transmit.

**All three high bits are active for each command** (p. 23 note 2). Direction 0
does not disable bits 7 and 6. Neither remote=0 nor run=0 means "leave unchanged."
No neutral read or independent remote-state query is documented for this HEX subset.

## Required operations and derived command bytes

Table 3, p. 22, assigns `00001` to setpoint (two data bytes), `01001` to outlet
fluid temperature (two), and `01000` to faults/status (one). The all-zero selector
for control-only operations is evidenced by E0/A0 examples in Table 9, p. 23;
it is **not separately listed in Table 3**.

The following are logical command/payload bytes. The complete framing remains
subject to U02 below. Response sizes come from Tables 3 and 9.

| Operation | Remote | Run | Direction | Parameter | Command | Host data after command | Expected data from chiller |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Set setpoint, remote/run | 1 | 1 | 1 | 0x01 | 0x80 + 0x40 + 0x20 + 0x01 = **E1** | LOW, HIGH | No acknowledgement documented |
| Read setpoint, remote/run | 1 | 1 | 0 | 0x01 | 0x80 + 0x40 + 0x01 = **C1** | None | 2 bytes |
| Read outlet temperature, remote/run | 1 | 1 | 0 | 0x09 | 0x80 + 0x40 + 0x09 = **C9** | None | 2 bytes |
| Read faults/status, remote/run | 1 | 1 | 0 | 0x08 | 0x80 + 0x40 + 0x08 = **C8** | None | 1 byte |
| Remote start only | 1 | 1 | 1 | 0x00 | 0x80 + 0x40 + 0x20 = **E0** | None | No acknowledgement documented |
| Remote stop only | 1 | 0 | 1 | 0x00 | 0x80 + 0x20 = **A0** | None | No acknowledgement documented |

These reproduce Table 9 by bit construction, rather than treating the examples
as unrelated opcodes. For **remote/standby**, clearing bit 6 derives queries
**81**, **89**, **88**, and setpoint write **A1**. These are table-derived
combinations, not separately demonstrated Table 9 examples; their operation in
standby needs controlled physical validation. No local-mode query is authorized
by default.

Consequences:

- C9/C8/C1 request remote/run even if the device was in standby before the query.
- A later C9 after A0 can request run again. Queued queries must not retain an
  obsolete run bit; build them at dispatch from current authorized intent.
- A constrained query session permits repeated assertion of its selected state.
  It does not observe the previous state without affecting it. Changing that
  selected state is a control operation requiring the applicable scope.
- Status received after a query cannot prove what the run state was before the
  query. A successful response also cannot prove that a pump or thermal process
  is physically operating correctly.

## Temperature representation

Table 4, p. 22, encodes temperatures in **0.1 degree F**, LOW first, HIGH second.
For the nonnegative values illustrated in M5:

```text
raw_tenths_f = LOW + 256 * HIGH
temperature_f = raw_tenths_f / 10
temperature_c = (temperature_f - 32) * 5 / 9
```

Golden examples: 0.1 F -> `01 00`; 30.0 F -> `2C 01`; 50.0 F -> `F4 01`;
70.0 F -> `BC 02`. Thus a remote/run write of 10 C has the logical bytes
`E1 F4 01`. Do not send strings containing those hex characters.

Design choice: use integer tenths F internally for wire values. Convert explicit
Celsius inputs using decimal arithmetic; quantize to the nearest 0.1 F with a
documented tie rule (Decimal ROUND_HALF_UP, half away from zero). Check both requested and
quantized values against approved device/application bounds, and report requested,
effective, and read-back values separately. Never silently clamp a request.

M5 says an out-of-range setpoint is ignored (Table 4). Host-side validation and a
separately paced readback are therefore necessary; a successful serial write does
not confirm acceptance. No write ACK, checksum, transaction ID, or HEX error reply
is specified in the inspected section; do not invent any.

M5 provides no explicit signed HEX temperature rule. Negative Celsius is not
negative Fahrenheit: -5 C is 23 F, represented by positive 230 tenths F. Do not
infer two's-complement encoding from the separate ASCII protocol. Values whose
meaning is unverified must be retained raw and flagged, not interpreted as a
plausible measurement.

The codec represents unsigned 16-bit tenths-F words. Representability is checked
after quantization, so floating-point conversion noise at 0 F and 6553.5 F does
not break a valid roundtrip. All 65,536 words roundtrip through both unit APIs in
tests. This mathematical range is not a device operating range. Hardware decoding
adds a provisional -20..100 C plausibility check; control bounds default to absent.

## Fault/status byte and explicit profiles

The project requirements include the **legacy-r2** profile. Hardware construction
does not select a profile automatically:

| Bit | Mask | User-required legacy meaning |
| --- | --- | --- |
| 0 | 0x01 | Tank level low |
| 1 | 0x02 | Fan failure |
| 2 | 0x04 | Unknown/reserved, preserved |
| 3 | 0x08 | Pump failure |
| 4 | 0x10 | RTD open |
| 5 | 0x20 | RTD short |
| 6 | 0x40 | Unknown/reserved, preserved; not assumed to report standby |
| 7 | 0x80 | Unknown/reserved, preserved |

Thus unknown_mask = raw & 0xC4 in legacy-r2. This is an explicit user-specified
contract, not a transcription of M5 or proof of the target's firmware. The driver
does not claim reported RUN/STANDBY for this profile; only command intent is known.
Selecting it does not authorize hardware. Its applicability must be confirmed in U01.

The separate **thermocube-ii-m5** profile retains the supplied manual verbatim in
meaning, with unknown_mask = raw & 0x80. Both profiles preserve the full raw byte.
Any active fault or unknown bit inhibits further queries; no automatic profile
detection, alarm clear or restart occurs. Every possible byte is tested in both.

M5 Table 5, p. 22, assigns:

| Bit | Mask | Meaning |
| --- | --- | --- |
| 0 | 0x01 | Tank level low |
| 1 | 0x02 | Fan failure |
| 2 | 0x04 | Flow fault, with flowmeter option |
| 3 | 0x08 | Pump failure |
| 4 | 0x10 | Leak detected, with leak-detector option |
| 5 | 0x20 | RTD fault |
| 6 | 0x40 | Stop / standby status |
| 7 | 0x80 | Unassigned |

Bits 0..5 are documented fault indications. Bit 6 must be displayed as standby,
not classified as a hardware failure merely because it shares the fault byte.
Preserve bit 7 and any unexpected option flags in raw records and show an unknown
or configuration warning; do not silently discard them. `0x40` means standby
with no defined fault bits; `0x49` combines standby, tank-low, and pump-failure.

For the M5 profile, a fresh bit 6 can support reported standby / not-standby.
Display running as a reported controller state, not verified flow or cooling.
Unknown, stale, and commanded states must be distinct. HEX has no documented
remote-mode readback in this subset. The richer ASCII STAT1A/FLTS1A mapping is
not the HEX mapping and is out of scope.

## Excluded manual features

M5 additionally lists flowrate `0x0A`, cooling/heating percentage `0x1E` with
direction 0, and alarm-clear/restart `0x1E` with direction 1 (Tables 3 and 9).
These are documented for context but not required application functionality.
In particular, **FE clears alarms and restarts**; it is not a recovery probe.
Do not implement or transmit it as automatic reconnect/error handling. No ASCII,
RS-485, Ethernet, alarm-width, RTD-offset, or tuning functionality is planned.

## Transaction scheduling and recovery design

One controller per physical port permits one outstanding transaction. Its callers
share a lock and monotonic limiter. There is no global registry or ownership-token
layer; the application owns the controller and uses OS serial exclusivity.
Implemented minimum interval between command starts is **350 ms**,
which exceeds 1/3 s and avoids treating rounded 333 ms as an exact safe bound.
The implementation waits this interval after write completion as well, and after
a physical open before its first write. No accumulated credits, catch-up bursts,
or early limiter reset on reconnect. Enforce
the same limit for stop and error paths. One logical request is at most 3 bytes
before the unresolved terminator; never pipeline to use the 8-byte allowance.

For three regular queries (faults, temperature, setpoint), a nominal schedule at 0, 0.35, 0.70, 1.05, ... seconds
gives each signal approximately one update per 1.05 s, rather than three fields
at exactly 1 Hz. Transactions that take longer move the schedule later. GUI
refresh cadence is independent. Manual control or verification consumes slots and delays/skips polls. A fault
or state mismatch ends a status observation after its first query. Both setpoint writes
and start have a fault preflight; setpoint write/readback therefore costs three
command slots. Monitor CSV failure stops its polling; optional core logging does
not control the caller's experiment. Status timestamps precede the first query
and conservatively age all values; fields are never merged from older samples.

Use bounded read and write deadlines. Accumulate partial reads until the expected
data length or deadline; do not wait for an echo. An expired/partial/ambiguous
transaction invalidates stream alignment and measurement freshness. Late bytes
must not satisfy the next request. Check the deadline after a read/write returns as well as before waiting.
Stop polling and disarm; reopen or resume only
under the validated recovery policy. Draining the current buffer alone cannot
prove that a later response will not arrive. Do not retry a write with uncertain
delivery. No RS-232 maximum response time is specified here; the 200 ms figure
on p. 26 is in the RS-485 section and is not adopted as an R2 guarantee.

## Uncertainty register and gates

| ID | Evidence / unresolved question | Consequence and closure |
| --- | --- | --- |
| U01 | M5 p. 20 identifies T22-T26; requested part uses `400`. `-AR` and `-267` are not defined in the supplied option list | Confirm nameplate, controller generation, firmware, and variant documentation. Gate hardware command enable and the selected fault/status profile. Do not assume auto-restart semantics from the suffix |
| U02 | p. 23 note 6 instructs CRLF on Windows and LF on Linux/Apple in the HEX section; Tables 4/9 show binary-only sequences | Obtain manufacturer clarification or a known-good captured exchange for this firmware before selecting hardware framing. Raw binary serial transport does not inherently depend on host OS. No automatic framing probes or platform-based terminator choice |
| U03 | User's 8-byte storage statement absent from M5 | Retain the conservative cap and single outstanding request; clarify provenance/capacity before hardware validation, without testing overflow |
| U04 | HEX signedness, malformed/out-of-range replies, ACKs, latency, and resynchronization are incompletely specified | Implement only established encodings; choose bounded provisional host timeouts and validate responses/recovery under approved tests |
| U05 | Remote/run controls are active, but detailed fault interaction, standby queries, and control application timing are not established for the target | Establish physical state explicitly, use one reviewed query at a time initially, and compare reported state with the panel and behavior |
| U06 | DCE/DTE wording conflicts on p. 21; option-specific signals can share DB9 | Verify actual signal routing and RS-232 voltage compatibility before any connection; do not attach unknown handshake pins |
| U07 | M5 pp. 6, 20, 33 describe LT limits, but target configuration, coolant, and attached load are unknown | Approved control range must be the intersection of confirmed device, fluid, and load limits; no range is enabled by default |

U02 is a safety issue, not cosmetic formatting: an unanticipated LF byte `0A`
decodes by Table 2 as local/standby, direction 0, parameter 0x0A. CR `0D` also
clears the control bits and selects an unlisted parameter. If interpreted as new
commands they could alter state. Conversely, omitting required framing could
prevent communication. Neither interpretation may be silently selected for
hardware. Pure encoding tests can proceed against logical bytes after review;
physical framing remains gated.
