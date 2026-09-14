# Source provenance

## Authoritative supplied manual

- File: [Thermocube-II-rev-M5.pdf](Thermocube-II-rev-M5.pdf).
- Supplied by the user in session prompt 3 on 2026-09-14.
- Archived unchanged in this directory for durable reference.
- Size: 2,620,360 bytes; 48 PDF pages.
- SHA-256: `e7a4508828bd439a76958e20a6e660393470b5a07fd95f510c69a0700fbb529d`.
- Document: ThermoCube II 200-650W Thermoelectric Chiller Manual, 52-14790-1,
  version M5.
- [Manufacturer-hosted M5 reference](https://www.sscooling.com/wp-content/uploads/2025/07/Thermocube-II-rev-M5.pdf)
  was also located. Extraction used the actual supplied local file; online bytes
  were not substituted or asserted to have the same digest.

Printed page N is PDF viewer page N+1 (the cover is PDF page 1). Key source map:

| Printed pages | Material used |
| --- | --- |
| 4, 6, 13 | Coolant margin, operating range, and dry-running precautions |
| 15 | Standby can retain pump operation; remote control locks keypad |
| 20 | ThermoCube II part-number options, R2 default HEX, LT limits |
| 21 | RS-232 settings, wiring, polling, data refresh |
| 22 | Command bit table, parameter selectors, byte lengths, temperature and fault tables |
| 23 | Always-active command warning, even command spacing, no echo, newline ambiguity, examples |
| 24-26 | Separate ASCII/status and RS-485 sections, used to avoid importing unrelated semantics |
| 33 | LT fluid/temperature limitations |

Inspection used bundled Python 3.12.14 with pypdf for extraction and Poppler
pdftoppm for visual verification. Printed pp. 20-23 were rendered at 120 dpi and
inspected, plus pp. 15 and 33 at 110 dpi. pypdf reported duplicate PDF dictionary
keys; the relevant tables and safety statements were checked against rendered
pages rather than relying only on extraction. Temporary text/renders are ignored under tmp/ and can be regenerated from this
source. The critical review re-extracted printed pp. 21–23 and visually rechecked
pp. 22–23, including the active-bit and newline notes.

## Organizational reference

The [agentic-engineering-template](https://github.com/cct1123/agentic-engineering-template)
README, [AGENTS](https://github.com/cct1123/agentic-engineering-template/blob/main/AGENTS.md),
and [architecture](https://github.com/cct1123/agentic-engineering-template/blob/main/ARCHITECTURE.md)
were inspected on 2026-09-14 using the rendered GitHub main-branch pages. No exact
upstream commit was resolved or vendored. The initial workspace adapted that structure. The critical review removed its
redundant project/plan/checkpoint/ledger files; only concise contributor guidance,
source provenance and reproducible test/candidate evidence remain.

No upstream template Git history was imported.

## Other references and exclusions

- [pyserial API](https://pyserial.readthedocs.io/en/latest/pyserial_api.html): explicit
  open behavior, partial reads, finite write timeout, and possible RTS/DTR changes
  on opening informed the transport design.
- [Dash development tools](https://dash.plotly.com/devtools): the reloader runs
  application initialization twice, motivating one explicit worker and disabled
  reload for hardware sessions.
- A manufacturer ThermoCube 200-500 M33 manual was located before the supplied
  file arrived. It is not the authoritative source for this specification and no
  older fault mapping was adopted at initialization. The follow-up user prompt
  explicitly requires the legacy mapping; implementation now includes it as a
  separate named contract alongside the authoritative M5 profile. This does not
  establish target compatibility. ThermoCube II M3 search results likewise did
  not replace the supplied M5 document.

Manual procedures and imported reference text do not grant permission to execute
hardware operations. The user's active instructions govern the work.
