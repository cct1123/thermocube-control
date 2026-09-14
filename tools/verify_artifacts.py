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
import app.cli
assert Path(thermocube.__file__).is_relative_to(sys.argv[1])
assert Path(app.cli.__file__).is_relative_to(sys.argv[1])
from app.dash_app import create_app
from thermocube.acquisition import AcquisitionService
from thermocube.simulator import Simulator
service = AcquisitionService(Simulator())
web = create_app(service).server.test_client()
assert web.get('/').status_code == 200
assert web.get('/_dash-layout').status_code == 200
assert web.get('/assets/style.css').status_code == 200
assert not service.is_running
sys.argv = ['thermocube', '--headless', '--duration', '0.1', '--log-dir', sys.argv[2]]
app.cli.main()
print('PASS: installed-wheel imports, CSS, Dash endpoints, headless CSV and shutdown')
"""

SOURCE_CHECK = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import thermocube, pytest
assert Path(thermocube.__file__).is_relative_to(sys.argv[1])
raise SystemExit(pytest.main(['-q', 'tests']))
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
                str(temporary / "logs"),
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
