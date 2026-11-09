from __future__ import annotations

import base64

import pytest

from epoch_switch import config
from epoch_switch.agents.regulation.r1_active_reader.r1_document_loader import (
    R1DocumentLoader,
)
from epoch_switch.core.envelope import CaseEnvelope


# 1x1 transparent JPEG-ish placeholder — content does not matter, only bytes.
_TINY_IMAGE_BYTES = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkI"
    "CQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQ"
    "EBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIA"
    "AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEB"
    "AQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX"
    "/9k="
)


@pytest.fixture()
def regulations_dir(tmp_path, monkeypatch):
    root = tmp_path / "regulations"
    root.mkdir()
    monkeypatch.setattr(config, "REGULATIONS_DIR", root)
    return root


def _make_envelope(regulation_sources: dict[str, str]) -> CaseEnvelope:
    return CaseEnvelope(
        case_id="test-case",
        usecase_ref="test-uc",
        natural_request="test",
        regulation_refs=list(regulation_sources),
        regulation_sources=regulation_sources,
        site_filter={},
        time_window=("2024-01-01", "2024-12-31"),
        expected_output_family="qualitative",
    )


def test_source_path_resolves_nested_markdown_and_rejects_traversal(regulations_dir):
    nested = regulations_dir / "esrs" / "2026"
    nested.mkdir(parents=True)
    (nested / "ESRS_E1_2026.md").write_text("# doc", encoding="utf-8")

    loader = R1DocumentLoader()
    resolved = loader._source_path("esrs/2026/ESRS_E1_2026.md")
    assert resolved == (nested / "ESRS_E1_2026.md").resolve()

    with pytest.raises(ValueError):
        loader._source_path("../secrets.pdf")


def test_load_markdown_splits_pages_and_extracts_images(regulations_dir):
    assets = regulations_dir / "esrs" / "assets"
    assets.mkdir(parents=True)
    (assets / "figure.jpg").write_bytes(_TINY_IMAGE_BYTES)

    doc_dir = regulations_dir / "esrs" / "2026"
    doc_dir.mkdir(parents=True)
    md_path = doc_dir / "ESRS_E1_2026.md"
    md_path.write_text(
        "# ESRS E1 (2026)\n\n"
        "- **Standard:** ESRS E1\n\n"
        "---\n\n"
        "## Objective\n\n"
        "**1.** The objective of this Standard is...\n\n"
        "## Application Requirements\n\n"
        "**AR 15** See the figure below.\n\n"
        "![ESRS E1 figure](../assets/figure.jpg)\n",
        encoding="utf-8",
    )

    env = _make_envelope({"REG-6": "esrs/2026/ESRS_E1_2026.md"})
    loader = R1DocumentLoader()
    pages, source_hashes = loader.load(env)

    assert len(pages) == 3  # preamble + "Objective" + "Application Requirements"
    assert pages[0]["page"] == 1
    assert pages[1]["heading"] == "Objective"
    assert pages[2]["heading"] == "Application Requirements"
    assert "esrs/2026/ESRS_E1_2026.md" in source_hashes

    image_pages = [p for p in pages if p["images"]]
    assert len(image_pages) == 1
    image = image_pages[0]["images"][0]
    assert image["media_type"] == "image/jpeg"
    assert image["alt"] == "ESRS E1 figure"
    assert base64.b64decode(image["data_b64"]) == _TINY_IMAGE_BYTES


def test_hashes_includes_referenced_image_assets_for_markdown(regulations_dir):
    assets = regulations_dir / "esrs" / "assets"
    assets.mkdir(parents=True)
    (assets / "figure.jpg").write_bytes(_TINY_IMAGE_BYTES)

    doc_dir = regulations_dir / "esrs" / "2026"
    doc_dir.mkdir(parents=True)
    md_path = doc_dir / "ESRS_E1_2026.md"
    md_path.write_text(
        "## Section\n\n![alt](../assets/figure.jpg)\n", encoding="utf-8"
    )

    env = _make_envelope({"REG-6": "esrs/2026/ESRS_E1_2026.md"})
    loader = R1DocumentLoader()
    hashes = loader.hashes(env)

    assert "esrs/2026/ESRS_E1_2026.md" in hashes
    assert any("figure.jpg" in key for key in hashes)
