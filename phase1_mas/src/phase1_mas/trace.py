"""Append-only JSONL tracing for every observable workflow action.

JSONL is used instead of a framework-specific tracing format so each line can
be inspected with standard tools and later aggregated into Phase 1 metrics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import to_jsonable, utc_now_iso


class TraceRecorder:
    """Keep an in-memory trace and optionally mirror it to an append-only file."""

    def __init__(self, run_id: str, output_path: Path | None = None) -> None:
        self.run_id = run_id
        self.output_path = output_path
        self.events: list[dict[str, Any]] = []
        self._sequence = 0
        self._file = None

        # Opening once avoids partially interleaved JSON objects and lets a
        # long-running Phase 2 experiment stream events safely to disk.
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = output_path.open("w", encoding="utf-8")

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        """Record one event and return the JSON-safe event dictionary."""

        self._sequence += 1
        event = {
            "event_id": f"evt_{self._sequence:04d}",
            "run_id": self.run_id,
            "timestamp": utc_now_iso(),
            "event_type": event_type,
            **to_jsonable(payload),
        }
        self.events.append(event)
        if self._file is not None:
            self._file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            self._file.flush()
        return event

    def close(self) -> None:
        """Close the JSONL file; calling it twice is harmless."""

        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "TraceRecorder":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a completed trace file for assertions or later metric analysis."""

    with path.open(encoding="utf-8") as trace_file:
        return [json.loads(line) for line in trace_file if line.strip()]
