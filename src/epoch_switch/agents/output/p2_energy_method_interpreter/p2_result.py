"""P2 result model — Energy-Method Interpreter (Debate topology, analyst_a slot)."""
from __future__ import annotations

from pydantic import BaseModel

from epoch_switch.agents.output._debate_common.method_interpretation import (
    MethodInterpretationRow,
)


class P2Result(BaseModel):
    """Full output produced by P2 for one registry case."""

    registry_id: str
    rows: list[MethodInterpretationRow]
