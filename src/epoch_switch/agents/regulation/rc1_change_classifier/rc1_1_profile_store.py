"""Registry-keyed RC1.1 active and archive records.

Verbatim copy of cp1_profile_store.py with agent_id='RC1.1' and artifact
prefix 'rc1-1-'. RC1.1 shares this package with RC1.2 but keeps its own shelf
directory (`rc1_1_registry_profiles/`) -- see AGENTS.md's blind-pass agent
folder/shelf exception. Access only via this module. The CLI is the only
layer allowed to import multiple agent store modules in a single run
(AGENTS.md: Agent-local shelves rule).
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE_DIR = Path(__file__).parent / "rc1_1_registry_profiles"


def load_active(registry_id: str) -> dict[str, Any] | None:
    return _load(PROFILE_DIR / registry_id / "active.json")


def promote_active(
    registry_id: str,
    payload: dict[str, Any],
    *,
    run_id: str,
    review_round: int,
    input_refs: dict[str, Any],
    archive_existing: bool = True,
    llm_provenance: dict | None = None,
) -> Path:
    if archive_existing:
        snapshot_active(registry_id, reason="superseded", run_id=run_id)
    return _write(
        PROFILE_DIR / registry_id / "active.json",
        _record(registry_id, payload, "active", "RC1.1 blind match accepted", run_id, review_round, input_refs, llm_provenance=llm_provenance),
    )


def archive_candidate(
    registry_id: str,
    payload: dict[str, Any],
    *,
    status: str,
    reason: str,
    run_id: str,
    review_round: int | None,
    input_refs: dict[str, Any],
    llm_provenance: dict | None = None,
) -> Path:
    return _write_archive(
        registry_id,
        _record(registry_id, payload, status, reason, run_id, review_round, input_refs, llm_provenance=llm_provenance),
    )


def snapshot_active(registry_id: str, *, reason: str, run_id: str) -> Path | None:
    active = load_active(registry_id)
    if not active:
        return None
    snapshot = dict(active)
    snapshot.update(status=reason, reason=reason.replace("_", " "), archived_at=_now(), archive_run_id=run_id)
    return _write_archive(registry_id, snapshot)


def retire_active(registry_id: str, *, reason: str, run_id: str) -> Path | None:
    path = PROFILE_DIR / registry_id / "active.json"
    active = load_active(registry_id)
    if not active:
        return None
    active.update(status="retired_by_user", reason=reason, archived_at=_now(), archive_run_id=run_id)
    target = _write_archive(registry_id, active)
    path.unlink()
    return target


def list_archive(registry_id: str) -> list[dict[str, Any]]:
    directory = PROFILE_DIR / registry_id / "archive"
    return [_load(path) for path in sorted(directory.glob("*.json"))] if directory.exists() else []


def _record(registry_id, payload, status, reason, run_id, review_round, input_refs, llm_provenance=None):
    created_at = _now()
    return {
        "schema_version": "1",
        "artifact_id": f"rc1-1-{uuid.uuid4().hex[:12]}",
        "agent_id": "RC1.1",
        "registry_id": registry_id,
        "status": status,
        "reason": reason,
        "created_at": created_at,
        "approved_at": created_at if status == "active" else None,
        "run_id": run_id,
        "review_round": review_round,
        "input_refs": input_refs,
        "payload_sha256": _hash(payload),
        "llm_provenance": llm_provenance,
        "payload": payload,
    }


def _write_archive(registry_id, record):
    directory = PROFILE_DIR / registry_id / "archive"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return _write(directory / f"{timestamp}_{record['artifact_id']}.json", record)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _load(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()
