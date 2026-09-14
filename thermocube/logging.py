"""Versioned UTF-8 CSV samples/events with explicit timestamps and missing values."""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from thermocube.models import Event, Snapshot

SAMPLE_FIELDS = (
    "schema_version",
    "session_id",
    "sequence",
    "timestamp_utc",
    "source",
    "connected",
    "responding",
    "mode",
    "armed",
    "run_intent",
    "run_reported",
    "temperature_c",
    "setpoint_c",
    "temperature_raw_tenths_f",
    "setpoint_raw_tenths_f",
    "temperature_at_utc",
    "setpoint_at_utc",
    "faults_at_utc",
    "fault_profile",
    "faults_raw_hex",
    "active_faults",
    "unknown_mask_hex",
    "thermal_direction",
    "quality",
    "error",
)
EVENT_FIELDS = (
    "schema_version",
    "session_id",
    "timestamp_utc",
    "source",
    "operation",
    "outcome",
    "detail",
    "tx_hex",
    "rx_hex",
    "tx_started_at_utc",
    "tx_monotonic_s",
)


def _stamp(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()


class CsvLogger:
    """Single-session files, exclusive creation, flushing each record (about 1 Hz)."""

    def __init__(
        self, directory: Path | str, session_id: str, *, metadata: dict[str, Any] | None = None
    ) -> None:
        if metadata and {"schema_version", "session_id"} & metadata.keys():
            raise ValueError("Session metadata cannot replace schema_version or session_id")
        if not session_id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in session_id
        ):
            raise ValueError("Session ID must contain only letters, digits, '-' or '_'")
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id
        self._lock = threading.RLock()
        self._sequence = 0
        self._samples = (folder / f"{session_id}-samples.csv").open(
            "x", newline="", encoding="utf-8"
        )
        try:
            self._events = (folder / f"{session_id}-events.csv").open(
                "x", newline="", encoding="utf-8"
            )
            self._sample_writer = csv.DictWriter(self._samples, fieldnames=SAMPLE_FIELDS)
            self._event_writer = csv.DictWriter(self._events, fieldnames=EVENT_FIELDS)
            self._sample_writer.writeheader()
            self._event_writer.writeheader()
            self._samples.flush()
            self._events.flush()
            with (folder / f"{session_id}-session.json").open("x", encoding="utf-8") as handle:
                json.dump(
                    {"schema_version": 1, "session_id": session_id, **(metadata or {})},
                    handle,
                    indent=2,
                )
        except Exception:
            self._samples.close()
            if hasattr(self, "_events"):
                self._events.close()
            raise

    def sample(self, value: Snapshot) -> None:
        with self._lock:
            self._sequence += 1
            faults = value.faults
            row = dict(
                zip(
                    SAMPLE_FIELDS,
                    (
                        1,
                        self.session_id,
                        self._sequence,
                        _stamp(value.timestamp),
                        value.source,
                        value.connected,
                        value.responding,
                        value.mode.value,
                        value.armed,
                        value.run_intent,
                        value.run_reported,
                        value.temperature_c,
                        value.setpoint_c,
                        value.temperature_raw,
                        value.setpoint_raw,
                        _stamp(value.temperature_at),
                        _stamp(value.setpoint_at),
                        _stamp(value.faults_at),
                        faults.profile.value if faults else "",
                        f"{faults.raw:02X}" if faults else "",
                        "|".join(faults.active) if faults else "",
                        f"{faults.unknown_mask:02X}" if faults else "",
                        value.thermal_direction,
                        value.quality,
                        value.error,
                    ),
                    strict=True,
                )
            )
            self._sample_writer.writerow(row)
            self._samples.flush()

    def event(self, event: Event, source: str) -> None:
        with self._lock:
            row = asdict(event)
            row["timestamp_utc"] = _stamp(row.pop("timestamp"))
            row["tx_started_at_utc"] = _stamp(row.pop("tx_started_at"))
            row.update(schema_version=1, session_id=self.session_id, source=source)
            self._event_writer.writerow(row)
            self._events.flush()

    def close(self) -> None:
        with self._lock:
            try:
                self._samples.close()
            finally:
                self._events.close()

    def __enter__(self) -> CsvLogger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
