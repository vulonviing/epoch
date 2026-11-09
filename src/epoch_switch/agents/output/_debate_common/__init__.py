"""Shared infrastructure for the Debate topology method-interpreter agents (P2, P3).

Exports:
- ``MethodInterpretationRow`` — per-site-per-quarter row model shared by P2/P3.
- ``run_method_interpretation`` — batching + web-search execution helper.

Neither export carries an agent_id or a profile store; they are method-agnostic
utilities.  Method identity (regulatory grounding, lens) lives in the calling
agent's ``.md`` prompt, not here.
"""
from epoch_switch.agents.output._debate_common.method_interpretation import (
    MethodInterpretationRow,
    MethodInterpretationResult,
)
from epoch_switch.agents.output._debate_common.debate_batch import (
    run_method_interpretation,
)

__all__ = [
    "MethodInterpretationRow",
    "MethodInterpretationResult",
    "run_method_interpretation",
]
