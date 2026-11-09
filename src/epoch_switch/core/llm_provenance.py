"""ProvenanceCollector — accumulates LLM call metadata into the
``llm_provenance`` block required by AGENTS.md's "LLM Provenance and
Reasoning Configuration" hard rule (audited as R22-R24).

Record shape (see .agents/llm-backend-reference.md):

    {
      "provider": "foundry_anthropic",
      "backend_preset": "opus48",
      "model": "claude-opus-4-8",
      "reasoning": {"enabled": true, "mode": "adaptive", "effort": "high"},
      "tokens": {"reported_as": "split", "input": 18412, "output": 3907},
      "calls": 1
    }

Usage, from an LLM/Hybrid agent:

    prov = ProvenanceCollector(provider=..., backend_preset=..., model=model)
    raw, _ = llm.complete_json(system, user, provenance=prov)
    ...
    return payload, prov.to_record()

The collector is thread-safe: P1 batches by location concurrently, and
LLMClient instances are cached per backend preset (core/llm_client.py's
_clients dict) and therefore shared across concurrent calls.
"""
from __future__ import annotations

import threading
from typing import Any


class ProvenanceCollector:
    """Accumulates call_meta dicts from one or more LLM calls made by a single
    agent invocation into one llm_provenance record."""

    def __init__(self, *, provider: str, backend_preset: str, model: str):
        self._provider = provider
        self._backend_preset = backend_preset
        self._model = model
        self._lock = threading.Lock()
        self._reasoning: dict[str, Any] | None = None
        self._reported_as: str | None = None
        self._input_total = 0
        self._output_total = 0
        self._combined_total = 0
        self._calls = 0

    def add(self, call_meta: dict[str, Any]) -> None:
        """Fold one adapter call's call_meta into the running totals."""
        with self._lock:
            self._calls += 1
            # Reasoning is expected to be constant across an agent's calls
            # (same backend, same preset) — keep the first observed value.
            if self._reasoning is None:
                self._reasoning = call_meta.get("reasoning")

            tokens = call_meta.get("tokens") or {}
            reported_as = tokens.get("reported_as")
            self._reported_as = reported_as or self._reported_as
            if reported_as == "combined":
                self._combined_total += tokens.get("total") or 0
            else:
                self._input_total += tokens.get("input") or 0
                self._output_total += tokens.get("output") or 0

    def to_record(self) -> dict[str, Any]:
        """Build the final llm_provenance block for this agent's shelf record."""
        if self._reported_as == "combined":
            tokens: dict[str, Any] = {
                "reported_as": "combined",
                "total": self._combined_total,
            }
        else:
            tokens = {
                "reported_as": "split",
                "input": self._input_total,
                "output": self._output_total,
            }
        return {
            "provider": self._provider,
            "backend_preset": self._backend_preset,
            "model": self._model,
            "reasoning": self._reasoning
            or {"enabled": False, "mode": None, "effort": None},
            "tokens": tokens,
            "calls": self._calls,
        }
