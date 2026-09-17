# R2 - Regulation Scope Reviewer

**Status:** Active CLI-stage agent
**Type:** LLM interpretation with deterministic validation
**Stage:** After human-approved DA1 mapping; before downstream handoff

R2 converts the broad R1 discovery profile into a human-reviewable regulation
scope supported by the approved DA1 data contract. It writes new scoped
assessment statements, but every statement remains traceable to unchanged R1
finding IDs and citations.

## Inputs

- Full registry snapshot
- Full R1 profile
- Human-approved DA1 mapping scope and handoff request

## Outputs

- `in_scope_findings`: new in-scope assessment text
- `blocked_findings`: registry-relevant findings unsupported by approved data
- `excluded_findings`: findings outside the human-approved boundary
- `limitations`: residual scope qualifications

The CLI shows blocked and excluded findings once as audit warnings. A human
must approve the complete R2 scope before it becomes active.

## Artifact Locality

All R2 history lives under:

`registry_profiles/<registry_id>/archive/`

Records are tagged as rejected, abandoned, validation failed, refresh snapshot,
superseded, or retired.

The latest approved profile is:

`registry_profiles/<registry_id>/active.json`

The active record identifies the exact active R1 and DA1 records it used.
