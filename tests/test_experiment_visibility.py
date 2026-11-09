"""Verifies experiment runs are routed to the right git-visibility root.

Tabular use cases (UC1-UC3, pipeline_family="tabular") read data/real/ and
must land under experiments/real/ (gitignored, confidential). Document-family
use cases (UC4, pipeline_family="document") read only public regulation/report
text and must land under experiments/public/ (committed). The routing lives
entirely in config.create_experiment_dir's `visibility` param -- this test
checks that param, not any use-case-id special-casing.
"""
from __future__ import annotations

import epoch_switch.config as config
from epoch_switch.usecases.registry import UC1, UC2, UC3, UC4


def test_default_visibility_is_real(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPERIMENTS_DIR", tmp_path / "real")
    monkeypatch.setattr(config, "PUBLIC_EXPERIMENTS_DIR", tmp_path / "public")
    (tmp_path / "real").mkdir()
    (tmp_path / "public").mkdir()

    run_dir = config.create_experiment_dir("test_run")

    assert run_dir.parent == tmp_path / "real"


def test_public_visibility_routes_to_public_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPERIMENTS_DIR", tmp_path / "real")
    monkeypatch.setattr(config, "PUBLIC_EXPERIMENTS_DIR", tmp_path / "public")
    (tmp_path / "real").mkdir()
    (tmp_path / "public").mkdir()

    run_dir = config.create_experiment_dir("test_run", visibility="public")

    assert run_dir.parent == tmp_path / "public"
    readme = (run_dir / "README.md").read_text(encoding="utf-8")
    assert "`public`" in readme
    assert "committed to git" in readme


def test_tabular_usecases_are_tabular_family():
    for seed in (UC1, UC2, UC3):
        assert seed.pipeline_family == "tabular"


def test_document_usecase_is_document_family():
    assert UC4.pipeline_family == "document"
