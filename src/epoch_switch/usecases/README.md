# Use Case Registry

The registry is the machine-readable list of user requests. Each
`UsecaseSeed.id` is a stable registry ID.

## Registry Content

```python
class UsecaseSeed:
    id: str
    natural_request: str
    regulation_refs: list[str]
    regulation_sources: dict[str, str]
    site_filter: dict
    time_window: tuple[str, str]
    expected_output: str
```

The registry intentionally does not contain a list of required operational
fields. R1 discovers possible fields from the request and regulation. DA1
compares those candidates with the runtime catalog. A human approves, excludes,
or remaps the candidates and decides whether the result is sufficient.

Approved outputs are owned by each agent:

```text
agents/<group>/<agent>/registry_profiles/<registry_id>/
  active.json
  archive/
```

R1 becomes active when the human approves the DA1 scope. DA1 keeps the complete
mapping review package. R2 becomes active only after its separate human review.
Handoff requires a compatible active chain.

Regulation source texts remain under `regulations/` (external, to-be-
interpreted material only — see `regulations/README.md`). This module's
docstrings and comments are the domain-layer rationale for each seed;
there is no separate spec-file directory anymore.
