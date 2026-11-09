import os
from pathlib import Path

import pytest


os.environ.setdefault("EPOCH_LLM_API_KEY", "test-key")


EXTERNAL_CORPUS_TEST_MODULES = {
    "test_candidate_mapper.py",
    "test_corpus_bundle.py",
    "test_document_pipeline_e2e.py",
    "test_esrs_parser.py",
    "test_rc1_1_blind_matcher.py",
    "test_rc1_change_classifier.py",
    "test_rd3_join.py",
    "test_report_and_hints.py",
    "test_rm1_1_blind_exposure.py",
    "test_rm1_exposure_mapper.py",
    "test_uc4_auto_mode.py",
}
REPO_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_CORPUS_SENTINELS = (
    REPO_ROOT / "regulations/esrs/baseline_2025_amended/ESRS_E1_2025_amended.md",
    REPO_ROOT / "regulations/esrs/2026/ESRS_E1_2026.md",
    REPO_ROOT / "regulations/comparison_helpers/E1/log_of_amendments_2025_E1.md",
    REPO_ROOT / "data/siemens_sustainability_2025/index/esrs_index.md",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip only integration modules whose third-party corpus is not shipped."""
    if all(path.is_file() for path in EXTERNAL_CORPUS_SENTINELS):
        return
    marker = pytest.mark.skip(
        reason=(
            "external_corpus: UC4 source documents are not redistributed; "
            "see data/siemens_sustainability_2025/README.md and regulations/esrs/README.md"
        )
    )
    for item in items:
        if Path(str(item.fspath)).name in EXTERNAL_CORPUS_TEST_MODULES:
            item.add_marker(marker)
