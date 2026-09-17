"""C2 output schema — Coalition consolidation.

C2 is a pure LLM agent (Coalition synthesis-coordinator slot). It is the
first agent to see every division's C1 attestation together -- this is the
Coalition convergence point, matching topology_library.py's
``synthesis_coordinator`` slot description.

Pydantic validates structure and type only. Content of LLM-generated text
fields is never checked against expected values (AGENTS.md: Agent Type
Classification, Validation Restraint).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class C2Result(BaseModel):
    """Full output produced by C2 for one registry case."""

    registry_id: str
    portfolio_total_t: float          # C2's own sum of every division's subtotal_t
    deterministic_portfolio_total_t: float  # D3's authoritative total, carried unchanged
    thin_data_divisions: list[str] = Field(default_factory=list)
    data_volume_coverage_note: str    # plain-language coverage assessment
    readiness_assessment: str         # FY2027 mandatory-reporting readiness read
    carried_caveats: list[str] = Field(default_factory=list)
