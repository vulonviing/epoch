# persona_assessor/ — P4, P5, P6 (UC4 document pipeline)

One Python class (`PersonaAssessorAgent`), three `agent_id` instances:
`P4` (Conservative / minimal-change), `P5` (Balanced), `P6` (Maximum-
Assurance). This is a deliberate deviation from the one-class-per-agent
convention used elsewhere (compare `p1_result_interpreter/`) — the three
personas differ only in prompt framing (`P4.md` / `P5.md` / `P6.md`), not in
code or output schema, so a single class instantiated three times avoids
duplicating identical logic.

`BaseAgent.role_prompt_path` resolves from `type(self)` (this module's
directory) plus `self.agent_id`, so each instance loads its own `.md` file
automatically — no dispatch code needed here.

All three characters share `persona_result.PersonaVerdict` as their output
schema (`character_id`, `verdict_kind`, `recommended_action`, `effort_estimate`,
`effort_rationale`, `risk_posture_note`, `rationale`, `citations`,
`siemens_evidence_quotes`, `confidence`). This is what
lets F2 (the final synthesizer) compare structured fields across personas
instead of parsing free text.

P4 and P5 emit one `primary` row per approved RC1/RM1 finding. P6 does the
same and may add `supplemental` rows linked to an existing finding's DR ids.
F2 folds those extra concerns into the same final DR row and identifies a
P6-only concern in `persona_divergence`; P6 never expands the RC1 boundary.

`persona_profile_store.py` also deviates from the one-store-per-agent
convention for the same reason: its functions take `agent_id` as an explicit
parameter and keep shelves separate under
`registry_profiles/<agent_id>/<registry_id>/`.

All three personas always run, independently, regardless of which topology
TS1 selects for the case — see `usecases/registry.py`'s UC4 seed and
`selection/stage2_binding.py`'s `DOCUMENT_TOPOLOGY_AGENTS` table. TS1's
choice of topology is a measured signal about whether it recognized this
shape, not a switch that changes how many personas run.

Adding a fourth persona is a new `.md` file plus a new entry in
`persona_assessor.VALID_CHARACTER_IDS` and `stage2_binding._DOC_CHAIN` — no
new class.
