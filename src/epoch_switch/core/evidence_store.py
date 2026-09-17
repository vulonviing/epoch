"""Shared blackboard — agents write structured results; others read by key."""
from __future__ import annotations
import json
import threading
from pathlib import Path
from typing import Any


class EvidenceStore:
    """Thread-safe in-memory store backed by a JSON file per run."""

    def __init__(self, run_dir: Path):
        self._store: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._path = run_dir / "evidence_store.json"

    def write(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = value
            self._persist()

    def read(self, key: str) -> Any | None:
        with self._lock:
            return self._store.get(key)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._store.keys())

    def all(self) -> dict:
        with self._lock:
            return dict(self._store)

    def _persist(self) -> None:
        self._path.write_text(json.dumps(self._store, indent=2, default=str))
