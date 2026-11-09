# R1 - Regulation Field Discovery

R1 receives a unique registry ID, the user question and scope, and the complete
configured regulation document. It independently produces:

- source-grounded regulatory findings,
- `core` possible fields directly needed by the question,
- `related` possible fields from the directly associated regulatory context.

The registry does not predeclare those fields, and R1 does not see the runtime
data catalog. Python validates the output shape and verifies that cited pages
belong to the supplied document. It does not enforce use-case-specific field
names.

Discovery profiles remain cached per registry under
`registry_profiles/<registry_id>/active.json`. R1 becomes active only when the
human accepts the corresponding DA1 review. The cache signature includes
the registry request (as `r1_registry_input_sha256`), PDF hashes, prompt hash,
model, loader version, and schema version (`R1_PROFILE_SCHEMA_VERSION = "5"`).

### Enum hard rules (R1.md)

`R1.md` enforces two hard-rule sections that fix the output vocabulary:

- **`requirement_type`** — exactly 7 allowed values: `obligation, threshold, deadline,
  method, verification, responsible_party, scope`. A mapping guide covers common
  edge cases (e.g. penalty/sanction → `obligation`; measurement procedure → `method`).
  The model must never invent a new type.
- **`possible_fields.role`** — exactly 4 allowed values: `entity, time, measure, qualifier`.

### Validation and self-repair (`R1_MAX_REPAIR_ATTEMPTS = 3`)

After each LLM call, `execute_preselection` validates the output with Pydantic and checks
citation grounding. If validation fails, a self-repair attempt is made (up to
`R1_MAX_REPAIR_ATTEMPTS = 3` total attempts): the invalid output and error message are
sent back to the LLM with an instruction to fix the contract. On success after repair, a
`validation_repaired` audit record is written to the archive. If all 3 attempts fail, a
`validation_failed` audit record is saved and `ValueError` is raised. The CLI wraps this
in a red panel — no raw traceback reaches the user.

### Grounding consistency (R1.md rules)

Three rules enforced via prompt:
- **Page–selection consistency:** every finding's `page` must appear in `selected_pages`.
- **Full-coverage:** every element of `natural_request` must be grounded or listed in
  `missing_regulatory_context` — silent omission is not allowed.
- **Entity-scope:** findings are only cited in fields whose entity scope matches the
  registry (e.g. data-centre-specific Annex findings must not ground general-site fields).
