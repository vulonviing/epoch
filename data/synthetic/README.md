# Synthetic tabular dataset

Every CSV in this directory is project-generated synthetic data. It preserves
the tabular schema used by EPOCH without representing a real Siemens site,
address, relationship, or measurement.

The committed dataset is the fixed thesis-delivery fixture. Release checks use
`SHA256SUMS` to verify it in place; they do not regenerate it. The generator
scripts under `scripts/` remain available only to document how the fixture was
created.

`.master_dims.json` is generator metadata for the committed synthetic fixture,
not a confidential source file.
