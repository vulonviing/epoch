"""P3 result model — Reported-Method Interpreter (Debate topology, analyst_b slot)."""
from __future__ import annotations

from pydantic import BaseModel

from epoch_switch.agents.output._debate_common.method_interpretation import (
    MethodInterpretationRow,
)


class P3Result(BaseModel):
    """Full output produced by P3 for one registry case."""

    registry_id: str
    rows: list[MethodInterpretationRow]
