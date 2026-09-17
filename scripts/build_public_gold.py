#!/usr/bin/env python3
"""Build masked disclosure copies of the frozen v002 evaluation golds.

The private packages remain the scoring source of truth. This builder creates
publication artifacts under ``public/gold/`` and verifies the complete staged
tree before replacing the previous generated copy.

Run from the repository root and point it at an authorized private evaluation
tree. The source path is never written into the publication artifacts:
    .venv/bin/python scripts/build_public_gold.py --source-root /path/to/model_comparison
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from epoch_anonymize import (  # noqa: E402
    ARTIFACT_ID_KEYS,
    MASK,
    PERSON_KEYS,
    RUN_ID_KEYS,
    TABULAR_REGISTRY_IDS,
    TIMESTAMP_KEYS,
    LeakFound,
    _is_numeric_masked_key,
    anonymize_payload,
    collect_denylist,
    mask_payload_shape,
    verify_no_leaks,
)


PUBLIC_ROOT = REPO_ROOT / "public"
OUT_DIR = PUBLIC_ROOT / "gold"
VERSION = "v002"
REGISTRY_IDS = (
    "uc1_enefg_threshold_check",
    "uc2_ets1_scope_memo",
    "uc2_1_ets1_scope_memo_annual",
    "uc3_csrd_scope2_measure",
)
LOCAL_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/](?:Users|Documents)[\\/]|/Users/|/home/)")
INTERNAL_SOURCE_SYSTEM_RE = re.compile(r"\bSESIS\b", re.IGNORECASE)
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


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def source_tree(source_root: Path, registry_id: str) -> dict[str, Any]:
    gold_root = source_root / registry_id / "gold"
    frozen_root = gold_root / "real" / "frozen" / VERSION
    tree = {
        "contract.json": load_json(gold_root / "contract.json"),
        "review_template.json": load_json(gold_root / "review_template.json"),
    }
    for path in sorted(frozen_root.rglob("*.json")):
        tree[f"{VERSION}/{path.relative_to(frozen_root).as_posix()}"] = load_json(path)
    return tree


def sanitize_package(source_root: Path, registry_id: str) -> tuple[dict[str, Any], list[str]]:
    private_tree = source_tree(source_root, registry_id)
    denyset: set[str] = set()
    collect_denylist(private_tree, denyset)
    denylist = sorted(denyset, key=len, reverse=True)
    tree = anonymize_payload(private_tree, registry_id, denylist)

    registry = mask_payload_shape(tree[f"{VERSION}/registry_snapshot.json"])
    tree[f"{VERSION}/registry_snapshot.json"] = registry
    registry_sha256 = canonical_hash(registry)
    manifest = tree[f"{VERSION}/manifest.json"]
    manifest["current_registry_sha256"] = registry_sha256
    manifest["source_registry_sha256"] = registry_sha256
    manifest["reviewer"] = MASK
    if isinstance(manifest.get("git"), dict):
        manifest["git"]["commit"] = MASK
    manifest["public_export"] = {
        "collection_cardinality_redacted": True,
        "data_derived_numbers_masked": True,
        "geography_redacted": True,
        "llm_prose_redacted": True,
        "mask_value": MASK,
        "masked": True,
        "metadata_identifiers_tokenized": True,
        "payload_values_redacted": True,
        "private_fingerprints_redacted": True,
        "purpose": "disclosure_only",
        "source_version": VERSION,
    }

    input_manifest = tree[f"{VERSION}/input_manifest.json"]
    if registry_id in TABULAR_REGISTRY_IDS:
        input_manifest["real_data_files"] = [
            {"path": MASK, "sha256": MASK, "size_bytes": MASK}
        ]
        input_manifest["note"] = (
            "Confidential source-file names, counts, hashes, and sizes are redacted."
        )

    artifact_hashes: dict[str, str] = {}
    artifact_paths = sorted(
        key for key in tree if key.startswith(f"{VERSION}/source_artifacts/")
    )
    for key in artifact_paths:
        record = tree[key]
        record["payload"] = mask_payload_shape(record["payload"])
        payload_sha256 = canonical_hash(record["payload"])
        record["payload_sha256"] = payload_sha256
        artifact_hashes[record["artifact_id"]] = payload_sha256

    for key in artifact_paths:
        record = tree[key]
        for ref_name, ref in record.get("input_refs", {}).items():
            if ref_name == "registry_sha256" and isinstance(ref, str):
                record["input_refs"][ref_name] = registry_sha256
                continue
            if not isinstance(ref, dict):
                continue
            artifact_id = ref.get("artifact_id")
            if artifact_id in artifact_hashes:
                ref["payload_sha256"] = artifact_hashes[artifact_id]
            elif "payload_sha256" in ref:
                ref["payload_sha256"] = MASK

    checkpoint_paths = sorted(
        key for key in tree if key.startswith(f"{VERSION}/checkpoints/")
    )
    for key in checkpoint_paths:
        tree[key]["payload"] = mask_payload_shape(tree[key]["payload"])
        gold_meta = tree[key].get("_gold", {})
        source_artifact_id = gold_meta.get("source_artifact_id")
        if source_artifact_id in artifact_hashes:
            gold_meta["source_payload_sha256"] = artifact_hashes[source_artifact_id]
        elif "source_payload_sha256" in gold_meta:
            gold_meta["source_payload_sha256"] = MASK

    return tree, denylist


def case_readme(registry_id: str, checkpoint_count: int) -> str:
    return f"""# {registry_id} public gold

