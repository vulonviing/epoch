"""TS1 — Topology Selector agent package.

Exports:
- TopologySelectorAgent  — the agent class (use execute_selection for CLI integration)
- TopologySelection      — the output schema (Pydantic model)
- topology_selector_store — agent-local shelf module (active.json + archive/)
"""
from epoch_switch.agents.orchestration.topology_selector.topology_selector import (
    TopologySelectorAgent,
)
from epoch_switch.agents.orchestration.topology_selector.topology_selection import (
    TopologySelection,
)
from epoch_switch.agents.orchestration.topology_selector import topology_selector_store

__all__ = ["TopologySelectorAgent", "TopologySelection", "topology_selector_store"]
