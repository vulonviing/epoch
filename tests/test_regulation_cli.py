from __future__ import annotations

import json
from types import SimpleNamespace

from epoch_switch import regulation_cli as cli
from epoch_switch.agents.data.da1_request_planner import da1_profile_store
from epoch_switch.agents.data.d1_loader import d1_profile_store
from epoch_switch.agents.data.d2_missing_value import d2_profile_store
from epoch_switch.agents.orchestration.cp1_case_profiler import cp1_profile_store
from epoch_switch.agents.orchestration.topology_selector import topology_selector_store
from epoch_switch.agents.regulation.r1_active_reader import r1_profile_store
from epoch_switch.agents.regulation.r2_scope_reviewer import r2_profile_store
from epoch_switch.core.data_catalog import (
    DataCatalogBuilder,
)
from epoch_switch.usecases.registry import UC2, UC3, list_usecases


def mapping_report():
    return {
        "summary": "Mapped what is available.",
        "field_mappings": [
            {
                "field_id": "PF-001",
                "field_name": "site_identity",
                "role": "entity",
                "priority": "core",
                "status": "mapped",
                "catalog_targets": [
                    {"kind": "entity_grain", "name": "site"}
                ],
                "alternative_targets": [],
                "reason": "Site grain exists.",
            },
            {
                "field_id": "PF-002",
                "field_name": "annual_energy",
                "role": "measure",
                "priority": "core",
                "status": "mapped",
                "catalog_targets": [
                    {"kind": "measure", "name": "energy_mwh"}
                ],
                "alternative_targets": [],
                "reason": "Energy measure exists.",
            },
            {
                "field_id": "PF-003",
                "field_name": "waste_heat_temperature",
                "role": "measure",
                "priority": "related",
                "status": "unmapped",
                "catalog_targets": [],
                "alternative_targets": [],
                "reason": "No heat measure exists.",
            },
        ],
        "suggested_request": {
            "source_domain": "energy",
            "entity_grain": "site",
            "time_grain": "year",
            "filters": {"iso2_code": ["DE"]},
            "measures": ["energy_mwh"],
            "quality_policy": "exclude_inconsistent",
        },
        "notes": [],
        "validation": {"ok": True, "errors": [], "warnings": []},
    }


def test_registry_lists_each_usecase_once():
    assert [key for key, _ in list_usecases()] == ["1", "2", "2.1", "3", "4"]


def test_review_supports_approve_exclude_and_remap():
    catalog = DataCatalogBuilder().build()
    decisions_by_id = {
        "PF-001": ("approved", {"kind": "entity_grain", "name": "site"}),
        "PF-002": ("remapped", {"kind": "measure", "name": "co2_t"}),
        "PF-003": ("excluded", None),
    }
    decisions = cli.review_mappings(
        mapping_report(),
        catalog,
        decider=lambda mapping, _: decisions_by_id[mapping["field_id"]],
    )
    assert [item["decision"] for item in decisions] == [
        "approved",
        "remapped",
        "excluded",
    ]


def test_approved_scope_contains_only_human_approved_targets():
    catalog = DataCatalogBuilder().build()
    decisions = [
        {
            "field_id": "PF-001",
            "field_name": "site_identity",
            "priority": "core",
            "decision": "approved",
            "catalog_target": {"kind": "entity_grain", "name": "site"},
        },
        {
            "field_id": "PF-002",
            "field_name": "annual_energy",
            "priority": "core",
            "decision": "approved",
            "catalog_target": {"kind": "measure", "name": "energy_mwh"},
        },
        {
            "field_id": "PF-003",
            "field_name": "waste_heat_temperature",
            "priority": "related",
            "decision": "unresolved",
            "catalog_target": None,
        },
    ]
    scope = cli.build_approved_mapping_scope(UC2, mapping_report(), decisions)
    handoff = scope["handoff_data_request"]
    assert handoff["entity_grain"] == "site"
    assert handoff["measures"] == ["energy_mwh"]
    assert handoff["approved_field_ids"] == ["PF-001", "PF-002"]
    assert "PF-003" not in handoff["approved_field_ids"]


# ── Shared D2 stub ───────────────────────────────────────────────────────────


def _fake_d2_pass_payload():
    return {
        "verdict": "pass",
        "routing_recommendation": "normal",
        "evidence_completeness": 1.0,
        "max_severity": "info",
        "summary": "All data-quality checks passed.",
        "assessable_claims": ["R2F-001"],
        "blocked_claims": [],
        "cp1_signal": {
            "evidence_assessable": True,
            "evidence_completeness": 1.0,
            "blocked_claims": [],
            "routing_recommendation": "normal",
        },
    }


