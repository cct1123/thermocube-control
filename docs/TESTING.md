# Hardware-free validation

Candidate 0.2.0: **81 tests pass**, with **95.96% statement coverage** (642/669).
Ruff, mypy, dependency and build checks pass. The isolated wheel smoke checks
and all 81 tests from the extracted source archive also pass.

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
- Interrupted open/read/write/close, cancellation during failed-open cleanup,
  and interrupted RUN delivery without replay or implicit STOP.
- Simulator heat/cool/standby behavior and retained state across link loss.
- Legacy simulation preserves requested state without inventing reported run state.
- Optional monitor lifecycle, bounded history, CSV gaps/failures and no hidden reconnect.
- Failed worker construction/startup closes CSV and leaves stop() safe.
- Dash HTTP layout/callbacks/assets, confirmation, stale/future data and backend locks.
- Core and headless monitoring without GUI dependencies; CLI success/failure shutdown.

The new suite groups exhaustive codec checks into meaningful scenarios instead
of reporting hundreds of parameterized bit cases as separate engineering tests.
Old tests of removed registries, factories, queue IDs and policy objects were deleted.

## Repeat

~~~powershell
uv sync --locked --extra gui
uv lock --check
uv run --locked ruff check src tests tools examples
uv run --locked ruff format --check src tests tools examples
uv run --locked mypy
uv run --locked --extra gui pytest -q --cov=thermocube --cov-fail-under=90 --cov-report=term-missing --cov-report=json:records/coverage.json --junitxml=records/tests.xml
uv pip check
uv build
uv run --locked --extra gui python tools/verify_artifacts.py
~~~

Keep only the candidate wheel in dist/. The artifact checker installs it offline
in its own uv environment, tests its metadata/imports/assets/headless CLI, then
syncs the extracted source archive into another environment and runs the complete
suite and example. The lockfile supplies dependencies for both environments.
No source-path injection or inherited site-packages can mask packaging errors.
Temporary environments are removed when the check finishes.

Results live in [REVIEW.md](REVIEW.md), [static checks](../records/software-checks.json)
and the [source manifest](../records/candidate-manifest.json). Raw test/build output
is generated under records/ and excluded from Git, as are dist/, caches and logs.
`uv.lock` replaces the requirements constraints file and includes distribution
hashes. `--locked` rejects dependency changes until the lockfile is regenerated.

Local checks use Windows/Python 3.12.14. The CI matrix declares Windows/Ubuntu and
Python 3.11/3.12; only actually observed runs should be claimed. None of these
tests verifies actual framing, signed HEX, fault-profile applicability, wiring,
OS/adapter timing bounds or physical stop behavior. All physical stages remain gated.
