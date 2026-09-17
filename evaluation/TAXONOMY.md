# Evaluation taxonomy

EPOCH evaluates each use case and run independently. Gold is a structural and
human-reviewed anchor, not a requirement that an LLM reproduce one sample's
wording.

## T1 — Selection correctness

The selected topology must match the expected Direct, Debate, or Coalition
shape, and the observed post-selection agent set must match that topology.

## T2 — Process conformance

The run must preserve pipeline order, human-gate placement, isolated-memory
boundaries, topology-specific input boundaries, and the advisory-only role of
external commentary agents.

## T3 — Output substantiveness

Required fields and collections must exist. Closed enums and deterministic
values are checked mechanically; LLM-composed fields are judged semantically
against their cited source using a 2/1/0 rubric:

- `2`: correct, grounded, internally consistent, and responsive.
- `1`: broadly correct but weakly grounded or materially incomplete.
- `0`: absent, contradictory, fabricated, or unsupported by its cited source.

Different wording, identifiers, or grounded grouping choices are not defects by
themselves.

## T4 — Execution integrity

The terminal record must complete in the current run. Records, provenance,
payload hashes, statuses, topology artifacts, and review rounds must form one
consistent run without stale carry-over.

## T5 — Negative activation control

No agent outside the selected topology may appear in the run, apart from the
explicitly advisory EX1 and EX2 stages.

## Publication boundary

The public packages under `public/gold/` support structural inspection of four
tabular cases. Rescoring requires authorized local run sets and underlying
sources. UC4 payloads are withheld because they reproduce third-party source
excerpts.