class FakeD2:
    """Stub D2 agent that always returns a 'pass' verdict without LLM calls."""

    def __init__(self):
        self.last_llm_provenance = None

    def execute_from_scope(
        self, envelope, store, *, registry_id, in_scope_findings,
        handoff_data_request, normalized_data_request, data_product,
    ):
        return SimpleNamespace(payload=_fake_d2_pass_payload())


# ─────────────────────────────────────────────────────────────────────────────


def test_pipeline_returns_to_da1_then_saves_compatible_active_set(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "get_llm", lambda: object())
    r1 = {
        "verdict": "grounded",
        "summary": "Grounded.",
        "selected_pages": [],
        "findings": [
            {
                "finding_id": "RF-001",
                "requirement_type": "threshold",
                "statement": "Threshold.",
                "source_ref": "REG-2",
                "source_file": "EnEfG.pdf",
                "page": 8,
                "evidence_excerpt": "threshold",
            }
        ],
        "possible_fields": [
            {
                "field_id": "PF-001",
                "name": "site_identity",
                "role": "entity",
                "priority": "core",
                "reason": "Site-level assessment.",
                "finding_refs": ["RF-001"],
            },
            {
                "field_id": "PF-002",
                "name": "annual_energy",
                "role": "measure",
                "priority": "core",
                "reason": "Threshold input.",
                "finding_refs": ["RF-001"],
            },
            {
                "field_id": "PF-003",
                "name": "waste_heat_temperature",
                "role": "measure",
                "priority": "related",
                "reason": "Related waste-heat context.",
                "finding_refs": ["RF-001"],
            },
        ],
        "source_hashes": {},
        "cache_signature": {},
    }
    report = mapping_report()
    catalog = DataCatalogBuilder().build()

    class FakeR1:
        def __init__(self, llm):
            self.last_llm_provenance = None

        def execute_preselection(self, envelope, store, cache_mode):
            envelope.regulation_profile = r1
            return SimpleNamespace(payload=r1)

    class FakeDA1:
        def __init__(self):
            self.last_llm_provenance = None

        def execute(self, capsule, envelope, store):
            return SimpleNamespace(
                payload={"catalog": catalog, "mapping_report": report}
            )

    class FakeR2:
        def __init__(self):
            self.calls = 0
            self.last_llm_provenance = None

        def review(self, **kwargs):
            self.calls += 1
            return {
                "schema_version": "1",
                "registry_id": UC2.id,
                "summary": f"Round {self.calls}",
                "in_scope_findings": [
                    {
                        "r2_finding_id": "R2F-001",
                        "statement": "Approved threshold scope.",
                        "assessment_boundary": "Threshold assessment only.",
                        "requirement_type": "threshold",
                        "r1_finding_refs": ["RF-001"],
                        "approved_field_ids": ["PF-001", "PF-002"],
                        "citations": [],
                    }
                ],
                "blocked_findings": [],
                "excluded_findings": [],
                "limitations": [],
            }

    cp1_response = {
        "task_type": "compliance_check",
        "risk_level": "high",
        "uncertainty": "low",
        "deadline_proximity_days": None,
        "assurance_artifact": "single_attestation",
        "dual_method_required": False,
        "cluster_count": 1,
        "assurance_level": "regulatory",
        "cross_functional_need": False,
        "output_profile": {
            "output_type": "qualitative",
            "form": "structured_memo",
            "description": "Interpretive compliance memo with article citations.",
        },
        "signal_sources": {
            "task_type": ["registry", "r1"],
            "risk_level": ["registry", "r1"],
            "uncertainty": ["r1", "da1"],
            "assurance_artifact": ["r2", "da1"],
            "dual_method_required": ["registry"],
            "cluster_count": ["da1"],
            "assurance_level": ["registry", "r1"],
            "cross_functional_need": ["registry"],
            "output_profile": ["registry"],
        },
        "limitations": [
            "deadline_proximity_days: requires structured deadline",
        ],
    }

    class FakeCP1:
        def __init__(self):
            self.last_llm_provenance = None

        def execute_preselection(self, envelope, store, *, approved_profile_context=None, **_):
            return SimpleNamespace(payload=cp1_response, sender_agent="CP1")

    class FakeD1:
        def execute_from_handoff(self, envelope, store, handoff_request):
            return SimpleNamespace(
                payload={
                    "data_request": handoff_request,
                    "summary": {"tables": ["energy"], "total_rows": 1},
                }
            )

    ts_response = {
        "topology_scores": {"Direct": 70, "Debate": 20, "Coalition": 10},
        "selected_topology_id": "Direct",
        "rationale": "Threshold check, low uncertainty, single domain.",
        "alternatives": [
            {"topology_id": "Debate", "why_not": "No interpretive ambiguity."},
            {"topology_id": "Coalition", "why_not": "Single domain."},
        ],
        "signal_sources": {"selected_topology_id": ["uncertainty", "task_type"]},
        "bindings": [],
        "limitations": [],
    }

    class FakeTS1:
        def __init__(self):
            self.last_llm_provenance = None

        def execute_selection(self, envelope, store, *, case_profile):
            return SimpleNamespace(payload=ts_response, sender_agent="TS1")

    monkeypatch.setattr(cli, "ActiveRegulationReader", FakeR1)
    monkeypatch.setattr(cli, "DataRequestPlannerAgent", FakeDA1)
    monkeypatch.setattr(cli, "DataLoaderAgent", FakeD1)
    monkeypatch.setattr(r1_profile_store, "PROFILE_DIR", tmp_path / "r1")
    monkeypatch.setattr(da1_profile_store, "PROFILE_DIR", tmp_path / "da1")
    monkeypatch.setattr(r2_profile_store, "PROFILE_DIR", tmp_path / "r2")
    monkeypatch.setattr(cp1_profile_store, "PROFILE_DIR", tmp_path / "cp1")
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path / "d1")
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path / "d2")
    monkeypatch.setattr(topology_selector_store, "PROFILE_DIR", tmp_path / "ts")
    fake_r2 = FakeR2()
    actions = iter(["back", "approve"])

    result = cli.build_profile_pipeline(
        seed=UC2,
        experiment_dir=tmp_path / "experiment",
        refresh=False,
        decider=lambda item, _: (
            ("approved", item["catalog_targets"][0])
            if item["catalog_targets"]
            else ("unresolved", None)
        ),
        approval_provider=lambda: True,
        r2_review_provider=lambda _: next(actions),
        r2_agent=fake_r2,
        d2_agent=FakeD2(),
        cp1_agent=FakeCP1(),
        cp1_review_provider=lambda _: "approve",
        ts_agent=FakeTS1(),
        ts_review_provider=lambda _: "approve",
    )

    assert fake_r2.calls == 2
    assert cli.active_set_ready(result)
    assert result["r2"]["payload"]["summary"] == "Round 2"
    assert result["r2"]["payload"]["human_review"]["decision"] == "approved"
    assert result["cp1"]["payload"]["task_type"] == "compliance_check"
    archived_r2 = r2_profile_store.list_archive(UC2.id)
    assert len(archived_r2) == 1
    assert archived_r2[0]["status"] == "rejected"


