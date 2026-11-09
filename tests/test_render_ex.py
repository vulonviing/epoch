"""Structural render test for EX1/EX2's CLI panel (regulation_cli.render_ex).

No LLM-content assertions (AGENTS.md: Validation Restraint) -- this only
checks that render_ex prints what a fixed payload contains: full point
text (no truncation), source URLs, and search_notes.
"""
from __future__ import annotations

from rich.console import Console

from epoch_switch.regulation_cli import _EPOCH_THEME, render_ex


def _console() -> Console:
    return Console(record=True, width=100, theme=_EPOCH_THEME, highlight=False)

LONG_POINT = (
    "EFRAG's December 2025 comparison note treats E1-6 gross Scope 3 "
    "emissions disclosure requirements as retained rather than merged "
    "into the revised standard, which is a longer sentence than the old "
    "120-character table cell could ever have shown in full."
)

PAYLOAD = {
    "registry_id": "uc3_csrd_scope2_measure",
    "stage_id": "EX1",
    "bullets": [
        {
            "point": LONG_POINT,
            "stance": "challenges",
            "relevance": "high",
            "grounding": "web_verified",
            "sources": [
                {
                    "institution": "EFRAG",
                    "url": "https://www.efrag.org/example-comparison-note",
                    "title": "ESRS Set 1 Comparison Dec 2025",
                    "published": "2025-12-01",
                }
            ],
        },
        {
            "point": "No corroborating institutional material was found for this claim.",
            "stance": "adds_context",
            "relevance": "low",
            "grounding": "model_knowledge",
            "sources": [],
        },
    ],
    "institutions_consulted": ["EFRAG", "European Commission"],
    "search_notes": ["Could not confirm CDO-specific Scope 2 guidance via search this run."],
}


def test_render_ex_shows_full_point_text_and_source_url() -> None:
    console = _console()
    render_ex(PAYLOAD, console)
    text = " ".join(
        token for token in console.export_text().split() if token != "│"
    )

    assert " ".join(LONG_POINT.split()) in text
    assert "https://www.efrag.org/example-comparison-note" in text
    assert "Could not confirm CDO-specific Scope 2 guidance" in text


def test_render_ex_prints_one_panel_per_bullet() -> None:
    console = _console()
    render_ex(PAYLOAD, console)
    text = console.export_text()

    assert "EX1 · 1/2 · challenges · high · web_verified" in text
    assert "EX1 · 2/2 · adds_context · low · model_knowledge" in text
