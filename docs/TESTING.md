# Hardware-free validation

Candidate 0.2.0: **65 tests pass**, with **94.97% statement coverage** (623/656).
Ruff, mypy, dependency and build checks pass. The isolated wheel smoke checks
and all 65 tests from the extracted source archive also pass.

Tests exercise public operations against an independent literal-byte serial peer.
The real controller's pyserial construction, opening, pacing, read/write and error
handling execute; no production transport factory or subclass bypass is used.
Real serial opening and port enumeration are blocked before test collection.

Coverage includes:

- Literal manual/derived command vectors through public operations in RUN and STANDBY.
- All 65,536 temperature words in Celsius and Fahrenheit; quantization and bounds.
- Every fault byte in both profiles, preserving unknown/reserved bits.
- Explicit connection/query/control gates; complete compound operation ordering.
- Partial, short, extra, malformed and late data; timeouts, uncertain writes,
  rejected setpoints, no replay, recovery and rearming after reconnect.
- Concurrent callers, STOP followed by disconnect, monotonic pacing with both
  a deterministic clock and real elapsed time over fake serial.
- Simulator heat/cool/standby behavior and retained state across link loss.
- Optional monitor lifecycle, bounded history, CSV gaps/failures and no hidden reconnect.
- Dash HTTP layout/callbacks/assets, confirmation, stale/future data and backend locks.
- Core and headless monitoring without GUI dependencies; CLI success/failure shutdown.

The new suite groups exhaustive codec checks into meaningful scenarios instead
of reporting hundreds of parameterized bit cases as separate engineering tests.
Old tests of removed registries, factories, queue IDs and policy objects were deleted.

## Repeat

~~~powershell
.\.venv\Scripts\python -m pip install -c requirements-lock.txt -e '.[gui,dev]'
.\.venv\Scripts\python -m ruff check thermocube tests tools examples
.\.venv\Scripts\python -m ruff format --check thermocube tests tools examples
.\.venv\Scripts\python -m mypy thermocube
.\.venv\Scripts\python -m pytest -q --cov=thermocube --cov-fail-under=90 --cov-report=term-missing --cov-report=json:records/coverage.json --junitxml=records/tests.xml
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m build --no-isolation
.\.venv\Scripts\python tools/verify_artifacts.py
~~~

Keep only the candidate wheel in dist/. The artifact checker installs it offline
in a fresh temporary directory, tests its imports/assets/headless CLI, then runs
the complete suite from the extracted source archive. Temporary copies are removed.

Results live in [REVIEW.md](REVIEW.md), [static checks](../records/software-checks.json)
and the [source manifest](../records/candidate-manifest.json). Raw test/build output
is generated under records/ and excluded from Git, as are dist/, caches and logs.
The constraints file records tested versions; it is not a hash-locked download list.

Local checks use Windows/Python 3.12.14. The CI matrix declares Windows/Ubuntu and
Python 3.11/3.12; only actually observed runs should be claimed. None of these
tests verifies actual framing, signed HEX, fault-profile applicability, wiring,
OS/adapter timing bounds or physical stop behavior. All physical stages remain gated.
