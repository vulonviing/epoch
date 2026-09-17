"""Output-construction agents: interpret computation results and build deliverables.

Person (P) agents: single-owner analysts for Direct and Debate topologies.
- P1 (ResultInterpreterAgent): per-site regulatory interpretation, lead_interpreter slot.
- P2 (EnergyMethodInterpreterAgent): Method A (energy-derived) analyst, Debate analyst_a.
- P3 (ReportedMethodInterpreterAgent): Method B (reported Scope 2) analyst, Debate analyst_b.

S1 (SynthesizerAgent): converges the Debate chain's two independent reads.

F1 (FinalizerAgent): topology-agnostic deliverable composer with human gate.
"""
