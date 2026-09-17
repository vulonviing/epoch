from __future__ import annotations

import json
from pathlib import Path

from epoch_switch.agents.data.d1_loader import d1_profile_store


def test_promote_active_creates_active_json(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)

    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": {"request_id": "r1"}, "summary": {"tables": ["energy"]}},
        run_id="run_1",
        review_round=1,
        input_refs={"da1": {"artifact_id": "da1-abc", "payload_sha256": "aaa"}},
    )

    active = d1_profile_store.load_active("test_registry")
    assert active is not None
    assert active["agent_id"] == "D1"
    assert active["status"] == "active"
    assert active["registry_id"] == "test_registry"
    assert active["payload"]["data_request"]["request_id"] == "r1"
    assert active["payload"]["summary"]["tables"] == ["energy"]
    assert active["input_refs"]["da1"]["artifact_id"] == "da1-abc"
    assert active["run_id"] == "run_1"


def test_load_active_returns_none_for_unknown_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)
    assert d1_profile_store.load_active("nonexistent") is None


def test_archive_candidate_does_not_touch_active(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)

    d1_profile_store.archive_candidate(
        "test_registry",
        {"data_request": {}},
        status="human_rejected",
        reason="test rejection",
        run_id="run_1",
        review_round=1,
        input_refs={},
    )

    assert d1_profile_store.load_active("test_registry") is None
    archive = d1_profile_store.list_archive("test_registry")
    assert len(archive) == 1
    assert archive[0]["status"] == "human_rejected"
    assert archive[0]["reason"] == "test rejection"


def test_promote_active_archives_previous(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)

    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": {"request_id": "v1"}, "summary": {}},
        run_id="run_1",
        review_round=1,
        input_refs={},
    )

    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": {"request_id": "v2"}, "summary": {}},
        run_id="run_2",
        review_round=2,
        input_refs={},
    )

    active = d1_profile_store.load_active("test_registry")
    assert active["payload"]["data_request"]["request_id"] == "v2"
    assert active["run_id"] == "run_2"

    archive = d1_profile_store.list_archive("test_registry")
    assert len(archive) == 1
    assert archive[0]["payload"]["data_request"]["request_id"] == "v1"
    assert archive[0]["status"] == "superseded"


def test_retire_active_removes_active_json(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)

    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": {}},
        run_id="run_1",
        review_round=1,
        input_refs={},
    )
    assert d1_profile_store.load_active("test_registry") is not None

    d1_profile_store.retire_active(
        "test_registry",
        reason="manual retirement",
        run_id="run_2",
    )
    assert d1_profile_store.load_active("test_registry") is None
    archive = d1_profile_store.list_archive("test_registry")
    assert len(archive) == 1
    assert archive[0]["status"] == "retired_by_user"


def test_snapshot_active_copies_to_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)

    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": {}},
        run_id="run_1",
        review_round=1,
        input_refs={},
    )

    d1_profile_store.snapshot_active(
        "test_registry",
        reason="refresh_snapshot",
        run_id="run_2",
    )

    # Active still exists
    assert d1_profile_store.load_active("test_registry") is not None
    archive = d1_profile_store.list_archive("test_registry")
    assert len(archive) == 1
    assert archive[0]["status"] == "refresh_snapshot"


def test_load_data_request_shortcut(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)

    data_req = {"request_id": "r1", "entity_grain": "site"}
    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": data_req, "summary": {}},
        run_id="run_1",
        review_round=1,
        input_refs={},
    )

    result = d1_profile_store.load_data_request("test_registry")
    assert result is not None
    assert result["request_id"] == "r1"
    assert result["entity_grain"] == "site"


def test_archive_existing_false_preserves_old_active(tmp_path, monkeypatch):
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path)

    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": {"request_id": "v1"}},
        run_id="run_1",
        review_round=1,
        input_refs={},
    )

    d1_profile_store.promote_active(
        "test_registry",
        {"data_request": {"request_id": "v2"}},
        run_id="run_2",
        review_round=2,
        input_refs={},
        archive_existing=False,
    )

    active = d1_profile_store.load_active("test_registry")
    assert active["payload"]["data_request"]["request_id"] == "v2"
    archive = d1_profile_store.list_archive("test_registry")
    assert len(archive) == 0
