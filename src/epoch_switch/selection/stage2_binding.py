"""Minimal Stage-2 topology → computation agent binding seed.

Maps each topology in Λ to the ordered list of Stage-2 agent IDs that should
run after TS1 approval.

Direct topology runs as a sequential, dependent chain: each agent in the list
receives the prior agent's output together with the isolated memory.  The list
order is the execution order.  Currently every topology routes computation
through D3 (the deterministic per-site computation agent); interpretation and
output agents follow topology-specific shapes.

Debate topology uses a nested list to express the parallel group:
  ``["D3", ["P2", "P3"], "S1", "F1"]``
A nested list entry means the agents in it run concurrently (neither sees the
other's output before reconciliation — the Debate independence rule).

F1 (Finalizer) is the last agent in every topology and holds the human review gate.
It is topology-agnostic: it reads ``demand.output_profile.form`` to choose its output
shape and prompt, then composes the reader-facing deliverable from whatever the
upstream chain produced.

This is a seed for the future general slot-binder over
``topology_library.Slot`` / ``BaseAgent.can_fill``; a fully general binder
over those slots may replace this module later, but the concrete Coalition
wiring (C1/C2, UC3) it was seeded for now lives here like every other
topology's.

Usage (CLI)::

    from epoch_switch.selection.stage2_binding import agents_for, flat_agents_for
    for item in agents_for(selected_topology):
        ...  # item is a str (sequential) or list[str] (parallel group)
    # membership checks use flat_agents_for:
    if "F1" in flat_agents_for(selected_topology):
        ...
"""
from __future__ import annotations

# Maps topology id → ordered list of Stage-2 agent IDs (or nested parallel groups).
# Direct: sequential chain — D3 (deterministic computation), P1 (LLM regulatory
#   interpreter), F1 (finalizer — human gate fires here).
# Debate: D3 first, then P2 + P3 run concurrently (parallel group, nested list),
#   then S1 (synthesizer), then F1 (finalizer — human gate fires here only).
#   Neither P2 nor P3 sees the other's output before S1.
# Coalition: D3 first, then C1 (divisional attestor — fans out one isolated
#   LLM call per division found in D3's rows; the division count is
#   data-driven, so it appears here as a single sequential entry, exactly
#   like P1's per-location fan-out never appears as a nested list), then C2
#   (synthesis coordinator — first agent to see every division's attestation
#   together), then F1 (finalizer — human gate fires here only).
# F1 is the last agent in every topology.  It is topology-agnostic: it reads
#   demand.output_profile.form and composes the reader-facing deliverable from
#   whatever upstream outputs it receives.
TOPOLOGY_AGENTS: dict[str, list] = {
    "Direct":    ["D3", "P1", "F1"],
    "Debate":    ["D3", ["P2", "P3"], "S1", "F1"],
    "Coalition": ["D3", "C1", "C2", "F1"],
}

# UC4 document pipeline (registry.py UsecaseSeed.pipeline_family="document").
# All three risk-posture personas always run, independently, regardless of
# which topology TS1 selects -- see AGENTS.md / the UC4 plan: this is the
# registry's explicit demand (output-quality priority), not a derivative of
# the topology choice. Which topology TS1 actually picks for this shape is a
# measured signal, not a switch on agent count. RD3 now runs before TS1 (it
# joins RC1+RM1 and is approved at Gate 1, alongside RC1/RM1), so it is not a
# Stage-2 agent here -- see D3's analogous omission in output_binding_for.
# F2 is the document family's finalizer/human-gate agent.
_DOC_CHAIN: list = [["P4", "P5", "P6"], "F2"]
DOCUMENT_TOPOLOGY_AGENTS: dict[str, list] = {
    "Direct":    _DOC_CHAIN,
    "Debate":    _DOC_CHAIN,
    "Coalition": _DOC_CHAIN,
}

# Reader-facing role labels travel with the runtime binding so every UI surface
# describes the same agent without maintaining a second frontend registry.
STAGE2_AGENT_ROLES: dict[str, str] = {
    "P1": "Result interpreter",
    "P2": "Energy-method interpreter",
    "P3": "Reported-method interpreter",
    "S1": "Synthesizer",
    "F1": "Finalizer",
    "P4": "Conservative assessor",
    "P5": "Balanced assessor",
    "P6": "Maximum-assurance assessor",
    "F2": "ESRS impact finalizer",
    "C1": "Divisional attestor",
    "C2": "Coalition synthesis coordinator",
}


def _table_for(family: str) -> dict[str, list]:
    if family == "document":
        return DOCUMENT_TOPOLOGY_AGENTS
    return TOPOLOGY_AGENTS


def agents_for(topology_id: str, *, family: str = "tabular") -> list:
    """Return the ordered Stage-2 agent IDs for *topology_id*.

    Entries are either strings (sequential) or lists of strings (parallel
    group — agents that run concurrently and independently of each other).
    ``family`` selects which binding table to read (registry.py
    UsecaseSeed.pipeline_family) — default "tabular" keeps every existing
    caller's behaviour unchanged.

    Returns an empty list for unknown topology IDs rather than raising,
    so the CLI can surface a human-readable message instead of a crash.
    """
    return _table_for(family).get(topology_id, [])


def flat_agents_for(topology_id: str, *, family: str = "tabular") -> list[str]:
    """Return a flat list of all Stage-2 agent IDs for *topology_id*.

    Parallel groups (nested lists) are flattened into the result.  Use this
    for membership checks (``"P2" in flat_agents_for(...)``); use
    ``agents_for`` when you need to detect parallel groups.

    Returns an empty list for unknown topology IDs.
    """
    out: list[str] = []
    for item in agents_for(topology_id, family=family):
        if isinstance(item, list):
            out.extend(item)
        else:
            out.append(item)
    return out


def output_binding_for(topology_id: str, *, family: str = "tabular") -> dict:
    """Return the output-tier binding for display and runtime inspection.

    D3 is intentionally omitted: it is the upstream computation stage and
    has its own pipeline surface. An empty rank list means the selected
    topology has no implemented output-tier binding yet.
    """
    ranks = []
    for item in agents_for(topology_id, family=family):
        if item == "D3":
            continue
        agent_ids = item if isinstance(item, list) else [item]
        ranks.append(
            {
                "mode": "parallel" if isinstance(item, list) else "sequential",
                "agents": [
                    {
                        "agent_id": agent_id,
                        "role": STAGE2_AGENT_ROLES.get(agent_id, agent_id),
                    }
                    for agent_id in agent_ids
                ],
            }
        )

    flat_agents = [
        agent["agent_id"]
        for rank in ranks
        for agent in rank["agents"]
    ]
    final_agent_id = flat_agents[-1] if flat_agents else None
    return {
        "topology_id": topology_id,
        "implemented": bool(ranks),
        "ranks": ranks,
        "final_agent_id": final_agent_id,
        "human_gate_after": final_agent_id if final_agent_id in ("F1", "F2") else None,
    }