def test_cp1_reject_leaves_r1_da1_r2_active_but_no_cp1(tmp_path, monkeypatch):
    """Rejecting CP1 must archive the output and leave the CP1 shelf empty."""
    from types import SimpleNamespace

    monkeypatch.setattr(cli, "get_llm", lambda: object())

    cp1_payload = {
        "task_type": "compliance_check",
        "risk_level": "low",
        "uncertainty": "low",
        "deadline_proximity_days": None,
        "dual_method_required": False,
        "cluster_count": 1,
        "assurance_level": "routine",
        "cross_functional_need": False,
        "signal_sources": {
            "task_type": ["registry"],
            "risk_level": ["registry"],
            "uncertainty": ["registry"],
            "dual_method_required": ["registry"],
            "cluster_count": ["registry"],
            "assurance_level": ["registry"],
            "cross_functional_need": ["registry"],
        },
        "limitations": [],
    }

    r1_data = {
        "verdict": "grounded",
        "summary": "ok",
        "selected_pages": [],
        "findings": [
            {
                "finding_id": "RF-001",
                "requirement_type": "threshold",
                "statement": "T",
                "source_ref": "REG-2",
                "source_file": "EnEfG.pdf",
                "page": 8,
                "evidence_excerpt": "t",
            }
        ],
        "possible_fields": [
            {
                "field_id": "PF-001",
                "name": "site_identity",
                "role": "entity",
                "priority": "core",
                "reason": "site",
                "finding_refs": ["RF-001"],
            }
        ],
        "source_hashes": {},
        "cache_signature": {},
    }

    catalog = DataCatalogBuilder().build()
    report = {
        "summary": "ok",
        "field_mappings": [
            {
                "field_id": "PF-001",
                "field_name": "site_identity",
                "role": "entity",
                "priority": "core",
                "status": "mapped",
                "catalog_targets": [{"kind": "entity_grain", "name": "site"}],
                "alternative_targets": [],
                "reason": "ok",
            }
        ],
        "suggested_request": {
            "source_domain": "energy",
            "entity_grain": "site",
            "time_grain": "year",
            "filters": {"iso2_code": ["DE"]},
            "measures": ["energy_mwh"],
            "quality_policy": "exclude_inconsistent",
        },
        "notes": [],
        "validation": {"ok": True, "errors": [], "warnings": []},
    }

    r2_data = {
        "schema_version": "1",
        "registry_id": UC2.id,
        "summary": "ok",
        "in_scope_findings": [
            {
                "r2_finding_id": "R2F-001",
                "statement": "ok",
                "assessment_boundary": "threshold only",
                "requirement_type": "threshold",
                "r1_finding_refs": ["RF-001"],
                "approved_field_ids": ["PF-001"],
                "citations": [],
            }
        ],
        "blocked_findings": [],
        "excluded_findings": [],
        "limitations": [],
    }

    class FakeR1:
        def __init__(self, llm):
            self.last_llm_provenance = None
        def execute_preselection(self, envelope, store, cache_mode):
            envelope.regulation_profile = r1_data
            return SimpleNamespace(payload=r1_data)

    class FakeDA1:
        def __init__(self):
            self.last_llm_provenance = None

        def execute(self, capsule, envelope, store):
            return SimpleNamespace(payload={"catalog": catalog, "mapping_report": report})

    class FakeR2:
        def __init__(self):
            self.last_llm_provenance = None

        def review(self, **kwargs):
            return r2_data

    class FakeCP1:
        def __init__(self):
            self.last_llm_provenance = None

        def execute_preselection(self, envelope, store, *, approved_profile_context=None, **_):
            return SimpleNamespace(payload=cp1_payload, sender_agent="CP1")

    class FakeD1:
        def execute_from_handoff(self, envelope, store, handoff_request):
            return SimpleNamespace(
                payload={
                    "data_request": handoff_request,
                    "summary": {"tables": ["energy"], "total_rows": 1},
                }
            )

    monkeypatch.setattr(cli, "ActiveRegulationReader", FakeR1)
    monkeypatch.setattr(cli, "DataRequestPlannerAgent", FakeDA1)
    monkeypatch.setattr(cli, "DataLoaderAgent", FakeD1)
    monkeypatch.setattr(r1_profile_store, "PROFILE_DIR", tmp_path / "r1")
    monkeypatch.setattr(da1_profile_store, "PROFILE_DIR", tmp_path / "da1")
    monkeypatch.setattr(r2_profile_store, "PROFILE_DIR", tmp_path / "r2")
    monkeypatch.setattr(cp1_profile_store, "PROFILE_DIR", tmp_path / "cp1")
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path / "d1")
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path / "d2")

    result = cli.build_profile_pipeline(
        seed=UC2,
        experiment_dir=tmp_path / "experiment",
        refresh=False,
        decider=lambda item, _: (
            ("approved", item["catalog_targets"][0])
            if item.get("catalog_targets")
            else ("unresolved", None)
        ),
        approval_provider=lambda: True,
        r2_review_provider=lambda _: "approve",
        r2_agent=FakeR2(),
        d2_agent=FakeD2(),
        cp1_agent=FakeCP1(),
        cp1_review_provider=lambda _: "reject",
    )

    # R1/DA1/R2/D1/D2 active, CP1 shelf empty
    assert result["r1"] is not None
    assert result["da1"] is not None
    assert result["r2"] is not None
    assert result["d1"] is not None
    assert result["d2"] is not None
    assert result["cp1"] is None
    assert not cli.active_set_ready(result)

    archived = cp1_profile_store.list_archive(UC2.id)
    assert len(archived) == 1
    assert archived[0]["status"] == "human_rejected"


