"""Global structural catalog of the topology universe Λ = {Direct, Debate, Coalition}.

This module defines the *shape* of each topology — slots, interaction pattern,
description — but contains NO selection logic.  Selection heuristics, discriminators,
and cascade guidance live exclusively in TS1.md (the TS1 agent prompt).

Design mirrors registry.py:
- Three module-level TopologySpec instances (DIRECT, DEBATE, COALITION).
- LAMBDA dict keyed by topology id — the closed universe the selector must
  choose from.  The selector may choose but may not invent.
- get(id) / list_topologies() accessors (mirror get() / list_usecases() in registry.py).

Topology philosophy (Direct ↔ Debate ↔ Coalition):
  Direct   — sequential, dependent chain; single ownership;
             each step receives the prior step's output + isolated memory.
  Debate   — 2..N independent agents read the same scope in parallel, then reconcile;
             requires interpretive ambiguity or dual-method verification.
  Coalition — multiple domain-specialist agents each own a distinct sub-result in
             parallel, then a synthesis step assembles the whole.
  The critical boundary: Direct steps are DEPENDENT (each builds on the last);
  Debate agents are INDEPENDENT (neither sees the other's work before reconciliation).

Stage-2 note: these slots describe topology semantics. Concrete runtime bindings
are static in stage2_binding.py; TS1 chooses the topology, not its agent count.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Slot(BaseModel):
    """A named agent position within a topology.

    In stage 1 (topology selection) slots are structural placeholders.
    Stage 2 (agent binding) assigns a concrete agent to each slot.
    """

    position_id: str
    role: str
    role_description: str = ""


class TopologySpec(BaseModel):
    """Immutable descriptor for one member of Λ."""

    id: Literal["Direct", "Debate", "Coalition"]
    display_name: str
    description: str
    interaction_pattern: str
    slots: list[Slot] = []


# ── The three topologies ───────────────────────────────────────────────────────

DIRECT = TopologySpec(
    id="Direct",
    display_name="Direct",
    description=(
        "A sequential, dependent chain of steps under single ownership.  Each step "
        "receives the prior step's output together with the isolated memory (approved "
        "R2 findings, DA1 handoff, registry demand) and deepens the interpretation — "
        "it does not produce an independent second reading. The current runtime "
        "binding is the fixed D3 -> P1 -> F1 chain. Used when the "
        "interpretation is clear, the method is well-defined, and one team can own "
        "every step of the result.  Assurance artifact: single attestation."
    ),
    interaction_pattern="sequential chain: step → step → result",
    slots=[
        Slot(
            position_id="lead_interpreter",
            role="Lead interpreter (step 1)",
            role_description=(
                "First step in the Direct chain.  Applies the approved regulation "
                "rules and isolated memory to the loaded data and produces an initial "
                "interpreted result for the next step (or the final output if the "
                "chain has only one step)."
            ),
        ),
        Slot(
            position_id="dependent_step",
            role="Dependent follow-on step (steps 2..N)",
            role_description=(
                "Zero or more follow-on steps, each receiving the prior step's output "
                "plus the isolated memory.  Every step deepens or completes the "
                "interpretation under the same single ownership — it is NOT an "
                "independent second reading (that is Debate)."
            ),
        ),
    ],
)

DEBATE = TopologySpec(
    id="Debate",
    display_name="Debate",
    description=(
        "Two or more independent agents interpret the same approved scope and "
        "findings separately, then reconcile.  Used when the regulation is "
        "genuinely ambiguous and readers might reach different conclusions that "
        "must be surfaced and resolved before a result can be issued."
    ),
    interaction_pattern="parallel-readers (2..N) → reconciliation → result",
    slots=[
        Slot(
            position_id="analyst_a",
            role="First interpreter",
            role_description=(
                "Produces an independent interpretation without seeing "
                "any other reader's view."
            ),
        ),
        Slot(
            position_id="analyst_b",
            role="Second interpreter",
            role_description=(
                "Produces an independent interpretation without seeing "
                "any other reader's view."
            ),
        ),
        Slot(
            position_id="additional_analyst",
            role="Additional interpreter (optional, repeatable)",
            role_description=(
                "Zero or more additional independent interpretations of the same "
                "approved scope.  Each reader works without seeing any other "
                "reader's view before reconciliation."
            ),
        ),
        Slot(
            position_id="reconciler",
            role="Reconciler",
            role_description=(
                "Compares all interpretations, surfaces disagreement or agreement, "
                "and produces the final reconciled result."
            ),
        ),
    ],
)

COALITION = TopologySpec(
    id="Coalition",
    display_name="Coalition",
    description=(
        "Multiple domain-specialist agents each contribute independently from "
        "their own perspective, then a synthesis step merges them into a single "
        "result that requires multi-owner sign-off.  Used when the case spans "
        "multiple technical or organisational domains, each of which must produce "
        "its own sub-result before the whole can be assembled."
    ),
    interaction_pattern="parallel-domain-agents → synthesis → multi-owner-result",
    slots=[
        Slot(
            position_id="domain_specialist_a",
            role="Domain specialist A",
            role_description=(
                "Independently analyses the case from one technical or "
                "organisational perspective."
            ),
        ),
        Slot(
            position_id="domain_specialist_b",
            role="Domain specialist B",
            role_description=(
                "Independently analyses the case from a second technical or "
                "organisational perspective."
            ),
        ),
        Slot(
            position_id="synthesis_coordinator",
            role="Synthesis coordinator",
            role_description=(
                "Merges all domain contributions into a coherent result and "
                "coordinates multi-owner approval."
            ),
        ),
    ],
)


# ── Closed universe Λ ─────────────────────────────────────────────────────────

LAMBDA: dict[str, TopologySpec] = {
    "Direct": DIRECT,
    "Debate": DEBATE,
    "Coalition": COALITION,
}


def get(topology_id: str) -> TopologySpec:
    """Return the TopologySpec for *topology_id* (exact match, case-sensitive).

    Raises KeyError if the id is not a member of Λ.  The selector must always
    use this function to validate its output — any id not in LAMBDA is rejected.
    """
    try:
        return LAMBDA[topology_id]
    except KeyError:
        known = ", ".join(sorted(LAMBDA))
        raise KeyError(
            f"Unknown topology id '{topology_id}'. "
            f"Λ is a closed universe: {known}"
        ) from None


def list_topologies() -> list[tuple[str, TopologySpec]]:
    """Return all (id, spec) pairs in cascade-priority order: Coalition, Debate, Direct."""
    return [(t.id, t) for t in [COALITION, DEBATE, DIRECT]]
