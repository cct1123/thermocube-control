"""Verify the built wheel and source archive in fresh temporary directories."""

import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]

SERIAL_GUARD = """
import serial
from serial.tools import list_ports

def forbidden(*args, **kwargs):
    raise AssertionError('Physical serial access forbidden')

serial.Serial.open = forbidden
list_ports.comports = forbidden
"""

WHEEL_CHECK = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import thermocube
from importlib.metadata import distribution
from thermocube.gui import main
assert Path(thermocube.__file__).is_relative_to(sys.argv[1])
assert {p.name for p in (Path(sys.argv[1]) / 'thermocube').glob('*.py')} == {'__init__.py', 'controller.py', 'simulator.py', 'gui.py'}
assert not (Path(sys.argv[1]) / 'app').exists()
entry = next(e for e in distribution('thermocube-control').entry_points if e.name == 'thermocube')
assert entry.value == 'thermocube.gui:main'
from thermocube.gui import Monitor, create_app
from thermocube.simulator import Simulator
monitor = Monitor(Simulator())
web = create_app(monitor).server.test_client()
assert web.get('/').status_code == 200
assert web.get('/_dash-layout').status_code == 200
assert web.get('/assets/style.css').status_code == 200
assert not monitor.is_running
sys.argv = ['thermocube', '--headless', '--duration', '0.1', '--csv', sys.argv[2]]
main()
print('PASS: installed-wheel imports, CSS, Dash endpoints, headless CSV and shutdown')
"""

SOURCE_CHECK = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import thermocube, pytest
assert Path(thermocube.__file__).is_relative_to(sys.argv[1])
result = pytest.main(['-q', 'tests'])
if result == 0:
    import runpy
    runpy.run_path('examples/experiment.py', run_name='__main__')
raise SystemExit(result)
"""


def main() -> None:
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Keep exactly one candidate wheel in dist; build before checking")
    wheel = wheels[0]
    release = "-".join(wheel.name.split("-")[:2])
    with TemporaryDirectory(prefix="thermocube-artifacts-") as directory:
        temporary = Path(directory)
        installed = temporary / "installed"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "--isolated",
                "install",
                "--no-index",
                "--no-deps",
                "--target",
                str(installed),
                str(wheel),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                SERIAL_GUARD + WHEEL_CHECK,
                str(installed),
                str(temporary / "simulation.csv"),
            ],
            check=True,
            cwd=temporary,
        )
        with tarfile.open(ROOT / "dist" / f"{release}.tar.gz") as archive:
            archive.extractall(temporary / "source", filter="data")
        source = temporary / "source" / release
        assert (source / "tests/conftest.py").is_file()
        assert (source / "requirements-lock.txt").is_file()
        # Explicitly load the archive, never the editable checkout.
        subprocess.run(
            [sys.executable, "-I", "-c", SERIAL_GUARD + SOURCE_CHECK, str(source)],
            check=True,
            cwd=source,
        )
        print("PASS: source archive fixtures, constraints and complete offline suite")


if __name__ == "__main__":
    main()