def test_r1_hard_stop_shows_panel_and_returns_none(tmp_path, monkeypatch):
    """When R1 raises (after repair exhaustion), CLI returns None without a traceback."""
    monkeypatch.setattr(cli, "get_llm", lambda: object())
    monkeypatch.setattr(r1_profile_store, "PROFILE_DIR", tmp_path / "r1")
    monkeypatch.setattr(da1_profile_store, "PROFILE_DIR", tmp_path / "da1")
    monkeypatch.setattr(r2_profile_store, "PROFILE_DIR", tmp_path / "r2")
    monkeypatch.setattr(cp1_profile_store, "PROFILE_DIR", tmp_path / "cp1")
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path / "d1")
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path / "d2")

    class BrokenR1:
        def __init__(self, llm):
            pass

        def execute_preselection(self, envelope, store, cache_mode):
            raise ValueError("R1 returned invalid output after 3 repair attempts; audit saved to x")

    monkeypatch.setattr(cli, "ActiveRegulationReader", BrokenR1)

    result = cli.build_profile_pipeline(
        seed=UC2,
        experiment_dir=tmp_path / "experiment",
        refresh=False,
    )

    assert result is None
    # Active R1 shelf must remain empty (nothing was promoted)
    assert r1_profile_store.load_active(UC2.id) is None


