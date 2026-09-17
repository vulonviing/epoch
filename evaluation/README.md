# Disclosure-safe evaluation material

This directory publishes the project-authored evaluation method without
shipping confidential or third-party source-bearing artifacts.

Included:

- `TAXONOMY.md`: definitions of the five evaluation tests.
- `JUDGE_PROMPT.md`: a reference procedure for authorized local run sets.
- `peer_review/`: the fixed rubric and anonymized aggregate scores.
- `uc4_document_audit/`: methodology and withholding explanation.
- `public/gold/` at the repository root: four masked tabular disclosure packages.
- `publication_manifest.json`: case-by-case topology, gold, and review boundary.

Not included:

- private-data gold drafts or frozen source packages;
- raw model runs and LLM records;
- individual reviewer PDFs, comments, identities, or transcripts;
- UC4 audit packets and gold payloads containing Siemens/EFRAG excerpts.

The gold packages document contracts, topology labels, structure, and
provenance. Every run-payload scalar is masked and lists are collapsed to a
single structural exemplar, so they are not runnable replacements for the
withheld benchmark inputs. Reproduction requiring restricted inputs must be
performed locally by an authorized user.
