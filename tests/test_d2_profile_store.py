"""Unit tests for d2_profile_store — registry-keyed shelf operations."""
from __future__ import annotations

from epoch_switch.agents.data.d2_missing_value import d2_profile_store


REGISTRY_ID = "uc_test_store"
RUN_ID = "2026-06-13T000000Z_test"


def _sample_payload() -> dict:
    return {
        "verdict": "pass",
        "routing_recommendation": "normal",
        "evidence_completeness": 0.95,
        "summary": "All checks passed.",
        "assessable_claims": ["claim_1"],
        "blocked_claims": [],
    }


def test_promote_creates_active_json(tmp_path, monkeypatch):
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    d2_profile_store.promote_active(
        REGISTRY_ID,
        _sample_payload(),
        run_id=RUN_ID,
        review_round=1,
        input_refs={"d1": {"artifact_id": "d1-abc", "payload_sha256": "sha"}},
        archive_existing=False,
    )
    record = d2_profile_store.load_active(REGISTRY_ID)
    assert record is not None
    assert record["agent_id"] == "D2"
    assert record["registry_id"] == REGISTRY_ID
    assert record["status"] == "active"
    assert record["artifact_id"].startswith("d2-")
    assert record["payload"]["verdict"] == "pass"


def test_load_verdict_returns_verdict_string(tmp_path, monkeypatch):
    """load_verdict returns payload['verdict'] — the verdict string."""
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    d2_profile_store.promote_active(
        REGISTRY_ID,
        _sample_payload(),
        run_id=RUN_ID,
        review_round=1,
        input_refs={},
        archive_existing=False,
    )
    assert d2_profile_store.load_verdict(REGISTRY_ID) == "pass"


def test_load_active_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    assert d2_profile_store.load_active("nonexistent_registry") is None


def test_load_verdict_returns_none_when_no_active(tmp_path, monkeypatch):
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    assert d2_profile_store.load_verdict("nonexistent_registry") is None


def test_promote_archives_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    d2_profile_store.promote_active(
        REGISTRY_ID,
        _sample_payload(),
        run_id=RUN_ID,
        review_round=1,
        input_refs={},
        archive_existing=False,
    )
    first_artifact_id = d2_profile_store.load_active(REGISTRY_ID)["artifact_id"]
    d2_profile_store.promote_active(
        REGISTRY_ID,
        {**_sample_payload(), "verdict": "warning"},
        run_id=RUN_ID,
        review_round=2,
        input_refs={},
        archive_existing=True,
    )
    assert d2_profile_store.load_verdict(REGISTRY_ID) == "warning"
    archive = d2_profile_store.list_archive(REGISTRY_ID)
    assert len(archive) == 1
    assert archive[0]["artifact_id"] == first_artifact_id


def test_archive_candidate_writes_to_archive_not_active(tmp_path, monkeypatch):
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    d2_profile_store.archive_candidate(
        REGISTRY_ID,
        _sample_payload(),
        status="pipeline_stopped",
        reason="Data quality insufficient",
        run_id=RUN_ID,
        review_round=1,
        input_refs={},
    )
    assert d2_profile_store.load_active(REGISTRY_ID) is None
    archive = d2_profile_store.list_archive(REGISTRY_ID)
    assert len(archive) == 1
    assert archive[0]["status"] == "pipeline_stopped"


def test_snapshot_active_archives_without_removing(tmp_path, monkeypatch):
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    d2_profile_store.promote_active(
        REGISTRY_ID,
        _sample_payload(),
        run_id=RUN_ID,
        review_round=1,
        input_refs={},
        archive_existing=False,
    )
    d2_profile_store.snapshot_active(REGISTRY_ID, reason="refresh_snapshot", run_id=RUN_ID)
    assert d2_profile_store.load_active(REGISTRY_ID) is not None
    assert len(d2_profile_store.list_archive(REGISTRY_ID)) == 1


def test_registry_keyed_paths_are_isolated(tmp_path, monkeypatch):
    """Two different registries store independently."""
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path)
    d2_profile_store.promote_active(
        "registry_a",
        {**_sample_payload(), "verdict": "pass"},
        run_id=RUN_ID,
        review_round=1,
        input_refs={},
        archive_existing=False,
    )
    d2_profile_store.promote_active(
        "registry_b",
        {**_sample_payload(), "verdict": "warning"},
        run_id=RUN_ID,
        review_round=1,
        input_refs={},
        archive_existing=False,
    )
    assert d2_profile_store.load_verdict("registry_a") == "pass"
    assert d2_profile_store.load_verdict("registry_b") == "warning"
    assert d2_profile_store.load_active("registry_a") is not None
    assert d2_profile_store.load_active("registry_b") is not None
