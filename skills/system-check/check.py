#!/usr/bin/env python3
"""Deterministic checks for the /system-check audit skill.

Canonical location: skills/system-check/check.py. Do not fork a copy under
.claude/ or .codex/ — both routing stubs invoke this file directly.

Stdlib only. Read-only: never edits source, never touches a shelf, never
re-runs the pipeline. Findings are informational; the rules they check are
described in RULES.md (this script implements the "script" and the script
half of the "both" checks — never the "read" checks, which are judgment
calls left to the audit skill's LLM pass).

Usage:
    python skills/system-check/check.py [--run-dir PATH] [--rules R05,R07]
                                         [--json] [--static]

Exit code: 0 if every finding is PASS/WARN/N-A, 1 if any finding is FAIL.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "src" / "epoch_switch" / "agents"
EXCLUDE_DIR_NAMES = {".claude", ".codex", ".venv", "graphify-out", "node_modules", "__pycache__"}

ARCHIVE_NAME_RE = re.compile(r"^\d{8}T\d{6,}Z_[A-Za-z0-9\-]+\.json$")
# Two coexisting key conventions observed in evidence_store.json: most agents key by
# an 8-hex case hash (rc1_2_change_classification_ccdcbc8e); D1/D2's data-quality keys
# key by the registry id itself instead (data_catalog_uc1_enefg_threshold_check).
EVIDENCE_KEY_HASH_RE = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{8}$")
EVIDENCE_KEY_REGISTRY_RE = re.compile(r"^[a-z][a-z0-9_]*_uc[0-9][a-z0-9_]*$")

# Known-deterministic agent modules per AGENTS.md's Agent Type Classification table.
DETERMINISTIC_AGENT_DIRS = [
    AGENTS_DIR / "data" / "d1_loader",
    AGENTS_DIR / "regulation" / "rd3_join",
]
DETERMINISTIC_CORPUS_DIR = REPO_ROOT / "src" / "epoch_switch" / "corpus"

# Per AGENTS.md's Agent Type Classification table, plus P1/P2/P3/S1/C1/C2/F1 which the
# table omits but which are LLM by code inspection (see AGENTS.md's P/T prompt-style
# contract). D3 is deterministic (D1's downstream join/summary step, no LLM call).
LLM_OR_HYBRID_AGENT_IDS = {
    "R1", "DA1", "R2", "D2", "CP1", "TS1", "P4", "P5", "P6",
    "RC1.1", "RC1.2", "RM1.1", "RM1.2", "EX1", "EX2",
    "P1", "P2", "P3", "S1", "C1", "C2", "F1", "F2",
}
DETERMINISTIC_AGENT_IDS = {"D1", "D3", "RD3"}
LLM_CLIENT_PATH = REPO_ROOT / "src" / "epoch_switch" / "core" / "llm_client.py"
CONFIG_PATH = REPO_ROOT / "src" / "epoch_switch" / "config.py"
# BaseAgent is a plain ABC interface all agents implement regardless of type (see
# agents/base.py) -- it is not itself evidence of an LLM call. The actual LLM call
# surface is core.llm_client (LLMClient / llm_for_agent) and the call_json* methods.
LLM_CALL_MARKERS = ("core.llm_client", "LLMClient", "llm_for_agent", "call_json_websearch", "call_json(")

DOCUMENT_ARTIFACT_STEMS = [
    "rd1_corpus_summary", "rd2_candidates", "rc1_1_output_round",
    "rc1_2_output_round", "rm1_1_output_round", "rm1_2_output_round",
    "rd3_join_round", "cp1_output_round", "ts1_output_round",
    "p4_output_round", "p5_output_round", "p6_output_round",
    "f2_output_round", "active_document_set", "evidence_store",
]
TABULAR_ARTIFACT_STEMS = [
    "r1_output", "da1_mapping_report", "approved_mapping_scope_round",
    "r2_output_round", "d2_output_round", "d3_output_round",
    "cp1_output_round", "ts1_output_round", "p1_output_round",
    "f1_output_round", "active_profile_set", "data_catalog",
    "human_decisions_round", "evidence_store",
]
# EX1/EX2 are advisory-only and legitimately absent (backend without web search,
# or a resume run that skips EX1) -- never required for R01 completeness.
OPTIONAL_ARTIFACT_STEMS = {"ex1_output_round", "ex2_output_round"}

# Per AGENTS.md's "Run identity and cross-run provenance": agents resumable across
# runs by design (_PRE_TOPOLOGY_AGENTS in regulation_cli.py) -- a foreign run_id on
# one of these is a WARN when marked carried_over, never a hard FAIL. Everything
# else (gate-final / stage-2 agents) is FAIL on any cross-run input_refs mismatch.
PRE_TOPOLOGY_AGENT_IDS = {"R1", "DA1", "R2", "D1", "D2", "CP1", "TS1", "D3"}
TERMINAL_AGENT = {"tabular": "F1", "document": "F2"}
SNAPSHOT_NAME = {"tabular": "active_profile_set.json", "document": "active_document_set.json"}
RUN_PROVENANCE_RULE_IDS = ("R25", "R26")


@dataclass
class Finding:
    rule: str
    status: str  # PASS | WARN | FAIL | N/A
    evidence: str
    suggested_fix: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, rule: str, status: str, evidence: str, suggested_fix: str = "") -> None:
        self.findings.append(Finding(rule, status, evidence, suggested_fix))


def _iter_py_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
            continue
        yield path


def _iter_agent_dirs():
    """Yield each immediate agent folder: agents/<group>/<agent_name>/."""
    for group_dir in AGENTS_DIR.iterdir():
        if not group_dir.is_dir() or group_dir.name in EXCLUDE_DIR_NAMES:
            continue
        for agent_dir in group_dir.iterdir():
            if agent_dir.is_dir() and agent_dir.name not in EXCLUDE_DIR_NAMES:
                yield agent_dir


# ---------------------------------------------------------------------------
# R05 -- agent-local shelf layout
# ---------------------------------------------------------------------------

def check_r05(report: Report) -> None:
    if not AGENTS_DIR.exists():
        report.add("R05", "N/A", "src/epoch_switch/agents not found")
        return
    bad_active = []
    bad_archive = []
    n_active = 0
    n_archive = 0
    for agent_dir in _iter_agent_dirs():
        shelf_root = agent_dir / "registry_profiles"
        if not shelf_root.is_dir():
            continue
        for active_json in shelf_root.rglob("active.json"):
            n_active += 1
            rel = active_json.relative_to(shelf_root)
            depth = len(rel.parts)
            # Allowed: <registry_id>/active.json (depth 2) or
            # <agent_id>/<registry_id>/active.json (depth 3).
            if depth not in (2, 3):
                bad_active.append(str(active_json.relative_to(REPO_ROOT)))
        for archive_file in shelf_root.rglob("archive/*.json"):
            n_archive += 1
            if not ARCHIVE_NAME_RE.match(archive_file.name):
                bad_archive.append(str(archive_file.relative_to(REPO_ROOT)))
    if bad_active or bad_archive:
        evidence = f"{len(bad_active)} misplaced active.json, {len(bad_archive)} malformed archive names"
        report.add("R05", "FAIL", evidence + ": " + ", ".join((bad_active + bad_archive)[:5]))
    else:
        report.add("R05", "PASS", f"{n_active} active.json, {n_archive} archive files match the two allowed shapes")


# ---------------------------------------------------------------------------
# R06 / R08 -- store-access boundary and agent self-sufficiency
# ---------------------------------------------------------------------------

def _agent_folder_of(path: Path) -> Path | None:
    try:
        rel = path.relative_to(AGENTS_DIR)
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    return AGENTS_DIR / rel.parts[0] / rel.parts[1]


def _imported_module_names(tree: ast.AST) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def check_r06_r08(report: Report) -> None:
    if not AGENTS_DIR.exists():
        report.add("R06/R08", "N/A", "src/epoch_switch/agents not found")
        return
    violations = []
    files_checked = 0
    for path in _iter_py_files(AGENTS_DIR):
        own_agent_folder = _agent_folder_of(path)
        if own_agent_folder is None:
            continue
        files_checked += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for mod_name in _imported_module_names(tree):
            if "epoch_switch.agents." not in mod_name:
                continue
            parts = mod_name.split(".")
            try:
                idx = parts.index("agents")
            except ValueError:
                continue
            target_group_agent = parts[idx + 1: idx + 3]
            if len(target_group_agent) < 2:
                continue
            if target_group_agent[1].startswith("_"):
                continue  # leading-underscore folder: deliberate shared infra (e.g. output/_debate_common), not an agent
            target_folder = AGENTS_DIR / target_group_agent[0] / target_group_agent[1]
            if target_folder != own_agent_folder:
                violations.append(f"{path.relative_to(REPO_ROOT)} imports {mod_name}")
    if violations:
        report.add(
            "R06/R08", "FAIL",
            f"{len(violations)} cross-agent import(s): " + "; ".join(violations[:5]),
            "Route the shared data through the owning agent's store, or a CLI-layer orchestrator, not a direct import.",
        )
    else:
        report.add("R06/R08", "PASS", f"no cross-agent imports found in {files_checked} agent module(s)")


# ---------------------------------------------------------------------------
# R07 -- input_refs provenance
# ---------------------------------------------------------------------------

REQUIRED_RECORD_KEYS = {
    "schema_version", "artifact_id", "agent_id", "registry_id", "status",
    "created_at", "input_refs", "payload_sha256", "payload",
}


def _sha256_of(payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_r07(report: Report) -> None:
    if not AGENTS_DIR.exists():
        report.add("R07", "N/A", "src/epoch_switch/agents not found")
        return
    missing_keys = []
    bad_refs = []
    n_checked = 0
    for agent_dir in _iter_agent_dirs():
        shelf_root = agent_dir / "registry_profiles"
        if not shelf_root.is_dir():
            continue
        for active_json in shelf_root.rglob("active.json"):
            try:
                record = json.loads(active_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            n_checked += 1
            rel = str(active_json.relative_to(REPO_ROOT))
            missing = REQUIRED_RECORD_KEYS - record.keys()
            if missing:
                missing_keys.append(f"{rel} missing {sorted(missing)}")
                continue
            input_refs = record.get("input_refs") or {}
            for ref_name, ref in input_refs.items():
                if isinstance(ref, str):
                    continue  # a bare hash reference (e.g. R1's registry_sha256) is valid
                if isinstance(ref, dict):
                    if not ref:
                        continue  # empty dict: a topology-optional upstream (e.g. S1's "p2" in Coalition)
                    has_identity = "artifact_id" in ref or "registry_id" in ref or ref.get("state") == "candidate"
                    if has_identity and "payload_sha256" in ref:
                        continue
                    bad_refs.append(f"{rel}::{ref_name} missing an identity key + payload_sha256")
                    continue
                bad_refs.append(f"{rel}::{ref_name} unexpected input_refs entry type")
    if missing_keys or bad_refs:
        evidence = "; ".join((missing_keys + bad_refs)[:5])
        report.add("R07", "FAIL", f"{len(missing_keys)} record(s) missing keys, {len(bad_refs)} bad input_refs: {evidence}")
    else:
        report.add("R07", "PASS", f"{n_checked} active.json record(s) carry required keys and well-formed input_refs")


# ---------------------------------------------------------------------------
# R02 -- deterministic agents never call an LLM
# ---------------------------------------------------------------------------

def check_r02(report: Report) -> None:
    offenders = []
    dirs_checked = 0
    for det_dir in DETERMINISTIC_AGENT_DIRS + [DETERMINISTIC_CORPUS_DIR]:
        if not det_dir.exists():
            continue
        dirs_checked += 1
        for path in _iter_py_files(det_dir):
            if path.name.endswith("_profile_store.py"):
                continue  # store modules only serialize records, never call an LLM
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = [m for m in LLM_CALL_MARKERS if m in text]
            if hits:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {', '.join(hits)}")
    if dirs_checked == 0:
        report.add("R02", "N/A", "no known-deterministic agent directories found")
    elif offenders:
        report.add("R02", "FAIL", "; ".join(offenders[:5]),
                    "Deterministic agents must not import BaseAgent or call an LLM backend function.")
    else:
        report.add("R02", "PASS", f"no LLM call surface found in {dirs_checked} deterministic dir(s)")


# ---------------------------------------------------------------------------
# R15 -- EX1/EX2 advisory-only, no leakage into non-EX shelves
# ---------------------------------------------------------------------------

def check_r15(report: Report, run_dir: Path | None) -> None:
    leaks = []
    n_checked = 0
    if AGENTS_DIR.exists():
        for agent_dir in _iter_agent_dirs():
            if agent_dir.name == "ex_commentator":
                continue
            shelf_root = agent_dir / "registry_profiles"
            if not shelf_root.is_dir():
                continue
            for active_json in shelf_root.rglob("active.json"):
                try:
                    record = json.loads(active_json.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                n_checked += 1
                payload = record.get("payload")
                if isinstance(payload, dict) and ({"ex1", "ex2"} & payload.keys()):
                    leaks.append(str(active_json.relative_to(REPO_ROOT)))
    ex_present = []
    ex_missing = []
    if run_dir is not None:
        for stem in ("ex1_output_round", "ex2_output_round"):
            matches = list(run_dir.glob(f"{stem}*.json"))
            (ex_present if matches else ex_missing).append(stem)
    if leaks:
        report.add("R15", "FAIL", f"ex1/ex2 key found inside {len(leaks)} non-EX shelf payload(s): {', '.join(leaks[:5])}",
                    "Remove the ex1/ex2 key from the agent's own payload; add it only to a separately-named gate-payload dict.")
        return
    evidence = f"no ex1/ex2 leakage in {n_checked} non-EX shelf record(s)"
    if run_dir is not None:
        if ex_missing:
            evidence += f"; run-level: missing {', '.join(ex_missing)} (WARN -- may be a legitimate skip)"
            report.add("R15", "WARN", evidence)
            return
        evidence += f"; run-level: {', '.join(ex_present)} present"
    report.add("R15", "PASS", evidence)


# ---------------------------------------------------------------------------
# R19 -- data convention / visibility routing
# ---------------------------------------------------------------------------

def check_r19(report: Report) -> None:
    src_root = REPO_ROOT / "src" / "epoch_switch"
    if not src_root.exists():
        report.add("R19", "N/A", "src/epoch_switch not found")
        return
    call_sites = []
    hardcoded = []
    for path in _iter_py_files(src_root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "create_experiment_dir(" in text:
            for i, line in enumerate(text.splitlines(), start=1):
                if "create_experiment_dir(" in line:
                    call_sites.append(f"{path.relative_to(REPO_ROOT)}:{i}")
        for match in re.finditer(r'visibility\s*=\s*["\'](?:public|real)["\']\s*if\s+([^\n]+?)\s+else', text):
            condition = match.group(1)
            if "pipeline_family" not in condition:
                hardcoded.append(f"{path.relative_to(REPO_ROOT)}: {condition.strip()}")
    if hardcoded:
        report.add("R19", "FAIL", "visibility routed by something other than pipeline_family: " + "; ".join(hardcoded[:5]),
                    "Route visibility only through seed.pipeline_family, per config.create_experiment_dir's contract.")
    else:
        report.add("R19", "PASS", f"{len(call_sites)} create_experiment_dir call site(s), all routed by pipeline_family: {', '.join(call_sites)}")


# ---------------------------------------------------------------------------
# R22/R23/R24 -- LLM provenance: identity, reasoning/effort, token accounting
# ---------------------------------------------------------------------------

def check_r22_r24(report: Report) -> None:
    if not AGENTS_DIR.exists():
        report.add("R22", "N/A", "src/epoch_switch/agents not found")
        report.add("R23", "N/A", "src/epoch_switch/agents not found")
        report.add("R24", "N/A", "src/epoch_switch/agents not found")
        return

    n_records = 0
    n_llm_records = 0
    n_with_provenance = 0
    n_reasoning_ok = 0
    n_tokens_ok = 0
    sample_missing = []
    for agent_dir in _iter_agent_dirs():
        shelf_root = agent_dir / "registry_profiles"
        if not shelf_root.is_dir():
            continue
        for active_json in shelf_root.rglob("active.json"):
            try:
                record = json.loads(active_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            n_records += 1
            agent_id = record.get("agent_id")
            is_llm = agent_id in LLM_OR_HYBRID_AGENT_IDS
            if is_llm:
                n_llm_records += 1
            provenance = record.get("llm_provenance")
            if is_llm and isinstance(provenance, dict) and provenance:
                n_with_provenance += 1
                reasoning = provenance.get("reasoning") or {}
                effort = reasoning.get("effort")
                if reasoning.get("enabled") is True and reasoning.get("mode") and effort not in (None, "", "none"):
                    n_reasoning_ok += 1
                tokens = provenance.get("tokens") or {}
                reported_as = tokens.get("reported_as")
                if (reported_as == "split" and "input" in tokens and "output" in tokens) or (
                    reported_as == "combined" and "total" in tokens
                ):
                    n_tokens_ok += 1
            elif is_llm and len(sample_missing) < 5:
                sample_missing.append(str(active_json.relative_to(REPO_ROOT)))

    if n_llm_records == 0:
        report.add("R22", "N/A", "no LLM/Hybrid-agent shelf records found")
        report.add("R23", "N/A", "no LLM/Hybrid-agent shelf records found")
        report.add("R24", "N/A", "no LLM/Hybrid-agent shelf records found")
        return

    fix_hint = ("core/llm_client.py's adapters return (content, duration_ms) only and "
                "never read resp.usage; see AGENTS.md 'LLM Provenance and Reasoning "
                "Configuration' for the target record shape.")

    if n_with_provenance == n_llm_records:
        report.add("R22", "PASS", f"{n_with_provenance}/{n_llm_records} LLM/Hybrid shelf record(s) carry llm_provenance")
    else:
        report.add("R22", "FAIL",
                    f"{n_with_provenance}/{n_llm_records} LLM/Hybrid shelf record(s) carry llm_provenance; "
                    f"missing e.g. {', '.join(sample_missing)}", fix_hint)

    if n_with_provenance and n_reasoning_ok == n_with_provenance:
        report.add("R23", "PASS", f"{n_reasoning_ok}/{n_with_provenance} record(s) with llm_provenance have reasoning enabled and a concrete effort")
    else:
        report.add("R23", "FAIL",
                    f"{n_reasoning_ok}/{n_llm_records} LLM/Hybrid record(s) show reasoning.enabled=true with a non-none effort",
                    fix_hint + " Also confirm every reachable backend preset in config.py's BACKENDS sets reasoning_effort "
                    "or an equivalent, and that the foundry_anthropic adapter sends thinking/output_config.")

    if n_with_provenance and n_tokens_ok == n_with_provenance:
        report.add("R24", "PASS", f"{n_tokens_ok}/{n_with_provenance} record(s) with llm_provenance carry well-formed token counts")
    else:
        report.add("R24", "FAIL",
                    f"{n_tokens_ok}/{n_llm_records} LLM/Hybrid record(s) carry a well-formed tokens block (reported_as + input/output or total)",
                    fix_hint + " Capture usage.input_tokens/usage.output_tokens (Anthropic) or "
                    "usage.prompt_tokens/usage.completion_tokens (OpenAI) in _BaseAdapter.call.")


# ---------------------------------------------------------------------------
# R10 -- EvidenceStore key pattern
# ---------------------------------------------------------------------------

def check_r10(report: Report, run_dir: Path | None) -> None:
    if run_dir is None:
        report.add("R10", "N/A", "no run directory available (use --run-dir or a discoverable run)")
        return
    evidence_path = run_dir / "evidence_store.json"
    if not evidence_path.exists():
        report.add("R10", "WARN", f"{evidence_path.relative_to(REPO_ROOT)} not found")
        return
    try:
        store = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.add("R10", "FAIL", f"evidence_store.json is not valid JSON: {exc}")
        return
    bad_keys = [
        k for k in store.keys()
        if not (EVIDENCE_KEY_HASH_RE.match(k) or EVIDENCE_KEY_REGISTRY_RE.match(k))
    ]
    if bad_keys:
        report.add("R10", "FAIL", f"{len(bad_keys)} key(s) do not match <slug>_<8-hex>: {bad_keys[:5]}")
    else:
        report.add("R10", "PASS", f"{len(store)} evidence_store key(s) match <slug>_<8-hex hash>")


# ---------------------------------------------------------------------------
# R01 -- run completeness for the detected pipeline family
# ---------------------------------------------------------------------------

def _detect_family(run_dir: Path) -> str | None:
    if (run_dir / "active_document_set.json").exists():
        return "document"
    if (run_dir / "active_profile_set.json").exists():
        return "tabular"
    return None


def check_r01(report: Report, run_dir: Path | None) -> None:
    if run_dir is None:
        report.add("R01", "N/A", "no run directory available (use --run-dir or a discoverable run)")
        return
    family = _detect_family(run_dir)
    if family is None:
        report.add("R01", "WARN", f"{run_dir.relative_to(REPO_ROOT)} has neither active_document_set.json nor active_profile_set.json")
        return
    expected = DOCUMENT_ARTIFACT_STEMS if family == "document" else TABULAR_ARTIFACT_STEMS
    present = {p.name for p in run_dir.glob("*.json")}
    missing = []
    for stem in expected:
        if not any(name.startswith(stem) for name in present):
            missing.append(stem)
    missing_required = [m for m in missing if m not in OPTIONAL_ARTIFACT_STEMS]
    if missing_required:
        report.add("R01", "FAIL", f"{family} run missing required artifact(s): {', '.join(missing_required)}",
                    "Confirm this run completed; a partial/aborted run should not be treated as a finished checkpoint.")
    else:
        note = " (older pre-blind-pass shape: rc1/rm1 not split)" if family == "document" and "rc1_1_output_round" in missing else ""
        report.add("R01", "PASS", f"{family} run has the expected artifact set{note}")


# ---------------------------------------------------------------------------
# Run discovery (mirrors regulation_cli.py / build_packets.py conventions)
# ---------------------------------------------------------------------------

def _most_recent_run_dir(experiments_root: Path) -> Path | None:
    if not experiments_root.is_dir():
        return None
    candidates = []
    for entry in experiments_root.iterdir():
        if not entry.is_dir() or entry.name in EXCLUDE_DIR_NAMES:
            continue
        run_dirs = sorted(entry.glob("run_*"))
        if run_dirs:
            candidates.append((entry.name, run_dirs[-1]))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


def _experiment_roots() -> list[Path]:
    """Return every configured visibility/data-mode root under experiments/."""
    experiments_dir = REPO_ROOT / "experiments"
    if not experiments_dir.is_dir():
        return []
    return sorted(
        path
        for path in experiments_dir.iterdir()
        if path.is_dir() and path.name not in EXCLUDE_DIR_NAMES
    )


def discover_run_dir() -> Path | None:
    candidates = [
        run
        for root in _experiment_roots()
        if (run := _most_recent_run_dir(root)) is not None
    ]
    if not candidates:
        return None
    # Prefer whichever run directory's parent has the later name (ISO-sortable).
    candidates.sort(key=lambda r: r.parent.name)
    return candidates[-1]


# ---------------------------------------------------------------------------
# R25 / R26 -- run identity & cross-run provenance
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_snapshot(run_dir: Path, family: str) -> dict | None:
    name = SNAPSHOT_NAME.get(family)
    if name is None:
        return None
    data = _load_json(run_dir / name)
    return data if isinstance(data, dict) else None


def _stem_to_agent_id(slug: str) -> str:
    """'ts1' -> 'TS1', 'cp1' -> 'CP1', 'rc1_1' -> 'RC1.1' (the two-part blind-pass ids)."""
    m = re.match(r"^([a-z]+)1_([12])$", slug)
    if m:
        return f"{m.group(1).upper()}1.{m.group(2)}"
    return slug.upper()


def _snapshot_registry_id(snap: dict) -> str | None:
    for record in snap.values():
        if isinstance(record, dict) and record.get("registry_id"):
            return record["registry_id"]
    return None


def _iter_runs():
    """Yield (parent_dir_name, run_dir, family, snapshot) for every run with a
    readable snapshot, across all experiment roots, newest parent-name first."""
    results = []
    for root in _experiment_roots():
        for entry in root.iterdir():
            if not entry.is_dir() or entry.name in EXCLUDE_DIR_NAMES:
                continue
            for run_dir in sorted(entry.glob("run_*")):
                family = _detect_family(run_dir)
                if family is None:
                    continue
                snap = _load_snapshot(run_dir, family)
                if snap is None:
                    continue
                results.append((entry.name, run_dir, family, snap))
    results.sort(key=lambda t: t[0], reverse=True)
    return results


def _classify_input_ref_mismatches(rel: str, record: dict, fails: list, warns: list) -> None:
    agent_id = record.get("agent_id")
    record_run_id = record.get("run_id")
    for ref_name, ref in (record.get("input_refs") or {}).items():
        if not isinstance(ref, dict):
            continue
        ref_run_id = ref.get("run_id")
        if not ref_run_id or not record_run_id or ref_run_id == record_run_id:
            continue
        if agent_id in PRE_TOPOLOGY_AGENT_IDS:
            if record.get("carried_over") is True and record.get("carried_from_run_id") == ref_run_id:
                continue  # legitimate, documented carry-over
            warns.append(f"{rel}::{ref_name} run_id={ref_run_id} vs record run_id={record_run_id} (pre-topology, unmarked)")
        else:
            fails.append(f"{rel}::{ref_name} run_id={ref_run_id} vs record run_id={record_run_id} (post-gate agent {agent_id})")


def check_r25(report: Report, run_dir: Path | None) -> None:
    fails: list = []
    warns: list = []
    n_checked = 0
    if AGENTS_DIR.exists():
        for agent_dir in _iter_agent_dirs():
            shelf_root = agent_dir / "registry_profiles"
            if not shelf_root.is_dir():
                continue
            for active_json in shelf_root.rglob("active.json"):
                record = _load_json(active_json)
                if record is None:
                    continue
                n_checked += 1
                _classify_input_ref_mismatches(str(active_json.relative_to(REPO_ROOT)), record, fails, warns)
    if run_dir is not None:
        family = _detect_family(run_dir)
        snap = _load_snapshot(run_dir, family) if family else None
        if snap:
            rel_run = str(run_dir.relative_to(REPO_ROOT))
            for stage_key, record in snap.items():
                if not isinstance(record, dict):
                    continue
                n_checked += 1
                _classify_input_ref_mismatches(f"{rel_run}::{stage_key}", record, fails, warns)
    if fails:
        report.add("R25", "FAIL", f"{len(fails)} post-gate cross-run input_refs: " + "; ".join(fails[:5]),
                    "Confirm the gate-final/stage-2 agent did not consume a stale upstream run; re-run it against the current run's approved shelf.")
    elif warns:
        report.add("R25", "WARN", f"{len(warns)} pre-topology cross-run input_refs without a carried_over marker: " + "; ".join(warns[:5]),
                    "Mark the record carried_over: true + carried_from_run_id if this reuse across runs is intentional.")
    else:
        report.add("R25", "PASS", f"{n_checked} record(s) checked (live shelves + run snapshot), no cross-run input_refs found")


def check_r26(report: Report, last_runs: int, registry: str | None) -> None:
    runs = _iter_runs()
    if registry:
        runs = [r for r in runs if _snapshot_registry_id(r[3]) == registry]
    if not runs:
        report.add("R26", "N/A", "no runs discovered" + (f" for registry {registry}" if registry else ""))
        return

    groups: dict[str, list] = {}
    for parent_name, run_dir, family, snap in runs:
        rid = _snapshot_registry_id(snap) or "(unknown registry)"
        groups.setdefault(rid, []).append((parent_name, run_dir, family, snap))

    fails: list = []
    warns: list = []
    n_runs_checked = 0
    for rid, entries in groups.items():
        for parent_name, run_dir, family, snap in entries[:last_runs]:
            n_runs_checked += 1
            terminal_id = TERMINAL_AGENT.get(family)
            terminal_record = next(
                (r for r in snap.values() if isinstance(r, dict) and r.get("agent_id") == terminal_id), None
            )
            if terminal_record is not None and terminal_record.get("run_id") != parent_name:
                warns.append(f"{rid}/{parent_name}: terminal {terminal_id} run_id={terminal_record.get('run_id')} != run directory (not end-to-end)")

            for stage_key, record in snap.items():
                if isinstance(record, dict):
                    _classify_input_ref_mismatches(f"{rid}/{parent_name}::{stage_key}", record, fails, warns)

            stems = DOCUMENT_ARTIFACT_STEMS if family == "document" else TABULAR_ARTIFACT_STEMS
            present_files = {p.name for p in run_dir.glob("*.json")}
            for stem in stems:
                if not stem.endswith("_output_round"):
                    continue
                slug = stem[: -len("_output_round")]
                if slug in ("ex1", "ex2"):
                    continue  # advisory-only, legitimately absent (see R15/OPTIONAL_ARTIFACT_STEMS)
                file_present = any(name.startswith(stem) for name in present_files)
                expected_agent_id = _stem_to_agent_id(slug)
                agent_present = any(
                    isinstance(r, dict) and r.get("agent_id") == expected_agent_id for r in snap.values()
                )
                if file_present and not agent_present:
                    fails.append(f"{rid}/{parent_name}: {stem}*.json exists but {expected_agent_id} absent from snapshot (silent drop)")

    if fails:
        report.add("R26", "FAIL", f"{len(fails)} finding(s) across {n_runs_checked} run(s): " + "; ".join(fails[:5]))
    elif warns:
        report.add("R26", "WARN", f"{len(warns)} finding(s) across {n_runs_checked} run(s): " + "; ".join(warns[:5]))
    else:
        report.add("R26", "PASS", f"{n_runs_checked} run(s) across {len(groups)} registry(ies) show no incompleteness, cross-run edges, or silent drops")


ALL_CHECKS = {
    "R01": lambda report, run_dir: check_r01(report, run_dir),
    "R02": lambda report, run_dir: check_r02(report),
    "R05": lambda report, run_dir: check_r05(report),
    "R06/R08": lambda report, run_dir: check_r06_r08(report),
    "R07": lambda report, run_dir: check_r07(report),
    "R10": lambda report, run_dir: check_r10(report, run_dir),
    "R15": lambda report, run_dir: check_r15(report, run_dir),
    "R19": lambda report, run_dir: check_r19(report),
}
# R22/R23/R24 share one scan (check_r22_r24 emits all three findings at once); a
# plain dict entry per id would re-run it per selected id or skip it on a partial
# --rules selection, so it is dispatched separately in main() below.
LLM_PROVENANCE_RULE_IDS = ("R22", "R23", "R24")


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic checks for the /system-check audit skill.")
    parser.add_argument("--run-dir", type=Path, default=None, help="Path to a run_NNN directory to audit.")
    parser.add_argument("--rules", type=str, default=None, help="Comma-separated subset, e.g. R05,R07.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a table.")
    parser.add_argument("--static", action="store_true", help="Skip run-artifact checks; source tree only.")
    parser.add_argument("--last-runs", type=int, default=3, help="R26: how many recent runs per registry to audit (default 3).")
    parser.add_argument("--registry", type=str, default=None, help="R26: scope to one registry_id instead of every registry found.")
    args = parser.parse_args()

    run_dir = None
    if not args.static:
        run_dir = args.run_dir.resolve() if args.run_dir else discover_run_dir()

    selected = None
    if args.rules:
        selected = {r.strip().upper() for r in args.rules.split(",") if r.strip()}

    report = Report()
    for rule_id, fn in ALL_CHECKS.items():
        if selected and rule_id not in selected and not any(rule_id.startswith(s) for s in selected):
            continue
        if rule_id in ("R01", "R10") and args.static:
            report.add(rule_id, "N/A", "skipped: --static")
            continue
        fn(report, run_dir)

    if not selected or any(r in selected for r in LLM_PROVENANCE_RULE_IDS):
        check_r22_r24(report)
        if selected:
            # check_r22_r24 always emits all three (one shared scan) -- trim to what
            # was actually asked for so a --rules R24 run doesn't also print R22/R23.
            report.findings = [f for f in report.findings if f.rule not in LLM_PROVENANCE_RULE_IDS or f.rule in selected]

    # R25/R26 are dispatched outside ALL_CHECKS for the same reason as R22-R24:
    # R25 takes the shared run_dir, R26 takes its own --last-runs/--registry args,
    # and each must run independently under a partial --rules selection.
    if not selected or "R25" in selected:
        check_r25(report, run_dir)
    if not selected or "R26" in selected:
        if args.static:
            report.add("R26", "N/A", "skipped: --static")
        else:
            check_r26(report, args.last_runs, args.registry)

    if args.json:
        print(json.dumps(
            {
                "run_dir": str(run_dir.relative_to(REPO_ROOT)) if run_dir else None,
                "findings": [f.__dict__ for f in report.findings],
            },
            indent=2,
        ))
    else:
        header = f"system-check deterministic pass — run: {run_dir.relative_to(REPO_ROOT) if run_dir else '(static, no run)'}"
        print(header)
        print("-" * len(header))
        for f in report.findings:
            print(f"[{f.status:4}] {f.rule:8} {f.evidence}")
            if f.suggested_fix:
                print(f"         -> {f.suggested_fix}")

    return 1 if any(f.status == "FAIL" for f in report.findings) else 0


if __name__ == "__main__":
    sys.exit(main())
