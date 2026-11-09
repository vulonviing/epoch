# Reference gold-scoring procedure

Use this procedure only with an authorized local run set and its corresponding
source documents. The disclosure packages in `public/gold/` are insufficient
for a full rescore because confidential inputs and source-bearing UC4 artifacts
are intentionally absent.

1. Identify the registry, pipeline family, expected topology, and frozen gold
   version from the local contract and manifest.
2. Exclude every run used to construct the gold; scoring a source run against
   itself is circular.
3. Derive the expected topology from the registry demand and topology library
   before reading TS1's answer.
4. Apply T1, T2, and T5 first; record one evidence-backed verdict per run.
5. Apply T3 field-by-field. Treat gold as a structural template, judge semantic
   claims against their own cited sources, and record every score below `2`.
6. Apply T4 deterministically to completion, status, provenance, hashes,
   topology artifacts, and review rounds.
7. Report excluded runs, missing inputs, data drift, and non-applicable checks
   separately from failures.
8. Keep all generated findings and reports inside the evaluated use case's
   local evaluation directory; do not commit restricted inputs or outputs.

See `TAXONOMY.md` for the definitions and scoring rubric. Do not infer missing
sources, improvise a gold, or compare LLM prose by literal wording.
