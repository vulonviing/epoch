from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ROOT = REPO_ROOT / "src/epoch_switch/agents"

EXPECTED_AGENT_IDS = {
    "R1", "DA1", "R2", "D1", "D2", "D3", "CP1", "TS1",
    "P1", "P2", "P3", "S1", "C1", "C2", "F1",
    "RC1.1", "RC1.2", "RM1.1", "RM1.2", "RD3",
    "P4", "P5", "P6", "F2", "EX1", "EX2",
}


def test_agent_index_names_every_executable_agent() -> None:
    index = (AGENTS_ROOT / "AGENT_INDEX.md").read_text(encoding="utf-8")
    for agent_id in EXPECTED_AGENT_IDS:
        assert f"| {agent_id} |" in index


def test_every_agent_prompt_declares_runtime_classification() -> None:
    prompts = [
        path
        for path in AGENTS_ROOT.rglob("*.md")
        if path.name not in {"README.md", "AGENT_INDEX.md"}
    ]
    assert prompts
    for prompt in prompts:
        first_line = prompt.read_text(encoding="utf-8").splitlines()[0]
        assert first_line.startswith("<!-- runtime: "), prompt


def test_public_package_is_cli_only() -> None:
    assert not (REPO_ROOT / "src/epoch_switch/webapi").exists()
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "epoch-web" not in pyproject
    assert 'web = [' not in pyproject


def test_d2_does_not_import_d1_implementation() -> None:
    source = (
        AGENTS_ROOT / "data/d2_missing_value/d2_missing_value.py"
    ).read_text(encoding="utf-8")
    assert "agents.data.d1_loader" not in source


def test_runtime_outputs_are_gitignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "experiments/*/" in gitignore
    assert "registry_profiles/" in gitignore
