"""Local launcher. Simulation is default; hardware requires an explicit reviewed file."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from thermocube import __version__
from thermocube.acquisition import AcquisitionService
from thermocube.driver import ThermoCube
from thermocube.logging import CsvLogger
from thermocube.models import Device, FaultProfile, Mode, TemperatureLimits
from thermocube.simulator import Simulator
from thermocube.transport import HardwareApproval, SerialConfig, SerialTransport


def hardware_from_config(config: dict[str, Any]) -> ThermoCube:
    allowed = {"serial", "profile", "limits_c", "approval", "mode"}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError("Unknown hardware configuration keys")
    if not {"serial", "profile"} <= config.keys():
        raise ValueError("Hardware configuration requires serial and profile")
    profile = FaultProfile(config["profile"])
    bounds = config.get("limits_c")
    if bounds is not None and (not isinstance(bounds, list) or len(bounds) != 2):
        raise ValueError("limits_c must be [minimum, maximum] or null")
    limits = TemperatureLimits(*bounds) if bounds is not None else None
    approval_data = config.get("approval")
    approval = None
    if approval_data is not None:
        approval = HardwareApproval(profile=profile, setpoint_limits=limits, **approval_data)
    return ThermoCube(
        SerialTransport(SerialConfig(**config["serial"]), approval=approval),
        profile=profile,
        limits=limits,
        mode=Mode(config.get("mode", Mode.OBSERVE.value)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ThermoCube R2 controller; defaults to simulation")
    parser.add_argument("--config", type=Path, help="Explicit reviewed hardware configuration JSON")
    parser.add_argument(
        "--headless", action="store_true", help="Acquire without the GUI for --duration seconds"
    )
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--port", type=int, default=8050, help="Local web port, not a serial port")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0 or not 1 <= args.port <= 65535:
        parser.error("Duration must be finite/positive and web port in 1..65535")
    config = (
        json.loads(args.config.read_text(encoding="utf-8"))
        if args.config
        else {"backend": "simulation"}
    )
    device: Device = hardware_from_config(config) if args.config else Simulator()
    session_id = uuid4().hex
    logger = CsvLogger(
        args.log_dir,
        session_id,
        metadata={
            "source": device.cached_snapshot.source,
            "application_version": __version__,
            "config": config,
            "acquisition_interval_s": 1.05,
        },
    )
    service = AcquisitionService(device, logger=logger)
    service.start(connect=args.headless)
    try:
        if args.headless:
            print(f"Acquiring {device.cached_snapshot.source}; session {session_id}", flush=True)
            time.sleep(args.duration)
            print(
                f"Samples: {len(service.history)}; quality: {service.latest.quality}; error: {service.error}"
            )
            if service.error or service.latest.quality != "good":
                raise RuntimeError(
                    service.error or service.latest.error or "No fresh acquisition data"
                )
        else:
            from waitress import serve

            from app.dash_app import create_app

            app = create_app(service)
            print(
                f"ThermoCube ({device.cached_snapshot.source}): http://127.0.0.1:{args.port}",
                flush=True,
            )
            serve(app.server, host="127.0.0.1", port=args.port, threads=4)
    except KeyboardInterrupt:
        pass
    finally:
        service.close()


if __name__ == "__main__":
    main()
