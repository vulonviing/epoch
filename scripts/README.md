# Public data and gold builders

The scripts in this directory build project-owned publication artifacts. They
must never read from `data/real/` or write confidential inputs into this
repository.

## Synthetic tabular data provenance

The committed thesis fixture is frozen and verified through
`data/synthetic/SHA256SUMS`; release and CI checks do not regenerate it. The
following historical generation order is retained for provenance:

```bash
python scripts/gen_synthetic_energy.py
python scripts/gen_synthetic_emissions.py
python scripts/gen_synthetic_emissions_raw.py
python scripts/gen_synthetic_env_installations.py
python scripts/gen_synthetic_overview.py
python scripts/gen_synthetic_dims.py
```

The generators use fixed seeds and project-owned constants. Site identities,
addresses, relationships, and measurements are fabricated. Do not run these
generators as part of the thesis-delivery release; verify the committed hashes
instead.

## Masked gold disclosure

`build_public_gold.py` accepts an authorized private `model_comparison`
directory, reduces four tabular packages to structure-only disclosures,
rebuilds their hashes, and replaces `public/gold/` only after the complete
temporary build passes its leak checks:

```bash
python scripts/build_public_gold.py --source-root /path/to/model_comparison
```

The source path is not written to the output. Payload scalars are masked and
lists are collapsed to prevent cardinality disclosure. UC4 is deliberately not
exported; the builder writes a withholding notice instead.

The document-family corpus is third-party material and has no download script
in this release. Supply it locally as described in the README files under
`data/siemens_sustainability_2025/` and `regulations/esrs/`.

Before staging or publishing, run the repository-side disclosure checks:

```bash
python scripts/verify_public_release.py
```

The verifier checks forbidden paths and terms, official-PDF hashes, the
four-package gold allowlist, UC4's README-only boundary, and every published
gold checksum.
