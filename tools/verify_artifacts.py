"""Verify the built wheel and source archive in fresh temporary directories."""

import os
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
import thermocube
from importlib.metadata import distribution
from thermocube.gui import main
assert Path(thermocube.__file__).is_relative_to(sys.argv[1])
assert {p.name for p in Path(thermocube.__file__).parent.glob('*.py')} == {'__init__.py', 'controller.py', 'simulator.py', 'gui.py'}
assert thermocube.__version__ == distribution('thermocube-control').version
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
        with tarfile.open(ROOT / "dist" / f"{release}.tar.gz") as archive:
            archive.extractall(temporary / "source", filter="data")
        source = temporary / "source" / release
        assert (source / "tests/conftest.py").is_file()
        assert (source / "uv.lock").is_file()
        assert (source / "src/thermocube/__init__.py").is_file()
        assert not (source / "thermocube").exists()
        uv = os.environ.get("UV", "uv")
        for label, project, code in (
            ("wheel", ROOT, WHEEL_CHECK),
            ("source", source, SOURCE_CHECK),
        ):
            environment = temporary / f"{label}-env"
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            subprocess.run(
                [
                    uv,
                    "sync",
                    "--quiet",
                    "--locked",
                    "--extra",
                    "gui",
                    "--python",
                    sys.executable,
                    "--project",
                    str(project),
                    *(["--no-install-project"] if label == "wheel" else []),
                ],
                env={
                    **os.environ,
                    "VIRTUAL_ENV": str(environment),
                    "UV_PROJECT_ENVIRONMENT": str(environment),
                },
                check=True,
            )
            if label == "wheel":
                subprocess.run(
                    [
                        uv,
                        "pip",
                        "install",
                        "--quiet",
                        "--python",
                        str(python),
                        "--no-index",
                        "--no-deps",
                        str(wheel),
                    ],
                    check=True,
                )
            # Real installations in independent environments: subprocesses cannot import the checkout.
            subprocess.run(
                [
                    str(python),
                    "-I",
                    "-c",
                    SERIAL_GUARD + code,
                    str(environment if label == "wheel" else source / "src"),
                    str(temporary / "simulation.csv"),
                ],
                check=True,
                cwd=temporary if label == "wheel" else source,
            )
        print("PASS: source archive, locked installation, example and complete offline suite")


if __name__ == "__main__":
    main()
