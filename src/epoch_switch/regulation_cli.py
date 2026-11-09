"""Interactive R1 -> DA1 -> R2 agent-local profile pipeline."""
from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

from epoch_switch import config
from epoch_switch.agents.data.da1_request_planner import DataRequestPlannerAgent
from epoch_switch.agents.data.da1_request_planner import da1_profile_store
from epoch_switch.agents.data.da1_request_planner.da1_data_request_planner import (
    catalog_target_exists,
)
from epoch_switch.core.data_catalog import expand_region_bucket, resolve_source_domains
from epoch_switch.agents.data.d1_loader import DataLoaderAgent
from epoch_switch.agents.data.d1_loader import d1_profile_store
from epoch_switch.agents.data.d1_loader.derived_case_facts_builder import (
    build_derived_case_facts,
)
from epoch_switch.agents.data.d2_missing_value import MissingValueAnalyst
from epoch_switch.agents.data.d2_missing_value import d2_profile_store
from epoch_switch.agents.data.d3_time_series import TimeSeriesComputeAgent
from epoch_switch.agents.data.d3_time_series import d3_profile_store
from epoch_switch.agents.output.p1_result_interpreter import ResultInterpreterAgent
from epoch_switch.agents.output.p1_result_interpreter import p1_profile_store
from epoch_switch.agents.output.f1_finalizer import FinalizerAgent
from epoch_switch.agents.output.f1_finalizer import f1_profile_store
from epoch_switch.agents.output.p2_energy_method_interpreter import EnergyMethodInterpreterAgent
from epoch_switch.agents.output.p2_energy_method_interpreter import p2_profile_store
from epoch_switch.agents.output.p3_reported_method_interpreter import ReportedMethodInterpreterAgent
from epoch_switch.agents.output.p3_reported_method_interpreter import p3_profile_store
from epoch_switch.agents.output.s1_synthesizer import SynthesizerAgent
from epoch_switch.agents.output.s1_synthesizer import s1_profile_store
from epoch_switch.agents.output.c1_divisional_attestor import DivisionalAttestorAgent
from epoch_switch.agents.output.c1_divisional_attestor import c1_profile_store
from epoch_switch.agents.output.c2_coalition_synthesizer import CoalitionSynthesizerAgent
from epoch_switch.agents.output.c2_coalition_synthesizer import c2_profile_store
from epoch_switch.selection.stage2_binding import agents_for as stage2_agents_for
from epoch_switch.selection.stage2_binding import flat_agents_for as stage2_flat_agents_for
from epoch_switch.agents.orchestration.cp1_case_profiler import CaseProfilerAgent
from epoch_switch.agents.orchestration.cp1_case_profiler import cp1_profile_store
from epoch_switch.agents.orchestration.topology_selector import TopologySelectorAgent
from epoch_switch.agents.orchestration.topology_selector import topology_selector_store
from epoch_switch.agents.regulation.rc1_change_classifier import ChangeClassifierAgent
from epoch_switch.agents.regulation.rc1_change_classifier import rc1_profile_store
from epoch_switch.agents.regulation.rc1_change_classifier import BlindChangeMatcherAgent
from epoch_switch.agents.regulation.rc1_change_classifier import rc1_1_profile_store
from epoch_switch.agents.regulation.rm1_exposure_mapper import ExposureMapperAgent
from epoch_switch.agents.regulation.rm1_exposure_mapper import rm1_profile_store
from epoch_switch.agents.regulation.rm1_exposure_mapper import BlindExposureMatcherAgent
from epoch_switch.agents.regulation.rm1_exposure_mapper import rm1_1_profile_store
from epoch_switch.agents.regulation.rd3_join import build_join
from epoch_switch.agents.regulation.rd3_join import rd3_profile_store
from epoch_switch.corpus import candidate_mapper
from epoch_switch.corpus.corpus_bundle import CorpusBundle, extract_corpus, map_candidates
from epoch_switch.agents.output.persona_assessor import PersonaAssessorAgent
from epoch_switch.agents.output.persona_assessor import persona_profile_store
from epoch_switch.agents.output.f2_impact_finalizer import ImpactFinalizerAgent
from epoch_switch.agents.output.f2_impact_finalizer import f2_profile_store
from epoch_switch.agents.regulation.r1_active_reader import ActiveRegulationReader
from epoch_switch.agents.regulation.r1_active_reader import r1_profile_store
from epoch_switch.agents.regulation.r2_scope_reviewer import (
    RegulationScopeReviewer,
)
from epoch_switch.agents.regulation.r2_scope_reviewer import r2_profile_store
from epoch_switch.agents.external.ex_commentator import ExternalCommentatorAgent
from epoch_switch.agents.external.ex_commentator import ex_profile_store
from epoch_switch.core.envelope import CaseEnvelope
from epoch_switch.core.evidence_store import EvidenceStore
from epoch_switch.core.llm_client import get_llm
from epoch_switch.usecases.registry import UsecaseSeed, get, list_usecases


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Visual theme ──────────────────────────────────────────────────────────────
# Central Rich theme — Siemens teal (#009999) accent; semantic aliases for
# success / warn / error so every border_style call goes through a name, not
# an ad-hoc color string.
_EPOCH_THEME = Theme(
    {
        "accent":     "#009999",
        "accent.dim": "#006666",
        "success":    "green",
        "warn":       "yellow",
        "error":      "red",
        "muted":      "dim",
    }
)

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Build and review agent-local R1 -> DA1 -> R2 registry profiles.",
)
console = Console(highlight=False, theme=_EPOCH_THEME)

# ── Banner ────────────────────────────────────────────────────────────────────
_BANNER_ART = """\
███████╗██████╗  ██████╗  ██████╗██╗  ██╗
██╔════╝██╔══██╗██╔═══██╗██╔════╝██║  ██║
█████╗  ██████╔╝██║   ██║██║     ███████║
██╔══╝  ██╔═══╝ ██║   ██║██║     ██╔══██║
███████╗██║     ╚██████╔╝╚██████╗██║  ██║
╚══════╝╚═╝      ╚═════╝  ╚═════╝╚═╝  ╚═╝"""


def render_banner(output_console: Console = console) -> None:
    """Print the EPOCH startup banner — called once at interactive_main entry."""
    ab = config.ACTIVE_BACKEND
    parts = [f"[bold]{ab.name}[/bold]", ab.kind, ab.model]
    if ab.reasoning_effort:
        parts.append(f"effort={ab.reasoning_effort}")
    backend_line = " · ".join(parts)
    body = (
        f"[accent]{_BANNER_ART}[/accent]\n\n"
        f"  [bold]Regulation Compliance Pipeline[/bold]\n"
        f"  [muted]R1 -> DA1 -> R2 -> D1 -> D2 -> CP1 -> TS1 -> D3 -> P1 -> F1[/muted]\n\n"
        f"  [muted]{backend_line}[/muted]"
    )
    output_console.print(Panel(body, border_style="accent", expand=False))
    output_console.print()


# ── Pipeline step tracker ─────────────────────────────────────────────────────
STEPS: list[tuple[str, str]] = [
    ("R1",  "Regulation Discovery"),
    ("DA1", "Catalog Mapping"),
    ("R2",  "Scope Review"),
    ("EX1", "External Perspective (Stage 1)"),
    ("D1",  "Data Load"),
    ("D2",  "Quality Preflight"),
    ("CP1", "Case Profile"),
    ("TS1", "Topology Selection"),
    ("D3",  "Site Computation"),
    ("P1",  "Result Interpretation"),
    ("P2",  "Energy-Method Read"),
    ("P3",  "Reported-Method Read"),
    ("S1",  "Reconciliation"),
    ("C1",  "Divisional Attestation"),
    ("C2",  "Coalition Consolidation"),
    ("EX2", "External Perspective (Stage 2)"),
    ("F1",  "Finalization"),
]

# UC4 document pipeline (registry.py UsecaseSeed.pipeline_family="document").
# Separate tracker so its progress bar doesn't misreport against the tabular
# STEPS list above -- render_progress(steps=DOCUMENT_STEPS, ...) selects it.
DOCUMENT_STEPS: list[tuple[str, str]] = [
    ("RD1",   "Corpus Extraction"),
    ("RD2",   "Candidate Mapping"),
    ("RC1.1", "Blind Change Matching"),
    ("RC1.2", "Change Classification"),
    ("RM1.1", "Blind Exposure Matching"),
    ("RM1.2", "Siemens Exposure Mapping"),
    ("RD3",   "Change/Exposure Join"),
    ("EX1",   "External Perspective (Stage 1)"),
    ("CP1",   "Case Profile"),
    ("TS1",   "Topology Selection"),
    ("P4",  "Conservative Assessment"),
    ("P5",  "Balanced Assessment"),
    ("P6",  "Maximum-Assurance Assessment"),
    ("EX2", "External Perspective (Stage 2)"),
    ("F2",  "Impact Finalization"),
]


def render_progress(
    active_key: str,
    completed_keys: list[str],
    output_console: Console = console,
    *,
    steps: list[tuple[str, str]] = STEPS,
) -> None:
    """Print a pipeline progress tracker and step header.

    Completed stages are marked ✔ (green), the active stage ◉ (accent/bold),
    and pending stages · (dim).  A ruled header names the current step.
    """
    step_index = next(
        (i for i, (k, _) in enumerate(steps) if k == active_key), 0
    )
    n = len(steps)

    parts: list[str] = []
    for key, _ in steps:
        if key in completed_keys:
            parts.append(f"[success]✔ {key}[/success]")
        elif key == active_key:
            parts.append(f"[bold accent]◉ {key}[/bold accent]")
        else:
            parts.append(f"[muted]· {key}[/muted]")
    tracker = "   ".join(parts)

    _, label = steps[step_index]
    header = f"  Step {step_index + 1}/{n}  ·  {active_key}  ·  {label}"
    rule = "[accent]" + "─" * 60 + "[/accent]"

    output_console.print()
    output_console.print(f"  {tracker}")
    output_console.print(rule)
    output_console.print(f"[bold]{header}[/bold]")
    output_console.print(rule)
    output_console.print()


# ── Action-required panel ─────────────────────────────────────────────────────

def render_action_panel(
    question: str,
    options_text: str,
    output_console: Console = console,
) -> None:
    """Print a prominent ACTION REQUIRED panel before a human-approval prompt.

    Args:
        question:     One-sentence description of what needs deciding.
        options_text: The available choices, e.g. "[a] approve   [b] back   [q] quit".
    """
    body = f"{question}\n\n[muted]{options_text}[/muted]"
    output_console.print(
        Panel(
            body,
            title="[bold]⏸  ACTION REQUIRED[/bold]",
            border_style="accent",
            expand=False,
        )
    )


def build_envelope(seed: UsecaseSeed) -> CaseEnvelope:
    return CaseEnvelope.new(
        usecase_ref=seed.id,
        natural_request=seed.natural_request,
        regulation_refs=deepcopy(seed.regulation_refs),
        regulation_sources=deepcopy(seed.regulation_sources),
        site_filter=deepcopy(seed.site_filter),
        time_window=seed.time_window,
        time_grain=seed.time_grain,
        expected_output_family=seed.expected_output,
    )


def choose_usecase(output_console: Console = console) -> str:
    table = Table(title="Registry Use Cases", box=box.ROUNDED)
    table.add_column("#", justify="right")
    table.add_column("Registry ID")
    table.add_column("Question")
    for key, seed in list_usecases():
        table.add_row(key, seed.id, seed.natural_request)
    output_console.print(table)
    return typer.prompt("Which registry should run", default="2")


def _json_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _active_ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": record["artifact_id"],
        "payload_sha256": record["payload_sha256"],
        "run_id": record.get("run_id"),
    }


def load_active_set(registry_id: str) -> dict[str, Any]:
    return {
        "r1": r1_profile_store.load_active(registry_id),
        "da1": da1_profile_store.load_active(registry_id),
        "r2": r2_profile_store.load_active(registry_id),
        "cp1": cp1_profile_store.load_active(registry_id),
        "d1": d1_profile_store.load_active(registry_id),
        "d2": d2_profile_store.load_active(registry_id),
        "ts": topology_selector_store.load_active(registry_id),
        "d3": d3_profile_store.load_active(registry_id),
        "p1": p1_profile_store.load_active(registry_id),
        "p2": p2_profile_store.load_active(registry_id),
        "p3": p3_profile_store.load_active(registry_id),
        "s1": s1_profile_store.load_active(registry_id),
        "c1": c1_profile_store.load_active(registry_id),
        "c2": c2_profile_store.load_active(registry_id),
        "f1": f1_profile_store.load_active(registry_id),
        # Advisory only -- never part of active_set_ready's chain-integrity check.
        "ex1": ex_profile_store.load_active("EX1", registry_id),
        "ex2": ex_profile_store.load_active("EX2", registry_id),
    }


def active_set_ready(active: dict[str, Any]) -> bool:
    r1, da1, r2, cp1, d1, d2, ts = (
        active["r1"], active["da1"], active["r2"],
        active.get("cp1"), active.get("d1"), active.get("d2"),
        active.get("ts"),
    )
    if not all((r1, da1, r2, cp1, d1, d2, ts)):
        return False
    da1_refs = da1.get("input_refs", {})
    r2_refs = r2.get("input_refs", {})
    d1_refs = d1.get("input_refs", {})
    d2_refs = d2.get("input_refs", {})
    cp1_refs = cp1.get("input_refs", {})
    ts_refs = ts.get("input_refs", {})
    return (
        da1_refs.get("r1") == _active_ref(r1)
        and r2_refs.get("r1") == _active_ref(r1)
        and r2_refs.get("da1") == _active_ref(da1)
        and d1_refs.get("da1") == _active_ref(da1)
        and d1_refs.get("r2") == _active_ref(r2)
        and d2_refs.get("d1") == _active_ref(d1)
        and d2_refs.get("r2") == _active_ref(r2)
        and cp1_refs.get("r1") == _active_ref(r1)
        and cp1_refs.get("da1") == _active_ref(da1)
        and cp1_refs.get("r2") == _active_ref(r2)
        and cp1_refs.get("d1") == _active_ref(d1)
        and cp1_refs.get("d2") == _active_ref(d2)
        and ts_refs.get("cp1") == _active_ref(cp1)
    )


def snapshot_active_set(registry_id: str, run_id: str) -> None:
    r1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    da1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    r2_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    cp1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    d1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    d2_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    topology_selector_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    d3_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    p1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    p2_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    p3_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    s1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    c1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    c2_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    f1_profile_store.snapshot_active(
        registry_id, reason="refresh_snapshot", run_id=run_id
    )
    for stage_id in ("EX1", "EX2"):
        ex_profile_store.snapshot_active(
            stage_id, registry_id, reason="refresh_snapshot", run_id=run_id
        )


# ── UC4 document pipeline shelf helpers ─────────────────────────────────────
# Parallel to load_active_set/active_set_ready/snapshot_active_set above, but
# scoped to the document family's own agents (RC1.1/RC1.2/RM1.1/RM1.2/P4/P5/
# P6/F2) plus the reused CP1/TS1. Kept separate rather than folded into the
# tabular functions so UC1-3 behaviour and shelf keys are untouched by this
# addition.

def load_document_active_set(registry_id: str) -> dict[str, Any]:
    return {
        "rc1_1": rc1_1_profile_store.load_active(registry_id),
        "rc1_2": rc1_profile_store.load_active(registry_id),
        "rm1_1": rm1_1_profile_store.load_active(registry_id),
        "rm1_2": rm1_profile_store.load_active(registry_id),
        "rd3": rd3_profile_store.load_active(registry_id),
        "cp1": cp1_profile_store.load_active(registry_id),
        "ts": topology_selector_store.load_active(registry_id),
        "p4": persona_profile_store.load_active("P4", registry_id),
        "p5": persona_profile_store.load_active("P5", registry_id),
        "p6": persona_profile_store.load_active("P6", registry_id),
        "f2": f2_profile_store.load_active(registry_id),
        # Advisory only -- never part of document_active_set_ready's check.
        "ex1": ex_profile_store.load_active("EX1", registry_id),
        "ex2": ex_profile_store.load_active("EX2", registry_id),
    }


def document_active_set_ready(active: dict[str, Any]) -> bool:
    rc1_1, rc1_2, rm1_1, rm1_2, rd3, cp1, ts = (
        active.get("rc1_1"), active.get("rc1_2"), active.get("rm1_1"), active.get("rm1_2"),
        active.get("rd3"), active.get("cp1"), active.get("ts"),
    )
    if not all((rc1_1, rc1_2, rm1_1, rm1_2, rd3, cp1, ts)):
        return False
    rc1_2_refs = rc1_2.get("input_refs", {})
    rm1_2_refs = rm1_2.get("input_refs", {})
    rd3_refs = rd3.get("input_refs", {})
    cp1_refs = cp1.get("input_refs", {})
    ts_refs = ts.get("input_refs", {})
    return (
        rc1_2_refs.get("rc1_1") == _active_ref(rc1_1)
        and rm1_2_refs.get("rm1_1") == _active_ref(rm1_1)
        and rd3_refs.get("rc1_2") == _active_ref(rc1_2)
        and rd3_refs.get("rm1_2") == _active_ref(rm1_2)
        and cp1_refs.get("rd3") == _active_ref(rd3)
        and ts_refs.get("cp1") == _active_ref(cp1)
    )


def snapshot_document_active_set(registry_id: str, run_id: str) -> None:
    rc1_1_profile_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    rc1_profile_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    rm1_1_profile_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    rm1_profile_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    rd3_profile_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    cp1_profile_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    topology_selector_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    for character_id in ("P4", "P5", "P6"):
        persona_profile_store.snapshot_active(character_id, registry_id, reason="refresh_snapshot", run_id=run_id)
    f2_profile_store.snapshot_active(registry_id, reason="refresh_snapshot", run_id=run_id)
    for stage_id in ("EX1", "EX2"):
        ex_profile_store.snapshot_active(stage_id, registry_id, reason="refresh_snapshot", run_id=run_id)


def load_document_corpus_artifacts(
    registry_id: str, run_dir_name: str | None = None
) -> dict[str, Any]:
    """RD1 + RD2 for one use case, from a specific run or the newest owning run.

    RD1/RD2 are deterministic pre-agent stages: they write plain JSON to the
    run dir (see build_document_pipeline), not to a shelf, so there is no
    envelope to load through a *_profile_store, and the JSON itself carries no
    registry id (keyed only by standard). RD2's per-candidate `hints` (the
    amendment-note corpus slice the scorer read, ~3.8 MB total) are dropped
    here -- the reader-facing decision is (basis, score); hint counts and a
    short preview are kept so the drill-down stays honest without
    re-embedding the full text.

    If `run_dir_name` is given by a programmatic caller, that directory
    is read directly -- the caller already knows which run it means. Otherwise
    this scans PUBLIC_EXPERIMENTS_DIR newest-first and returns the first run
    that (a) belongs to `registry_id`, per its README's `Run name`/`Purpose`
    line (see config.create_experiment_dir), and (b) has RD2 output -- multiple
    document use cases can share this directory, and a run can abort before
    RD2 ever writes.
    """
    from collections import Counter

    def _read(run_dir: Path) -> dict[str, Any] | None:
        rd2_path = run_dir / "run_001" / "rd2_candidates.json"
        rd1_path = run_dir / "run_001" / "rd1_corpus_summary.json"
        if not rd2_path.exists():
            return None
        rd1 = json.loads(rd1_path.read_text(encoding="utf-8")) if rd1_path.exists() else {}
        rd2_raw = json.loads(rd2_path.read_text(encoding="utf-8"))
        rd2 = {
            standard: [
                {
                    "standard": c.get("standard"),
                    "old_dr_ids": c.get("old_dr_ids", []),
                    "new_dr_ids": c.get("new_dr_ids", []),
                    "basis": c.get("basis"),
                    "score": c.get("score"),
                    "n_hints": len(c.get("hints", [])),
                    "hint_token_counts": dict(Counter(h.get("token") for h in c.get("hints", []))),
                    "hint_preview": [
                        {
                            "token": h.get("token"),
                            "text": (h.get("text") or "")[:320],
                            "authoritative": h.get("authoritative", False),
                        }
                        for h in c.get("hints", [])[:3]
                    ],
                }
                for c in candidates
            ]
            for standard, candidates in rd2_raw.items()
        }
        return {
            "run_dir": run_dir.name,
            "rd1": rd1,
            "rd2": rd2,
            "confidence_floor": candidate_mapper._CONFIDENT_SCORE,
            "next_best_band": candidate_mapper._CONFIDENT_GAP,
        }

    if run_dir_name:
        result = _read(config.PUBLIC_EXPERIMENTS_DIR / run_dir_name)
        if result is not None:
            return result

    run_dirs = sorted(
        (d for d in config.PUBLIC_EXPERIMENTS_DIR.iterdir() if d.is_dir()),
        reverse=True,
    )
    for run_dir in run_dirs:
        if not _run_owns_registry(run_dir, registry_id):
            continue
        result = _read(run_dir)
        if result is not None:
            return result
    return {"run_dir": None, "rd1": {}, "rd2": {}, "confidence_floor": None, "next_best_band": None}


def _experiments_root_for(family: str) -> Path:
    """The experiment root a use case's runs live under, per the Data Convention
    (config.create_experiment_dir's `visibility` routing, read side)."""
    return config.PUBLIC_EXPERIMENTS_DIR if family == "document" else config.EXPERIMENTS_DIR


def _run_owns_registry(run_dir: Path, registry_id: str) -> bool:
    """True if this run directory's README names `registry_id` (CLI runs put it
    in the `Purpose:` line, web runs in the `Run name:` line -- both are plain
    substring matches, see config.create_experiment_dir)."""
    readme = run_dir / "README.md"
    if not readme.exists():
        return False
    return registry_id in readme.read_text(encoding="utf-8")


def _run_snapshot_file(family: str) -> str:
    return "active_document_set.json" if family == "document" else "active_profile_set.json"


_TERMINAL_AGENT = {"tabular": "f1", "document": "f2"}


def _own_run_records(snapshot: dict[str, Any], run_dir_name: str) -> dict[str, Any]:
    """Only the agent records this run actually produced (`run_id` matches the
    run directory), dropping records the shelf carried over from an earlier
    run -- the snapshot is a live-shelf dump on most write paths, so it can
    contain agents this run never touched."""
    return {
        key: record
        for key, record in snapshot.items()
        if isinstance(record, dict) and record.get("run_id") == run_dir_name
    }


_PRE_TOPOLOGY_AGENTS = {"R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3"}