def test_handoff_readiness_rejects_stale_r2(tmp_path, monkeypatch):
    monkeypatch.setattr(r1_profile_store, "PROFILE_DIR", tmp_path / "r1")
    monkeypatch.setattr(da1_profile_store, "PROFILE_DIR", tmp_path / "da1")
    monkeypatch.setattr(r2_profile_store, "PROFILE_DIR", tmp_path / "r2")
    monkeypatch.setattr(cp1_profile_store, "PROFILE_DIR", tmp_path / "cp1")
    monkeypatch.setattr(d1_profile_store, "PROFILE_DIR", tmp_path / "d1")
    monkeypatch.setattr(d2_profile_store, "PROFILE_DIR", tmp_path / "d2")
    monkeypatch.setattr(topology_selector_store, "PROFILE_DIR", tmp_path / "ts")

    r1_profile_store.promote_active(
        "registry", {"v": 1}, run_id="one", review_round=1, input_refs={}
    )
    r1 = r1_profile_store.load_active("registry")
    da1_profile_store.promote_active(
        "registry",
        {"approved_mapping_scope": {}},
        run_id="one",
        review_round=1,
        input_refs={"r1": cli._active_ref(r1)},
    )
    da1 = da1_profile_store.load_active("registry")
    r2_profile_store.promote_active(
        "registry",
        {"summary": "old"},
        run_id="one",
        review_round=1,
        input_refs={"r1": cli._active_ref(r1), "da1": cli._active_ref(da1)},
    )
    r2 = r2_profile_store.load_active("registry")
    d1_profile_store.promote_active(
        "registry",
        {"data_request": {}, "summary": {}},
        run_id="one",
        review_round=1,
        input_refs={"da1": cli._active_ref(da1), "r2": cli._active_ref(r2)},
    )
    d1 = d1_profile_store.load_active("registry")
    d2_profile_store.promote_active(
        "registry",
        {"verdict": "pass", "routing_recommendation": "normal"},
        run_id="one",
        review_round=1,
        input_refs={"d1": cli._active_ref(d1), "r2": cli._active_ref(r2)},
    )
    d2 = d2_profile_store.load_active("registry")
    cp1_profile_store.promote_active(
        "registry",
        {"task_type": "compliance_check"},
        run_id="one",
        review_round=1,
        input_refs={
            "r1": cli._active_ref(r1),
            "da1": cli._active_ref(da1),
            "r2": cli._active_ref(r2),
            "d1": cli._active_ref(d1),
            "d2": cli._active_ref(d2),
        },
    )
    cp1 = cp1_profile_store.load_active("registry")
    topology_selector_store.promote_active(
        "registry",
        {
            "topology_scores": {"Direct": 80, "Debate": 15, "Coalition": 5},
            "selected_topology_id": "Direct",
            "rationale": "Clear threshold rule.",
            "alternatives": [],
            "signal_sources": {},
            "bindings": [],
            "limitations": [],
        },
        run_id="one",
        review_round=1,
        input_refs={"cp1": cli._active_ref(cp1)},
    )
    assert cli.active_set_ready(cli.load_active_set("registry"))

    # Refreshing R1 breaks the chain — CP1 input_refs now stale
    r1_profile_store.promote_active(
        "registry", {"v": 2}, run_id="two", review_round=1, input_refs={}
    )
    assert not cli.active_set_ready(cli.load_active_set("registry"))


# ── EMEA composite region bucket expansion ────────────────────────────────────