This directory is a structure-only disclosure copy of frozen gold `{VERSION}`.
It contains {checkpoint_count} checkpoint schemas and a value-redacted supporting
source-artifact graph. The private package remains the scoring source of truth.

Every payload scalar is masked and every collection is collapsed to
at most one structural exemplar, so site and row counts cannot be reconstructed.
Private source fingerprints are removed, and every public checksum is calculated
again after masking. This package is not a runnable benchmark.
"""


def write_case(
    build_root: Path, source_root: Path, registry_id: str
) -> tuple[dict[str, Any], list[str]]:
    tree, denylist = sanitize_package(source_root, registry_id)
    case_dir = build_root / registry_id
    for relative, value in tree.items():
        write_json(case_dir / relative, value)

    checkpoint_dir = case_dir / VERSION / "checkpoints"
    manifest_path = case_dir / VERSION / "manifest.json"
    manifest = load_json(manifest_path)
    manifest["checkpoint_file_sha256"] = {
        path.name: file_hash(path) for path in sorted(checkpoint_dir.glob("*.json"))
    }
    write_json(manifest_path, manifest)

    checkpoint_count = len(manifest["checkpoint_file_sha256"])
    (case_dir / "README.md").write_text(
        case_readme(registry_id, checkpoint_count), encoding="utf-8"
    )
    checksum_paths = sorted(
        path for path in case_dir.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    checksum_text = "".join(
        f"{file_hash(path)}  {path.relative_to(case_dir).as_posix()}\n"
        for path in checksum_paths
    )
    (case_dir / "SHA256SUMS").write_text(checksum_text, encoding="utf-8")

    index_row = {
        "checkpoint_count": checkpoint_count,
        "expected_topology": manifest["expected_topology"],
        "frozen_at": manifest["frozen_at"],
        "package_path": f"{registry_id}/{VERSION}",
        "registry_id": registry_id,
        "reviewer": MASK,
        "sha256sums_sha256": file_hash(case_dir / "SHA256SUMS"),
        "source_run_ids": manifest["source_run_ids"],
        "version": VERSION,
    }
    return index_row, denylist


def root_readme() -> str:
    return """# Public frozen-gold disclosure

These four packages are structure-only derivatives of the human-reviewed frozen
`v002` evaluation golds. They expose checkpoint contracts, source-artifact shape,
topology labels, and provenance without publishing run payload values.

Every payload scalar is `****`, and each list is collapsed to at most one
structural exemplar so site and row cardinality cannot be reconstructed. These
are disclosure artifacts, not runnable replacements for the confidential
tabular benchmark. UC4 is represented only by a withholding notice because its
gold contains third-party report and EFRAG-derived excerpts without an explicit
redistribution grant. See `uc4_esrs_2026_impact/README.md`.
"""


def uc4_notice() -> str:
    return """# UC4 gold package not redistributed

The UC4 frozen gold contains excerpts and derived text from Siemens' FY2025
Sustainability Statement and EFRAG materials. Public availability and citation
do not by themselves grant permission to redistribute those materials inside a
new repository, so this package is intentionally withheld.

