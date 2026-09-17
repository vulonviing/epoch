# EPOCH

EPOCH (**E**nvironmental **P**rotection **O**rchestration **C**hain
**H**ierarchy) is the public companion repository for the Master's thesis
*Audit-Aware Topology Selection for LLM Agents* (University of Konstanz,
2026), developed in an industrial context at Siemens AG.

The system maps a case, requested output, and assurance profile to one of
three execution shapes:

```text
pi(case, output_profile, assurance_profile) -> Direct | Debate | Coalition
```

This is a CLI-only research release. A separate, read-only presentation of the
thesis runs is available at <https://emrecanulu.com/epoch/>; that viewer belongs
to a different repository and is not part of this code release.

## Disclosure boundary

No confidential raw data, runtime shelves, raw model runs, individual peer
reviews, or source-bearing UC4 gold is included.

- `data/synthetic/` is the fixed, entirely synthetic tabular fixture.
- `regulations/` contains selected official legal PDFs and source metadata.
- `evaluation/` publishes the method, taxonomy, rubric, and anonymous aggregate
  peer-review result.
- `public/gold/` contains four structure-only derivatives of frozen tabular
  gold. Every run-payload scalar is masked and collection cardinality is
  collapsed; these packages are not runnable benchmark copies.
- UC4 source text and gold remain withheld. The repository provides setup and
  methodology documentation only.

See `NOTICE`, `evaluation/README.md`, and `public/gold/README.md` for the exact
publication boundary.

### Fresh history, not a filtered mirror

This repository's Git history begins at its own initial commit. It was
authored from scratch as a disclosure-safe export, not produced by filtering
or truncating the private development repository's history. That private
repository carries confidential Siemens data throughout its history and
cannot be made public in any form, including its commit log. Any thesis
statement that cites private-repository commit timestamps (for example,
development-time corroboration for a specific use case) refers to history
that is not published here and cannot be independently re-checked from this
repository.

### What this repository cannot independently re-verify

Some thesis claims rest on records this repository deliberately excludes, for
the same confidentiality reason as above:

- **Raw run records.** Full per-agent input/output payloads for real-data
  evaluation runs are not published, because the underlying case data is
  confidential Siemens data. `public/gold/` ships masked, structure-only
  derivatives instead; see `public/gold/README.md`.
- **Individual peer-review responses.** The thesis quotes or summarizes
  specific reviewer scores and comments; the underlying per-reviewer records
  are not redistributed here, only anonymized aggregate results (see
  `evaluation/README.md`, `NOTICE`).
- **UC4 gold and source excerpts.** The Siemens Sustainability Statement and
  EFRAG-derived corpus material are withheld, so UC4 results reported in the
  thesis cannot be recomputed from this repository.

These are disclosure limits, not corrections to the thesis: the underlying
records exist and were reviewed before the thesis was written, but this
repository is not the evidentiary record for them.

## Executable pipelines

Tabular use cases:

```text
R1 -> DA1 -> human -> R2 -> human -> D1 -> D2 -> derived facts -> CP1 -> TS1
   -> Direct:   D3 -> P1 -> F1
   -> Debate:   D3 -> (P2 || P3) -> S1 -> F1
   -> Coalition:D3 -> C1 -> C2 -> F1
   -> human final review
```

Document-family use cases:

```text
RD1 -> RD2 -> RC1.1 -> RC1.2 -> RM1.1 -> RM1.2 -> RD3 -> human
    -> CP1 -> TS1 -> (P4 || P5 || P6) -> F2 -> human
```

The complete map from every agent ID to its prompt, implementation, result
schema, and shelf module is in `src/epoch_switch/agents/AGENT_INDEX.md`.
RD1/RD2 are deterministic corpus stages, not LLM agents.

## Installation and use

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env

epoch-regulation --help
epoch-regulation build --usecase 2
epoch-regulation show --usecase 2
```

Full LLM runs require credentials for one of the research backend presets in
`.env.example`. Unit tests, package verification, disclosure checks, and static
architecture checks do not require LLM credentials or confidential data.

UC4 additionally requires lawfully obtained local report and ESRS corpora; see
`data/siemens_sustainability_2025/README.md` and `regulations/esrs/README.md`.

## Reproducibility and verification

The committed synthetic dataset is frozen for thesis delivery and is not
regenerated during verification. Its hashes are recorded in
`data/synthetic/SHA256SUMS`; generator scripts remain as provenance.

```bash
pytest -q
python scripts/verify_public_release.py
python skills/system-check/check.py --static --json

python -m build
python scripts/verify_distribution.py dist/epoch_switch-0.1.0-py3-none-any.whl
```

The public evaluation inventory is in `evaluation/publication_manifest.json`.
The four disclosed cases cover Direct, Debate, and Coalition; UC4 is documented
as withheld because its gold reproduces third-party source excerpts.

## Citation and license

Citation metadata is provided in `CITATION.cff`. Project-authored code and
documentation are licensed under Apache-2.0; third-party legal texts retain
their own source terms. See `LICENSE`, `NOTICE`, and
`regulations/SOURCE_MANIFEST.md`.
