# DA1 - Data Catalog Mapping

DA1 receives R1's independently discovered possible fields and the current
runtime data catalog. It returns one mapping report row for every possible
field:

- `mapped` with exact catalog targets,
- `unmapped` when no defensible target exists,
- optional alternative targets for human review.

Unmapped fields do not automatically invalidate the business request. Python
checks structural integrity, catalog target existence, and optional source
binding validity (table/column existence). A human reviewer approves, excludes,
or remaps fields and decides whether the final set is sufficient.

### Source binding

Each `catalog_targets` entry now includes source binding fields populated by
the DA1 LLM from the catalog:

| Field | Description |
|---|---|
| `source_table` | Table in `catalog.source_tables` (null for grain/domain targets) |
| `source_column` | Exact column in that table (null if not column-level) |
| `aggregation` | `sum`, `weighted`, `lookup`, or `none` |
| `filters` | Inline `WHERE` conditions for derived measures (e.g. `media_name == Electricity`) |
| `quality_flags` | Relevant quality columns from `column_quality_policies` |

`alternative_targets` keep the compact `{kind, name}` format.

### time_grain consistency

`suggested_request.time_grain` always reflects the widest grain required by any
`core` possible field. If any core field maps to `multi_year`, the suggested
request uses `multi_year`.

The regulation CLI promotes DA1 output only after human approval.

### Agent-local persistence

The regulation CLI persists DA1-owned registry artefacts inside this folder:

- `registry_profiles/<registry_id>/active.json` - complete approved review
  package: catalog, mapping report, human decisions, approved scope, and handoff
- `registry_profiles/<registry_id>/archive/` - tagged previous, rejected, and
  refresh records

DA1's folder is the source of truth; there is no shared template copy.
