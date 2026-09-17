"""Registry-keyed R2 active and archive records."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROFILE_DIR = Path(__file__).parent / "registry_profiles"


def load_active(registry_id: str) -> dict[str, Any] | None:
    return _load(PROFILE_DIR / registry_id / "active.json")


def load_approved(registry_id: str) -> dict[str, Any] | None:
    record = load_active(registry_id)
    return record.get("payload") if record else None


def promote_active(
    registry_id: str,
    payload: dict[str, Any],
    *,
    run_id: str,
    review_round: int,
    input_refs: dict[str, Any],
    approved_at: str | None = None,
    archive_existing: bool = True,
    llm_provenance: dict | None = None,
) -> Path:
    if archive_existing:
        snapshot_active(registry_id, reason="superseded", run_id=run_id)
    return _write(
        PROFILE_DIR / registry_id / "active.json",
        _record(
            registry_id,
            payload,
            status="active",
            reason="R2 scope approved by human",
            run_id=run_id,
            review_round=review_round,
            input_refs=input_refs,
            approved_at=approved_at,
            llm_provenance=llm_provenance,
        ),
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
        _record(
            registry_id,
            payload,
            status=status,
            reason=reason,
            run_id=run_id,
            review_round=review_round,
            input_refs=input_refs,
            llm_provenance=llm_provenance,
        ),
    )


def save_attempt(
    registry_id: str,
    profile: dict[str, Any],
    *,
    review_status: str,
    reviewed_at: str | None = None,
    run_id: str = "legacy",
    review_round: int | None = None,
    input_refs: dict[str, Any] | None = None,
) -> Path:
    return archive_candidate(
        registry_id,
        profile,
        status=review_status,
        reason=f"R2 review ended as {review_status}",
        run_id=run_id,
        review_round=review_round,
        input_refs=input_refs or {},
    )


def save_validation_attempt(
    registry_id: str,
    *,
    raw_output: dict[str, Any],
    validation_error: str,
    repaired_output: dict[str, Any] | None,
    repair_error: str | None,
    review_status: str,
) -> Path:
    payload = {
        "raw_output": raw_output,
        "validation_error": validation_error,
        "repaired_output": repaired_output,
        "repair_error": repair_error,
    }
    return archive_candidate(
        registry_id,
        payload,
        status=review_status,
        reason=repair_error or validation_error,
        run_id="validation",
        review_round=None,
        input_refs={},
    )


def snapshot_active(registry_id: str, *, reason: str, run_id: str) -> Path | None:
    active = load_active(registry_id)
    if not active:
        return None
    snapshot = dict(active)
    snapshot["status"] = reason
    snapshot["reason"] = reason.replace("_", " ")
    snapshot["archived_at"] = _now()
    snapshot["archive_run_id"] = run_id
    return _write_archive(registry_id, snapshot)


def retire_active(registry_id: str, *, reason: str, run_id: str) -> Path | None:
    path = PROFILE_DIR / registry_id / "active.json"
    active = load_active(registry_id)
    if not active:
        return None
    retired = dict(active)
    retired["status"] = "retired_by_user"
    retired["reason"] = reason
    retired["archived_at"] = _now()
    retired["archive_run_id"] = run_id
    target = _write_archive(registry_id, retired)
    path.unlink()
    return target


def list_archive(registry_id: str) -> list[dict[str, Any]]:
    directory = PROFILE_DIR / registry_id / "archive"
    return [_load(path) for path in sorted(directory.glob("*.json"))] if directory.exists() else []


def _record(
    registry_id: str,
    payload: dict[str, Any],
    *,
    status: str,
    reason: str,
    run_id: str,
    review_round: int | None,
    input_refs: dict[str, Any],
    approved_at: str | None = None,
    llm_provenance: dict | None = None,
) -> dict[str, Any]:
    created_at = _now()
    return {
        "schema_version": "1",
        "artifact_id": f"r2-{uuid.uuid4().hex[:12]}",
        "agent_id": "R2",
        "registry_id": registry_id,
        "status": status,
        "reason": reason,
        "created_at": created_at,
        "approved_at": approved_at or (created_at if status == "active" else None),
        "run_id": run_id,
        "review_round": review_round,
        "input_refs": input_refs,
        "payload_sha256": _hash(payload),
        "llm_provenance": llm_provenance,
        "payload": payload,
    }


def _write_archive(registry_id: str, record: dict[str, Any]) -> Path:
    directory = PROFILE_DIR / registry_id / "archive"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return _write(directory / f"{timestamp}_{record['artifact_id']}.json", record)


def _write(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
