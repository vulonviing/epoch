"""C1 output schema — per-division Scope 2 attestation.

C1 is a pure LLM agent (Coalition domain-specialist slot). It fans out one
independent call per division found in D3's consolidation payload and
attests each division's sub-total on its own merits.

Pydantic validates structure and type only. Content of LLM-generated text
fields (narrative, provenance_note, carried_caveats) is never checked
against expected values -- that is the LLM's responsibility (AGENTS.md:
Agent Type Classification, Validation Restraint).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DivisionAttestation(BaseModel):
    """Attestation for a single division's Scope 2 sub-total."""

    division: str                 # e.g. "DI", "SMO" -- carried from D3, unchanged
    subtotal_t: float             # D3's deterministic subtotal for this division, unchanged
    years: list[str] = Field(default_factory=list)  # years D3 reported for this division
    n_rows: int = 0                # D3's row count for this division, unchanged
    data_volume_status: Literal["adequate", "thin"]  # C1's own row-count read
    narrative: str                # C1's plain-language reading of this division's figure
    provenance_note: str          # assessment of evidence sufficiency behind the sub-total
    carried_caveats: list[str] = Field(default_factory=list)


class C1Result(BaseModel):
    """Top-level C1 output: one attestation per division, independently produced."""

    registry_id: str
    rows: list[DivisionAttestation]
