# UC4 ESRS corpus — local source required

EFRAG-derived ESRS text, JSON responses, comparison helpers, figures, and the
Basis for Conclusions are not redistributed in this repository. Their public
availability does not by itself grant permission to reproduce the corpus here.

For a local UC4 run, lawfully obtain and prepare the sources using this layout:

```text
regulations/
├── esrs/
│   ├── baseline_2025_amended/ESRS_E1_2025_amended.md ... ESRS_E5_2025_amended.md
│   └── 2026/ESRS_E1_2026.md ... ESRS_E5_2026.md
└── comparison_helpers/
    └── E1/ ... E5/
```

The runtime paths are relative to the repository and must not be replaced with
machine-specific absolute paths. All corpus files other than this README are
gitignored. EFRAG terms: <https://knowledgehub.efrag.org/eng/terms-and-conditions>.
