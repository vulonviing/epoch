from __future__ import annotations

import json

from epoch_switch.agents.data.da1_request_planner import da1_profile_store
from epoch_switch.agents.orchestration.cp1_case_profiler import cp1_profile_store
from epoch_switch.agents.regulation.r1_active_reader import r1_profile_store
from epoch_switch.agents.regulation.r2_scope_reviewer import r2_profile_store


def ref(record):
    return {
        "artifact_id": record["artifact_id"],
        "payload_sha256": record["payload_sha256"],
    }


def test_r1_and_da1_keep_active_and_tagged_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(r1_profile_store, "PROFILE_DIR", tmp_path / "r1")
    monkeypatch.setattr(da1_profile_store, "PROFILE_DIR", tmp_path / "da1")

    r1_profile_store.promote_active(
        "registry", {"version": 1}, run_id="run-1", review_round=1, input_refs={}
    )
    r1_profile_store.promote_active(
        "registry", {"version": 2}, run_id="run-2", review_round=1, input_refs={}
    )
    r1 = r1_profile_store.load_active("registry")
    da1_profile_store.promote_active(
        "registry",
        {"approved_mapping_scope": {"approved": [1]}},
        run_id="run-2",
        review_round=1,
        input_refs={"r1": ref(r1)},
    )

    assert r1["payload"] == {"version": 2}
    assert da1_profile_store.load_approved_scope("registry") == {"approved": [1]}
    archived = r1_profile_store.list_archive("registry")
    assert archived[0]["status"] == "superseded"


def test_refresh_snapshot_does_not_remove_active(tmp_path, monkeypatch):
    monkeypatch.setattr(r2_profile_store, "PROFILE_DIR", tmp_path / "r2")
    r2_profile_store.promote_active(
        "registry", {"version": 1}, run_id="run-1", review_round=1, input_refs={}
    )

    r2_profile_store.snapshot_active(
        "registry", reason="refresh_snapshot", run_id="refresh-1"
    )

    assert r2_profile_store.load_active("registry")["payload"] == {"version": 1}
    assert r2_profile_store.list_archive("registry")[0]["status"] == "refresh_snapshot"


def test_r2_keeps_rejected_and_validation_records(tmp_path, monkeypatch):
    monkeypatch.setattr(r2_profile_store, "PROFILE_DIR", tmp_path / "r2")
    r2_profile_store.save_attempt(
        "registry", {"version": 1}, review_status="rejected"
    )
    path = r2_profile_store.save_validation_attempt(
        "registry",
        raw_output={"bad": 1},
        validation_error="overlap",
        repaired_output={"bad": 2},
        repair_error="still overlap",
        review_status="validation_failed",
    )

    statuses = {item["status"] for item in r2_profile_store.list_archive("registry")}
    assert statuses == {"rejected", "validation_failed"}
    assert json.loads(path.read_text(encoding="utf-8"))["payload"]["repair_error"] == "still overlap"


def test_cp1_uses_same_active_archive_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(cp1_profile_store, "PROFILE_DIR", tmp_path / "cp1")
    cp1_profile_store.promote_active(
        "registry", {"risk_level": "high"}, run_id="run", review_round=1, input_refs={}
    )
    cp1_profile_store.archive_candidate(
        "registry",
        {"risk_level": "medium"},
        status="human_rejected",
        reason="reviewed",
        run_id="run-2",
        review_round=1,
        input_refs={},
    )

    assert cp1_profile_store.load_active("registry")["payload"]["risk_level"] == "high"
    assert cp1_profile_store.list_archive("registry")[0]["status"] == "human_rejected"
