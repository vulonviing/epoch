"""Topology selection subsystem — global structural library.

The topology_library module is the single source of truth for the closed
universe Λ = {Direct, Debate, Coalition}.  No selection logic lives here.
"""
from epoch_switch.selection import topology_library

__all__ = ["topology_library"]
