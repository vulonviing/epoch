#!/usr/bin/env python3
"""Verify the disclosure boundary of the public repository working tree."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_PATH_PARTS = (
    ("data", "real"),
    ("experiments", "real"),
    ("gold", "real"),
    ("peer_review", "reviewed"),
    ("peer_review", "reviews"),
    ("peer_review", "_incoming"),
    ("registry_profiles",),
)
FORBIDDEN_TEXT_PATTERNS = {
    "internal source-system name": re.compile(r"\bSESIS\b", re.IGNORECASE),
    "machine-local path": re.compile(r"(?:[A-Za-z]:[\\/](?:Users|Documents)[\\/]|/Users/|/home/)"),
    "reviewer identifier": re.compile(r"\b(?:CGA|AMF|XK)\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential assignment": re.compile(
        r"(?m)^[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|GITHUB_TOKEN|"
        r"CLIENT_SECRET|PASSWORD|PRIVATE_KEY)[ \t]*="
        r"[ \t]*[^ \t\r\n#][^\r\n]*$"
    ),
}
CONTENT_SCAN_EXEMPT = {
    Path("scripts/build_public_gold.py"),
    Path("scripts/epoch_anonymize.py"),
    Path("scripts/verify_public_release.py"),
}
OFFICIAL_PDF_HASHES = {
    "CELEX_32003L0087_EN_TXT.pdf": "b3941901e4fe1af3defcffa323c00a5c6f7f44c51c9ea5785c7a995c3bd68445",
    "CELEX_32022L2464_EN_TXT.pdf": "75e720eb787db9fb125ae527e1590591e45e4672805848022c5d7bd69e6a349e",
    "EnEfG.pdf": "c112d86dc72a65a2ff648040191dc97576bd7300806995fba0d778fe5d980bb2",
}
EXPECTED_GOLD = {
    "uc1_enefg_threshold_check",
    "uc2_ets1_scope_memo",
    "uc2_1_ets1_scope_memo_annual",
    "uc3_csrd_scope2_measure",
}
DATA_DERIVED_NUMERIC_KEYS = {
    "abs_delta",
    "available_fiscal_years",
    "bu_rc_id",
    "evidence_completeness",
    "fiscal_year",
    "method_a",
    "method_b",
    "method_figure",
    "near_breach_ratio",
    "pct_diff",
    "portfolio_total_t_vs_deterministic",
    "size_bytes",
    "total_rows",
    "value",
    "values",
    "Approved",
}
TIMESTAMP_KEYS = {"approved_at", "archived_at", "created_at", "frozen_at", "updated_at"}
PRIVATE_RUN_ID_RE = re.compile(r"\d{4}-\d{2}-\d{2}_[0-9]{6}Z_regulation_cli")
PRIVATE_ARTIFACT_ID_RE = re.compile(r"^[a-z][a-z0-9.]*-[0-9a-f]{12}$", re.IGNORECASE)
SENSITIVE_PROSE_NUMBER_RE = re.compile(
    r"(?:(?<![\w.])~?\d+(?:[.,]\d+)?(?:\s*[-–]\s*\d+(?:[.,]\d+)?)?\s*%"
    r"(?![0-9A-Fa-f])|"
    r"(?<![\w.])\d+(?:[.,]\d+)?(?:\s*[-/]?\s*[A-Za-z]+){0,3}\s+"
    r"(?:sites?|rows?|records?|entries|divisions?|locations?|"
    r"installations?|compliant|obligated|flagged)\b|"
    r"(?<![\w.-])\d+\s+of\s+\d+\b|"
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand)\b)",
    re.IGNORECASE,
)
SCIENTIFIC_NUMBER_RE = re.compile(
    r"(?<![\w.-])-?\d+(?:\.\d+)?e[+-]?\d+(?![\w.-])", re.IGNORECASE
)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
    )
    candidates = (REPO_ROOT / item.decode("utf-8") for item in output.split(b"\0") if item)
    return sorted(path for path in candidates if path.is_file())


def verify_paths(files: list[Path]) -> None:
    for path in files:
        parts = path.relative_to(REPO_ROOT).parts
        for forbidden in FORBIDDEN_PATH_PARTS:
            width = len(forbidden)
            if any(parts[index : index + width] == forbidden for index in range(len(parts) - width + 1)):
                raise RuntimeError(f"forbidden publication path: {path.relative_to(REPO_ROOT)}")
        if parts[:2] == ("experiments", "synthetic") or parts[:2] == ("experiments", "public"):
            raise RuntimeError(f"generated run artifact is tracked: {path.relative_to(REPO_ROOT)}")


def verify_text(files: list[Path]) -> int:
    scanned = 0
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        if path.relative_to(REPO_ROOT) in CONTENT_SCAN_EXEMPT:
            continue
        for label, pattern in FORBIDDEN_TEXT_PATTERNS.items():
            if pattern.search(text):
                raise RuntimeError(f"{label} found in {path.relative_to(REPO_ROOT)}")
    return scanned


def is_data_derived_numeric_key(key: str) -> bool:
    lowered = key.lower()
    return (
        key in DATA_DERIVED_NUMERIC_KEYS
        or lowered.startswith("n_")
        or (
            lowered.endswith("_count")
            and lowered not in {"checkpoint_count", "expected_count"}
        )
        or key in {"gap", "avg_3yr", "subtotal_t", "portfolio_total_t"}
        or lowered.endswith(("_mwh", "_kwh", "_gwh", "_tco2e", "_t"))
    )


def verify_json_privacy(value: object, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            values = child if isinstance(child, list) else [child]
            if is_data_derived_numeric_key(key) and any(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in values
            ):
                raise RuntimeError(f"unmasked data-derived number: {child_path}")
            if key in TIMESTAMP_KEYS and any(
                isinstance(item, str) and item not in {"", "****"} for item in values
            ):
                raise RuntimeError(f"exact private timestamp: {child_path}")
            if any(
                isinstance(item, str) and PRIVATE_RUN_ID_RE.search(item) for item in values
            ):
                raise RuntimeError(f"private run identifier: {child_path}")
            if key in {"artifact_id", "source_artifact_id"} and any(
                isinstance(item, str) and PRIVATE_ARTIFACT_ID_RE.fullmatch(item)
                for item in values
            ):
                raise RuntimeError(f"private artifact identifier: {child_path}")
            verify_json_privacy(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            verify_json_privacy(child, path=f"{path}[{index}]")
    elif isinstance(value, str) and (
        SENSITIVE_PROSE_NUMBER_RE.search(value) or SCIENTIFIC_NUMBER_RE.search(value)
    ):
        raise RuntimeError(f"data-derived number in prose: {path}")


def verify_checksums() -> int:
    checked = 0
    for checksum_file in sorted((REPO_ROOT / "public/gold").rglob("SHA256SUMS")):
        for line in checksum_file.read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            target = checksum_file.parent / relative
            if file_hash(target) != expected:
                raise RuntimeError(f"checksum mismatch: {target.relative_to(REPO_ROOT)}")
            checked += 1
    return checked


def verify_gold_shape() -> None:
    gold_root = REPO_ROOT / "public/gold"
    index = json.loads((gold_root / "index.json").read_text(encoding="utf-8"))
    observed = {row["registry_id"] for row in index}
    if observed != EXPECTED_GOLD:
        raise RuntimeError(f"gold registry mismatch: {sorted(observed)}")
    uc4 = gold_root / "uc4_esrs_2026_impact"
    if sorted(path.name for path in uc4.iterdir()) != ["README.md"]:
        raise RuntimeError("UC4 must contain only its withholding README")
    for path in sorted(gold_root.rglob("*.json")):
        verify_json_privacy(json.loads(path.read_text(encoding="utf-8")))


def _verify_masked_shape(value: object, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _verify_masked_shape(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        if len(value) > 1:
            raise RuntimeError(f"disclosed collection cardinality at {path}")
        for index, child in enumerate(value):
            _verify_masked_shape(child, path=f"{path}[{index}]")
        return
    if value not in {None, "****"}:
        raise RuntimeError(f"unmasked disclosure payload value at {path}")


def verify_structure_only_gold() -> None:
    gold_root = REPO_ROOT / "public/gold"
    for case_dir in sorted(path for path in gold_root.iterdir() if path.is_dir()):
        version_dir = case_dir / "v002"
        if not version_dir.exists():
            continue
        registry = json.loads((version_dir / "registry_snapshot.json").read_text(encoding="utf-8"))
        _verify_masked_shape(registry, path=f"{case_dir.name}.registry_snapshot")
        input_manifest = json.loads((version_dir / "input_manifest.json").read_text(encoding="utf-8"))
        if len(input_manifest.get("real_data_files", [])) > 1:
            raise RuntimeError(f"source-file cardinality disclosed in {case_dir.name}")
        for folder in ("checkpoints", "source_artifacts"):
            for artifact in sorted((version_dir / folder).glob("*.json")):
                record = json.loads(artifact.read_text(encoding="utf-8"))
                _verify_masked_shape(
                    record.get("payload"),
                    path=f"{case_dir.name}.{folder}.{artifact.stem}.payload",
                )


def verify_evaluation_manifest() -> None:
    manifest_path = REPO_ROOT / "evaluation/publication_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest.get("use_cases", [])
    observed = {row.get("registry_id") for row in rows}
    expected = EXPECTED_GOLD | {"uc4_esrs_2026_impact"}
    if observed != expected:
        raise RuntimeError(f"evaluation publication manifest mismatch: {sorted(observed)}")
    if sum(int(row.get("peer_review_count", 0)) for row in rows) != 6:
        raise RuntimeError("peer-review aggregate count must remain six")
    for row in rows:
        if row.get("peer_review_status") != "aggregate_only":
            raise RuntimeError(f"individual peer-review publication enabled: {row}")
        target = REPO_ROOT / str(row.get("gold_path", ""))
        if not target.exists():
            raise RuntimeError(f"evaluation publication path missing: {target}")


def verify_synthetic_checksums() -> int:
    checksum_file = REPO_ROOT / "data/synthetic/SHA256SUMS"
    checked = 0
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split("  ", 1)
        target = checksum_file.parent / relative
        if file_hash(target) != expected:
            raise RuntimeError(f"synthetic checksum mismatch: {relative}")
        checked += 1
    return checked


def verify_single_root_history() -> None:
    commits = subprocess.check_output(
        ["git", "rev-list", "--all"], cwd=REPO_ROOT, text=True
    ).splitlines()
    if len(set(commits)) != 1:
        raise RuntimeError(
            f"public history must contain one sanitized root commit; found {len(set(commits))}"
        )


def verify_official_pdfs() -> None:
    for name, expected in OFFICIAL_PDF_HASHES.items():
        path = REPO_ROOT / "regulations" / name
        if file_hash(path) != expected:
            raise RuntimeError(f"official PDF hash mismatch: regulations/{name}")


def main() -> int:
    history = "--history" in sys.argv[1:]
    files = tracked_files()
    verify_paths(files)
    text_count = verify_text(files)
    verify_gold_shape()
    verify_structure_only_gold()
    verify_evaluation_manifest()
    verify_official_pdfs()
    checksum_count = verify_checksums()
    synthetic_count = verify_synthetic_checksums()
    if history:
        verify_single_root_history()
    print(
        "Public release verification passed: "
        f"{len(files)} tracked files, {text_count} text files, "
        f"{checksum_count} gold checksums, {synthetic_count} synthetic checksums, "
        "4 masked gold packages, 0 forbidden findings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