def test_build_handoff_emea_expands_to_derived_filter():
    """UC3 site_filter={'region':'EMEA'} must produce emea:[True] in the handoff.

    'EMEA' is not a literal cdp_region value — it is a composite region bucket
    that the executor materialises as a boolean derived column.  The handoff must
    carry the derived filter, not the literal, so the validator does not reject it.
    """
    catalog = DataCatalogBuilder().build()
    # Minimal decisions: one measure approved, no entity/time grain override.
    decisions = [
        {
            "field_id": "PF-001",
            "field_name": "scope2_emissions",
            "priority": "core",
            "decision": "approved",
            "catalog_target": {"kind": "measure", "name": "scope2_market_proxy_t"},
        },
    ]
    scope = cli.build_approved_mapping_scope(UC3, _uc3_mapping_report(), decisions)
    handoff = scope["handoff_data_request"]
    filters = handoff.get("filters", {})
    assert "emea" in filters, (
        f"Handoff must carry derived 'emea' filter, not a literal region. Got filters: {filters}"
    )
    assert filters["emea"] == [True], f"emea filter must be [True], got: {filters['emea']}"
    assert "cdp_region" not in filters, (
        f"Handoff must not carry literal cdp_region; 'EMEA' is not a valid value. Got: {filters}"
    )
    assert "region_name" not in filters, (
        f"Handoff must not carry region_name alias; EMEA expansion supersedes it. Got: {filters}"
    )


def _uc3_mapping_report():
    """Minimal mapping report for UC3 (one core field mapped, one unmapped)."""
    return {
        "summary": "UC3 Scope 2 mapping.",
        "field_mappings": [
            {
                "field_id": "PF-001",
                "field_name": "scope2_emissions",
                "role": "measure",
                "priority": "core",
                "status": "mapped",
                "catalog_targets": [
                    {"kind": "measure", "name": "scope2_market_proxy_t"}
                ],
                "reason": "Proxy via co2_factor ingredient.",
            },
        ],
        "suggested_request": {
            "source_domain": "emissions",
            "entity_grain": "site",
            "time_grain": "year",
            "measures": ["scope2_market_proxy_t"],
            "filters": {},
        },
    }


# ── bu_rc division code alias ────────────────────────────────────────────────


def test_build_handoff_bu_rc_maps_to_bu_rc_group():
    """UC2 site_filter={'bu_rc': ['DI','SMO']} must produce bu_rc_group in the handoff.

    The CLI filter_aliases table formerly mapped 'bu_rc' → 'bu_rc_code', which the
    legacy alias chain further translated to 'bu_rc_name' (full company names), so
    short division codes like 'DI'/'SMO' were validated against the wrong column and
    rejected.  After the fix, 'bu_rc' aliases directly to 'bu_rc_group', the real
    column that holds division codes DI/SMO/SI/SRE/Advanta/CDO/RC-DE.
    """
    scope = cli.build_approved_mapping_scope(UC2, _uc2_mapping_report(), _uc2_decisions())
    handoff = scope["handoff_data_request"]
    filters = handoff.get("filters", {})
    assert "bu_rc_group" in filters, (
        f"Handoff must carry 'bu_rc_group' filter (real column). Got filters: {filters}"
    )
    assert set(filters["bu_rc_group"]) == {"DI", "SMO"}, (
        f"bu_rc_group must contain exactly DI and SMO. Got: {filters['bu_rc_group']}"
    )
    assert "bu_rc_code" not in filters, (
        f"Handoff must not carry ambiguous legacy key 'bu_rc_code'. Got: {filters}"
    )
    assert "bu_rc_name" not in filters, (
        f"Handoff must not carry 'bu_rc_name' (full names, wrong column). Got: {filters}"
    )


def _uc2_decisions():
    """Minimal approved decisions for UC2: one measure approved."""
    return [
        {
            "field_id": "PF-001",
            "field_name": "annual_co2_emissions",
            "priority": "core",
            "decision": "approved",
            "catalog_target": {"kind": "measure", "name": "co2_t"},
        },
    ]


def _uc2_mapping_report():
    """Minimal mapping report for UC2 (one core field mapped)."""
    return {
        "summary": "UC2 ETS1 compliance mapping.",
        "field_mappings": [
            {
                "field_id": "PF-001",
                "field_name": "annual_co2_emissions",
                "role": "measure",
                "priority": "core",
                "status": "mapped",
                "catalog_targets": [
                    {"kind": "measure", "name": "co2_t"}
                ],
                "reason": "Direct match.",
            },
        ],
        "suggested_request": {
            "source_domain": "emissions",
            "entity_grain": "site",
            "time_grain": "year",
            "measures": ["co2_t"],
            "filters": {},
        },
    }
