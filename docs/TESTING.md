# Hardware-free validation

Candidate **0.1.1**: **640 tests pass** on Windows / Python 3.12.14. Statement
coverage is 93% (1,198/1,284 statements); Dash presentation/callback code has 100%.
Counts include parameterized bit/fault combinations and are not 640 independent
hardware scenarios. Coverage is not evidence of electrical or device safety.

The review added 22 cases. Eleven initial cases failed against 0.1.0 and passed
after correction; subsequent cases cover import isolation, immutable approval,
transport replacement, shutdown admission, configuration and timeout consistency.
The original 618-test baseline passed while those failure scenarios were missing.

## What is tested

| Area | Evidence |
| --- | --- |
| Protocol | All 256 command-bit combinations; literal manual vectors and standby derivations; every unsigned word roundtrips through C/F; byte order, rounding, units and invalid inputs; all fault bytes in both explicit profiles |
| Production driver/transport | Independent fake serial bytes, exact response lengths, partial/extra/late replies, timeout including late completion, uncertainty/no replay, fault preflights, explicit locks and gates |
| Pacing/concurrency | Shared 350 ms limiter, mixed traffic/rolling-window count, delayed/failed writes, stop/queries, reconnect and replacement ownership/quarantine |
| Service | Headless worker, bounded history/queues/results, reconnect without hardware rearm, stop preserved before disconnect, closing rejects new work, no worker leak |
| Logging | UTF-8/CSV quoting, UTC and per-field times, raw/profile values, requested/uncertain/cancelled outcomes, identity protection, disk-failure polling inhibition and permitted shutdown |
| Simulator | Heating/cooling/standby evolution, independent clock, connection persistence, faults/delays/timeouts; same public API as the driver, without claiming hardware fidelity |
| GUI/CLI | Real Dash/Flask layout/assets/HTTP callbacks, confirmation/cancellation, stale/future per-field state, faults/unknowns, failed headless exit, shutdown |
| Packaging | Wheel/source build, isolated installed-wheel imports/assets/headless run, complete suite from extracted source archive |

Normal pytest guards block OS serial opening and enumeration **before test
collection imports**, then reinforce this per test. A fresh subprocess also
checks package/app imports with those functions blocked. Mocked physical-policy
tests replace the opening operation only; no actual COM port is inspected.
The API simulator does not replace independent fake-byte tests of the real driver.

## Repeat locally

Install from the root README, then:

~~~powershell
.\.venv\Scripts\python -m ruff check thermocube app tests tools
.\.venv\Scripts\python -m ruff format --check thermocube app tests tools
.\.venv\Scripts\python -m mypy thermocube app
.\.venv\Scripts\python -m pytest -q --cov=thermocube --cov=app --cov-report=term-missing --cov-report=json:records/coverage.json --junitxml=records/tests.xml
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m build --no-isolation
.\.venv\Scripts\python tools/verify_artifacts.py
~~~

Keep only the current candidate wheel in dist. The artifact checker installs it
offline into a fresh temporary directory and tests the extracted source archive.
It verifies import origins and removes temporary files when finished; no manual
installation or cleanup is needed.

Versioned evidence: [review](REVIEW.md), [static checks](../records/software-checks.json)
and [source manifest](../records/candidate-manifest.json). Raw JUnit, coverage and
build output are generated locally under records/ and excluded from Git. Build
artifacts remain under dist/ and are also excluded. The manifest covers source
and concise evidence, not generated archives or this manifest itself.

The constraints file records tested dependencies; it is not a hash-locked download
manifest. CI declares Windows/Ubuntu and Python 3.11/3.12 but has not run remotely
at the time of the recorded local review. Interactive-browser evidence is from 0.1.0; 0.1.1 GUI
changes are verified with fresh HTTP callback and presentation tests.

## Unvalidated physically

Stage 0–7 tests in [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) have not run.
No fixture establishes framing, signedness, actual fault semantics, electrical
levels, USB delivery timing, minimum safe limits or physical stop behavior.
Plausible two-byte values can be corrupt: this protocol has no documented checksum
or transaction identity. OS/adapter blocking has no guaranteed hard upper bound.

Any change to framing, control bits, pacing, lifecycle, limits or the selected
profile invalidates dependent physical evidence until the applicable stages are
repeated under approval. No physical test is part of default collection.