def _checked_refs(agent_id: str, refs: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Same-run consumption (AGENTS.md / R25). Gate-final and stage-2 agents may
    only consume inputs produced by their own run; pre-topology agents are
    resumable by design and are exempt (their carry-over is already surfaced
    by _scoped_snapshot). Only entries shaped like an upstream shelf ref
    (carrying "artifact_id", per _active_ref()/the EX1/EX2 candidate variant)
    are checked -- non-artifact entries such as a registry demand snapshot
    have no run_id of their own and are not upstream shelf consumption. Refs
    in "candidate" state (EX1/EX2's gate-payload variant, which has no
    upstream run_id of its own) are exempt too. Any other ref whose run_id
    does not match this run is a same-run consumption violation -- hard fail
    rather than silently proceeding."""
    if agent_id in _PRE_TOPOLOGY_AGENTS:
        return refs
    for key, ref in refs.items():
        if not isinstance(ref, dict) or "artifact_id" not in ref or ref.get("state") == "candidate":
            continue
        ref_run_id = ref.get("run_id")
        if ref_run_id != run_id:
            raise RuntimeError(
                f"R25 same-run consumption violation: {agent_id} tried to consume "
                f"input_refs['{key}'] from run '{ref_run_id}', but {agent_id} is "
                f"running under run '{run_id}'."
            )
    return refs


def _scoped_snapshot(active: dict[str, Any], run_id: str) -> tuple[dict[str, Any], list[str]]:
    """Scope a freshly-loaded shelf dump down to what this run actually produced,
    before it is persisted as active_profile_set.json / active_document_set.json.

    Pre-topology agents (R1..D3) are resumable across runs by design, so a
    foreign run_id there is kept but marked `carried_over` for audit. Stage-2
    topology agents (P1/P2/P3/S1/C1/C2/F1, P4/P5/P6/F2) and EX1/EX2 must belong
    to this run -- a foreign run_id there means the shelf is a stale leftover
    from an earlier run, and the record is dropped rather than persisted as if
    this run produced it. See _own_run_records, which implements the same
    run_id check on the read side."""
    scoped: dict[str, Any] = {}
    dropped: list[str] = []
    for key, record in active.items():
        if not isinstance(record, dict) or record.get("run_id") == run_id:
            scoped[key] = record
            continue
        agent_id = record.get("agent_id", key.upper())
        if agent_id in _PRE_TOPOLOGY_AGENTS:
            scoped[key] = {**record, "carried_over": True, "carried_from_run_id": record.get("run_id")}
        else:
            dropped.append(agent_id)
    return scoped, dropped


def _write_run_snapshot(run_dir: Path, active: dict[str, Any], run_id: str, family: str) -> dict[str, Any]:
    """Single choke point for writing active_profile_set.json / active_document_set.json.
    Scopes the raw shelf dump to this run (see _scoped_snapshot) so the persisted
    artifact reflects what this run produced, not whatever every agent's shelf
    happens to hold across all past runs of this registry."""
    scoped, dropped = _scoped_snapshot(active, run_id)
    if dropped:
        console.print(
            Panel(
                f"Stale shelf record(s) from an earlier run were excluded: {', '.join(dropped)}.",
                title="Stale shelf records dropped",
                border_style="warn",
            )
        )
    _write_json(run_dir / _run_snapshot_file(family), scoped)
    return scoped


def _snapshot_is_end_to_end(snapshot: dict[str, Any], run_dir_name: str, family: str) -> bool:
    """True only if the terminal agent (f1/f2) is present and was produced by
    this run, not inherited from a stale shelf entry. File existence alone is
    not a completion signal -- the snapshot is written on human-rejection and
    post-gate exception paths too."""
    terminal = _TERMINAL_AGENT.get(family)
    if terminal is None:
        return False
    record = snapshot.get(terminal)
    return isinstance(record, dict) and record.get("run_id") == run_dir_name


def list_runs_for_registry(registry_id: str, family: str) -> list[dict[str, Any]]:
    """Every run directory owned by `registry_id`, newest first.

    A run is `complete` only if its terminal agent record (f1 for tabular, f2
    for document) is present in the snapshot *and* that record's `run_id`
    matches this run directory -- plain file existence is not enough, since
    the snapshot is a live-shelf dump written on human-rejection and
    post-gate exception paths too, and can carry a terminal record inherited
    from an earlier, unrelated run. Directory names are a sortable UTC
    timestamp (config.create_experiment_dir), so name-sort reverse=True is
    newest-first with no parsing needed for ordering.
    """
    root = _experiments_root_for(family)
    if not root.exists():
        return []
    snapshot_name = _run_snapshot_file(family)
    run_dirs = sorted((d for d in root.iterdir() if d.is_dir()), reverse=True)
    runs = []
    for run_dir in run_dirs:
        if not _run_owns_registry(run_dir, registry_id):
            continue
        snapshot_path = run_dir / "run_001" / snapshot_name
        snapshot: dict[str, Any] = {}
        if snapshot_path.exists():
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                snapshot = {}
        own_records = _own_run_records(snapshot, run_dir.name)
        runs.append({
            "run_dir": run_dir.name,
            "started_at": _parse_run_started_at(run_dir.name),
            "complete": _snapshot_is_end_to_end(snapshot, run_dir.name, family),
            "agent_count": len(own_records),
        })
    return runs


def _parse_run_started_at(run_dir_name: str) -> str | None:
    """Parse the leading `YYYY-MM-DD_HHMMSSZ` timestamp off a run directory
    name (config.create_experiment_dir's naming scheme) into ISO-8601 UTC."""
    try:
        stamp = datetime.strptime(run_dir_name[:18], "%Y-%m-%d_%H%M%SZ")
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc).isoformat()


def load_run_active_set(
    registry_id: str, family: str, run_dir_name: str | None = None
) -> dict[str, Any] | None:
    """The frozen active-set snapshot for one run -- same shape as
    load_active_set/load_document_active_set, just read from a dated run
    directory instead of the live shelves.

    If `run_dir_name` is omitted, the newest *complete* run owned by
    `registry_id` is used. Returns None if no such run/snapshot exists.
    """
    root = _experiments_root_for(family)
    snapshot_name = _run_snapshot_file(family)

    if run_dir_name:
        candidates = [run_dir_name]
    else:
        candidates = [r["run_dir"] for r in list_runs_for_registry(registry_id, family) if r["complete"]]

    for run_dir in candidates:
        snapshot_path = root / run_dir / "run_001" / snapshot_name
        if snapshot_path.exists():
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            return _own_run_records(snapshot, run_dir)
    return None


def load_document_provision_texts(registry_id: str) -> dict[str, Any]:
    """Full 2025/2026 disclosure-requirement paragraph text, keyed for lookup.

    None of RC1.1/RC1.2/RD3/F2 carry the actual paragraph text downstream --
    only filename+paragraph-number references (old_paragraph_refs,
    new_paragraph_refs, citations). The real text lives only in RD1's
    in-memory CorpusBundle and is normally discarded once RD1's summary is
    written. extract_corpus() is deterministic and reads straight from
    UsecaseSeed.document_sources, independent of any run, so this re-runs it
    on demand for programmatic export (a citation reference is sufficient for
    CLI review; an export may request the quoted text itself).
    """
    seed = get(registry_id)
    if seed is None or seed.document_sources is None:
        return {}
    bundle = extract_corpus(seed.document_sources.model_dump(), config.PROJECT_ROOT)

    def _dr_map(drs: list[Any]) -> dict[str, dict[str, str]]:
        return {
            dr.dr_id: {p.paragraph_id: p.text for p in [*dr.paragraphs, *dr.ar_paragraphs]}
            for dr in drs
        }

    return {
        standard: {"old": _dr_map(sc.old_drs), "new": _dr_map(sc.new_drs)}
        for standard, sc in bundle.standards.items()
    }


def _render_legend(
    title: str, entries: dict[str, str], output_console: Console = console
) -> None:
    """Print a small Concept / plain-language-meaning legend table.

    Used by every agent's render_* function to disclose, in non-technical
    1-2 sentence language, what each distinct status/category/signal it
    shows actually means (AGENTS.md HARD RULE: plain-language disclosure).
    """
    table = Table(title=title, box=box.SIMPLE, show_header=False)
    table.add_column("Concept", style="bold")
    table.add_column("What it means")
    for concept, desc in entries.items():
        table.add_row(concept, desc)
    output_console.print(table)


def _render_llm_backend(output_console: Console = console) -> None:
    """Print a one-line panel showing the active LLM backend.

    Surfaces backend name, kind, model, and (for Azure OpenAI reasoning models)
    the reasoning_effort so stress-test runs are self-documenting in the log.
    """
    ab = config.ACTIVE_BACKEND
    parts = [f"[bold]{ab.name}[/bold]", ab.kind, ab.model]
    if ab.reasoning_effort:
        parts.append(f"effort={ab.reasoning_effort}")
    if ab.api_version:
        parts.append(f"api={ab.api_version}")
    label = " · ".join(parts)
    output_console.print(
        Panel(label, title="LLM Backend", border_style="dim", expand=False)
    )


def render_registry(seed: UsecaseSeed, output_console: Console = console) -> None:
    body = Table.grid(padding=(0, 2))
    body.add_column(style="bold", no_wrap=True)
    body.add_column()
    body.add_row("Registry ID", seed.id)
    body.add_row("Question", seed.natural_request)
    body.add_row("Regulation", json.dumps(seed.regulation_sources, ensure_ascii=False))
    body.add_row("Scope", json.dumps(seed.site_filter, ensure_ascii=False))
    body.add_row("Time", f"{seed.time_window[0]} -> {seed.time_window[1]}")
    body.add_row("Expected output", seed.expected_output)
    output_console.print(Panel(body, title="Registry Input", border_style="accent"))


R1_LEGEND = {
    "Possible Fields": (
        "Data points R1 thinks might be needed to answer the registry's "
        "question, based on the regulation text. These are candidates only "
        "and have not yet been checked against the actual data catalog."
    ),
    "core / related priority": (
        "'core' fields are directly needed to answer the question; "
        "'related' fields add useful supporting context but are not "
        "strictly required."
    ),
}


def render_r1(profile: dict[str, Any], output_console: Console = console) -> None:
    output_console.print(
        Panel(
            profile.get("summary", ""),
            title=f"R1 | {profile.get('verdict', '').upper()}",
            border_style="success" if profile.get("verdict") == "grounded" else "warn",
        )
    )
    table = Table(title="R1 Possible Fields", box=box.ROUNDED)
    table.add_column("ID")
    table.add_column("Field")
    table.add_column("Role")
    table.add_column("Priority")
    table.add_column("Why")
    for field in profile.get("possible_fields", []):
        table.add_row(
            field["field_id"],
            field["name"],
            field["role"],
            field["priority"],
            field["reason"],
        )
    output_console.print(table)
    _render_legend("R1 — what this means", R1_LEGEND, output_console)


DA1_LEGEND = {
    "mapped": (
        "There is a defensible catalog target for this field -- a real "
        "column or measure that can stand in for it."
    ),
    "unmapped": (
        "No catalog target exists for this field. This is a normal report "
        "result, not a pipeline failure -- some regulatory concepts simply "
        "are not present in the raw data."
    ),
}


def render_da1(report: dict[str, Any], output_console: Console = console) -> None:
    table = Table(title="DA1 Catalog Mapping Report", box=box.ROUNDED)
    table.add_column("ID")
    table.add_column("Field")
    table.add_column("Priority")
    table.add_column("Status")
    table.add_column("Catalog targets")
    table.add_column("Reason")
    for item in report.get("field_mappings", []):
        targets = ", ".join(
            f"{target['kind']}:{target['name']}"
            for target in item.get("catalog_targets", [])
        )
        table.add_row(
            item["field_id"],
            item["field_name"],
            item["priority"],
            item["status"],
            targets or "-",
            item.get("reason", ""),
        )
    output_console.print(table)
    _render_legend("DA1 — what this means", DA1_LEGEND, output_console)
    errors = report.get("validation", {}).get("errors", [])
    if errors:
        output_console.print(
            Panel("\n".join(f"- {item}" for item in errors), title="Structural Errors")
        )


R2_LEGEND = {
    "In-scope findings": (
        "Findings that can be safely assessed with the data fields the "
        "human reviewer approved. The system can produce a defensible "
        "result for these."
    ),
    "Blocked findings": (
        "Findings the registry still asks about, but the approved data "
        "does not include the operational evidence needed to assess them. "
        "The request stays valid -- it just cannot be answered right now."
    ),
    "Excluded findings": (
        "Findings that were deliberately left outside the approved scope "
        "from the start. These were never expected to be answered, so a "
        "lack of evidence is not the reason they are missing."
    ),
}


def render_r2(profile: dict[str, Any], output_console: Console = console) -> None:
    has_issues = bool(
        profile.get("blocked_findings") or profile.get("excluded_findings")
    )
    output_console.print(
        Panel(
            profile.get("summary", ""),
            title="R2 | SCOPED REGULATION PROFILE",
            border_style="warn" if has_issues else "success",
        )
    )
    table = Table(title="R2 In-Scope Findings", box=box.ROUNDED)
    table.add_column("ID")
    table.add_column("R1 refs")
    table.add_column("Approved fields")
    table.add_column("Scoped statement")
    table.add_column("Assessment boundary")
    for item in profile.get("in_scope_findings", []):
        table.add_row(
            item["r2_finding_id"],
            ", ".join(item.get("r1_finding_refs", [])),
            ", ".join(item.get("approved_field_ids", [])),
            item["statement"],
            item["assessment_boundary"],
        )
    output_console.print(table)
    _render_legend("R2 — what this means", R2_LEGEND, output_console)

    for key, title in (
        ("blocked_findings", "R2 BLOCKED FINDINGS - HUMAN ACKNOWLEDGEMENT REQUIRED"),
        ("excluded_findings", "R2 EXCLUDED FINDINGS - HUMAN ACKNOWLEDGEMENT REQUIRED"),
    ):
        items = profile.get(key, [])
        if items:
            lines = [
                f"{item['r1_finding_id']}: {item['reason']}"
                for item in items
            ]
            output_console.print(
                Panel(
                    "\n".join(lines),
                    title=title,
                    border_style="yellow",
                )
            )


CP1_SIGNAL_DESCRIPTIONS = {
    "task_type": (
        "What kind of job this is (e.g. ongoing monitoring vs. a one-time "
        "compliance check). This shapes which steps and checks are needed."
    ),
    "assurance_artifact": (
        "The type of evidence this output must carry. "
        "single_attestation: one owner, one evidence chain -- traceable to one "
        "responsible actor. "
        "reconciliation_record: the same claim read independently by two or more parties, "
        "with a documented comparison showing where they agree or disagree and how "
        "the difference was resolved. "
        "distributed_signoff: multiple domain owners each produce a sub-result "
        "attributed to their domain, assembled with multi-owner accountability. "
        "The topology selector uses this as a primary routing signal when "
        "scoring the topology library."
    ),
    "assurance_level": (
        "How much rigor the output needs to withstand -- from internal-only "
        "use up to direct legal/regulatory submission. Higher levels "
        "demand stricter verification."
    ),
    "dual_method_required": (
        "Whether the same result must be produced by two independent "
        "methods and compared, rather than computed once. False means a "
        "single calculation is considered sufficient. "
        "(Block A — supports assurance_artifact determination.)"
    ),
    "cluster_count": (
        "The minimum number of distinct expert domains that must each "
        "contribute independently to the final result -- not a count of "
        "tables, fields, or output figures. A domain counts only when a "
        "different organisational owner is required: one team cannot produce "
        "all sub-results without crossing an organisational boundary. "
        "When cluster_count >= 2, cross_functional_need must also be true "
        "(both signals encode the same multi-ownership fact). "
        "(Block A — supports assurance_artifact determination.)"
    ),
    "cross_functional_need": (
        "Whether multiple organizational owners must jointly sign off "
        "before this result can be closed. False means one team can "
        "decide alone. "
        "(Block A — supports assurance_artifact determination.)"
    ),
    "risk_level": (
        "How serious the consequences would be if a decision here turned "
        "out to be wrong or missed. High means a significant regulatory "
        "penalty or material financial loss is plausible. "
        "(Block B — escalation signal, does not change artifact type.)"
    ),
    "deadline_proximity_days": (
        "How many days remain until a regulatory deadline. This stays "
        "empty until a structured deadline date and a deterministic day "
        "calculation are available. "
        "(Block B — escalation signal.)"
    ),
    "task_type": (
        "A descriptive label for the kind of analytical task this case "
        "represents. For orientation only -- the assurance_artifact above "
        "is a stronger routing signal than this label. "
        "(Block C — context, non-routing.)"
    ),
    "uncertainty": (
        "How much ambiguity is left in the human-approved scope, after "
        "review. High means the approved findings still carry significant "
        "interpretive judgment. This does not by itself trigger independent "
        "verification -- TS1 weighs it with assurance_artifact and supporting "
        "signals. "
        "(Block C — context, non-routing.)"
    ),
    "output_profile": (
        "What kind of output the case must produce and in what form. "
        "Shapes the deliverable within the chosen topology; does not "
        "select the topology. CP1 confirms or refines the declared output "
        "type based on what the available data can support. "
        "(Block C — form-shaping only.)"
    ),
}


def render_cp1(profile: dict[str, Any], output_console: Console = console) -> None:
    sources = profile.get("signal_sources", {})

    # ── Primary panel: assurance artifact ──────────────────────────────────────
    artifact_val = profile.get("assurance_artifact") or "unknown"
    artifact_src = ", ".join(sources.get("assurance_artifact", [])) or "-"
    artifact_color = {
        "single_attestation": "green",
        "reconciliation_record": "yellow",
        "distributed_signoff": "red",
    }.get(artifact_val, "white")
    output_console.print(Panel(
        f"[bold {artifact_color}]{artifact_val}[/bold {artifact_color}]\n\n"
        f"Sources: {artifact_src}\n\n"
        f"{CP1_SIGNAL_DESCRIPTIONS.get('assurance_artifact', '')}",
        title="CP1 Required Assurance Artifact",
        border_style=artifact_color,
    ))

    # ── Block A: assurance evidence ────────────────────────────────────────────
    block_a_fields = [
        "assurance_level",
        "dual_method_required",
        "cluster_count",
        "cross_functional_need",
    ]
    table_a = Table(title="Block A — Assurance Evidence", box=box.ROUNDED)
    table_a.add_column("Signal")
    table_a.add_column("Value")
    table_a.add_column("Sources")
    table_a.add_column("What it means")
    for field in block_a_fields:
        value = profile.get(field)
        src = ", ".join(sources.get(field, [])) or "-"
        table_a.add_row(
            field,
            str(value) if value is not None else "null",
            src,
            CP1_SIGNAL_DESCRIPTIONS.get(field, "-"),
        )
    output_console.print(table_a)

    # ── Block B: escalation ────────────────────────────────────────────────────
    block_b_fields = [
        "risk_level",
        "deadline_proximity_days",
    ]
    table_b = Table(title="Block B — Escalation (orthogonal to artifact)", box=box.ROUNDED)
    table_b.add_column("Signal")
    table_b.add_column("Value")
    table_b.add_column("Sources")
    table_b.add_column("What it means")
    for field in block_b_fields:
        value = profile.get(field)
        src = ", ".join(sources.get(field, [])) or "-"
        table_b.add_row(
            field,
            str(value) if value is not None else "null",
            src,
            CP1_SIGNAL_DESCRIPTIONS.get(field, "-"),
        )
    output_console.print(table_b)

    # ── Block C: context (non-routing) ─────────────────────────────────────────
    block_c_fields = [
        "task_type",
        "uncertainty",
    ]
    table_c = Table(title="Block C — Context (non-routing)", box=box.ROUNDED)
    table_c.add_column("Signal")
    table_c.add_column("Value")
    table_c.add_column("Sources")
    table_c.add_column("What it means")
    for field in block_c_fields:
        value = profile.get(field)
        src = ", ".join(sources.get(field, [])) or "-"
        table_c.add_row(
            field,
            str(value) if value is not None else "null",
            src,
            CP1_SIGNAL_DESCRIPTIONS.get(field, "-"),
        )
    output_console.print(table_c)

    # output_profile — the 'o' in π(case, o, a); rendered separately so the
    # human can clearly see what they are approving as the selector's input.
    op = profile.get("output_profile") or {}
    op_src = ", ".join(sources.get("output_profile", [])) or "-"
    op_type = op.get("output_type", "-")
    op_form = op.get("form", "-")
    op_desc = op.get("description", "-")
    op_refined = any(
        s in ("d2", "r2", "d1") for s in sources.get("output_profile", [])
    )
    op_border = "yellow" if op_refined else "green"
    op_header = (
        "CP1 Output Profile (refined from declared demand)"
        if op_refined
        else "CP1 Output Profile (confirmed — matches declared demand)"
    )
    op_body = (
        f"[bold]Type:[/bold] {op_type}\n"
        f"[bold]Form:[/bold] {op_form}\n"
        f"[bold]Description:[/bold] {op_desc}\n"
        f"[bold]Sources:[/bold] {op_src}\n\n"
        f"{CP1_SIGNAL_DESCRIPTIONS.get('output_profile', '')}"
    )
    output_console.print(Panel(op_body, title=op_header, border_style=op_border))

    limitations = profile.get("limitations", [])
    if limitations:
        output_console.print(
            Panel(
                "\n".join(f"- {item}" for item in limitations),
                title="CP1 Limitations",
                border_style="yellow",
            )
        )


TS1_LEGEND = {
    "Topology scores": (
        "How confident the selector is in each topology, expressed as three "
        "integers that sum to 100.  The topology with the highest score is "
        "selected.  A spread-out distribution signals genuine uncertainty; a "
        "concentrated one signals a clear choice."
    ),
    "Direct": (
        "A sequential chain of steps, all under single ownership.  Each step "
        "builds on the previous one — the second step reads the first step's "
        "output, not an independent view of the same data.  The chain may have "
        "one step or several; the number is decided at runtime.  Used when the "
        "rule is clear and one team can own every step of the answer."
    ),
    "Debate": (
        "Two or more independent agents interpret the same scope separately and "
        "reconcile their views.  Used when the regulation is genuinely "
        "ambiguous and readers might reach different conclusions."
    ),
    "Coalition": (
        "Multiple domain specialists each contribute their own sub-result, "
        "then a coordinator assembles the final answer.  Used when the case "
        "spans multiple organisational or technical domains that cannot be "
        "owned by a single team."
    ),
    "Escalation": (
        "An overlay on top of the selected topology.  Flagged when the case "
        "is close to a regulatory threshold AND a deadline is imminent.  "
        "It does not change the topology — it signals that the pipeline should "
        "be expedited or given extra human attention."
    ),
}


def render_ts1(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render the TS1 topology selection in plain language for the human reviewer.

    Shows the confidence distribution, the selected topology, escalation status,
    rationale, and alternatives.  A plain-language legend follows so no opaque
    labels are left unexplained (AGENTS.md plain-language disclosure rule).
    """
    scores = payload.get("topology_scores", {})
    selected = payload.get("selected_topology_id", "-")
    rationale = payload.get("rationale", "-")
    alternatives = payload.get("alternatives", [])
    limitations = payload.get("limitations", [])

    # Scores table — three columns, one row
    scores_table = Table(title="TS1 Topology Confidence Distribution", box=box.ROUNDED)
    scores_table.add_column("Topology", style="bold")
    scores_table.add_column("Score / 100", justify="right")
    scores_table.add_column("Selected?", justify="center")
    for tid in ["Coalition", "Debate", "Direct"]:
        score = scores.get(tid, 0)
        is_selected = "✔ YES" if tid == selected else ""
        row_style = "bold" if tid == selected else ""
        scores_table.add_row(tid, str(score), is_selected, style=row_style)
    output_console.print(scores_table)

    # Rationale panel
    output_console.print(
        Panel(
            rationale,
            title=f"TS1 | SELECTED: {selected.upper()}",
            border_style="success",
        )
    )

    # Alternatives
    if alternatives:
        alt_lines = [
            f"[bold]{alt.get('topology_id', '?')}:[/bold] {alt.get('why_not', '')}"
            for alt in alternatives
        ]
        output_console.print(
            Panel(
                "\n".join(alt_lines),
                title="Alternatives — why not selected",
                border_style="muted",
            )
        )

    # Limitations
    if limitations:
        output_console.print(
            Panel(
                "\n".join(f"- {item}" for item in limitations),
                title="TS1 Limitations",
                border_style="yellow",
            )
        )

    _render_legend("TS1 — what this means", TS1_LEGEND, output_console)


D1_LEGEND = {
    "Data Product": (
        "What D1 actually fetched, following the approved data request -- "
        "row count, time range, measures. This is the raw foundation every "
        "later step builds on."
    ),
    "Quality policy": (
        "How suspect or incomplete records are handled (e.g. flagged only, "
        "or excluded). This determines which rows count toward the result."
    ),
}


def render_d1(payload: dict[str, Any], output_console: Console = console) -> None:
    summary = payload.get("summary", {})
    table = Table(title="D1 Data Product", box=box.ROUNDED)
    table.add_column("Property")
    table.add_column("Value")
    table.add_row("Source domain", summary.get("source_domain", "-"))
    table.add_row("Grain", summary.get("grain", "-"))
    table.add_row("Time range", f"{summary.get('time_range', ['', ''])[0]} → {summary.get('time_range', ['', ''])[1]}")
    table.add_row("Total rows", str(summary.get("total_rows", 0)))
    table.add_row("Tables", ", ".join(summary.get("tables", [])))
    table.add_row("Measures", ", ".join(summary.get("measures", [])))
    table.add_row("Quality policy", summary.get("quality_policy", "-"))
    output_console.print(table)
    _render_legend("D1 — what this means", D1_LEGEND, output_console)
    validation = summary.get("validation", {})
    if validation.get("warnings"):
        output_console.print(
            Panel(
                "\n".join(f"- {item}" for item in validation["warnings"]),
                title="D1 Warnings",
                border_style="yellow",
            )
        )


# ── UC4 document pipeline renders (AGENTS.md plain-language disclosure rule) ──


def render_rd1(bundle: "CorpusBundle", document_sources: dict[str, Any], output_console: Console = console) -> None:
    table = Table(title="RD1 Corpus Extraction", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("2025 DRs", justify="right")
    table.add_column("2026 DRs", justify="right")
    table.add_column("Hints", justify="right")
    for standard in document_sources["standards"]:
        sc = bundle.standards[standard]
        table.add_row(standard, str(len(sc.old_drs)), str(len(sc.new_drs)), str(len(sc.hints)))
    output_console.print(table)
    output_console.print(
        Panel(
            f"This step makes no decisions -- it turns the Markdown regulation and report "
            f"text into structured records. {len(bundle.report_sections)} Siemens report "
            f"section(s) and {len(bundle.index_candidates)} index candidate entries were "
            f"also extracted.",
            title="RD1",
            border_style="accent",
        )
    )


def render_rd2(bundle: "CorpusBundle", output_console: Console = console) -> None:
    table = Table(title="RD2 Deterministic Candidate Mapping", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("Confident match", justify="right")
    table.add_column("Ambiguous", justify="right")
    table.add_column("No 2026 counterpart", justify="right")
    table.add_column("No 2025 origin", justify="right")
    for standard, sc in bundle.standards.items():
        confident = sum(1 for c in sc.candidates if c.old_dr_ids and c.new_dr_ids and c.basis in ("title match", "merge candidate: multiple 2025 DRs match one 2026 DR title"))
        no_2026 = sum(1 for c in sc.candidates if c.old_dr_ids and not c.new_dr_ids)
        no_2025 = sum(1 for c in sc.candidates if c.new_dr_ids and not c.old_dr_ids)
        ambiguous = len(sc.candidates) - confident - no_2026 - no_2025
        table.add_row(standard, str(confident), str(ambiguous), str(no_2026), str(no_2025))
    output_console.print(table)
    output_console.print(
        Panel(
            "Ambiguous rows are not an error -- title similarity alone was not enough to "
            "pick one 2026 counterpart, so the decision is left to RC1, which reads the "
            "authoritative paragraph text. A confident match needs a title-similarity score "
            "of at least 0.45 with a 0.15 lead over the next-best candidate.",
            title="RD2",
            border_style="accent",
        )
    )


RC1_STATUS_LEGEND = {
    "Removed":             "No longer required under the 2026 standard.",
    "New":                 "Did not exist under the 2025 standard.",
    "Modified":            "Same requirement, but what must be disclosed changed.",
    "Merged":              "Two or more 2025 requirements combined into one 2026 requirement.",
    "Relocated":           "Moved to a different place in the standard, content unchanged.",
    "Renumbered":          "Same content, only the requirement's identifier changed.",
    "Retained":            "No material change.",
    "ApplicabilityChange": "The phase-in or applicability condition changed, not the substance.",
}


def render_rc1_1(payload: dict[str, Any], output_console: Console = console) -> None:
    rows = payload.get("rows", [])
    by_standard: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_standard.setdefault(row.get("standard", "-"), []).append(row)
    table = Table(title="RC1.1 Blind Change Matching", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("Matched", justify="right")
    table.add_column("Uncertain", justify="right")
    table.add_column("No counterpart", justify="right")
    for standard, srows in by_standard.items():
        matched = sum(1 for r in srows if r.get("old_dr_id") and r.get("new_dr_ids"))
        uncertain = sum(1 for r in srows if r.get("match_uncertain"))
        no_counterpart = sum(1 for r in srows if r.get("old_dr_id") and not r.get("new_dr_ids"))
        table.add_row(standard, str(matched), str(uncertain), str(no_counterpart))
    output_console.print(table)
    output_console.print(
        Panel(
            "This step never saw the deterministic title-matching suggestions -- it only read "
            "the two versions' full text and made its own matches.",
            title="RC1.1",
            border_style="accent",
        )
    )


def render_rc1(payload: dict[str, Any], output_console: Console = console) -> None:
    rows = payload.get("rows", [])
    table = Table(title="RC1.2 Regulatory Change Classification", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("2025 DR")
    table.add_column("2026 DR")
    table.add_column("Status")
    table.add_column("Uncertain?", justify="center")
    table.add_column("Conf.", justify="right")
    for row in rows:
        table.add_row(
            row.get("standard", "-"),
            ", ".join(row.get("old_dr_ids", [])) or "—",
            ", ".join(row.get("new_dr_ids", [])) or "—",
            row.get("change_status", "-"),
            "⚠" if row.get("mapping_uncertain") else "",
            f"{row.get('confidence', 0):.2f}",
        )
    output_console.print(table)
    _render_legend("RC1.2 — what each status means", RC1_STATUS_LEGEND, output_console)
    n_uncertain = sum(1 for r in rows if r.get("mapping_uncertain"))
    if n_uncertain:
        output_console.print(
            Panel(
                f"{n_uncertain} of {len(rows)} rows are flagged uncertain — the deterministic "
                f"title match did not clearly point to one 2026 counterpart, or RC1.2 could not "
                f"confidently resolve it from the authoritative text alone.",
                title="RC1.2 Uncertainty",
                border_style="yellow",
            )
        )
    n_rows = len(rows)
    n_blind_agree = sum(1 for r in rows if r.get("blind_agreement") == "agree")
    n_det_agree = sum(1 for r in rows if r.get("deterministic_agreement") == "agree")
    n_own_reading = sum(
        1 for r in rows if r.get("blind_agreement") != "agree" or r.get("deterministic_agreement") != "agree"
    )
    output_console.print(
        f"[dim]Agreement with deterministic candidates: {n_det_agree}/{n_rows}; "
        f"agreement with the blind pass (RC1.1): {n_blind_agree}/{n_rows}; "
        f"{n_own_reading} row(s) where RC1.2 preferred its own reading over at least one proposal.[/dim]"
    )


RM1_STATUS_LEGEND = {
    "reported":            "The report substantively addresses this requirement.",
    "partially_reported":  "Some but not all elements of the requirement are covered.",
    "omitted":             "Not addressed, and materiality is unclear.",
    "not_material":        "The double-materiality assessment found this topic non-material.",
    "not_applicable":      "The requirement does not apply to Siemens's situation.",
    "phase_in":            "Siemens invokes a phase-in / transitional provision here.",
    "unclear":             "Not enough evidence in the report to determine status.",
}


def render_rm1_1(payload: dict[str, Any], output_console: Console = console) -> None:
    rows = payload.get("rows", [])
    by_standard: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_standard.setdefault(row.get("standard", "-"), []).append(row)
    table = Table(title="RM1.1 Blind Exposure Matching", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("DRs seen", justify="right")
    table.add_column("With candidate section(s)", justify="right")
    table.add_column("Uncertain", justify="right")
    for standard, srows in by_standard.items():
        with_sections = sum(1 for r in srows if r.get("candidate_section_nos"))
        uncertain = sum(1 for r in srows if r.get("match_uncertain"))
        table.add_row(standard, str(len(srows)), str(with_sections), str(uncertain))
    output_console.print(table)
    output_console.print(
        Panel(
            "This step never saw RD2's deterministic section-index proposals -- it only read "
            "the old DR text and Siemens's report sections and proposed its own candidates.",
            title="RM1.1",
            border_style="accent",
        )
    )


def render_rm1(payload: dict[str, Any], output_console: Console = console) -> None:
    rows = payload.get("rows", [])
    table = Table(title="RM1.2 Siemens FY2025 Exposure", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("2025 DR")
    table.add_column("Reported status")
    table.add_column("Report section(s)")
    table.add_column("Conf.", justify="right")
    for row in rows:
        table.add_row(
            row.get("standard", "-"),
            row.get("dr_id", "-"),
            row.get("reported_status", "-"),
            ", ".join(row.get("report_section_refs", [])) or "—",
            f"{row.get('confidence', 0):.2f}",
        )
    output_console.print(table)
    _render_legend("RM1.2 — what each status means", RM1_STATUS_LEGEND, output_console)
    n_rows = len(rows)
    n_blind_agree = sum(1 for r in rows if r.get("blind_agreement") == "agree")
    n_index_agree = sum(1 for r in rows if r.get("index_agreement") == "agree")
    output_console.print(
        f"[dim]Agreement with deterministic index candidates: {n_index_agree}/{n_rows}; "
        f"agreement with the blind pass (RM1.1): {n_blind_agree}/{n_rows}. Low index "
        f"agreement is expected here -- Siemens's report was not written against RD2's "
        f"title-matching heuristics.[/dim]"
    )


ACTION_NEEDED_LEGEND = {
    "new_report_required":     "New 2026 requirement, no 2025 origin -- report from scratch.",
    "report_rewrite_required": "Siemens's existing report content has no place under 2026.",
    "report_update_required":  "Reported requirement whose 2026 content, number, or scope changed.",
    "status_review_required":  "The 2025 reporting status is omitted or unclear and must be reviewed first.",
    "no_action":               "Nothing to do -- unchanged and already handled, or not reported and not required.",
}


def render_rd3(payload: dict[str, Any], output_console: Console = console) -> None:
    rows = payload.get("rows", [])
    summary = payload.get("summary", {})
    table = Table(title=f"RD3 Change/Exposure Join ({len(rows)} rows)", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("2025 DR")
    table.add_column("2026 DR")
    table.add_column("Status")
    table.add_column("Reported")
    table.add_column("Orphan")
    table.add_column("Action needed")
    for row in rows:
        table.add_row(
            row.get("standard", "-"),
            row.get("old_dr_id", "") or "—",
            ", ".join(row.get("new_dr_ids", [])) or "—",
            row.get("change_status", "-") or "—",
            row.get("reported_status", "-") or "—",
            row.get("orphan_side", "none"),
            row.get("action_needed", "no_action"),
        )
    output_console.print(table)
    _render_legend("RD3 -- what each action means", ACTION_NEEDED_LEGEND, output_console)
    output_console.print(
        Panel(
            f"This is a deterministic full outer join of RC1.2 (2025<->2026) and RM1.2 "
            f"(2025<->Siemens) on the 2025 Disclosure Requirement id -- no LLM call. "
            f"{summary.get('n_orphan_2025', 0)} requirement(s) are brand new in 2026 "
            f"with no 2025 origin, {summary.get('n_orphan_2026', 0)} reported 2025 "
            f"requirement(s) have no 2026 counterpart. In this case an orphan finding "
            f"is a positive signal: it points at reporting content to strengthen or "
            f"newly produce, not a data gap.",
            title="RD3",
            border_style="accent",
        )
    )


EX_STANCE_LEGEND = {
    "supports":     "The institution's own material lines up with what this gate is about to approve.",
    "challenges":   "The institution's own material points the other way -- worth a second look.",
    "adds_context": "Neither for nor against -- useful background the gate payload does not carry.",
    "flags_gap":    "The institution's own material suggests something this gate payload may be missing.",
}
EX_GROUNDING_LEGEND = {
    "web_verified":    "EX found and cites a specific page at an allowed institution just now.",
    "model_knowledge": "EX could not confirm this via search this run -- treat as background, not fact.",
}
EX_STANCE_STYLE = {
    "challenges":   "red",
    "flags_gap":    "yellow",
    "supports":     "green",
    "adds_context": "blue",
}


def render_ex(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render an EX1/EX2 external-perspective commentary.

    EX is advisory only (AGENTS.md hard rule): its bullets never feed any
    downstream agent and never change the isolated memory. This renderer
    exists purely to inform the human at the gate that follows it.

    One panel per bullet, in EX's own order -- the pipeline does not
    re-rank or truncate EX's output (see EX1.md/EX2.md: EX writes bullets
    in its own order of importance).
    """
    stage_id = payload.get("stage_id", "EX")
    bullets = payload.get("bullets", [])
    output_console.print(
        Panel(
            f"{len(bullets)} external points, listed in EX's own order of "
            f"importance -- the ones it thinks you should weigh first come first.",
            title=f"{stage_id} — External Perspective",
            border_style="accent",
        )
    )
    for i, bullet in enumerate(bullets, start=1):
        stance = bullet.get("stance", "-")
        relevance = bullet.get("relevance", "-")
        grounding = bullet.get("grounding", "-")
        body = bullet.get("point", "")
        sources = bullet.get("sources", [])
        if sources:
            body += "\n"
            for s in sources:
                institution = s.get("institution", "-")
                published = s.get("published")
                suffix = f" ({published})" if published else ""
                body += f"\n[dim]{institution} — {s.get('title', '-')}{suffix}[/dim]"
                url = s.get("url")
                if url:
                    body += f"\n  [blue]{url}[/blue]"
        output_console.print(
            Panel(
                body,
                title=f"{stage_id} · {i}/{len(bullets)} · {stance} · {relevance} · {grounding}",
                border_style=EX_STANCE_STYLE.get(stance, "dim"),
            )
        )
    search_notes = payload.get("search_notes", [])
    if search_notes:
        output_console.print(
            Panel(
                "EX's own account of its search coverage -- why a bullet may be "
                "marked model_knowledge instead of web_verified:\n\n"
                + "\n".join(f"- {note}" for note in search_notes),
                title=f"{stage_id} — search coverage notes",
                border_style="dim",
            )
        )
    _render_legend(f"{stage_id} — what each stance means", EX_STANCE_LEGEND, output_console)
    _render_legend(f"{stage_id} — what each grounding means", EX_GROUNDING_LEGEND, output_console)
    institutions = ", ".join(payload.get("institutions_consulted", [])) or "none"
    output_console.print(
        Panel(
            f"Consulted: {institutions}.\n\n"
            f"This commentary is [bold]not binding[/bold]. It does not change the "
            f"approved regulatory or data boundary, and it is not consumed by any "
            f"downstream agent -- it exists only to inform the human decision at "
            f"the gate that follows.",
            title=f"{stage_id} — advisory, not a decision",
            border_style="warn",
        )
    )


EFFORT_LEGEND = {
    "none":   "No incremental reporting work -- already covered as-is.",
    "low":    "Small, quick change -- wording or a short addition.",
    "medium": "Real but bounded work -- a new paragraph or data pull.",
    "high":   "Substantial work -- a rewrite or new data collection.",
}


def render_persona(character_id: str, payload: dict[str, Any], output_console: Console = console) -> None:
    label = {"P4": "Conservative", "P5": "Balanced", "P6": "Maximum-Assurance"}.get(character_id, character_id)
    rows = payload.get("rows", [])
    table = Table(title=f"{character_id} — {label} Assessment ({len(rows)} findings)", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("2025→2026 DR")
    table.add_column("Recommended action")
    table.add_column("Effort")
    table.add_column("Why that effort")
    for row in rows[:20]:
        dr = ", ".join(row.get("old_dr_ids", [])) + " → " + ", ".join(row.get("new_dr_ids", []))
        table.add_row(
            row.get("standard", "-"), dr,
            (row.get("recommended_action", "") or "")[:70],
            row.get("effort_estimate", "-"),
            (row.get("effort_rationale", "") or "—")[:50],
        )
    if len(rows) > 20:
        table.caption = f"... and {len(rows) - 20} more rows (see the full shelf record)."
    output_console.print(table)
    _render_legend(f"{character_id} — what each effort level means", EFFORT_LEGEND, output_console)


STANDARD_SUMMARY_LEGEND = {
    "Change required?": "Yes if at least one DR under that standard needs action; No if every DR is no_action.",
    "Highest priority":  "The most urgent action_priority among that standard's action-required rows.",
}


def render_f2(payload: dict[str, Any], output_console: Console = console) -> None:
    rows = payload.get("rows", [])
    standard_summary = payload.get("standard_summary", [])
    if standard_summary:
        summary_table = Table(title="F2 Per-Standard Decision Summary", box=box.ROUNDED)
        summary_table.add_column("ESRS")
        summary_table.add_column("Change required?")
        summary_table.add_column("Affected DR", justify="right")
        summary_table.add_column("Total DR", justify="right")
        summary_table.add_column("Highest priority")
        for entry in standard_summary:
            summary_table.add_row(
                entry.get("standard", "-"),
                "Yes" if entry.get("change_required") else "No",
                str(entry.get("n_rows_action_required", 0)),
                str(entry.get("n_rows_total", 0)),
                entry.get("highest_priority", "") or "—",
            )
        output_console.print(summary_table)
        _render_legend("F2 summary — what each column means", STANDARD_SUMMARY_LEGEND, output_console)
    table = Table(title=f"F2 ESRS Impact Report ({len(rows)} rows)", box=box.ROUNDED)
    table.add_column("Std")
    table.add_column("2025 DR")
    table.add_column("2026 DR")
    table.add_column("Status")
    table.add_column("Siemens reported")
    table.add_column("Confidence", justify="right")
    table.add_column("Why")
    for row in rows:
        table.add_row(
            row.get("standard", "-"),
            row.get("dr_id_2025", "") or "—",
            row.get("dr_id_2026", "") or "—",
            row.get("change_status", "-"),
            row.get("siemens_reported", "") or "—",
            f"{row.get('confidence', 0):.2f}",
            (row.get("confidence_note", "") or "—")[:60],
        )
    output_console.print(table)
    summary = payload.get("executive_summary", [])
    if summary:
        output_console.print(
            Panel("\n".join(f"- {item}" for item in summary), title="Executive Summary", border_style="accent")
        )
    top_impacts = payload.get("top_impacts", [])
    if top_impacts:
        output_console.print(
            Panel("\n".join(f"- {item}" for item in top_impacts), title="Top Impacts", border_style="accent")
        )
    divergence = payload.get("persona_divergence", [])
    if divergence:
        output_console.print(
            Panel(
                "\n".join(f"- {item}" for item in divergence),
                title="Where the three assessors disagreed",
                border_style="yellow",
            )
        )
    limitations = payload.get("limitations", [])
    if limitations:
        output_console.print(
            Panel("\n".join(f"- {item}" for item in limitations), title="Limitations", border_style="yellow")
        )


D2_LEGEND = {
    "Verdict": (
        "Whether the observed data is enough to defend the approved "
        "claims: pass (fully defensible), warning (defensible with quality "
        "caveats), partial (some claims defensible, some not), or "
        "insufficient (no core claim is defensible)."
    ),
    "Routing": (
        "What the pipeline should do next based on the verdict -- proceed "
        "normally, increase assurance, restrict which claims are reported, "
        "or stop before producing a result."
    ),
    "Assessable claims": (
        "Claims that the available data can safely support."
    ),
    "Blocked claims": (
        "Claims the registry still asks about, but the available data "
        "cannot safely support -- the reason is always stated."
    ),
}


def render_d2(verdict: dict[str, Any], output_console: Console = console) -> None:
    routing = verdict.get("routing_recommendation", "-")
    v = verdict.get("verdict", "-")
    completeness = verdict.get("evidence_completeness")
    border = (
        "red" if routing == "stop"
        else "yellow" if routing in {"increase_assurance", "restrict_claims"}
        else "green"
    )
    table = Table(title="D2 Data Quality Preflight", box=box.ROUNDED)
    table.add_column("Property")
    table.add_column("Value")
    table.add_row("Verdict", v.upper())
    table.add_row("Routing", routing)
    table.add_row(
        "Evidence completeness",
        f"{completeness:.2f}" if completeness is not None else "-",
    )
    table.add_row(
        "Assessable claims",
        ", ".join(verdict.get("assessable_claims", [])) or "-",
    )
    output_console.print(table)
    _render_legend("D2 — what this means", D2_LEGEND, output_console)
    blocked = verdict.get("blocked_claims", [])
    if blocked:
        lines = [
            f"{bc.get('claim_id', '?')}: {bc.get('reason', '')}"
            for bc in blocked
            if isinstance(bc, dict)
        ]
        output_console.print(
            Panel(
                "\n".join(lines),
                title="D2 BLOCKED CLAIMS",
                border_style=border,
            )
        )
    summary = verdict.get("summary", "")
    if summary:
        output_console.print(
            Panel(summary, title=f"D2 | {v.upper()}", border_style=border)
        )


D3_LEGEND = {
    "Obligated": (
        "This site's three-year average energy use is at or above the regulatory "
        "threshold — it is legally required to meet this obligation."
    ),
    "Near-breach": (
        "This site's three-year average is within 5 % of the threshold — it is "
        "not yet obligated but is close enough to warrant early monitoring."
    ),
    "Compliant": (
        "This site's three-year average is clearly below the threshold — "
        "no obligation applies under this rule."
    ),
    "3-yr avg (MWh)": (
        "The mean of the site's annual energy consumption over the approved "
        "assessment window (typically three fiscal years)."
    ),
    "Gap (MWh)": (
        "Three-year average minus the threshold.  Negative values mean the site "
        "is below the threshold; positive values mean it is above."
    ),
}

D3_DUAL_LEGEND = {
    "Method A (energy-derived)": (
        "CO₂ equivalent computed by multiplying the site's energy consumption "
        "(MWh) by its grid emission factor.  Comes from the energy-consumption "
        "record — it is an indirect estimate."
    ),
    "Method B (reported)": (
        "CO₂ equivalent as directly reported by the site in the emissions "
        "record.  This is the site's own declared figure."
    ),
    "Δ (absolute)": (
        "The absolute difference between Method A and Method B for that site "
        "and year — how far apart the two figures are in tonnes CO₂e."
    ),
    "%Δ": (
        "The delta expressed as a percentage of the larger of the two values. "
        "A value above 5 % means the two methods disagree materially."
    ),
    "⚠ Flag": (
        "Shown when the percentage difference exceeds the 5 % materiality "
        "threshold.  A flagged site needs closer review before relying on "
        "either figure for regulatory reporting."
    ),
}

D3_CONSOLIDATION_LEGEND = {
    "Division sub-total (tCO₂e)": (
        "The total Scope 2 CO₂ equivalent for one business-unit division "
        "across all its EMEA sites for the given fiscal year.  Each division "
        "owns and produces its own figure independently."
    ),
    "Portfolio total (tCO₂e)": (
        "The sum of all division sub-totals — the consolidated group Scope 2 "
        "figure for the entire EMEA portfolio.  This is the number intended "
        "for inclusion in the ESRS E1 group disclosure."
    ),
    "Sites contributing": (
        "The number of distinct sites whose emissions records feed this "
        "division's sub-total.  A higher count means broader coverage."
    ),
    "Row count": (
        "The number of raw data rows aggregated for this division × year "
        "group.  Useful for auditing that the correct records were included."
    ),
}

_STATUS_STYLE = {
    "obligated": "red",
    "near_breach": "yellow",
    "compliant": "green",
}


def render_d3(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render the D3 computation result — dispatches on payload kind."""
    kind = payload.get("kind", "threshold")
    if kind == "dual_method":
        _render_d3_dual(payload, output_console)
    elif kind == "consolidation":
        _render_d3_consolidation(payload, output_console)
    else:
        _render_d3_threshold(payload, output_console)


def _render_d3_threshold(
    payload: dict[str, Any], output_console: Console = console
) -> None:
    """Render the threshold compliance table (kind='threshold')."""
    rows = payload.get("rows", [])
    registry_id = payload.get("registry_id", "-")
    site_count = payload.get("site_count", 0)
    n_obl = payload.get("n_obligated", 0)
    n_near = payload.get("n_near_breach", 0)
    n_comp = payload.get("n_compliant", 0)
    total = len(rows)

    if not rows:
        output_console.print(
            Panel(
                "No site computation results — energy data may be unavailable.",
                title="D3 Site Computation",
                border_style="yellow",
            )
        )
        return

    table = Table(
        title=f"D3 Site Compliance Computation | {registry_id}",
        box=box.ROUNDED,
    )
    table.add_column("Site", style="bold")
    table.add_column("Rule")
    table.add_column("3-yr avg (MWh)", justify="right")
    table.add_column("Threshold (MWh)", justify="right")
    table.add_column("Gap (MWh)", justify="right")
    table.add_column("Status")

    for row in rows:
        status = row.get("status", "-")
        style = _STATUS_STYLE.get(status, "")
        gap = row.get("gap", 0.0)
        gap_str = f"{gap:+,.1f}"
        table.add_row(
            row.get("location_name") or row.get("location_id", "-"),
            row.get("rule_label", row.get("rule_id", "-")),
            f"{row.get('avg_3yr', 0.0):,.1f}",
            f"{row.get('threshold', 0.0):,.1f}",
            gap_str,
            status.upper().replace("_", "-"),
            style=style,
        )

    output_console.print(table)
    _render_legend("D3 — what this means", D3_LEGEND, output_console)
    output_console.print(
        Panel(
            f"Sites assessed: {site_count}  |  "
            f"[red]Obligated: {n_obl}[/red]  "
            f"[yellow]Near-breach: {n_near}[/yellow]  "
            f"[green]Compliant: {n_comp}[/green]  "
            f"(total rule×site rows: {total})",
            title="D3 Summary",
            border_style="accent",
        )
    )


def _render_d3_dual(
    payload: dict[str, Any], output_console: Console = console
) -> None:
    """Render the dual-method CO₂e reconciliation table (kind='dual_method')."""
    rows = payload.get("rows", [])
    registry_id = payload.get("registry_id", "-")
    site_count = payload.get("site_count", 0)
    n_flagged = payload.get("n_flagged", 0)
    tolerance_pct = round(float(payload.get("tolerance", 0.05)) * 100, 1)

    if not rows:
        output_console.print(
            Panel(
                "No dual-method reconciliation results — emissions data may be "
                "unavailable or both method columns were absent.",
                title="D3 Dual-Method Reconciliation",
                border_style="yellow",
            )
        )
        return

    # Label columns from the first row (all rows share the same labels)
    label_a = rows[0].get("method_a_label", "Method A")
    label_b = rows[0].get("method_b_label", "Method B")

    table = Table(
        title=f"D3 Dual-Method CO₂e Reconciliation | {registry_id}",
        box=box.ROUNDED,
    )
    table.add_column("Site", style="bold")
    table.add_column("Year", justify="center")
    table.add_column(f"A: {label_a}", justify="right")
    table.add_column(f"B: {label_b}", justify="right")
    table.add_column("Δ (tCO₂e)", justify="right")
    table.add_column("%Δ", justify="right")
    table.add_column("⚠ Flag", justify="center")

    for row in rows:
        flagged = row.get("discrepancy_flag", False)
        flag_str = "[red]YES[/red]" if flagged else "[green]no[/green]"
        pct = float(row.get("pct_diff", 0.0)) * 100
        table.add_row(
            row.get("location_name") or row.get("location_id", "-"),
            str(row.get("year", "-")),
            f"{row.get('method_a', 0.0):,.1f}",
            f"{row.get('method_b', 0.0):,.1f}",
            f"{row.get('abs_delta', 0.0):,.1f}",
            f"{pct:.1f}%",
            flag_str,
            style="red" if flagged else "",
        )

    output_console.print(table)
    _render_legend("D3 — what this means", D3_DUAL_LEGEND, output_console)
    output_console.print(
        Panel(
            f"Sites assessed: {site_count}  |  "
            f"[red]Flagged (>{tolerance_pct}%): {n_flagged}[/red]  "
            f"(total site×year rows: {len(rows)})",
            title="D3 Dual-Method Summary",
            border_style="accent",
        )
    )


def _render_d3_consolidation(
    payload: dict[str, Any], output_console: Console = console
) -> None:
    """Render the division consolidation table (kind='consolidation')."""
    rows = payload.get("rows", [])
    registry_id = payload.get("registry_id", "-")
    portfolio_total_t = payload.get("portfolio_total_t", 0.0)
    division_count = payload.get("division_count", 0)
    site_count = payload.get("site_count", 0)
    measure_column = payload.get("measure_column", "scope2_location_t")

    if not rows:
        output_console.print(
            Panel(
                "No consolidation results — emissions data may be unavailable "
                "or the division grouping column was absent.",
                title="D3 Division Consolidation",
                border_style="yellow",
            )
        )
        return

    table = Table(
        title=f"D3 ESRS E1 Division Scope 2 Consolidation | {registry_id}",
        box=box.ROUNDED,
    )
    table.add_column("Division", style="bold")
    table.add_column("Year", justify="center")
    table.add_column(f"Sub-total ({measure_column}, tCO₂e)", justify="right")
    table.add_column("Sites contributing", justify="right")
    table.add_column("Row count", justify="right")

    for row in rows:
        table.add_row(
            str(row.get("division", "-")),
            str(row.get("year", "-")),
            f"{row.get('subtotal_t', 0.0):,.2f}",
            str(row.get("n_sites", 0)),
            str(row.get("n_rows", 0)),
        )

    output_console.print(table)
    _render_legend("D3 — what this means", D3_CONSOLIDATION_LEGEND, output_console)
    output_console.print(
        Panel(
            f"Divisions: {division_count}  |  "
            f"Sites total: {site_count}  |  "
            f"[bold]Portfolio total: {portfolio_total_t:,.2f} tCO₂e[/bold]  "
            f"(total division×year rows: {len(rows)})",
            title="D3 Consolidation Summary",
            border_style="accent",
        )
    )


P1_LEGEND = {
    "obligated": (
        "This site's average energy consumption exceeds the regulatory threshold.  "
        "It is subject to the energy-management obligation under the approved rule."
    ),
    "near_breach": (
        "This site's average energy consumption is below the threshold but within 5%% of it.  "
        "It is not currently obligated but is close enough to warrant monitoring.  "
        "A small increase in consumption would trigger the obligation."
    ),
    "compliant": (
        "This site's average energy consumption is below the threshold.  "
        "No energy-management obligation is triggered under the approved rule."
    ),
    "Location": (
        "Country, CDP regional grouping, and city sourced from the data (not inferred by P1).  "
        "The full street address and coordinates are also stored in the JSON artifact "
        "and used by P1 to ground its web-search queries to the specific town or district.  "
        "Empty when the underlying data does not include geographic attributes "
        "(for example, when D3 runs at division grain rather than site grain)."
    ),
    "Gap (MWh)": (
        "The difference between the site's measured energy average and the threshold.  "
        "A negative number means the site is below the threshold (good); "
        "a positive number means it is above (obligated)."
    ),
    "Interpretation": (
        "A plain-language explanation of what this site's threshold result means "
        "under the specific regulatory findings approved at the R2 gate.  "
        "P1 used a web search tool to ground location-specific and recent claims."
    ),
    "Carried caveats": (
        "Data or scope tensions that P1 noticed and passes forward to the next step.  "
        "Claims flagged as 'unverified / model-knowledge' could not be confirmed "
        "via web search and should be treated as indicative, not authoritative.  "
        "Other examples: fewer years of data than the regulation expects, or a "
        "mismatch between the approved threshold and the computation threshold."
    ),
}


def render_p1(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render the P1 regulatory interpretation in plain language.

    Shows the per-site interpretation table and any chain-level caveats.
    A legend follows so no opaque labels remain unexplained
    (AGENTS.md plain-language disclosure rule).
    """
    rows = payload.get("rows", [])
    registry_id = payload.get("registry_id", "-")
    chain_caveats = payload.get("carried_caveats", [])

    if not rows:
        output_console.print(
            Panel(
                "P1 produced no site interpretations -- D3 rows may be empty.",
                title="P1 Result Interpretation",
                border_style="yellow",
            )
        )
        return

    table = Table(
        title=f"P1 Regulatory Interpretation | {registry_id}",
        box=box.ROUNDED,
    )
    table.add_column("Site", style="bold")
    table.add_column("Location")
    table.add_column("Rule")
    table.add_column("Status")
    table.add_column("Gap (MWh)", justify="right")
    table.add_column("Interpretation")
    table.add_column("Site caveats")

    for row in rows:
        status = row.get("status", "-")
        style = _STATUS_STYLE.get(status, "")
        gap = row.get("gap", 0.0)
        gap_str = f"{gap:+,.1f}"
        caveats = row.get("carried_caveats", [])
        caveat_str = "; ".join(caveats) if caveats else "-"
        # Location column: country name / CDP region / city (most specific available)
        country_name = row.get("country_name", "")
        cdp_region = row.get("cdp_region", "")
        city = row.get("city", "")
        location_str = country_name or row.get("country", "")
        if cdp_region and cdp_region != location_str:
            location_str = f"{location_str} / {cdp_region}" if location_str else cdp_region
        if city:
            location_str = f"{location_str} / {city}" if location_str else city
        location_str = location_str or "-"
        table.add_row(
            f"{row.get('location_name', '-')} ({row.get('location_id', '-')})",
            location_str,
            row.get("rule_label", row.get("rule_id", "-")),
            status,
            gap_str,
            row.get("interpretation", "-"),
            caveat_str,
            style=style,
        )

    output_console.print(table)

    if chain_caveats:
        caveat_text = "\n".join(f"* {c}" for c in chain_caveats)
        output_console.print(
            Panel(
                caveat_text,
                title="P1 Chain-level caveats (carried to F1)",
                border_style="yellow",
            )
        )

    _render_legend("P1 -- what this means", P1_LEGEND, output_console)

    n_obl = sum(1 for r in rows if r.get("status") == "obligated")
    n_near = sum(1 for r in rows if r.get("status") == "near_breach")
    n_comp = sum(1 for r in rows if r.get("status") == "compliant")
    output_console.print(
        Panel(
            f"Sites interpreted: {len(rows)}  |  "
            f"[red]Obligated: {n_obl}[/red]  |  "
            f"[yellow]Near-breach: {n_near}[/yellow]  |  "
            f"[green]Compliant: {n_comp}[/green]  |  "
            f"Chain caveats: {len(chain_caveats)}",
            title="P1 Interpretation Summary",
            border_style="accent",
        )
    )


F1_ALERT_LEGEND = {
    "obligated": (
        "The site's average energy consumption exceeds the regulatory threshold.  "
        "The alert flag is set to YES — this site is a priority candidate under the approved rule."
    ),
    "near_breach": (
        "The site's average is below the threshold but within 5%% of it.  "
        "The alert flag is set to YES — the site warrants close monitoring; a small increase "
        "in consumption would trigger the full obligation."
    ),
    "compliant": (
        "The site's average energy consumption is clearly below the threshold.  "
        "The alert flag is set to NO — no obligation is triggered under the approved rule."
    ),
    "Alert flag": (
        "YES means the site is obligated or near-breach; NO means it is clearly compliant.  "
        "This is the binary output the use case requested."
    ),
    "Headline": (
        "A one-sentence plain-language summary of what this result means for the site and rule.  "
        "Written by F1 from P1's grounded interpretation."
    ),
    "Portfolio summary": (
        "Counts of distinct sites in each state across all rules.  "
        "The narrative gives the overall compliance picture in plain language."
    ),
    "Limitations": (
        "Scope and data caveats the reader must keep in mind.  "
        "Always includes the company-vs-site grain note: the statutory obligation rests on "
        "Siemens AG as a whole, not on individual sites; this screen is a planning instrument, "
        "not a strict legal determination."
    ),
    "Citations": (
        "The regulatory finding IDs and articles from the human-approved R2 boundary that "
        "were applied in producing this deliverable."
    ),
}


def _render_f1_alert(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render F1 yes_no_alert deliverable."""
    registry_id = payload.get("registry_id", "-")
    headline = payload.get("headline", "-")
    output_form = payload.get("output_form", "-")
    site_alerts = payload.get("site_alerts", [])
    portfolio = payload.get("portfolio_summary", {})
    limitations = payload.get("limitations", [])
    citations = payload.get("citations", [])

    # --- Portfolio headline ------------------------------------------------
    output_console.print(
        Panel(
            f"[bold]{headline}[/bold]\n[muted]Output form: {output_form}[/muted]",
            title=f"O2 Compliance Deliverable | {registry_id}",
            border_style="accent",
        )
    )

    if not site_alerts:
        output_console.print(
            Panel(
                "F1 produced no site alerts -- P1 rows may be empty.",
                title="O2 Site Alerts",
                border_style="yellow",
            )
        )
        return

    # --- Per-site alert table ---------------------------------------------
    table = Table(
        title="O2 Site Alerts",
        box=box.ROUNDED,
    )
    table.add_column("Site", style="bold")
    table.add_column("Location")
    table.add_column("Rule")
    table.add_column("Status")
    table.add_column("Alert")
    table.add_column("Gap (MWh)", justify="right")
    table.add_column("Headline")

    _ALERT_STYLE = {
        "obligated":  "red",
        "near_breach": "yellow",
        "compliant":  "green",
    }

    for row in site_alerts:
        status = row.get("status", "-")
        style = _ALERT_STYLE.get(status, "")
        alert = row.get("alert", False)
        alert_str = "[bold]YES[/bold]" if alert else "no"
        gap = row.get("gap", 0.0)
        gap_str = f"{gap:+,.1f}"
        location_display = row.get("location_display", "") or "-"
        table.add_row(
            f"{row.get('location_name', '-')} ({row.get('location_id', '-')})",
            location_display,
            row.get("rule_label", row.get("rule_id", "-")),
            status,
            alert_str,
            gap_str,
            row.get("headline", "-"),
            style=style,
        )

    output_console.print(table)

    # --- Portfolio summary ------------------------------------------------
    narrative = portfolio.get("narrative", "")
    n_sites = portfolio.get("n_sites", 0)
    n_obl = portfolio.get("n_obligated", 0)
    n_near = portfolio.get("n_near_breach", 0)
    n_comp = portfolio.get("n_compliant", 0)
    summary_lines = [
        f"Distinct sites: {n_sites}  |  "
        f"[red]Obligated: {n_obl}[/red]  |  "
        f"[yellow]Near-breach: {n_near}[/yellow]  |  "
        f"[green]Compliant: {n_comp}[/green]",
    ]
    if narrative:
        summary_lines.append(f"\n{narrative}")
    output_console.print(
        Panel(
            "\n".join(summary_lines),
            title="Portfolio Summary",
            border_style="accent",
        )
    )

    # --- Limitations -------------------------------------------------------
    if limitations:
        lim_text = "\n".join(f"* {lim}" for lim in limitations)
        output_console.print(
            Panel(
                lim_text,
                title="Limitations",
                border_style="yellow",
            )
        )

    # --- Citations ---------------------------------------------------------
    if citations:
        cite_text = "\n".join(f"* {c}" for c in citations)
        output_console.print(
            Panel(
                cite_text,
                title="Regulatory citations",
                border_style="muted",
            )
        )

    _render_legend("F1 (alert) -- what this means", F1_ALERT_LEGEND, output_console)


# ---------------------------------------------------------------------------
# P2 — Energy-Method Interpreter render
# ---------------------------------------------------------------------------

P2_LEGEND = {
    "method_figure": "The energy-derived Scope 2 value (Method A, Annex IV calculation).",
    "method_a / method_b": "Both method figures as carried from D3; method_a is the primary for P2.",
    "abs_delta": "Absolute difference between the two methods (tonne CO₂e). Computed by D3 — authoritative.",
    "pct_diff": "Percentage difference between methods relative to the larger figure. Computed by D3.",
    "discrepancy_flag": "True when pct_diff exceeds the 5% internal monitoring tolerance.",
    "interpretation": "P2's regulatory reading of this site×quarter under Method A (Annex IV).",
    "defensibility": "P2's assessment of how well this figure can be defended under audit.",
    "carried_caveats": "Unresolved uncertainties P2 flagged (e.g. unverifiable co2_factor).",
}


def render_p2(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render P2 Energy-Method interpretation rows."""
    rows = payload.get("rows", [])
    output_console.print(
        Panel(
            f"P2 — Energy-Method Interpreter: {len(rows)} site×quarter row(s) interpreted.",
            title="P2 complete",
            border_style="accent",
        )
    )
    if not rows:
        output_console.print("[dim]No rows.[/dim]")
        _render_legend("P2 -- what this means", P2_LEGEND, output_console)
        return

    table = Table(title="P2 — Energy-Method Rows", box=box.SIMPLE, show_lines=True)
    table.add_column("Site", no_wrap=True)
    table.add_column("Location", no_wrap=True)
    table.add_column("Quarter", no_wrap=True)
    table.add_column("Method A (t CO₂e)", justify="right")
    table.add_column("Δ%", justify="right")
    table.add_column("⚠ Flag", justify="center")
    table.add_column("Interpretation", max_width=50)
    table.add_column("Defensibility", max_width=20)
    table.add_column("Caveats", max_width=30)

    for r in rows:
        flag = "[red]YES[/red]" if r.get("discrepancy_flag") else "[green]no[/green]"
        pct = f"{r.get('pct_diff', 0.0):.1%}" if isinstance(r.get("pct_diff"), (int, float)) else "-"
        method_a = f"{r.get('method_a', r.get('method_figure', 0.0)):,.1f}"
        table.add_row(
            str(r.get("location_id", "")),
            str(r.get("location_name", "")),
            str(r.get("year", "")),
            method_a,
            pct,
            flag,
            str(r.get("interpretation", ""))[:200],
            str(r.get("defensibility", ""))[:100],
            "; ".join(r.get("carried_caveats", []))[:120],
        )
    output_console.print(table)
    _render_legend("P2 -- what this means", P2_LEGEND, output_console)


# ---------------------------------------------------------------------------
# P3 — Reported-Method Interpreter render
# ---------------------------------------------------------------------------

P3_LEGEND = {
    "method_figure": "The reported Scope 2 value (Method B, Art. 14(3) reporting obligation).",
    "method_a / method_b": "Both method figures as carried from D3; method_b is the primary for P3.",
    "abs_delta": "Absolute difference between the two methods (tonne CO₂e). Computed by D3 — authoritative.",
    "pct_diff": "Percentage difference between methods. Computed by D3 — authoritative.",
    "discrepancy_flag": "True when pct_diff exceeds the 5% internal monitoring tolerance.",
    "interpretation": "P3's regulatory reading of this site×quarter under Method B (Art. 14(3)).",
    "defensibility": "P3's assessment of how well this reported figure can be defended under audit.",
    "carried_caveats": "Unresolved uncertainties P3 flagged for the reported figure.",
}


def render_p3(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render P3 Reported-Method interpretation rows."""
    rows = payload.get("rows", [])
    output_console.print(
        Panel(
            f"P3 — Reported-Method Interpreter: {len(rows)} site×quarter row(s) interpreted.",
            title="P3 complete",
            border_style="accent",
        )
    )
    if not rows:
        output_console.print("[dim]No rows.[/dim]")
        _render_legend("P3 -- what this means", P3_LEGEND, output_console)
        return

    table = Table(title="P3 — Reported-Method Rows", box=box.SIMPLE, show_lines=True)
    table.add_column("Site", no_wrap=True)
    table.add_column("Location", no_wrap=True)
    table.add_column("Quarter", no_wrap=True)
    table.add_column("Method B (t CO₂e)", justify="right")
    table.add_column("Δ%", justify="right")
    table.add_column("⚠ Flag", justify="center")
    table.add_column("Interpretation", max_width=50)
    table.add_column("Defensibility", max_width=20)
    table.add_column("Caveats", max_width=30)

    for r in rows:
        flag = "[red]YES[/red]" if r.get("discrepancy_flag") else "[green]no[/green]"
        pct = f"{r.get('pct_diff', 0.0):.1%}" if isinstance(r.get("pct_diff"), (int, float)) else "-"
        method_b = f"{r.get('method_b', r.get('method_figure', 0.0)):,.1f}"
        table.add_row(
            str(r.get("location_id", "")),
            str(r.get("location_name", "")),
            str(r.get("year", "")),
            method_b,
            pct,
            flag,
            str(r.get("interpretation", ""))[:200],
            str(r.get("defensibility", ""))[:100],
            "; ".join(r.get("carried_caveats", []))[:120],
        )
    output_console.print(table)
    _render_legend("P3 -- what this means", P3_LEGEND, output_console)


# ---------------------------------------------------------------------------
# S1 — Reconciler render
# ---------------------------------------------------------------------------

S1_LEGEND = {
    "method_a / method_b": "Both Scope 2 figures; carried unchanged from D3 (deterministic engine).",
    "abs_delta": "Absolute difference (t CO₂e) between Method A and Method B — from D3, not re-derived here.",
    "pct_diff": "Percentage difference — from D3, not re-derived here.",
    "discrepancy_flag": (
        "Whether pct_diff exceeded the 5% internal tolerance. "
        "The 5% figure is a monitoring guideline (R2F-003), not a legally defined threshold."
    ),
    "materiality_verdict": "S1's plain-language judgement on whether the discrepancy is material for the reader.",
    "delta_interpretation": "S1's explanation of why the two methods differ for this site×quarter.",
    "reconciled_conclusion": "S1's final reconciled position combining both method reads.",
}


def render_s1(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render S1 synthciled per-site-per-quarter table."""
    rows = payload.get("rows", [])
    caveats = payload.get("carried_caveats", [])
    output_console.print(
        Panel(
            f"S1 — Reconciler: {len(rows)} row(s) reconciled.",
            title="S1 complete",
            border_style="accent",
        )
    )
    if not rows:
        output_console.print("[dim]No rows.[/dim]")
        _render_legend("S1 -- what this means", S1_LEGEND, output_console)
        return

    table = Table(title="S1 — Reconciled Site×Quarter", box=box.SIMPLE, show_lines=True)
    table.add_column("Site", no_wrap=True)
    table.add_column("Quarter", no_wrap=True)
    table.add_column("Method A", justify="right")
    table.add_column("Method B", justify="right")
    table.add_column("Δ (abs)", justify="right")
    table.add_column("Δ%", justify="right")
    table.add_column("Flag", justify="center")
    table.add_column("Materiality", max_width=20)
    table.add_column("Conclusion", max_width=50)

    for r in rows:
        flag = "[red]YES[/red]" if r.get("discrepancy_flag") else "[green]no[/green]"
        pct = f"{r.get('pct_diff', 0.0):.1%}" if isinstance(r.get("pct_diff"), (int, float)) else "-"
        abs_d = f"{r.get('abs_delta', 0.0):,.1f}" if isinstance(r.get("abs_delta"), (int, float)) else "-"
        ma = f"{r.get('method_a', 0.0):,.1f}" if isinstance(r.get("method_a"), (int, float)) else "-"
        mb = f"{r.get('method_b', 0.0):,.1f}" if isinstance(r.get("method_b"), (int, float)) else "-"
        table.add_row(
            str(r.get("location_name", r.get("location_id", ""))),
            str(r.get("year", "")),
            ma,
            mb,
            abs_d,
            pct,
            flag,
            str(r.get("materiality_verdict", ""))[:100],
            str(r.get("reconciled_conclusion", ""))[:200],
        )
    output_console.print(table)

    if caveats:
        output_console.print(
            Panel(
                "\n".join(f"• {c}" for c in caveats),
                title="S1 chain-level caveats",
                border_style="yellow",
            )
        )
    _render_legend("S1 -- what this means", S1_LEGEND, output_console)


# ---------------------------------------------------------------------------
# C1 / C2 — Coalition (UC3) renders
# ---------------------------------------------------------------------------

C1_LEGEND = {
    "division": "The in-scope division this row belongs to (e.g. DI, SMO).",
    "subtotal_t": "This division's Scope 2 sub-total (t CO₂e) — summed from D3's per-year figures, not re-derived.",
    "data_volume_status": "C1's own read: whether enough underlying rows back this division's figure -- 'adequate' or 'thin'.",
    "provenance_note": "C1's assessment of the evidence behind the sub-total — what the data_volume_status read is based on.",
}

C2_LEGEND = {
    "portfolio_total_t": "C2's own sum of every division's reported sub-total.",
    "deterministic_portfolio_total_t": "D3's independently-computed group total — a cross-check, not something C2 copies.",
    "thin_data_divisions": "Divisions whose C1 data_volume_status was 'thin' — carried through unchanged, never overruled.",
    "data_volume_coverage_note": "C2's plain-language statement of which divisions have adequate row coverage and which don't.",
    "readiness_assessment": "C2's read on FY2027 mandatory-reporting readiness given the data-volume picture above.",
}


def render_c1(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render C1's per-division data-volume reports."""
    rows = payload.get("rows", [])
    output_console.print(
        Panel(
            f"C1 — Divisional Attestor: {len(rows)} division(s) assessed independently.",
            title="C1 complete",
            border_style="accent",
        )
    )
    if not rows:
        output_console.print("[dim]No rows.[/dim]")
        _render_legend("C1 -- what this means", C1_LEGEND, output_console)
        return

    table = Table(title="C1 — Divisional Data-Volume Reports", box=box.SIMPLE, show_lines=True)
    table.add_column("Division", no_wrap=True)
    table.add_column("Sub-total (t)", justify="right")
    table.add_column("Years", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Narrative", max_width=60)

    for r in rows:
        status = r.get("data_volume_status", "")
        status_display = "[green]adequate[/green]" if status == "adequate" else "[red]thin[/red]"
        subtotal = f"{r.get('subtotal_t', 0.0):,.1f}" if isinstance(r.get("subtotal_t"), (int, float)) else "-"
        table.add_row(
            str(r.get("division", "")),
            subtotal,
            ", ".join(r.get("years", [])),
            status_display,
            str(r.get("narrative", ""))[:200],
        )
    output_console.print(table)
    _render_legend("C1 -- what this means", C1_LEGEND, output_console)


def render_c2(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render C2's group consolidation."""
    thin_data = payload.get("thin_data_divisions", [])
    portfolio_total = payload.get("portfolio_total_t", 0.0)
    det_total = payload.get("deterministic_portfolio_total_t", 0.0)
    output_console.print(
        Panel(
            f"C2 — Coalition Synthesis Coordinator: portfolio total {portfolio_total:,.1f} t "
            f"(D3 cross-check: {det_total:,.1f} t). "
            f"{len(thin_data)} division(s) with thin data." if thin_data
            else f"C2 — Coalition Synthesis Coordinator: portfolio total {portfolio_total:,.1f} t "
                 f"(D3 cross-check: {det_total:,.1f} t). All divisions have adequate data.",
            title="C2 complete",
            border_style="accent",
        )
    )
    if thin_data:
        output_console.print(
            Panel(
                "\n".join(f"• {d}" for d in thin_data),
                title="Thin-data divisions",
                border_style="yellow",
            )
        )
    output_console.print(
        Panel(
            payload.get("data_volume_coverage_note", ""),
            title="Data-volume coverage",
            border_style="accent.dim",
        )
    )
    output_console.print(
        Panel(
            payload.get("readiness_assessment", ""),
            title="FY2027 readiness assessment",
            border_style="accent.dim",
        )
    )
    _render_legend("C2 -- what this means", C2_LEGEND, output_console)


# ---------------------------------------------------------------------------
# O6 — Memo Composer render
# ---------------------------------------------------------------------------

F1_MEMO_LEGEND = {
    "headline": "One-sentence portfolio summary F1 composed after reviewing all upstream rows.",
    "portfolio_summary": "F1's tallied counts (entries, sites, flagged, material) derived by counting the entries above.",
    "sections": "Narrative memo sections (Overview, Methodology, Portfolio Results, Limitations).",
    "entries": "Per-site-per-quarter memo entries — one per S1 row.",
    "material": "Whether F1 judged the discrepancy reader-facing material (true/false).",
    "scope_determination": "F1's per-entry compliance position echoing S1's reconciled conclusion.",
    "limitations": "Limitations the compliance team must be aware of.",
    "citations": "Regulatory articles and R2 findings cited across the portfolio.",
}


def _render_f1_memo(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render F1 structured_memo deliverable."""
    headline = payload.get("headline", "")
    sections = payload.get("sections", [])
    entries = payload.get("entries", [])
    limitations = payload.get("limitations", [])
    citations = payload.get("citations", [])

    output_console.print(
        Panel(
            headline or "(no headline)",
            title="F1 — Compliance Memo Headline",
            border_style="accent",
        )
    )

    ps = payload.get("portfolio_summary", {})
    if ps:
        ps_line = (
            f"Entries: {ps.get('n_entries', 0)}   "
            f"Sites: {ps.get('n_sites', 0)}   "
            f"Flagged: {ps.get('n_flagged', 0)}   "
            f"Material: {ps.get('n_material', 0)}"
        )
        ps_narrative = ps.get("narrative", "")
        output_console.print(
            Panel(
                f"{ps_line}\n{ps_narrative}" if ps_narrative else ps_line,
                title="F1 — Portfolio Summary",
                border_style="accent",
            )
        )

    for sec in sections:
        output_console.print(
            Panel(
                str(sec.get("body", "")),
                title=f"§ {sec.get('heading', '')}",
                border_style="dim",
            )
        )

    if entries:
        table = Table(title="F1 — Per-Site-Per-Quarter Entries", box=box.SIMPLE, show_lines=True)
        table.add_column("Site", no_wrap=True)
        table.add_column("Quarter", no_wrap=True)
        table.add_column("Location", max_width=30)
        table.add_column("Method A", justify="right")
        table.add_column("Method B", justify="right")
        table.add_column("Flag", justify="center")
        table.add_column("Material", justify="center")
        table.add_column("Entry", max_width=50)

        for e in entries:
            flag = "[red]YES[/red]" if e.get("discrepancy_flag") else "[green]no[/green]"
            mat = "[bold]YES[/bold]" if e.get("material") else "no"
            ma = f"{e.get('method_a', 0.0):,.1f}" if isinstance(e.get("method_a"), (int, float)) else "-"
            mb = f"{e.get('method_b', 0.0):,.1f}" if isinstance(e.get("method_b"), (int, float)) else "-"
            table.add_row(
                str(e.get("location_name", e.get("location_id", ""))),
                str(e.get("year", "")),
                str(e.get("location_display", "")),
                ma,
                mb,
                flag,
                mat,
                str(e.get("entry_text", ""))[:200],
            )
        output_console.print(table)

    if limitations:
        output_console.print(
            Panel(
                "\n".join(f"• {lim}" for lim in limitations),
                title="F1 Limitations",
                border_style="yellow",
            )
        )

    if citations:
        output_console.print(
            Panel(
                "\n".join(f"• {c}" for c in citations),
                title="F1 Portfolio Citations",
                border_style="dim",
            )
        )

    _render_legend("F1 (memo) -- what this means", F1_MEMO_LEGEND, output_console)


F1_NUMERIC_LEGEND = {
    "headline": "One-sentence portfolio summary F1 composed after reviewing all division data-volume reports.",
    "portfolio_summary": "F1's portfolio total and division count, copied verbatim from D3 -- never re-summed.",
    "division_results": "One row per division -- the same data_volume_status/provenance_note C1 assigned, carried unchanged.",
    "data_volume_status": "C1's own read for that division: whether enough underlying rows back its figure -- 'adequate' or 'thin'. F1 cannot change this.",
    "readiness_assessment": "F1's deliverable-facing translation of C2's FY2027 mandatory-reporting readiness read.",
    "limitations": "Scope exclusions (e.g. RC country-companies) and thin-data divisions the compliance team must be aware of.",
    "citations": "Regulatory articles and R2 findings cited across the portfolio.",
}


def _render_f1_numeric(payload: dict[str, Any], output_console: Console = console) -> None:
    """Render F1 numeric_measurement deliverable (Coalition chain)."""
    headline = payload.get("headline", "")
    division_results = payload.get("division_results", [])
    readiness = payload.get("readiness_assessment", "")
    limitations = payload.get("limitations", [])
    citations = payload.get("citations", [])

    output_console.print(
        Panel(
            headline or "(no headline)",
            title="F1 — Divisional Measurement Headline",
            border_style="accent",
        )
    )

    ps = payload.get("portfolio_summary", {})
    if ps:
        ps_line = (
            f"Portfolio total: {ps.get('portfolio_total_t', 0.0):,.1f} t   "
            f"Divisions: {ps.get('division_count', 0)}   "
            f"Adequate: {ps.get('n_adequate', 0)}   "
            f"Thin: {ps.get('n_thin', 0)}"
        )
        ps_narrative = ps.get("narrative", "")
        output_console.print(
            Panel(
                f"{ps_line}\n{ps_narrative}" if ps_narrative else ps_line,
                title="F1 — Portfolio Summary",
                border_style="accent",
            )
        )

    if division_results:
        table = Table(title="F1 — Divisional Measurement Results", box=box.SIMPLE, show_lines=True)
        table.add_column("Division", no_wrap=True)
        table.add_column("Sub-total (t)", justify="right")
        table.add_column("Years", no_wrap=True)
        table.add_column("Status", justify="center")
        table.add_column("Entry", max_width=50)

        for r in division_results:
            status = r.get("data_volume_status", "")
            status_display = "[green]adequate[/green]" if status == "adequate" else "[red]thin[/red]"
            subtotal = f"{r.get('subtotal_t', 0.0):,.1f}" if isinstance(r.get("subtotal_t"), (int, float)) else "-"
            table.add_row(
                str(r.get("division", "")),
                subtotal,
                ", ".join(r.get("years", [])),
                status_display,
                str(r.get("entry_text", ""))[:200],
            )
        output_console.print(table)

    if readiness:
        output_console.print(
            Panel(
                readiness,
                title="FY2027 Readiness Assessment",
                border_style="accent.dim",
            )
        )

    if limitations:
        output_console.print(
            Panel(
                "\n".join(f"• {lim}" for lim in limitations),
                title="F1 Limitations",
                border_style="yellow",
            )
        )

    if citations:
        output_console.print(
            Panel(
                "\n".join(f"• {c}" for c in citations),
                title="F1 Portfolio Citations",
                border_style="dim",
            )
        )

    _render_legend("F1 (numeric) -- what this means", F1_NUMERIC_LEGEND, output_console)


def render_f1(payload: dict[str, Any], output_console: Console = console) -> None:
    """Dispatch F1 render by output_form."""
    form = payload.get("output_form", "")
    if form == "yes_no_alert":
        _render_f1_alert(payload, output_console)
    elif form == "numeric_measurement":
        _render_f1_numeric(payload, output_console)
    else:
        _render_f1_memo(payload, output_console)


ReviewDecider = Callable[
    [dict[str, Any], dict[str, Any]], tuple[str, dict[str, str] | None]
]

# A batch reviewer receives the full report + catalog once and returns a map
# {field_id -> (decision, catalog_target|None)} for all fields.  Used by the
# web UI so the full mapping board can be presented in one gate.
BatchReviewer = Callable[
    [dict[str, Any], dict[str, Any]], dict[str, tuple[str, dict[str, Any] | None]]
]


def interactive_decider(
    mapping: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[str, dict[str, str] | None]:
    targets = mapping.get("catalog_targets", [])
    current = (
        ", ".join(f"{item['kind']}:{item['name']}" for item in targets)
        if targets
        else "unmapped"
    )
    console.print(
        f"\n[bold]{mapping['field_id']} {mapping['field_name']}[/bold] "
        f"({mapping['priority']}, {mapping['role']})\n"
        f"DA1: {current}\n{mapping.get('reason', '')}"
    )
    choice = typer.prompt(
        "Decision [a=approve, x=exclude, r=remap, u=unresolved]",
        default="a" if targets else "u",
    ).strip().lower()
    if choice in {"a", "approve"} and targets:
        if len(targets) > 1:
            for index, target in enumerate(targets, start=1):
                console.print(f"  {index}. {target['kind']}:{target['name']}")
            selected = typer.prompt(
                "Which candidate for this field?", type=int, default=1
            )
            if selected < 1 or selected > len(targets):
                raise typer.BadParameter("Catalog target number is out of range")
            return "approved", targets[selected - 1]
        return "approved", targets[0]
    if choice in {"x", "exclude"}:
        return "excluded", None
    if choice in {"r", "remap"}:
        candidates = _all_catalog_targets(catalog)
        for index, target in enumerate(candidates, start=1):
            console.print(f"  {index}. {target['kind']}:{target['name']}")
        selected = typer.prompt("Catalog target number", type=int)
        if selected < 1 or selected > len(candidates):
            raise typer.BadParameter("Catalog target number is out of range")
        return "remapped", candidates[selected - 1]
    return "unresolved", None


def auto_decider(
    mapping: dict[str, Any],
    catalog: dict[str, Any],
) -> tuple[str, dict[str, str] | None]:
    """Non-interactive decider: approve if DA1 supplied a target, else unresolved."""
    targets = mapping.get("catalog_targets", [])
    return ("approved", targets[0]) if targets else ("unresolved", None)


_AUTO_PROVIDERS: dict[str, Any] = dict(
    decider=auto_decider,
    approval_provider=lambda: True,
    r2_review_provider=lambda _payload: "a",
    cp1_review_provider=lambda _payload: "a",
    ts_review_provider=lambda _payload: "a",
    f1_review_provider=lambda _payload: "a",
    # UC4 document-pipeline gates (regulation.py:pipeline_family=="document").
    # Harmless no-ops for tabular use cases -- build_profile_pipeline accepts
    # them as unused kwargs when seed.pipeline_family == "tabular".
    mapping_review_provider=lambda _payload: "a",
    f2_review_provider=lambda _payload: "a",
)


def review_mappings(
    report: dict[str, Any],
    catalog: dict[str, Any],
    *,
    decider: ReviewDecider = interactive_decider,
    batch_reviewer: "BatchReviewer | None" = None,
) -> list[dict[str, Any]]:
    """Review all field mappings and return a list of human decisions.

    Two modes:
    - ``batch_reviewer`` (programmatic caller): called once with the full report + catalog;
      returns a ``{field_id: (decision, target)}`` map for all fields at once.
    - ``decider`` (CLI, default): called per field in sequence (original path).
    """
    decisions: list[dict[str, Any]] = []

    if batch_reviewer is not None:
        batch_map = batch_reviewer(report, catalog)
        for mapping in report.get("field_mappings", []):
            field_id = mapping["field_id"]
            decision, target = batch_map.get(field_id, ("unresolved", None))
            if target is not None and not catalog_target_exists(target, catalog):
                raise ValueError(
                    f"Human-selected catalog target does not exist: {target}"
                )
            decisions.append(
                {
                    "field_id": field_id,
                    "field_name": mapping["field_name"],
                    "priority": mapping["priority"],
                    "decision": decision,
                    "catalog_target": target,
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return decisions

    for mapping in report.get("field_mappings", []):
        ordered_mapping = _prefer_role_matching_target(mapping)
        decision, target = decider(ordered_mapping, catalog)
        if target is not None and not catalog_target_exists(target, catalog):
            raise ValueError(
                f"Human-selected catalog target does not exist: {target}"
            )
        decisions.append(
            {
                "field_id": mapping["field_id"],
                "field_name": mapping["field_name"],
                "priority": mapping["priority"],
                "decision": decision,
                "catalog_target": target,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return decisions


def _measure_composite_key(target: dict[str, Any]) -> str:
    """Composite dedup key for a measure catalog target.

    Two targets sharing the same key represent identical data (true duplicate).
    Targets with the same ``name`` but different ``filters`` / ``quality_flags``
    produce distinct keys and are legitimately separate values.
    """
    filters = sorted(
        (target.get("filters") or []),
        key=lambda f: (f.get("column", ""), f.get("op", ""), str(f.get("value", ""))),
    )
    quality_flags = sorted(target.get("quality_flags") or [])
    return json.dumps(
        {"name": target.get("name", ""), "filters": filters, "quality_flags": quality_flags},
        sort_keys=True,
        ensure_ascii=False,
    )


_ROLE_TO_TARGET_KIND: dict[str, str] = {
    "measure": "measure",
    "entity": "entity_grain",
    "time": "time_grain",
    "qualifier": "filter",
}


def _prefer_role_matching_target(mapping: dict[str, Any]) -> dict[str, Any]:
    """Reorder a field mapping's catalog_targets so the target kind matching
    the field's own ``role`` sorts first.

    A field can legitimately carry several complementary targets of different
    kinds (e.g. ``site_id`` -> [entity_grain:site, filter:location_id]) — that
    is not the case this addresses. It only matters when two candidates of the
    *same* kind compete for one field (e.g. two ``measure`` targets), where
    DA1's list order between them is otherwise arbitrary (LLM output). This
    only reorders — no target is added, dropped, or rewritten.
    """
    targets = mapping.get("catalog_targets", [])
    expected_kind = _ROLE_TO_TARGET_KIND.get(mapping.get("role"))
    if len(targets) < 2 or expected_kind is None:
        return mapping
    preferred = [t for t in targets if t.get("kind") == expected_kind]
    if not preferred or len(preferred) == len(targets):
        return mapping
    rest = [t for t in targets if t not in preferred]
    return {**mapping, "catalog_targets": preferred + rest}


def build_handoff_request(
    seed: UsecaseSeed,
    report: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    suggested = deepcopy(report.get("suggested_request") or {})
    approved = [
        item
        for item in decisions
        if item["decision"] in {"approved", "remapped"}
        and item.get("catalog_target")
    ]
    measure_targets: list[dict[str, Any]] = []
    filters = dict(suggested.get("filters") or {})
    entity_grain = None
    time_grain = None
    # source_domain starts as None; if the human explicitly approved a
    # source_domain catalog target, that wins (explicit override).
    # Otherwise it is derived below from the approved measure names — never
    # from R1's suggested_request (isolated-memory principle).
    explicit_source_domain: str | None = None
    for item in approved:
        target = item["catalog_target"]
        if target["kind"] == "measure":
            measure_targets.append(target)
        elif target["kind"] == "filter":
            # An approved filter target carries no concrete catalog value
            # here (no value/values field on the target), so there is
            # nothing meaningful to constrain by. Do not register an
            # empty-list filter -- D1/D2 both treat empty values as "no
            # constraint", but an explicit [] entry is a trap for any
            # consumer that forgets that guard.
            pass
        elif target["kind"] == "entity_grain":
            entity_grain = target["name"]
        elif target["kind"] == "time_grain":
            time_grain = target["name"]
        elif target["kind"] == "source_domain":
            explicit_source_domain = target["name"]
    filter_aliases = {
        "country": "iso2_code",
        # Note: "region" is handled below — composite buckets like "EMEA" expand
        # to a derived filter (e.g. {"emea": [True]}) instead of aliasing to
        # region_name/cdp_region, which would carry a literal that the validator
        # correctly rejects.  Literal cdp_region values ("Europe", etc.) pass
        # through the bucket expansion unchanged (expand_region_bucket returns None)
        # and fall back to the region_name alias as before.
        "region": "region_name",
        "bu_rc": "bu_rc_group",  # real column where DI/SMO live; bu_rc_code is an
        # ambiguous legacy alias (catalog → bu_rc_id, planner → bu_rc_name) — never use here
        "size_tier": "size_tier",
        "location_type": "location_type",
        "performance_archetype": "performance_archetype",
    }
    for key, value in seed.site_filter.items():
        if key == "region":
            bucket = expand_region_bucket(value)
            if bucket:
                # Composite bucket (e.g. "EMEA") — expand to derived filter so the
                # human-approved handoff carries {"emea": [True]}, not the literal.
                for bk, bv in bucket.items():
                    filters.setdefault(bk, bv)
                continue
        filters.setdefault(filter_aliases.get(key, key), value)
    # Dedupe measures by composite key (name + filters + quality_flags) so that
    # two targets mapping to identical values collapse into one, but legitimately
    # distinct filtered variants (e.g. co2 for electricity vs co2 for all media)
    # are kept separate.  The output is still a flat list of measure names because
    # the downstream handoff format only carries names.
    seen_measure_keys: set[str] = set()
    measures: list[str] = []
    for tgt in measure_targets:
        composite_key = _measure_composite_key(tgt)
        if composite_key not in seen_measure_keys:
            seen_measure_keys.add(composite_key)
            measures.append(tgt["name"])
    # Defensive cleanup: strip any filter entries with no concrete value
    # (None / [] / "") regardless of how they got into `filters` -- e.g. via
    # suggested.get("filters") above or the site_filter alias loop -- so
    # downstream consumers (D1, D2) never have to special-case an empty
    # constraint that means "no filter".
    filters = {k: v for k, v in filters.items() if v not in (None, [], "")}
    # source_domain: explicit human choice wins; otherwise derive from catalog
    # using the approved measure names + filters (isolated-memory: R1 not consulted).
    if explicit_source_domain is not None:
        source_domain = explicit_source_domain
    else:
        measure_names = [tgt["name"] for tgt in measure_targets]
        source_domain = (resolve_source_domains(measure_names, filters) or ["energy"])[0]
    return {
        "request_id": f"{seed.id}_approved_profile",
        "intent": seed.natural_request,
        "source_domain": source_domain,
        "entity_grain": entity_grain,
        "time_grain": time_grain or seed.time_grain,
        "time_window": {
            "start": seed.time_window[0],
            "end": seed.time_window[1],
        },
        "filters": filters,
        "measures": measures,
        "quality_policy": suggested.get("quality_policy", "exclude_inconsistent"),
        "approved_field_ids": [item["field_id"] for item in approved],
    }


def build_approved_mapping_scope(
    seed: UsecaseSeed,
    report: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    approved_decisions = [
        deepcopy(item)
        for item in decisions
        if item["decision"] in {"approved", "remapped"}
        and item.get("catalog_target")
    ]
    mappings_by_id = {
        item["field_id"]: item for item in report.get("field_mappings", [])
    }
    approved_mappings: list[dict[str, Any]] = []
    for decision in approved_decisions:
        mapping = deepcopy(mappings_by_id[decision["field_id"]])
        mapping["status"] = "mapped"
        mapping["catalog_targets"] = [deepcopy(decision["catalog_target"])]
        mapping["human_decision"] = decision["decision"]
        approved_mappings.append(mapping)
    return {
        "schema_version": "1",
        "registry_id": seed.id,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_field_ids": [item["field_id"] for item in approved_decisions],
        "approved_mappings": approved_mappings,
        "human_decisions": deepcopy(decisions),
        "handoff_data_request": build_handoff_request(seed, report, decisions),
    }


def _filter_scope_for_cp1(approved_scope: dict[str, Any]) -> dict[str, Any]:
    """Return approved_mapping_scope with human_decisions stripped for CP1.

    CP1 receives only the approved scope (mapped fields) and the handoff request.
    Per-field human decisions (approve/unresolved/exclude) stay in DA1's shelf
    for audit; CP1 operates on the net result in approved_mappings.
    """
    return {
        "schema_version": approved_scope["schema_version"],
        "registry_id": approved_scope["registry_id"],
        "approved_at": approved_scope["approved_at"],
        "approved_field_ids": approved_scope["approved_field_ids"],
        "approved_mappings": approved_scope["approved_mappings"],
        "handoff_data_request": approved_scope["handoff_data_request"],
    }


# ---------------------------------------------------------------------------
# Resume-from support helpers
# ---------------------------------------------------------------------------

_RESUME_TAIL_COMMON: list[str] = ["D1", "D2", "CP1", "TS1", "D3"]
_RESUME_TAIL_DIRECT: list[str] = _RESUME_TAIL_COMMON + ["P1", "F1"]
_RESUME_TAIL_DEBATE: list[str] = _RESUME_TAIL_COMMON + ["P2", "S1", "F1"]
# P3 always runs together with P2; it is not a standalone resume point.
# F1 is the last agent in every topology; its resume tail is resolved from
# the persisted TS1 shelf (Direct → _RESUME_TAIL_DIRECT, Debate → _RESUME_TAIL_DEBATE).

_RESUME_TAIL: list[str] = _RESUME_TAIL_DIRECT
"""Backward-compat alias used by the Direct path and common-prefix stages.

For Debate topology resumes (P2/S1/F1) the tail is resolved dynamically from the
persisted TS1 shelf after topology selection, so this alias is not consulted.
"""


def _run_post_r2_tail(  # noqa: C901  (complex by design — mirrors pipeline nesting)
    *,
    seed: "UsecaseSeed",
    envelope: Any,
    store: Any,
    run_dir: Path,
    run_id: str,
    review_round: int,
    refresh: bool,
    handoff: dict[str, Any],
    approved_scope: dict[str, Any],
    r2_profile: dict[str, Any],
    r1_active: dict[str, Any],
    da1_active: dict[str, Any],
    r2_active: dict[str, Any],
    start_at: str = "D1",
    d2_agent: "MissingValueAnalyst | None" = None,
    cp1_agent: "CaseProfilerAgent | None" = None,
    ts_agent: "TopologySelectorAgent | None" = None,
    cp1_review_provider: "Callable[[dict[str, Any]], str] | None" = None,
    ts_review_provider: "Callable[[dict[str, Any]], str] | None" = None,
    f1_review_provider: "Callable[[dict[str, Any]], str] | None" = None,
    output_console: "Console" = console,
) -> "dict[str, Any] | None":
    """Run the post-R2 pipeline tail starting from *start_at*.

    When ``start_at == "D1"`` (normal build) all stages execute in order.
    For resume runs (start_at in {D2, D3, P1, P2, S1, F1}) earlier stages are
    rehydrated from their shelves; D1 is re-run deterministically to repopulate
    the evidence store when the target stage needs the data product (D2 or D3).
    """
    # Resolve the resume tail from topology when the start_at is topology-specific.
    # Debate tail: P2, S1, F1 (from Debate).  Direct tail: P1, F1 (from Direct).
    # For F1 we resolve from the topology stored in approved_scope if available,
    # else fall back to Direct.
    if start_at in ("P2", "S1"):
        _tail = _RESUME_TAIL_DEBATE
    elif start_at == "F1":
        _topo = approved_scope.get("selected_topology_id", "") if approved_scope else ""
        _tail = _RESUME_TAIL_DEBATE if _topo == "Debate" else _RESUME_TAIL_DIRECT
    else:
        _tail = _RESUME_TAIL  # Direct + common (covers D1, D2, CP1, TS1, D3, P1)
    begin = _tail.index(start_at)

    # ── D1 ─────────────────────────────────────────────────────────────────
    if begin == 0:
        # Normal build path: run D1 and promote its shelf entry.
        d1_exec = DataLoaderAgent()
        try:
            d1_message = d1_exec.execute_from_handoff(envelope, store, handoff)
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="D1 execution failed", border_style="red")
            )
            return None

        d1_payload = {
            "data_request": d1_message.payload["data_request"],
            "summary": d1_message.payload["summary"],
        }
        d1_profile_store.promote_active(
            seed.id,
            d1_payload,
            run_id=run_id,
            review_round=review_round,
            input_refs={
                "da1": _active_ref(da1_active),
                "r2": _active_ref(r2_active),
            },
            archive_existing=not refresh,
        )
        d1_active = d1_profile_store.load_active(seed.id)
        render_progress("D1", ["R1", "DA1", "R2"], output_console)
        render_d1(d1_message.payload, output_console)
    else:
        # Resume path: load D1 shelf; for D2/D3 re-run D1 to repopulate the
        # evidence store (shelf is NOT re-promoted -- approved record unchanged).
        d1_active = d1_profile_store.load_active(seed.id)
        if start_at in {"D2", "D3"}:
            output_console.print(
                Panel(
                    "Re-running deterministic D1 to repopulate the evidence store "
                    "(shelf not updated -- approved record unchanged).",
                    title="D1 store repopulation",
                    border_style="blue",
                )
            )
            try:
                DataLoaderAgent().execute_from_handoff(envelope, store, handoff)
            except Exception as exc:
                output_console.print(
                    Panel(
                        str(exc),
                        title="D1 store repopulation failed",
                        border_style="red",
                    )
                )
                return None

    # ── D2 ─────────────────────────────────────────────────────────────────
    if begin <= 1:
        data_product = store.read(f"data_product_{seed.id}")
        normalized_data_request = store.read(f"data_request_{seed.id}")
        d2_runner = d2_agent or MissingValueAnalyst()
        try:
            d2_message = d2_runner.execute_from_scope(
                envelope,
                store,
                registry_id=seed.id,
                in_scope_findings=r2_profile.get("in_scope_findings", []),
                handoff_data_request=handoff,
                normalized_data_request=normalized_data_request or {},
                data_product=data_product or {},
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="D2 execution failed", border_style="red")
            )
            return None

        d2_verdict = d2_message.payload
        d2_payload = {
            "verdict": d2_verdict.get("verdict"),
            "routing_recommendation": d2_verdict.get("routing_recommendation"),
            "evidence_completeness": d2_verdict.get("evidence_completeness"),
            "max_severity": d2_verdict.get("max_severity"),
            "summary": d2_verdict.get("summary"),
            "assessable_claims": d2_verdict.get("assessable_claims", []),
            "blocked_claims": d2_verdict.get("blocked_claims", []),
        }
        render_progress("D2", ["R1", "DA1", "R2", "D1"], output_console)
        render_d2(d2_verdict, output_console)

        if d2_verdict.get("routing_recommendation") == "stop":
            d2_profile_store.archive_candidate(
                seed.id,
                d2_payload,
                status="pipeline_stopped",
                reason="Data quality insufficient for the approved scope",
                run_id=run_id,
                review_round=review_round,
                input_refs={
                    "d1": _active_ref(d1_active),
                    "r2": _active_ref(r2_active),
                },
                llm_provenance=d2_runner.last_llm_provenance,
            )
            output_console.print(
                Panel(
                    d2_verdict.get("summary", ""),
                    title="Pipeline stopped -- data quality insufficient for the approved scope",
                    border_style="red",
                )
            )
            return None

        d2_profile_store.promote_active(
            seed.id,
            d2_payload,
            run_id=run_id,
            review_round=review_round,
            input_refs={
                "d1": _active_ref(d1_active),
                "r2": _active_ref(r2_active),
            },
            archive_existing=not refresh,
            llm_provenance=d2_runner.last_llm_provenance,
        )
        d2_active = d2_profile_store.load_active(seed.id)
        _write_json(run_dir / f"d2_output_round_{review_round:02d}.json", d2_payload)
    else:
        d2_active = d2_profile_store.load_active(seed.id)

    # ── derived_case_facts + CP1 ────────────────────────────────────────────
    if begin <= 2:
        # Surface any D1 time-window clamp so CP1 can emit it as a limitation.
        _d1_summary = d1_active["payload"].get("summary", {})
        _window_divergence: dict | None = None
        if _d1_summary.get("time_window_clamped"):
            req = _d1_summary.get("requested_time_range", [])
            eff = _d1_summary.get("effective_time_range", [])
            _window_divergence = {
                "requested": req,
                "effective": eff,
                "note": (
                    f"D1 clamped the DA1-approved time window "
                    f"(approved end: {req[1] if len(req) > 1 else '?'}, "
                    f"effective end: {eff[1] if len(eff) > 1 else '?'}) "
                    f"to the available data range. "
                    f"This divergence from the approved handoff is a D1 limitation."
                ),
            }
        derived = build_derived_case_facts(
            seed.threshold_parameters,
            store.read(f"data_product_{seed.id}") or {},
            quality_verdict=d2_active["payload"] if d2_active else {},
            window_divergence=_window_divergence,
        )

        cp1_runner = cp1_agent or CaseProfilerAgent()
        cp1_context = {
            "pipeline_family": seed.pipeline_family,
            "demand": seed.demand_snapshot(),
            "approved_scope": _filter_scope_for_cp1(approved_scope),
            "r2_in_scope_findings": r2_profile.get("in_scope_findings", []),
            "document_evidence": None,
            "derived_case_facts": derived,
            "data_quality": {
                "verdict": d2_active["payload"].get("verdict") if d2_active else None,
                "evidence_completeness": (
                    d2_active["payload"].get("evidence_completeness") if d2_active else None
                ),
                "blocked_claims": (
                    d2_active["payload"].get("blocked_claims", []) if d2_active else []
                ),
                "routing_recommendation": (
                    d2_active["payload"].get("routing_recommendation") if d2_active else None
                ),
            },
        }
        try:
            cp1_message = cp1_runner.execute_preselection(
                envelope, store, approved_profile_context=cp1_context
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="CP1 failed -- active CP1 unchanged", border_style="red")
            )
            active = load_active_set(seed.id)
            active = _write_run_snapshot(run_dir, active, run_id, "tabular")
            return active

        cp1_payload = cp1_message.payload
        _write_json(run_dir / f"cp1_output_round_{review_round:02d}.json", cp1_payload)
        render_progress("CP1", ["R1", "DA1", "R2", "D1", "D2"], output_console)
        render_cp1(cp1_payload, output_console)

        if cp1_review_provider is not None:
            cp1_action = cp1_review_provider(cp1_payload).strip().lower()
        else:
            render_action_panel(
                "Review the CP1 case profile above and choose an action.",
                "[a] approve   [r] reject",
                output_console,
            )
            cp1_action = typer.prompt("CP1 case profile", default="a").strip().lower()

        if cp1_action in {"a", "approve"}:
            cp1_profile_store.promote_active(
                seed.id,
                cp1_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs={
                    "r1": _active_ref(r1_active),
                    "da1": _active_ref(da1_active),
                    "r2": _active_ref(r2_active),
                    "d1": _active_ref(d1_active),
                    "d2": _active_ref(d2_active),
                },
                archive_existing=not refresh,
                llm_provenance=cp1_runner.last_llm_provenance,
            )
            output_console.print(
                Panel(
                    "CP1 case profile approved and saved.",
                    title="CP1 active",
                    border_style="green",
                )
            )
            cp1_active = cp1_profile_store.load_active(seed.id)
        else:
            cp1_profile_store.archive_candidate(
                seed.id,
                cp1_payload,
                status="human_rejected",
                reason="CP1 case profile rejected by human",
                run_id=run_id,
                review_round=review_round,
                input_refs={
                    "r1": _active_ref(r1_active),
                    "da1": _active_ref(da1_active),
                    "r2": _active_ref(r2_active),
                    "d1": _active_ref(d1_active),
                    "d2": _active_ref(d2_active),
                },
                llm_provenance=cp1_runner.last_llm_provenance,
            )
            output_console.print(
                Panel(
                    "CP1 profile rejected. R1/DA1/R2/D1 remain active; CP1 shelf is empty.",
                    title="CP1 not active",
                    border_style="yellow",
                )
            )
            active = load_active_set(seed.id)
            active = _write_run_snapshot(run_dir, active, run_id, "tabular")
            return active
    else:
        cp1_active = cp1_profile_store.load_active(seed.id)

    # ── TS1 ─────────────────────────────────────────────────────────────────
    if begin <= 3:
        ts_runner = ts_agent or TopologySelectorAgent()
        try:
            ts_message = ts_runner.execute_selection(
                envelope, store, case_profile=cp1_active["payload"]
            )
        except Exception as exc:
            output_console.print(
                Panel(
                    str(exc),
                    title="TS1 failed -- active TS1 unchanged",
                    border_style="red",
                )
            )
            active = load_active_set(seed.id)
            active = _write_run_snapshot(run_dir, active, run_id, "tabular")
            return active

        ts_payload = ts_message.payload
        _write_json(run_dir / f"ts1_output_round_{review_round:02d}.json", ts_payload)
        render_progress("TS1", ["R1", "DA1", "R2", "D1", "D2", "CP1"], output_console)
        render_ts1(ts_payload, output_console)

        if ts_review_provider is not None:
            ts_action = ts_review_provider(ts_payload).strip().lower()
        else:
            render_action_panel(
                "Review the TS1 topology selection above and choose an action.",
                "[a] approve   [r] reject",
                output_console,
            )
            ts_action = typer.prompt("TS1 topology selection", default="a").strip().lower()

        if ts_action in {"a", "approve"}:
            topology_selector_store.promote_active(
                seed.id,
                ts_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs={"cp1": _active_ref(cp1_active)},
                archive_existing=not refresh,
                llm_provenance=ts_runner.last_llm_provenance,
            )
            output_console.print(
                Panel(
                    "TS1 topology selection approved and saved.",
                    title="TS1 active",
                    border_style="green",
                )
            )
            ts_active = topology_selector_store.load_active(seed.id)
        else:
            topology_selector_store.archive_candidate(
                seed.id,
                ts_payload,
                status="human_rejected",
                reason="TS1 topology selection rejected by human",
                run_id=run_id,
                review_round=review_round,
                input_refs={"cp1": _active_ref(cp1_active)},
                llm_provenance=ts_runner.last_llm_provenance,
            )
            output_console.print(
                Panel(
                    "TS1 selection rejected. All other profiles remain active; TS1 shelf is empty.",
                    title="TS1 not active",
                    border_style="yellow",
                )
            )
            active = load_active_set(seed.id)
            active = _write_run_snapshot(run_dir, active, run_id, "tabular")
            return active
    else:
        ts_active = topology_selector_store.load_active(seed.id)

    selected_topology = ts_active["payload"].get("selected_topology_id", "")

    # ── D3 ─────────────────────────────────────────────────────────────────
    if begin <= 4 and "D3" in stage2_flat_agents_for(selected_topology):
        d3_data_product = store.read(f"data_product_{seed.id}") or {}
        d3_runner = TimeSeriesComputeAgent()
        try:
            d3_message = d3_runner.execute_computation(
                envelope,
                store,
                registry_id=seed.id,
                data_product=d3_data_product,
                handoff=handoff,
                threshold_parameters=seed.threshold_parameters,
                computation_spec=seed.computation_spec,
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="D3 computation failed", border_style="red")
            )
            active = load_active_set(seed.id)
            active = _write_run_snapshot(run_dir, active, run_id, "tabular")
            return active

        d3_payload = d3_message.payload
        d3_profile_store.promote_active(
            seed.id,
            d3_payload,
            run_id=run_id,
            review_round=review_round,
            input_refs={
                "d1": _active_ref(d1_active),
                "da1": _active_ref(da1_active),
                "ts": _active_ref(ts_active),
            },
            archive_existing=not refresh,
        )
        _write_json(run_dir / f"d3_output_round_{review_round:02d}.json", d3_payload)
        render_progress("D3", ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1"], output_console)
        render_d3(d3_payload, output_console)
        d3_active = d3_profile_store.load_active(seed.id)
    else:
        d3_active = d3_profile_store.load_active(seed.id)
        d3_payload = d3_active["payload"] if d3_active else {}

    # ── P1 ─────────────────────────────────────────────────────────────────
    if begin <= 5 and "P1" in stage2_flat_agents_for(selected_topology):
        p1_runner = ResultInterpreterAgent()
        try:
            o1_message = p1_runner.execute_interpretation(
                envelope,
                store,
                r2_in_scope_findings=r2_profile.get("in_scope_findings", []),
                d3_payload=d3_payload,
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="P1 interpretation failed", border_style="red")
            )
            active = load_active_set(seed.id)
            active = _write_run_snapshot(run_dir, active, run_id, "tabular")
            return active

        p1_payload = o1_message.payload
        p1_profile_store.promote_active(
            seed.id,
            p1_payload,
            run_id=run_id,
            review_round=review_round,
            input_refs=_checked_refs("P1", {
                "d3": _active_ref(d3_active),
                "r2": _active_ref(r2_active),
            }, run_id),
            archive_existing=not refresh,
            llm_provenance=p1_runner.last_llm_provenance,
        )
        _write_json(run_dir / f"p1_output_round_{review_round:02d}.json", p1_payload)
        render_progress(
            "P1", ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3"], output_console
        )
        render_p1(p1_payload, output_console)
        p1_active = p1_profile_store.load_active(seed.id)
    else:
        if "P1" in stage2_flat_agents_for(selected_topology):
            p1_active = p1_profile_store.load_active(seed.id)
        else:
            p1_active = None
        p1_payload = p1_active["payload"] if p1_active else {}

    # ── P2 ‖ P3 (Debate: two independent parallel reads) ────────────────────
    flat = stage2_flat_agents_for(selected_topology)
    if "P2" in flat and "P3" in flat:
        p2_runner = EnergyMethodInterpreterAgent()
        p3_runner = ReportedMethodInterpreterAgent()
        d3_payload_for_debate = d3_active["payload"] if d3_active else {}
        r2_findings = r2_profile.get("in_scope_findings", [])

        with ThreadPoolExecutor(max_workers=2) as pool:
            o3_future = pool.submit(
                p2_runner.execute_interpretation,
                envelope,
                store,
                r2_in_scope_findings=r2_findings,
                d3_payload=d3_payload_for_debate,
            )
            o4_future = pool.submit(
                p3_runner.execute_interpretation,
                envelope,
                store,
                r2_in_scope_findings=r2_findings,
                d3_payload=d3_payload_for_debate,
            )
            o3_message, o4_message = None, None
            try:
                o3_message = o3_future.result()
            except Exception as exc:
                output_console.print(
                    Panel(str(exc), title="P2 interpretation failed", border_style="red")
                )
            try:
                o4_message = o4_future.result()
            except Exception as exc:
                output_console.print(
                    Panel(str(exc), title="P3 interpretation failed", border_style="red")
                )

        if o3_message is not None:
            p2_payload = o3_message.payload
            render_progress(
                "P2",
                ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3"],
                output_console,
            )
            render_p2(p2_payload, output_console)
            p2_profile_store.promote_active(
                seed.id,
                p2_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs=_checked_refs("P2", {
                    "d3": _active_ref(d3_active),
                    "r2": _active_ref(r2_active),
                }, run_id),
                archive_existing=not refresh,
                llm_provenance=p2_runner.last_llm_provenance,
            )
            _write_json(
                run_dir / f"p2_output_round_{review_round:02d}.json", p2_payload
            )

        if o4_message is not None:
            p3_payload = o4_message.payload
            render_progress(
                "P3",
                ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3"],
                output_console,
            )
            render_p3(p3_payload, output_console)
            p3_profile_store.promote_active(
                seed.id,
                p3_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs=_checked_refs("P3", {
                    "d3": _active_ref(d3_active),
                    "r2": _active_ref(r2_active),
                }, run_id),
                archive_existing=not refresh,
                llm_provenance=p3_runner.last_llm_provenance,
            )
            _write_json(
                run_dir / f"p3_output_round_{review_round:02d}.json", p3_payload
            )

        p2_active = p2_profile_store.load_active(seed.id)
        p3_active = p3_profile_store.load_active(seed.id)
        p2_payload_final = p2_active["payload"] if p2_active else {}
        p3_payload_final = p3_active["payload"] if p3_active else {}
    else:
        p2_active, p3_active = None, None
        p2_payload_final, p3_payload_final = {}, {}

    # ── S1 (Synthesizer; promotes unconditionally — gate fires at F1 only) ───
    if "S1" in flat:
        s1_runner = SynthesizerAgent()
        try:
            s1_message = s1_runner.execute_reconciliation(
                envelope,
                store,
                o3_result=p2_payload_final,
                o4_result=p3_payload_final,
                r2_in_scope_findings=r2_profile.get("in_scope_findings", []),
                d3_payload=d3_active["payload"] if d3_active else {},
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="S1 synthciliation failed", border_style="red")
            )
        else:
            s1_payload = s1_message.payload
            render_progress(
                "S1",
                ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3", "P2", "P3"],
                output_console,
            )
            render_s1(s1_payload, output_console)
            s1_profile_store.promote_active(
                seed.id,
                s1_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs=_checked_refs("S1", {
                    "p2": _active_ref(p2_active) if p2_active else {},
                    "p3": _active_ref(p3_active) if p3_active else {},
                    "d3": _active_ref(d3_active) if d3_active else {},
                    "r2": _active_ref(r2_active),
                }, run_id),
                archive_existing=not refresh,
                llm_provenance=s1_runner.last_llm_provenance,
            )
            _write_json(
                run_dir / f"s1_output_round_{review_round:02d}.json", s1_payload
            )

        s1_active = s1_profile_store.load_active(seed.id)
        s1_payload_final = s1_active["payload"] if s1_active else {}
    else:
        s1_active = None
        s1_payload_final = {}

    # ── C1 (Divisional Attestor; fans out one isolated call per division) ────
    if "C1" in flat:
        c1_runner = DivisionalAttestorAgent()
        try:
            c1_message = c1_runner.execute_attestation(
                envelope,
                store,
                r2_in_scope_findings=r2_profile.get("in_scope_findings", []),
                d3_payload=d3_active["payload"] if d3_active else {},
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="C1 attestation failed", border_style="red")
            )
        else:
            c1_payload = c1_message.payload
            render_progress(
                "C1",
                ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3"],
                output_console,
            )
            render_c1(c1_payload, output_console)
            c1_profile_store.promote_active(
                seed.id,
                c1_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs=_checked_refs("C1", {
                    "d3": _active_ref(d3_active) if d3_active else {},
                    "r2": _active_ref(r2_active),
                }, run_id),
                archive_existing=not refresh,
                llm_provenance=c1_runner.last_llm_provenance,
            )
            _write_json(
                run_dir / f"c1_output_round_{review_round:02d}.json", c1_payload
            )

        c1_active = c1_profile_store.load_active(seed.id)
        c1_payload_final = c1_active["payload"] if c1_active else {}
    else:
        c1_active = None
        c1_payload_final = {}

    # ── C2 (Coalition Synthesis Coordinator; promotes unconditionally — gate fires at F1 only) ─
    if "C2" in flat:
        c2_runner = CoalitionSynthesizerAgent()
        try:
            c2_message = c2_runner.execute_consolidation(
                envelope,
                store,
                c1_result=c1_payload_final,
                r2_in_scope_findings=r2_profile.get("in_scope_findings", []),
                d3_payload=d3_active["payload"] if d3_active else {},
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="C2 consolidation failed", border_style="red")
            )
        else:
            c2_payload = c2_message.payload
            render_progress(
                "C2",
                ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3", "C1"],
                output_console,
            )
            render_c2(c2_payload, output_console)
            c2_profile_store.promote_active(
                seed.id,
                c2_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs=_checked_refs("C2", {
                    "c1": _active_ref(c1_active) if c1_active else {},
                    "d3": _active_ref(d3_active) if d3_active else {},
                    "r2": _active_ref(r2_active),
                }, run_id),
                archive_existing=not refresh,
                llm_provenance=c2_runner.last_llm_provenance,
            )
            _write_json(
                run_dir / f"c2_output_round_{review_round:02d}.json", c2_payload
            )

        c2_active = c2_profile_store.load_active(seed.id)
        c2_payload_final = c2_active["payload"] if c2_active else {}
    else:
        c2_active = None
        c2_payload_final = {}

    # ── F1 (Finalizer; final agent in every topology — human review gate here) ─
    if "F1" in flat:
        upstream: dict = {}
        if p1_active:
            upstream["P1"] = p1_active["payload"] if p1_active else {}
        if p2_active:
            upstream["P2"] = p2_payload_final
        if p3_active:
            upstream["P3"] = p3_payload_final
        if s1_active:
            upstream["S1"] = s1_payload_final
        if c1_active:
            upstream["C1"] = c1_payload_final
        if c2_active:
            upstream["C2"] = c2_payload_final
        _d3_shelf = d3_active["payload"] if d3_active else {}
        _d3_det_summary = _d3_shelf.get("deterministic_summary") or {}
        f1_runner = FinalizerAgent()
        try:
            f1_message = f1_runner.execute_finalization(
                envelope,
                store,
                upstream_outputs=upstream,
                r2_in_scope_findings=r2_profile.get("in_scope_findings", []),
                demand=seed.demand_snapshot(),
                d3_summary=_d3_det_summary or None,
            )
        except Exception as exc:
            output_console.print(
                Panel(str(exc), title="F1 finalization failed", border_style="red")
            )
        else:
            f1_payload = f1_message.payload
            prior_steps = [s for s in ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3",
                                        "P1", "P2", "P3", "S1", "C1", "C2"] if s in flat or s in ["R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3"]]
            render_progress("F1", prior_steps, output_console)
            render_f1(f1_payload, output_console)

            render_progress("EX2", prior_steps, output_console)
            ex2_runner = ExternalCommentatorAgent("EX2")
            try:
                ex2_msg = ex2_runner.execute_commentary(
                    envelope,
                    store,
                    registry_id=seed.id,
                    gate_payload=f1_payload,
                    demand=seed.demand_snapshot(),
                )
            except Exception as exc:
                output_console.print(Panel(str(exc), title="EX2 skipped", border_style="warn"))
                ex2_payload = None
            else:
                ex2_payload = ex2_msg.payload
                _write_json(run_dir / f"ex2_output_round_{review_round:02d}.json", ex2_payload)
                render_ex(ex2_payload, output_console)
                ex_profile_store.promote_active(
                    "EX2",
                    seed.id,
                    ex2_payload,
                    run_id=run_id,
                    review_round=review_round,
                    input_refs=_checked_refs("EX2", {
                        "gate_payload": {
                            "artifact_id": None,
                            "payload_sha256": _json_hash(f1_payload),
                            "state": "candidate",
                        },
                    }, run_id),
                    llm_provenance=ex2_runner.last_llm_provenance,
                )
            f1_gate_payload = {**f1_payload, "ex2": ex2_payload}

            if f1_review_provider is not None:
                f1_action = f1_review_provider(f1_gate_payload).strip().lower()
            else:
                render_action_panel(
                    "Review the F1 compliance deliverable above and choose an action.",
                    "[a] approve   [r] reject",
                    output_console,
                )
                f1_action = typer.prompt("F1 deliverable", default="a").strip().lower()

            # Build input_refs from whichever upstream shelves fed F1
            f1_input_refs: dict = {"r2": _active_ref(r2_active)}
            if d3_active:
                f1_input_refs["d3"] = _active_ref(d3_active)
            if p1_active:
                f1_input_refs["p1"] = _active_ref(p1_active)
            if p2_active:
                f1_input_refs["p2"] = _active_ref(p2_active)
            if p3_active:
                f1_input_refs["p3"] = _active_ref(p3_active)
            if s1_active:
                f1_input_refs["s1"] = _active_ref(s1_active)
            if c1_active:
                f1_input_refs["c1"] = _active_ref(c1_active)
            if c2_active:
                f1_input_refs["c2"] = _active_ref(c2_active)

            if f1_action in {"a", "approve"}:
                f1_profile_store.promote_active(
                    seed.id,
                    f1_payload,
                    run_id=run_id,
                    review_round=review_round,
                    input_refs=_checked_refs("F1", f1_input_refs, run_id),
                    archive_existing=not refresh,
                    llm_provenance=f1_runner.last_llm_provenance,
                )
                _write_json(
                    run_dir / f"f1_output_round_{review_round:02d}.json", f1_payload
                )
                output_console.print(
                    Panel(
                        "F1 deliverable approved and saved.",
                        title="F1 active",
                        border_style="green",
                    )
                )
            else:
                f1_profile_store.archive_candidate(
                    seed.id,
                    f1_payload,
                    status="human_rejected",
                    reason="F1 deliverable rejected by human",
                    run_id=run_id,
                    review_round=review_round,
                    input_refs=_checked_refs("F1", f1_input_refs, run_id),
                    llm_provenance=f1_runner.last_llm_provenance,
                )
                output_console.print(
                    Panel(
                        "F1 deliverable rejected. Upstream shelves remain active; F1 shelf is empty.",
                        title="F1 not active",
                        border_style="yellow",
                    )
                )

    active = load_active_set(seed.id)
    active = _write_run_snapshot(run_dir, active, run_id, "tabular")
    output_console.print(
        Panel(
            "All active agent profiles saved.",
            title="Agent-local profiles saved",
            border_style="green",
        )
    )
    return active


def _document_evidence_summary(rd3_payload: dict[str, Any]) -> dict[str, Any]:
    """Expose RD3's complete summary as the document-family CP1 evidence."""
    return {"rd3_summary": dict(rd3_payload["summary"])}


def build_document_pipeline(
    *,
    seed: UsecaseSeed,
    experiment_dir: Path,
    refresh: bool = False,
    rc1_1_agent: "BlindChangeMatcherAgent | None" = None,
    rc1_2_agent: "ChangeClassifierAgent | None" = None,
    rm1_1_agent: "BlindExposureMatcherAgent | None" = None,
    rm1_2_agent: "ExposureMapperAgent | None" = None,
    cp1_agent: "CaseProfilerAgent | None" = None,
    cp1_review_provider: "Callable[[dict[str, Any]], str] | None" = None,
    ts_agent: "TopologySelectorAgent | None" = None,
    ts_review_provider: "Callable[[dict[str, Any]], str] | None" = None,
    mapping_review_provider: "Callable[[dict[str, Any]], str] | None" = None,
    persona_agent_factory: "Callable[[str], PersonaAssessorAgent] | None" = None,
    f2_agent: "ImpactFinalizerAgent | None" = None,
    f2_review_provider: "Callable[[dict[str, Any]], str] | None" = None,
    output_console: Console = console,
) -> dict[str, Any] | None:
    """UC4 document pipeline: RC1.1/RC1.2/RM1.1/RM1.2 -> RD3 -> [gate] -> CP1 -> TS1 -> P4/P5/P6 -> F2 -> [gate].

    Parallel to build_profile_pipeline's tabular flow, kept as a separate
    function so the tabular pipeline (_run_post_r2_tail and everything it
    touches) is never modified by this addition. No resume/--from support
    yet for this family -- each run starts from RC1.
    """
    if seed.document_sources is None:
        output_console.print(
            Panel(
                f"'{seed.id}' has pipeline_family=\"document\" but no document_sources.",
                title="Configuration error",
                border_style="red",
            )
        )
        return None

    render_registry(seed, output_console)
    run_id = experiment_dir.name
    review_round = 1
    envelope = build_envelope(seed)
    run_dir = experiment_dir / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(run_dir)

    document_sources = seed.document_sources.model_dump()
    project_root = config.PROJECT_ROOT
    registry_ref = {"registry_sha256": _json_hash(seed.model_dump())}
    demand_snapshot = seed.demand_snapshot()
    demand_ref = {
        "registry_id": seed.id,
        "payload_sha256": _json_hash(demand_snapshot),
    }

    # ── RD1 ───────────────────────────────────────────────────────────────
    render_progress("RD1", [], output_console, steps=DOCUMENT_STEPS)
    try:
        bundle = extract_corpus(document_sources, project_root)
    except Exception as exc:
        output_console.print(Panel(str(exc), title="RD1 execution failed", border_style="red"))
        return None
    render_rd1(bundle, document_sources, output_console)
    _write_json(
        run_dir / "rd1_corpus_summary.json",
        {
            standard: {"n_old_drs": len(sc.old_drs), "n_new_drs": len(sc.new_drs), "n_hints": len(sc.hints)}
            for standard, sc in bundle.standards.items()
        },
    )

    # ── RD2 ───────────────────────────────────────────────────────────────
    render_progress("RD2", ["RD1"], output_console, steps=DOCUMENT_STEPS)
    try:
        bundle = map_candidates(bundle)
    except Exception as exc:
        output_console.print(Panel(str(exc), title="RD2 execution failed", border_style="red"))
        return None
    render_rd2(bundle, output_console)
    _write_json(
        run_dir / "rd2_candidates.json",
        {standard: [c.model_dump() for c in sc.candidates] for standard, sc in bundle.standards.items()},
    )

    # ── RC1.1: blind change matching (never sees RD2's candidates) ──────────
    render_progress("RC1.1", ["RD1", "RD2"], output_console, steps=DOCUMENT_STEPS)
    rc1_1_runner = rc1_1_agent or BlindChangeMatcherAgent()
    try:
        rc1_1_message = rc1_1_runner.execute_matching(
            envelope, store, registry_id=seed.id, bundle=bundle,
        )
    except Exception as exc:
        output_console.print(Panel(str(exc), title="RC1.1 execution failed", border_style="red"))
        return None
    rc1_1_payload = rc1_1_message.payload
    _write_json(run_dir / f"rc1_1_output_round_{review_round:02d}.json", rc1_1_payload)
    render_rc1_1(rc1_1_payload, output_console)

    # ── RC1.2: change classification (sees RC1.1's blind rows + RD2's candidates) ─
    render_progress("RC1.2", ["RD1", "RD2", "RC1.1"], output_console, steps=DOCUMENT_STEPS)
    rc1_2_runner = rc1_2_agent or ChangeClassifierAgent()
    try:
        rc1_2_message = rc1_2_runner.execute_classification(
            envelope, store, registry_id=seed.id, bundle=bundle, blind_rows=rc1_1_payload["rows"],
        )
    except Exception as exc:
        output_console.print(Panel(str(exc), title="RC1.2 execution failed", border_style="red"))
        return None
    rc1_2_payload = rc1_2_message.payload
    _write_json(run_dir / f"rc1_2_output_round_{review_round:02d}.json", rc1_2_payload)
    render_rc1(rc1_2_payload, output_console)

    # ── RM1.1: blind exposure matching (never sees RD2's index candidates) ──
    render_progress("RM1.1", ["RD1", "RD2", "RC1.1", "RC1.2"], output_console, steps=DOCUMENT_STEPS)
    rm1_1_runner = rm1_1_agent or BlindExposureMatcherAgent()
    try:
        rm1_1_message = rm1_1_runner.execute_matching(
            envelope, store, registry_id=seed.id, bundle=bundle,
        )
    except Exception as exc:
        output_console.print(Panel(str(exc), title="RM1.1 execution failed", border_style="red"))
        return None
    rm1_1_payload = rm1_1_message.payload
    _write_json(run_dir / f"rm1_1_output_round_{review_round:02d}.json", rm1_1_payload)
    render_rm1_1(rm1_1_payload, output_console)

    # ── RM1.2: Siemens exposure mapping (sees RM1.1's blind rows + RD2's index candidates) ─
    render_progress("RM1.2", ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1"], output_console, steps=DOCUMENT_STEPS)
    rm1_2_runner = rm1_2_agent or ExposureMapperAgent()
    try:
        rm1_2_message = rm1_2_runner.execute_mapping(
            envelope, store, registry_id=seed.id, bundle=bundle, blind_rows=rm1_1_payload["rows"],
        )
    except Exception as exc:
        output_console.print(Panel(str(exc), title="RM1.2 execution failed", border_style="red"))
        return None
    rm1_2_payload = rm1_2_message.payload
    _write_json(run_dir / f"rm1_2_output_round_{review_round:02d}.json", rm1_2_payload)
    render_rm1(rm1_2_payload, output_console)

    # ── RD3: deterministic full outer join of RC1.2 and RM1.2 ───────────────
    render_progress("RD3", ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1", "RM1.2"], output_console, steps=DOCUMENT_STEPS)
    rd3_result = build_join(seed.id, rc1_2_payload["rows"], rm1_2_payload["rows"])
    rd3_payload = rd3_result.model_dump()
    _write_json(run_dir / f"rd3_join_round_{review_round:02d}.json", rd3_payload)
    render_rd3(rd3_payload, output_console)

    # ── EX1: external perspective, before the Stage-1 (mapping) gate ────────
    render_progress("EX1", ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1", "RM1.2", "RD3"], output_console, steps=DOCUMENT_STEPS)
    ex1_runner = ExternalCommentatorAgent("EX1")
    try:
        ex1_msg = ex1_runner.execute_commentary(
            envelope,
            store,
            registry_id=seed.id,
            gate_payload={"rc1_2": rc1_2_payload, "rm1_2": rm1_2_payload, "rd3": rd3_payload},
            demand=seed.demand_snapshot(),
        )
    except Exception as exc:
        output_console.print(Panel(str(exc), title="EX1 skipped", border_style="warn"))
        ex1_payload = None
    else:
        ex1_payload = ex1_msg.payload
        _write_json(run_dir / f"ex1_output_round_{review_round:02d}.json", ex1_payload)
        render_ex(ex1_payload, output_console)
        ex_profile_store.promote_active(
            "EX1",
            seed.id,
            ex1_payload,
            run_id=run_id,
            review_round=review_round,
            input_refs=_checked_refs("EX1", {
                "gate_payload": {
                    "artifact_id": None,
                    "payload_sha256": _json_hash({"rc1_2": rc1_2_payload, "rm1_2": rm1_2_payload, "rd3": rd3_payload}),
                    "state": "candidate",
                },
            }, run_id),
            llm_provenance=ex1_runner.last_llm_provenance,
        )

    # ── Gate 1: mapping + exposure + join approval (locks the isolated memory) ─
    if mapping_review_provider is not None:
        mapping_action = mapping_review_provider(
            {"rc1_2": rc1_2_payload, "rm1_2": rm1_2_payload, "rd3": rd3_payload, "ex1": ex1_payload}
        ).strip().lower()
    else:
        render_action_panel(
            "Review the regulatory change classification (RC1.2), Siemens FY2025 "
            "exposure mapping (RM1.2), and their change/exposure join (RD3) above "
            "and choose an action.",
            "[a] approve   [r] reject",
            output_console,
        )
        mapping_action = typer.prompt("RC1.2/RM1.2/RD3 change + exposure join", default="a").strip().lower()

    if mapping_action in {"a", "approve"}:
        rc1_1_profile_store.promote_active(
            seed.id, rc1_1_payload, run_id=run_id, review_round=review_round,
            input_refs=registry_ref, archive_existing=not refresh,
            llm_provenance=rc1_1_runner.last_llm_provenance,
        )
        rc1_1_active = rc1_1_profile_store.load_active(seed.id)
        rc1_profile_store.promote_active(
            seed.id, rc1_2_payload, run_id=run_id, review_round=review_round,
            input_refs={"rc1_1": _active_ref(rc1_1_active)}, archive_existing=not refresh,
            llm_provenance=rc1_2_runner.last_llm_provenance,
        )
        rc1_active = rc1_profile_store.load_active(seed.id)
        rm1_1_profile_store.promote_active(
            seed.id, rm1_1_payload, run_id=run_id, review_round=review_round,
            input_refs=registry_ref, archive_existing=not refresh,
            llm_provenance=rm1_1_runner.last_llm_provenance,
        )
        rm1_1_active = rm1_1_profile_store.load_active(seed.id)
        rm1_profile_store.promote_active(
            seed.id, rm1_2_payload, run_id=run_id, review_round=review_round,
            input_refs={"rm1_1": _active_ref(rm1_1_active)}, archive_existing=not refresh,
            llm_provenance=rm1_2_runner.last_llm_provenance,
        )
        rm1_active = rm1_profile_store.load_active(seed.id)
        rd3_profile_store.promote_active(
            seed.id, rd3_payload, run_id=run_id, review_round=review_round,
            input_refs={"rc1_2": _active_ref(rc1_active), "rm1_2": _active_ref(rm1_active)},
            archive_existing=not refresh,
        )
        output_console.print(Panel("RC1.1/RC1.2/RM1.1/RM1.2/RD3 approved and saved.", title="Mapping active", border_style="green"))
        rd3_active = rd3_profile_store.load_active(seed.id)
    else:
        rc1_1_profile_store.archive_candidate(
            seed.id, rc1_1_payload, status="human_rejected", reason="RC1/RM1/RD3 mapping rejected by human",
            run_id=run_id, review_round=review_round, input_refs=registry_ref,
            llm_provenance=rc1_1_runner.last_llm_provenance,
        )
        rc1_profile_store.archive_candidate(
            seed.id, rc1_2_payload, status="human_rejected", reason="RC1/RM1/RD3 mapping rejected by human",
            run_id=run_id, review_round=review_round, input_refs=registry_ref,
            llm_provenance=rc1_2_runner.last_llm_provenance,
        )
        rm1_1_profile_store.archive_candidate(
            seed.id, rm1_1_payload, status="human_rejected", reason="RC1/RM1/RD3 mapping rejected by human",
            run_id=run_id, review_round=review_round, input_refs=registry_ref,
            llm_provenance=rm1_1_runner.last_llm_provenance,
        )
        rm1_profile_store.archive_candidate(
            seed.id, rm1_2_payload, status="human_rejected", reason="RC1/RM1/RD3 mapping rejected by human",
            run_id=run_id, review_round=review_round, input_refs=registry_ref,
            llm_provenance=rm1_2_runner.last_llm_provenance,
        )
        rd3_profile_store.archive_candidate(
            seed.id, rd3_payload, status="human_rejected", reason="RC1/RM1/RD3 mapping rejected by human",
            run_id=run_id, review_round=review_round, input_refs=registry_ref,
        )
        output_console.print(Panel("Mapping rejected. RC1.1/RC1.2/RM1.1/RM1.2/RD3 shelves are empty.", title="Not active", border_style="yellow"))
        active = load_document_active_set(seed.id)
        active = _write_run_snapshot(run_dir, active, run_id, "document")
        return active

    # ── CP1 (reused) ─────────────────────────────────────────────────────
    join_rows = rd3_active["payload"]["rows"]
    cp1_runner = cp1_agent or CaseProfilerAgent()
    cp1_context = {
        "pipeline_family": seed.pipeline_family,
        "demand": demand_snapshot,
        "approved_scope": None,
        "r2_in_scope_findings": None,
        "document_evidence": {
            "registry_id": seed.id,
            "standards": document_sources["standards"],
            "gate_1_approved_at": rd3_active.get("approved_at"),
            **_document_evidence_summary(rd3_active["payload"]),
        },
        "derived_case_facts": None,
        "data_quality": None,
    }
    try:
        cp1_message = cp1_runner.execute_preselection(envelope, store, approved_profile_context=cp1_context)
    except Exception as exc:
        output_console.print(Panel(str(exc), title="CP1 failed -- active CP1 unchanged", border_style="red"))
        active = load_document_active_set(seed.id)
        active = _write_run_snapshot(run_dir, active, run_id, "document")
        return active

    cp1_payload = cp1_message.payload
    _write_json(run_dir / f"cp1_output_round_{review_round:02d}.json", cp1_payload)
    render_progress("CP1", ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1", "RM1.2", "RD3"], output_console, steps=DOCUMENT_STEPS)
    render_cp1(cp1_payload, output_console)

    if cp1_review_provider is not None:
        cp1_action = cp1_review_provider(cp1_payload).strip().lower()
    else:
        render_action_panel(
            "Review the CP1 case profile above and choose an action.", "[a] approve   [r] reject", output_console,
        )
        cp1_action = typer.prompt("CP1 case profile", default="a").strip().lower()

    if cp1_action in {"a", "approve"}:
        cp1_profile_store.promote_active(
            seed.id, cp1_payload, run_id=run_id, review_round=review_round,
            input_refs={"rd3": _active_ref(rd3_active)},
            archive_existing=not refresh,
            llm_provenance=cp1_runner.last_llm_provenance,
        )
        output_console.print(Panel("CP1 case profile approved and saved.", title="CP1 active", border_style="green"))
        cp1_active = cp1_profile_store.load_active(seed.id)
    else:
        cp1_profile_store.archive_candidate(
            seed.id, cp1_payload, status="human_rejected", reason="CP1 case profile rejected by human",
            run_id=run_id, review_round=review_round,
            input_refs={"rd3": _active_ref(rd3_active)},
            llm_provenance=cp1_runner.last_llm_provenance,
        )
        output_console.print(Panel("CP1 profile rejected. RC1/RM1/RD3 remain active; CP1 shelf is empty.", title="CP1 not active", border_style="yellow"))
        active = load_document_active_set(seed.id)
        active = _write_run_snapshot(run_dir, active, run_id, "document")
        return active

    # ── TS1 (reused) ─────────────────────────────────────────────────────
    ts_runner = ts_agent or TopologySelectorAgent()
    try:
        ts_message = ts_runner.execute_selection(envelope, store, case_profile=cp1_active["payload"])
    except Exception as exc:
        output_console.print(Panel(str(exc), title="TS1 failed -- active TS1 unchanged", border_style="red"))
        active = load_document_active_set(seed.id)
        active = _write_run_snapshot(run_dir, active, run_id, "document")
        return active

    ts_payload = ts_message.payload
    _write_json(run_dir / f"ts1_output_round_{review_round:02d}.json", ts_payload)
    render_progress("TS1", ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1", "RM1.2", "RD3", "CP1"], output_console, steps=DOCUMENT_STEPS)
    render_ts1(ts_payload, output_console)

    if ts_review_provider is not None:
        ts_action = ts_review_provider(ts_payload).strip().lower()
    else:
        render_action_panel(
            "Review the TS1 topology selection above and choose an action.", "[a] approve   [r] reject", output_console,
        )
        ts_action = typer.prompt("TS1 topology selection", default="a").strip().lower()

    if ts_action in {"a", "approve"}:
        topology_selector_store.promote_active(
            seed.id, ts_payload, run_id=run_id, review_round=review_round,
            input_refs={"cp1": _active_ref(cp1_active)}, archive_existing=not refresh,
            llm_provenance=ts_runner.last_llm_provenance,
        )
        output_console.print(Panel("TS1 topology selection approved and saved.", title="TS1 active", border_style="green"))
        ts_active = topology_selector_store.load_active(seed.id)
    else:
        topology_selector_store.archive_candidate(
            seed.id, ts_payload, status="human_rejected", reason="TS1 topology selection rejected by human",
            run_id=run_id, review_round=review_round, input_refs={"cp1": _active_ref(cp1_active)},
            llm_provenance=ts_runner.last_llm_provenance,
        )
        output_console.print(Panel("TS1 selection rejected. CP1 remains active; TS1 shelf is empty.", title="TS1 not active", border_style="yellow"))
        active = load_document_active_set(seed.id)
        active = _write_run_snapshot(run_dir, active, run_id, "document")
        return active

    # ── P4 / P5 / P6 (always all three, concurrent, independent) ───────────
    # Personas read the human-approved RD3 shelf snapshot, not a fresh re-run --
    # this is the isolated-memory rule applied to UC4. All three evaluate the
    # exact same join_rows, independently of which topology TS1 selected.
    selected_topology = ts_active["payload"].get("selected_topology_id", "-")
    output_console.print(
        Panel(
            f"TS1 selected [bold]{selected_topology}[/bold]. All three persona assessors "
            f"(P4 Conservative, P5 Balanced, P6 Maximum-Assurance) evaluate the same RD3 "
            f"change/exposure join table independently of topology -- see registry.py's "
            f"UC4 note.",
            border_style="accent",
        )
    )
    persona_payloads: dict[str, dict[str, Any]] = {}
    _make_persona = persona_agent_factory or PersonaAssessorAgent
    persona_runners = {cid: _make_persona(cid) for cid in ("P4", "P5", "P6")}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(
                persona_runners[cid].execute_assessment,
                envelope, store, registry_id=seed.id, join_rows=join_rows,
            ): cid
            for cid in ("P4", "P5", "P6")
        }
        for future in futures:
            cid = futures[future]
            try:
                message = future.result()
            except Exception as exc:
                output_console.print(Panel(str(exc), title=f"{cid} execution failed", border_style="red"))
                active = load_document_active_set(seed.id)
                active = _write_run_snapshot(run_dir, active, run_id, "document")
                return active
            persona_payloads[cid] = message.payload
            persona_profile_store.promote_active(
                cid, seed.id, message.payload, run_id=run_id, review_round=review_round,
                input_refs=_checked_refs(cid, {"rd3": _active_ref(rd3_active)}, run_id),
                archive_existing=not refresh,
                llm_provenance=persona_runners[cid].last_llm_provenance,
            )

    for step_key, cid in (("P4", "P4"), ("P5", "P5"), ("P6", "P6")):
        render_progress(step_key, ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1", "RM1.2", "RD3", "CP1", "TS1"], output_console, steps=DOCUMENT_STEPS)
        render_persona(cid, persona_payloads[cid], output_console)
        _write_json(run_dir / f"{cid.lower()}_output_round_{review_round:02d}.json", persona_payloads[cid])

    # ── F2 (final agent -- holds Gate 2) ────────────────────────────────────
    render_progress("F2", ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1", "RM1.2", "RD3", "CP1", "TS1", "P4", "P5", "P6"], output_console, steps=DOCUMENT_STEPS)
    f2_runner = f2_agent or ImpactFinalizerAgent()
    try:
        f2_message = f2_runner.execute_finalization(
            envelope, store, registry_id=seed.id, join_rows=join_rows,
            persona_results={cid: persona_payloads[cid]["rows"] for cid in ("P4", "P5", "P6")},
            demand=demand_snapshot,
        )
    except Exception as exc:
        output_console.print(Panel(str(exc), title="F2 execution failed", border_style="red"))
        active = load_document_active_set(seed.id)
        active = _write_run_snapshot(run_dir, active, run_id, "document")
        return active

    f2_payload = f2_message.payload
    _write_json(run_dir / f"f2_output_round_{review_round:02d}.json", f2_payload)
    render_f2(f2_payload, output_console)

    # ── EX2: external perspective, before the Stage-2 (F2) gate ─────────────
    render_progress(
        "EX2",
        ["RD1", "RD2", "RC1.1", "RC1.2", "RM1.1", "RM1.2", "RD3", "CP1", "TS1", "P4", "P5", "P6"],
        output_console, steps=DOCUMENT_STEPS,
    )
    ex2_runner = ExternalCommentatorAgent("EX2")
    try:
        ex2_msg = ex2_runner.execute_commentary(
            envelope,
            store,
            registry_id=seed.id,
            gate_payload=f2_payload,
            demand=demand_snapshot,
        )
    except Exception as exc:
        output_console.print(Panel(str(exc), title="EX2 skipped", border_style="warn"))
        ex2_payload = None
    else:
        ex2_payload = ex2_msg.payload
        _write_json(run_dir / f"ex2_output_round_{review_round:02d}.json", ex2_payload)
        render_ex(ex2_payload, output_console)
        ex_profile_store.promote_active(
            "EX2",
            seed.id,
            ex2_payload,
            run_id=run_id,
            review_round=review_round,
            input_refs=_checked_refs("EX2", {
                "gate_payload": {
                    "artifact_id": None,
                    "payload_sha256": _json_hash(f2_payload),
                    "state": "candidate",
                },
            }, run_id),
            llm_provenance=ex2_runner.last_llm_provenance,
        )
    f2_gate_payload = {**f2_payload, "ex2": ex2_payload}

    if f2_review_provider is not None:
        f2_action = f2_review_provider(f2_gate_payload).strip().lower()
    else:
        render_action_panel(
            "Review the final ESRS impact report above and choose an action.", "[a] approve   [r] reject", output_console,
        )
        f2_action = typer.prompt("F2 ESRS impact report", default="a").strip().lower()

    f2_input_refs = {
        "rd3": _active_ref(rd3_active),
        "p4": _active_ref(persona_profile_store.load_active("P4", seed.id)),
        "p5": _active_ref(persona_profile_store.load_active("P5", seed.id)),
        "p6": _active_ref(persona_profile_store.load_active("P6", seed.id)),
        "demand": demand_ref,
    }
    if f2_action in {"a", "approve"}:
        f2_profile_store.promote_active(
            seed.id, f2_payload, run_id=run_id, review_round=review_round,
            input_refs=_checked_refs("F2", f2_input_refs, run_id), archive_existing=not refresh,
            llm_provenance=f2_runner.last_llm_provenance,
        )
        output_console.print(Panel("F2 ESRS impact report approved and saved.", title="F2 active", border_style="green"))
    else:
        f2_profile_store.archive_candidate(
            seed.id, f2_payload, status="human_rejected", reason="F2 impact report rejected by human",
            run_id=run_id, review_round=review_round, input_refs=_checked_refs("F2", f2_input_refs, run_id),
            llm_provenance=f2_runner.last_llm_provenance,
        )
        output_console.print(Panel("F2 report rejected. F2 shelf is empty.", title="F2 not active", border_style="yellow"))

    active = load_document_active_set(seed.id)
    active = _write_run_snapshot(run_dir, active, run_id, "document")
    output_console.print(Panel("All active agent profiles saved.", title="Agent-local profiles saved", border_style="green"))
    return active


def build_profile_pipeline(
    *,
    seed: UsecaseSeed,
    experiment_dir: Path,
    refresh: bool,
    start_from: str | None = None,
    decider: ReviewDecider = interactive_decider,
    batch_reviewer: "BatchReviewer | None" = None,
    approval_provider: Callable[[], bool] | None = None,
    r2_review_provider: Callable[[dict[str, Any]], str] | None = None,
    r2_agent: RegulationScopeReviewer | None = None,
    d2_agent: MissingValueAnalyst | None = None,
    cp1_agent: CaseProfilerAgent | None = None,
    cp1_review_provider: Callable[[dict[str, Any]], str] | None = None,
    ts_agent: TopologySelectorAgent | None = None,
    ts_review_provider: Callable[[dict[str, Any]], str] | None = None,
    f1_review_provider: Callable[[dict[str, Any]], str] | None = None,
    mapping_review_provider: Callable[[dict[str, Any]], str] | None = None,
    f2_review_provider: Callable[[dict[str, Any]], str] | None = None,
    output_console: Console = console,
) -> dict[str, Any] | None:
    if seed.pipeline_family == "document":
        # UC4 and any future document-family use case bypass the tabular
        # R1/DA1/R2/D1/D2/D3 chain entirely -- see build_document_pipeline's
        # own docstring. Every kwarg above that only that chain understands
        # (decider, batch_reviewer, r2_*, d2_agent, f1_review_provider, ...)
        # is silently unused here, same as the tabular path silently ignores
        # mapping_review_provider/f2_review_provider.
        return build_document_pipeline(
            seed=seed,
            experiment_dir=experiment_dir,
            refresh=refresh,
            cp1_agent=cp1_agent,
            cp1_review_provider=cp1_review_provider,
            ts_agent=ts_agent,
            ts_review_provider=ts_review_provider,
            mapping_review_provider=mapping_review_provider,
            f2_review_provider=f2_review_provider,
            output_console=output_console,
        )

    render_registry(seed, output_console)
    run_id = experiment_dir.name
    envelope = build_envelope(seed)
    run_dir = experiment_dir / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    store = EvidenceStore(run_dir)

    # ── Resume fast path ────────────────────────────────────────────────────
    # When start_from is set, skip R1/DA1 and the review loop entirely.
    # All upstream agents are rehydrated from their latest shelves.
    if start_from is not None:
        r1_active = r1_profile_store.load_active(seed.id)
        da1_active = da1_profile_store.load_active(seed.id)
        r2_active = r2_profile_store.load_active(seed.id)
        if not all((r1_active, da1_active, r2_active)):
            output_console.print(
                Panel(
                    "Cannot resume: R1, DA1, and R2 shelves must all be present. "
                    "Run the full pipeline first.",
                    title="Resume aborted",
                    border_style="red",
                )
            )
            return None
        handoff = da1_active["payload"]["handoff_data_request"]
        approved_scope = da1_active["payload"]["approved_mapping_scope"]
        r2_profile = r2_active["payload"]
        review_round = r2_active.get("review_round") or 1
        output_console.print(
            Panel(
                f"Resuming from {start_from} -- upstream agents loaded from shelves.",
                title="Resume mode",
                border_style="blue",
            )
        )
        # EX1 is advisory to the Stage-1 (R2) human gate only; a resume starts
        # past that gate, so there is nothing left for EX1 to advise on. No new
        # EX1 shelf is written this run -- state that plainly instead of
        # silently producing an EX2-only run.
        output_console.print(
            Panel(
                "EX1 did not run: this resume starts after the Stage-1 (R2) gate, "
                "so no Stage-1 gate remains for it to advise on.",
                title="EX1 skipped",
                border_style="warn",
            )
        )
        return _run_post_r2_tail(
            seed=seed,
            envelope=envelope,
            store=store,
            run_dir=run_dir,
            run_id=run_id,
            review_round=review_round,
            refresh=False,
            handoff=handoff,
            approved_scope=approved_scope,
            r2_profile=r2_profile,
            r1_active=r1_active,
            da1_active=da1_active,
            r2_active=r2_active,
            start_at=start_from,
            d2_agent=d2_agent,
            cp1_agent=cp1_agent,
            ts_agent=ts_agent,
            cp1_review_provider=cp1_review_provider,
            ts_review_provider=ts_review_provider,
            f1_review_provider=f1_review_provider,
            output_console=output_console,
        )

    render_progress("R1", [], output_console)
    if refresh:
        snapshot_active_set(seed.id, run_id)
    r1 = ActiveRegulationReader(get_llm())
    try:
        profile = r1.execute_preselection(
            envelope,
            store,
            cache_mode="refresh" if refresh else "use",
        ).payload
    except Exception as exc:
        output_console.print(
            Panel(
                str(exc),
                title="R1 validation failed after repair attempts — active R1 unchanged",
                border_style="red",
            )
        )
        return None
    _write_json(run_dir / "r1_output.json", profile)
    render_r1(profile, output_console)
    if profile.get("verdict") != "grounded":
        r1_profile_store.archive_candidate(
            seed.id,
            profile,
            status="insufficient",
            reason=profile.get("stop_reason") or "R1 could not ground the registry",
            run_id=run_id,
            review_round=None,
            input_refs={"registry_sha256": _json_hash(seed.model_dump())},
            llm_provenance=r1.last_llm_provenance,
        )
        output_console.print(
            Panel(profile.get("stop_reason", ""), title="Pipeline stopped", border_style="warn")
        )
        return None

    da1_runner = DataRequestPlannerAgent()
    da1_message = da1_runner.execute(
        "# Human-reviewed registry profile preparation", envelope, store
    )
    catalog = da1_message.payload["catalog"]
    report = da1_message.payload["mapping_report"]
    _write_json(run_dir / "data_catalog.json", catalog)
    _write_json(run_dir / "da1_mapping_report.json", report)
    render_progress("DA1", ["R1"], output_console)
    render_da1(report, output_console)
    if not report.get("validation", {}).get("ok", False):
        r1_profile_store.archive_candidate(
            seed.id,
            profile,
            status="unapproved_candidate",
            reason="DA1 returned invalid catalog targets",
            run_id=run_id,
            review_round=None,
            input_refs={"registry_sha256": _json_hash(seed.model_dump())},
            llm_provenance=r1.last_llm_provenance,
        )
        da1_profile_store.archive_candidate(
            seed.id,
            {"catalog_snapshot": catalog, "mapping_report": report},
            status="validation_failed",
            reason="DA1 returned invalid catalog targets",
            run_id=run_id,
            review_round=None,
            input_refs={},
            llm_provenance=da1_runner.last_llm_provenance,
        )
        output_console.print(
            Panel(
                "DA1 returned structurally invalid catalog targets. No review was saved.",
                title="Pipeline stopped",
                border_style="error",
            )
        )
        return None

    reviewer = r2_agent or RegulationScopeReviewer()
    review_round = 0
    while True:
        review_round += 1
        decisions = review_mappings(
            report, catalog, decider=decider, batch_reviewer=batch_reviewer
        )
        _write_json(
            run_dir / f"human_decisions_round_{review_round:02d}.json",
            decisions,
        )
        if approval_provider is None:
            render_action_panel(
                "Are the DA1 catalog mappings above sufficient to proceed to R2 scope review?",
                "[y] yes — continue to R2   [n] no — discard and stop",
                output_console,
            )
        da1_approved = (
            approval_provider()
            if approval_provider is not None
            else typer.confirm(
                "Proceed to R2 scope review?",
                default=True,
            )
        )
        if not da1_approved:
            r1_profile_store.archive_candidate(
                seed.id,
                profile,
                status="human_rejected",
                reason="DA1 mapping scope was marked insufficient",
                run_id=run_id,
                review_round=review_round,
                input_refs={"registry_sha256": _json_hash(seed.model_dump())},
                llm_provenance=r1.last_llm_provenance,
            )
            da1_profile_store.archive_candidate(
                seed.id,
                {
                    "catalog_snapshot": catalog,
                    "mapping_report": report,
                    "human_decisions": decisions,
                },
                status="human_rejected",
                reason="DA1 mapping scope was marked insufficient",
                run_id=run_id,
                review_round=review_round,
                input_refs={},
                llm_provenance=da1_runner.last_llm_provenance,
            )
            output_console.print(
                Panel(
                    "DA1 review marked insufficient. No active profile was changed.",
                    title="Profiles unchanged",
                    border_style="yellow",
                )
            )
            return None

        approved_scope = build_approved_mapping_scope(seed, report, decisions)
        r1_profile_store.promote_active(
            seed.id,
            profile,
            run_id=run_id,
            review_round=review_round,
            input_refs={"registry_sha256": _json_hash(seed.model_dump())},
            archive_existing=not refresh,
            llm_provenance=r1.last_llm_provenance,
        )
        r1_active = r1_profile_store.load_active(seed.id)
        da1_payload = {
            "catalog_snapshot": catalog,
            "mapping_report": report,
            "human_decisions": decisions,
            "approved_mapping_scope": approved_scope,
            "handoff_data_request": approved_scope["handoff_data_request"],
        }
        da1_profile_store.promote_active(
            seed.id,
            da1_payload,
            run_id=run_id,
            review_round=review_round,
            input_refs={"r1": _active_ref(r1_active)},
            archive_existing=not refresh,
            llm_provenance=da1_runner.last_llm_provenance,
        )
        da1_active = da1_profile_store.load_active(seed.id)
        store.write(
            f"approved_mapping_scope_{envelope.case_id}",
            approved_scope,
        )
        _write_json(
            run_dir / f"approved_mapping_scope_round_{review_round:02d}.json",
            approved_scope,
        )

        try:
            r2_profile = reviewer.review(
                registry_snapshot=seed.operative_snapshot(),
                r1_profile=profile,
                approved_mapping_scope=approved_scope,
            )
        except Exception as exc:
            output_console.print(
                Panel(
                    str(exc),
                    title="R2 validation failed - active R2 unchanged",
                    border_style="red",
                )
            )
            return None
        _write_json(
            run_dir / f"r2_output_round_{review_round:02d}.json",
            r2_profile,
        )
        render_progress("R2", ["R1", "DA1"], output_console)
        render_r2(r2_profile, output_console)

        render_progress("EX1", ["R1", "DA1"], output_console)
        ex1_runner = ExternalCommentatorAgent("EX1")
        try:
            ex1_msg = ex1_runner.execute_commentary(
                envelope,
                store,
                registry_id=seed.id,
                gate_payload=r2_profile,
                demand=seed.demand_snapshot(),
            )
        except Exception as exc:
            output_console.print(Panel(str(exc), title="EX1 skipped", border_style="warn"))
            ex1_payload = None
        else:
            ex1_payload = ex1_msg.payload
            _write_json(run_dir / f"ex1_output_round_{review_round:02d}.json", ex1_payload)
            render_ex(ex1_payload, output_console)
            ex_profile_store.promote_active(
                "EX1",
                seed.id,
                ex1_payload,
                run_id=run_id,
                review_round=review_round,
                input_refs=_checked_refs("EX1", {
                    "gate_payload": {
                        "artifact_id": None,
                        "payload_sha256": _json_hash(r2_profile),
                        "state": "candidate",
                    },
                    "r1": _active_ref(r1_active),
                    "da1": _active_ref(da1_active),
                }, run_id),
                llm_provenance=ex1_runner.last_llm_provenance,
            )
        # "ex1" is added only to the gate-display payload, never to r2_profile
        # itself -- r2_profile is what gets promoted into R2's own shelf, and
        # EX's advisory commentary must never become part of the isolated
        # memory (AGENTS.md hard rule).
        r2_gate_payload = {**r2_profile, "ex1": ex1_payload}

        if r2_review_provider is not None:
            action = r2_review_provider(r2_gate_payload).strip().lower()
        else:
            render_action_panel(
                "Review the R2 scope profile above and choose an action.",
                "[a] approve   [b] back to DA1   [q] quit",
                output_console,
            )
            action = typer.prompt(
                "R2 scope",
                default="a",
            ).strip().lower()
        reviewed_at = datetime.now(timezone.utc).isoformat()
        if action in {"a", "approve"}:
            review = {
                "decision": "approved",
                "reviewed_at": reviewed_at,
                "blocked_acknowledged": [
                    item["r1_finding_id"]
                    for item in r2_profile.get("blocked_findings", [])
                ],
                "excluded_acknowledged": [
                    item["r1_finding_id"]
                    for item in r2_profile.get("excluded_findings", [])
                ],
            }
            r2_profile_store.promote_active(
                seed.id,
                {**r2_profile, "human_review": review},
                run_id=run_id,
                review_round=review_round,
                input_refs={
                    "r1": _active_ref(r1_active),
                    "da1": _active_ref(da1_active),
                },
                approved_at=reviewed_at,
                archive_existing=not refresh,
                llm_provenance=reviewer.last_llm_provenance,
            )
            r2_active = r2_profile_store.load_active(seed.id)

            # ── D1 → O2 (delegated to _run_post_r2_tail) ────────────────────
            handoff = approved_scope["handoff_data_request"]
            return _run_post_r2_tail(
                seed=seed,
                envelope=envelope,
                store=store,
                run_dir=run_dir,
                run_id=run_id,
                review_round=review_round,
                refresh=refresh,
                handoff=handoff,
                approved_scope=approved_scope,
                r2_profile=r2_profile,
                r1_active=r1_active,
                da1_active=da1_active,
                r2_active=r2_active,
                start_at="D1",
                d2_agent=d2_agent,
                cp1_agent=cp1_agent,
                ts_agent=ts_agent,
                cp1_review_provider=cp1_review_provider,
                ts_review_provider=ts_review_provider,
                f1_review_provider=f1_review_provider,
                output_console=output_console,
            )
        if action in {"b", "back"}:
            r2_profile_store.save_attempt(
                seed.id,
                r2_profile,
                review_status="rejected",
                reviewed_at=reviewed_at,
                run_id=run_id,
                review_round=review_round,
                input_refs={
                    "r1": _active_ref(r1_active),
                    "da1": _active_ref(da1_active),
                },
            )
            output_console.print(
                Panel(
                    "R2 scope rejected. Returning to DA1 mapping review.",
                    title="Human review loop",
                    border_style="yellow",
                )
            )
            continue
        r2_profile_store.save_attempt(
            seed.id,
            r2_profile,
            review_status="abandoned",
            reviewed_at=reviewed_at,
            run_id=run_id,
            review_round=review_round,
            input_refs={
                "r1": _active_ref(r1_active),
                "da1": _active_ref(da1_active),
            },
        )
        output_console.print(
            Panel(
                "R2 scope was not approved. R1/DA1 remain active; handoff is blocked.",
                title="R2 not active",
                border_style="yellow",
            )
        )
        return None


def render_active_set(
    registry_id: str,
    active: dict[str, Any],
    output_console: Console = console,
) -> None:
    table = Table(title=f"Agent-Local Profiles | {registry_id}", box=box.ROUNDED)
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Artifact")
    table.add_column("Approved")
    table.add_column("Archive")
    stores = {
        "R1": r1_profile_store,
        "DA1": da1_profile_store,
        "R2": r2_profile_store,
        "CP1": cp1_profile_store,
        "D1": d1_profile_store,
        "D2": d2_profile_store,
        "TS1": topology_selector_store,
        "D3": d3_profile_store,
        "P1": p1_profile_store,
    }
    for key, label in (
        ("r1", "R1"), ("da1", "DA1"), ("r2", "R2"),
        ("cp1", "CP1"), ("d1", "D1"), ("d2", "D2"), ("ts", "TS1"), ("d3", "D3"),
        ("p1", "P1"),
    ):
        record = active.get(key)
        table.add_row(
            label,
            record.get("status", "") if record else "missing",
            record.get("artifact_id", "") if record else "-",
            str(record.get("approved_at", "")) if record else "-",
            str(len(stores[label].list_archive(registry_id))),
        )
    for key, label in (("ex1", "EX1"), ("ex2", "EX2")):
        record = active.get(key)
        table.add_row(
            f"{label} (advisory)",
            record.get("status", "") if record else "missing",
            record.get("artifact_id", "") if record else "-",
            str(record.get("approved_at", "")) if record else "-",
            str(len(ex_profile_store.list_archive(label, registry_id))),
        )
    output_console.print(table)
    if active.get("r2"):
        render_r2(active["r2"]["payload"], output_console)
    if active.get("ex1"):
        render_ex(active["ex1"]["payload"], output_console)
    if active.get("cp1"):
        render_cp1(active["cp1"]["payload"], output_console)
    if active.get("ts"):
        render_ts1(active["ts"]["payload"], output_console)
    if active.get("d1"):
        render_d1(active["d1"]["payload"], output_console)
    if active.get("d3"):
        render_d3(active["d3"]["payload"], output_console)
    if active.get("p1"):
        render_p1(active["p1"]["payload"], output_console)
    if active.get("ex2"):
        render_ex(active["ex2"]["payload"], output_console)
    if not active_set_ready(active):
        output_console.print(
            Panel(
                "R1, DA1, R2, D1, D2, CP1, and TS1 are missing or do not belong to the same approved chain. D3 and P1 run after TS1 approval.",
                title="Handoff blocked",
                border_style="yellow",
            )
        )


def _all_catalog_targets(catalog: dict[str, Any]) -> list[dict[str, str]]:
    targets = [
        {"kind": "source_domain", "name": name}
        for name in ("energy", "kpi", "energy_and_kpi", "emissions")
    ]
    targets.extend(
        {"kind": "entity_grain", "name": name}
        for name in catalog["allowed_grains"]["entity_grain"]
    )
    targets.extend(
        {"kind": "time_grain", "name": name}
        for name in catalog["allowed_grains"]["time_grain"]
    )
    targets.extend(
        {"kind": "measure", "name": name}
        for group in catalog["measures"].values()
        for name in group
    )
    targets.extend(
        {"kind": "filter", "name": name}
        for group in catalog["selectable_filters"].values()
        for name in group
    )
    return targets


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _seed(value: str | None) -> UsecaseSeed:
    return get(value or choose_usecase())


def _new_experiment(seed: UsecaseSeed, action: str) -> Path:
    return config.create_experiment_dir(
        "regulation_cli",
        description=f"{seed.id}: registry profile {action}",
        visibility="public" if seed.pipeline_family == "document" else "real",
    )


@app.command("build")
def build_command(
    usecase: str | None = typer.Option(None, "--usecase", "-u"),
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Auto-approve every human gate using default answers (no interactive prompts).",
    ),
    repeat: int = typer.Option(
        1,
        "--repeat",
        min=1,
        help="Run the full pipeline N times. Requires --auto.",
    ),
    resume_from: str | None = typer.Option(
        None,
        "--from",
        help=(
            "Resume from a specific agent (D2|D3|P1|F1). "
            "Upstream agents are reloaded from their latest shelves; "
            "upstream human gates are not re-shown. "
            "Cannot be combined with --auto."
        ),
    ),
) -> None:
    if repeat > 1 and not auto:
        raise typer.BadParameter("--repeat requires --auto.", param_hint="--repeat")
    if resume_from is not None and auto:
        raise typer.BadParameter("--from cannot be combined with --auto.", param_hint="--from")

    # ── resume path ──────────────────────────────────────────────────────────
    if resume_from is not None:
        _VALID_FROM = {"D2", "D3", "P1", "O2"}
        agent_key = resume_from.strip().upper()
        if agent_key not in _VALID_FROM:
            raise typer.BadParameter(
                f"--from must be one of {sorted(_VALID_FROM)}; got {resume_from!r}.",
                param_hint="--from",
            )
        seed = _seed(usecase)
        active = load_active_set(seed.id)
        # Define which shelves must exist for each entry point.
        _REQUIRED: dict[str, list[str]] = {
            "D2": ["r1", "da1", "r2", "d1"],
            "D3": ["r1", "da1", "r2", "d1", "d2", "cp1", "ts"],
            "P1": ["r1", "da1", "r2", "d1", "d2", "cp1", "ts", "d3"],
            "O2": ["r1", "da1", "r2", "d1", "d2", "cp1", "ts", "d3", "p1"],
        }
        missing = [k for k in _REQUIRED[agent_key] if not active.get(k)]
        if missing:
            console.print(
                Panel(
                    f"Cannot resume from {agent_key}: the following shelves are missing or empty: "
                    f"{missing}. Run the full pipeline first to populate them.",
                    title="Resume aborted",
                    border_style="red",
                )
            )
            raise typer.Exit(code=1)
        result = build_profile_pipeline(
            seed=seed,
            experiment_dir=_new_experiment(seed, f"resume-{agent_key}"),
            refresh=False,
            start_from=agent_key,
        )
        if result is None:
            raise typer.Exit(code=1)
        return

    if not auto:
        # ── interactive single-run (original behaviour) ──────────────────────
        seed = _seed(usecase)
        if any(load_active_set(seed.id).values()):
            raise typer.BadParameter(
                "Agent-local active profiles already exist; use edit or refresh.",
                param_hint="--usecase",
            )
        result = build_profile_pipeline(
            seed=seed,
            experiment_dir=_new_experiment(seed, "build"),
            refresh=False,
        )
        if result is None:
            raise typer.Exit(code=1)
        return

    # ── auto mode ────────────────────────────────────────────────────────────
    if usecase is None:
        raise typer.BadParameter(
            "--auto requires --usecase / -u (interactive registry selection is disabled in auto mode).",
            param_hint="--usecase",
        )
    seed = _seed(usecase)

    _DASH = "—"

    def _extract_run_signals(result: dict[str, Any] | None) -> dict[str, str]:
        if result is None:
            return {k: _DASH for k in ("topology", "artifact", "dual_method", "clusters", "cross_fn", "uncertainty")}
        ts = (result.get("ts") or {}).get("payload", {})
        cp1 = (result.get("cp1") or {}).get("payload", {})
        return {
            "topology": ts.get("selected_topology_id", _DASH),
            "artifact": cp1.get("assurance_artifact", _DASH),
            "dual_method": str(cp1.get("dual_method_required", _DASH)),
            "clusters": str(cp1.get("cluster_count", _DASH)),
            "cross_fn": str(cp1.get("cross_functional_need", _DASH)),
            "uncertainty": cp1.get("uncertainty", _DASH),
        }

    runs: list[tuple[int, str, str, dict[str, str]]] = []

    for i in range(repeat):
        has_active = any(load_active_set(seed.id).values())
        refresh = i > 0 or has_active
        label = f"auto build {i + 1}/{repeat}"
        experiment_dir = _new_experiment(seed, label)
        result = build_profile_pipeline(
            seed=seed,
            experiment_dir=experiment_dir,
            refresh=refresh,
            **_AUTO_PROVIDERS,
        )
        status = "PASS" if result is not None else "FAIL"
        signals = _extract_run_signals(result)
        runs.append((i + 1, experiment_dir.name, status, signals))
        console.print(f"[bold]Run {i + 1}/{repeat}[/bold] → {status}")

    # summary table
    from rich.table import Table

    table = Table(title=f"Auto-build summary — {seed.id}", show_lines=False)
    table.add_column("Run", style="bold", justify="right")
    table.add_column("Experiment dir", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Topology", justify="center")
    table.add_column("Artifact", justify="center")
    table.add_column("DualM", justify="center")
    table.add_column("Clusters", justify="center")
    table.add_column("CrossFn", justify="center")
    table.add_column("Uncertainty", justify="center")
    for run_no, dir_name, status, sig in runs:
        s_style = "green" if status == "PASS" else "red"
        topo = sig["topology"]
        topo_style = {"Direct": "cyan", "Debate": "yellow", "Coalition": "magenta"}.get(topo, "")
        topo_cell = f"[{topo_style}]{topo}[/{topo_style}]" if topo_style else topo
        table.add_row(
            str(run_no),
            dir_name,
            f"[{s_style}]{status}[/{s_style}]",
            topo_cell,
            sig["artifact"],
            sig["dual_method"],
            sig["clusters"],
            sig["cross_fn"],
            sig["uncertainty"],
        )
    console.print(table)

    passed = sum(1 for _, _, s, _ in runs if s == "PASS")
    console.print(f"{passed}/{repeat} passed.")
    if passed < repeat:
        raise typer.Exit(code=1)


@app.command("refresh")
def refresh_command(
    usecase: str | None = typer.Option(None, "--usecase", "-u"),
) -> None:
    seed = _seed(usecase)
    result = build_profile_pipeline(
        seed=seed,
        experiment_dir=_new_experiment(seed, "refresh"),
        refresh=True,
    )
    if result is None:
        raise typer.Exit(code=1)


@app.command("show")
def show_command(
    usecase: str | None = typer.Option(None, "--usecase", "-u"),
) -> None:
    seed = _seed(usecase)
    active = load_active_set(seed.id)
    if not any(active.values()):
        console.print("No active agent-local profiles exist.")
        raise typer.Exit(code=1)
    render_active_set(seed.id, active)


@app.command("edit")
def edit_command(
    usecase: str | None = typer.Option(None, "--usecase", "-u"),
) -> None:
    seed = _seed(usecase)
    if not any(load_active_set(seed.id).values()):
        console.print("No active profiles exist; run build first.")
        raise typer.Exit(code=1)
    result = build_profile_pipeline(
        seed=seed,
        experiment_dir=_new_experiment(seed, "edit"),
        refresh=False,
    )
    if result is None:
        raise typer.Exit(code=1)


@app.command("handoff")
def handoff_command(
    usecase: str | None = typer.Option(None, "--usecase", "-u"),
) -> None:
    seed = _seed(usecase)
    active = load_active_set(seed.id)
    render_active_set(seed.id, active)
    if not active_set_ready(active):
        console.print("No compatible R1/DA1/R2 active set is available for handoff.")
        raise typer.Exit(code=1)
    console.print(
        Panel(
            "R1, DA1, R2, D1, and CP1 active profiles are ready. "
            "The full profile pipeline is complete.",
            title="Pipeline boundary",
            border_style="success",
        )
    )


@app.command()
def data(
    usecase: str | None = typer.Option(None, "--usecase", "-u"),
) -> None:
    """Execute D1 data loading from approved handoff_data_request."""
    seed = _seed(usecase)
    active = load_active_set(seed.id)

    if not all((active["da1"], active["r2"])):
        console.print(
            Panel(
                "DA1 and R2 active profiles are required before D1.\n"
                "Run 'epoch-regulation build --usecase <id>' first.",
                title="D1 prerequisites missing",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    if active["d1"]:
        console.print(
            Panel(
                "D1 is already active. Use 'refresh' to re-run the full pipeline.",
                title="D1 already executed",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=0)

    approved_scope = active["da1"]["payload"].get("approved_mapping_scope", {})
    handoff = approved_scope.get("handoff_data_request")
    if not handoff:
        console.print(
            Panel(
                "No handoff_data_request found in DA1 active profile.",
                title="D1 error",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    envelope = build_envelope(seed)
    run_dir = _new_experiment(seed, "data_execution")
    store = EvidenceStore(run_dir)

    agent = DataLoaderAgent()
    try:
        message = agent.execute_from_handoff(envelope, store, handoff)
    except Exception as exc:
        console.print(
            Panel(
                str(exc),
                title="D1 execution failed",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    _write_json(run_dir / "d1_output.json", message.payload)
    render_d1(message.payload, console)

    summary = message.payload.get("summary", {})
    data_request = message.payload.get("data_request", {})
    d1_payload = {"data_request": data_request, "summary": summary}

    d1_profile_store.promote_active(
        seed.id,
        d1_payload,
        run_id=run_dir.name,
        review_round=1,
        input_refs={
            "da1": _active_ref(active["da1"]),
            "r2": _active_ref(active["r2"]),
        },
    )
    console.print(
        Panel(
            "D1 data product saved to shelf and evidence store.",
            title="D1 complete",
            border_style="green",
        )
    )


def interactive_main(usecase: str | None = None) -> None:
    render_banner(console)
    seed = _seed(usecase)
    active = load_active_set(seed.id)
    if not any(active.values()):
        result = build_profile_pipeline(
            seed=seed,
            experiment_dir=_new_experiment(seed, "interactive build"),
            refresh=False,
        )
        if result is None:
            raise typer.Exit(code=1)
        return
    render_active_set(seed.id, active)
    render_action_panel(
        "Active profiles found for this registry — choose an action.",
        "[h] handoff   [d] data load   [e] edit   [r] refresh   [q] quit",
    )
    action = typer.prompt(
        "Action",
        default="h",
    ).strip().lower()
    if action == "e":
        edit_command(seed.id)
    elif action == "r":
        refresh_command(seed.id)
    elif action == "h":
        handoff_command(seed.id)
    elif action == "d":
        data(seed.id)


@app.callback()
def main(
    ctx: typer.Context,
    usecase: str | None = typer.Option(None, "--usecase", "-u"),
) -> None:
    if ctx.invoked_subcommand is None:
        interactive_main(usecase)


if __name__ == "__main__":
    app()