The public repository retains the UC4 pipeline code and evaluation method. To
reproduce the case, obtain the source documents lawfully, place them in the
local-only paths documented under `data/siemens_sustainability_2025/` and
`regulations/esrs/`, and run the external-corpus test set locally.
"""


def verify_persons_and_paths(
    value: Any, *, path: str = "$", check_numeric_prose: bool = True
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in PERSON_KEYS and isinstance(child, str) and child.strip() and child != MASK:
                raise LeakFound(f"person field survived at {child_path}")
            values = child if isinstance(child, list) else [child]
            if _is_numeric_masked_key(key) and any(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in values
            ):
                raise LeakFound(f"data-derived number survived at {child_path}")
            if key in TIMESTAMP_KEYS and any(
                isinstance(item, str) and item not in {"", MASK} for item in values
            ):
                raise LeakFound(f"exact timestamp survived at {child_path}")
            if any(
                isinstance(item, str) and PRIVATE_RUN_ID_RE.search(item) for item in values
            ):
                raise LeakFound(f"private run identifier survived at {child_path}")
            if key in ARTIFACT_ID_KEYS and any(
                isinstance(item, str) and PRIVATE_ARTIFACT_ID_RE.fullmatch(item)
                for item in values
            ):
                raise LeakFound(f"private artifact identifier survived at {child_path}")
            verify_persons_and_paths(
                child, path=child_path, check_numeric_prose=check_numeric_prose
            )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            verify_persons_and_paths(
                child,
                path=f"{path}[{index}]",
                check_numeric_prose=check_numeric_prose,
            )
        return
    if isinstance(value, str):
        if LOCAL_PATH_RE.search(value):
            raise LeakFound(f"machine-local path survived at {path}")
        if INTERNAL_SOURCE_SYSTEM_RE.search(value):
            raise LeakFound(f"internal source-system name survived at {path}")
        if check_numeric_prose and (
            SENSITIVE_PROSE_NUMBER_RE.search(value) or SCIENTIFIC_NUMBER_RE.search(value)
        ):
            raise LeakFound(f"data-derived number survived in prose at {path}")


def verify_masked_shape(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            verify_masked_shape(child, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        if len(value) > 1:
            raise LeakFound(f"collection cardinality survived at {path}")
        for index, child in enumerate(value):
            verify_masked_shape(child, path=f"{path}[{index}]")
        return
    if value not in {None, MASK}:
        raise LeakFound(f"payload value survived at {path}")


def verify_build(build_root: Path, denylists: dict[str, list[str]]) -> None:
    global_denylist = sorted(
        {term for denylist in denylists.values() for term in denylist},
        key=len,
        reverse=True,
    )
    for path in sorted(item for item in build_root.rglob("*") if item.is_file()):
        relative = path.relative_to(build_root)
        registry_id = relative.parts[0] if relative.parts else ""
        check_patterns = True
        if path.suffix == ".json":
            value = load_json(path)
            verify_persons_and_paths(value)
            verify_no_leaks(value, global_denylist, check_patterns=check_patterns)
            if relative.name == "registry_snapshot.json":
                verify_masked_shape(value, path=str(relative))
            if len(relative.parts) >= 2 and relative.parts[-2] in {"checkpoints", "source_artifacts"}:
                verify_masked_shape(value.get("payload"), path=f"{relative}.payload")
        else:
            text = path.read_text(encoding="utf-8")
            verify_persons_and_paths(text, check_numeric_prose=False)
            verify_no_leaks(text, global_denylist, check_patterns=check_patterns)

    for registry_id in REGISTRY_IDS:
        case_dir = build_root / registry_id
        for line in (case_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            expected, relative = line.split("  ", 1)
            if file_hash(case_dir / relative) != expected:
                raise RuntimeError(f"Checksum mismatch: {registry_id}/{relative}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        required=True,
        type=Path,
        help="Authorized private model_comparison directory",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = args.source_root.expanduser().resolve()
    PUBLIC_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".gold-build-", dir=PUBLIC_ROOT))
    build_root = temporary / "gold"
    build_root.mkdir()
    try:
        index: list[dict[str, Any]] = []
        denylists: dict[str, list[str]] = {}
        for registry_id in REGISTRY_IDS:
            row, denylists[registry_id] = write_case(build_root, source_root, registry_id)
            index.append(row)

        write_json(build_root / "index.json", index)
        (build_root / "README.md").write_text(root_readme(), encoding="utf-8")
        uc4_dir = build_root / "uc4_esrs_2026_impact"
        uc4_dir.mkdir()
        (uc4_dir / "README.md").write_text(uc4_notice(), encoding="utf-8")
        verify_build(build_root, denylists)

        root_checksum_paths = sorted(
            path for path in build_root.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
        )
        (build_root / "SHA256SUMS").write_text(
            "".join(
                f"{file_hash(path)}  {path.relative_to(build_root).as_posix()}\n"
                for path in root_checksum_paths
            ),
            encoding="utf-8",
        )

        if OUT_DIR.exists():
            shutil.rmtree(OUT_DIR)
        shutil.move(str(build_root), str(OUT_DIR))
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    print(f"Wrote {len(REGISTRY_IDS)} masked gold packages to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
