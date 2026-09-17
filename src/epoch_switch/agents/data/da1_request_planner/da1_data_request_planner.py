"""DA1 possible-field to runtime-catalog mapping."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from epoch_switch import config
from epoch_switch.core.envelope import CaseEnvelope
from epoch_switch.core.llm_client import llm_for_agent
from epoch_switch.core.llm_provenance import ProvenanceCollector
from epoch_switch.core.data_catalog import legacy_catalog_view


class DataRequestPlanner:
    def __init__(self, prompt_path: Path | None = None):
        self.prompt_path = prompt_path
        self.last_llm_provenance: dict | None = None

    def plan(
        self,
        *,
        envelope: CaseEnvelope,
        catalog: dict[str, Any],
        capsule: str,
    ) -> dict[str, Any]:
        prompt = self._load_prompt()
        user = {
            "registry": {
                "registry_id": envelope.usecase_ref,
                "question": envelope.natural_request,
                "site_filter": envelope.site_filter,
                "time_window": envelope.time_window,
                "time_grain": envelope.time_grain,
                "expected_output": envelope.expected_output_family,
            },
            "r1_possible_fields": envelope.regulation_profile.get(
                "possible_fields", []
            ),
            "runtime_catalog": legacy_catalog_view(catalog),
            "position_context": capsule[:3000],
        }
        llm, model = llm_for_agent("DA1")
        backend = config.backend_for_agent("DA1")
        prov = ProvenanceCollector(
            provider=backend.kind, backend_preset=backend.name, model=model
        )
        result, _ = llm.complete_json(
            prompt,
            json.dumps(user, ensure_ascii=False, indent=2, default=str),
            max_tokens=config.DA1_MAX_TOKENS,
            model=model,
            stream=True,  # max_tokens=32000 trips the Anthropic non-stream 10-min guard
            provenance=prov,
        )
        report = normalize_mapping_report(result, envelope, catalog)
        self.last_llm_provenance = prov.to_record()
        return report

    def _load_prompt(self) -> str:
        if self.prompt_path is not None and self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8").strip()
        return "Map every R1 possible field to the supplied runtime catalog."


def normalize_mapping_report(
    report: dict[str, Any],
    envelope: CaseEnvelope,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Normalize DA1 output and apply catalog-existence checks only."""
    raw = dict(report or {})
    possible_fields = envelope.regulation_profile.get("possible_fields", [])
    returned = {
        str(item.get("field_id")): item
        for item in raw.get("field_mappings", [])
        if isinstance(item, dict) and item.get("field_id")
    }
    mappings: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    for field in possible_fields:
        field_id = str(field.get("field_id", ""))
        item = returned.get(field_id, {})
        targets = _normalize_targets(item.get("catalog_targets", []))
        alternatives = _normalize_targets(item.get("alternative_targets", []))

        # Validate catalog existence for primary targets and alternatives separately.
        # Invalid alternatives are always warnings (they are human-review suggestions,
        # not pipeline-critical).  Invalid primary targets are dropped; whether the
        # result is a warning or an error depends on what remains:
        #   - core field with zero valid primary targets → hard error (stop).
        #   - at least one valid primary target remains → warning (kept N, dropped M).
        #   - related/optional field with zero valid targets → unmapped + warning (normal).
        invalid_primary = [t for t in targets if not catalog_target_exists(t, catalog)]
        invalid_alternatives = [t for t in alternatives if not catalog_target_exists(t, catalog)]

        if invalid_alternatives:
            warnings.append(
                f"{field_id}: dropped {len(invalid_alternatives)} invalid alternative "
                f"target(s) not found in catalog: "
                f"{[t.get('name') for t in invalid_alternatives]}"
            )
            alternatives = [t for t in alternatives if catalog_target_exists(t, catalog)]

        if invalid_primary:
            targets = [t for t in targets if catalog_target_exists(t, catalog)]
            if targets:
                # Some valid primary targets remain — warn but do not stop.
                warnings.append(
                    f"{field_id}: dropped {len(invalid_primary)} invalid primary "
                    f"target(s) not found in catalog: "
                    f"{[t.get('name') for t in invalid_primary]}; "
                    f"kept {len(targets)} valid target(s)"
                )
            else:
                # No valid primary targets left; escalate to error only if core.
                priority = field.get("priority", "related")
                if priority == "core":
                    errors.append(
                        f"{field_id} (core): all primary catalog targets are invalid "
                        f"and no valid fallback exists: "
                        f"{[t.get('name') for t in invalid_primary]}"
                    )
                else:
                    warnings.append(
                        f"{field_id}: all primary catalog targets are invalid; "
                        f"field will be treated as unmapped"
                    )

        # Validate optional source binding fields on primary targets only.
        for target in targets:
            validate_source_binding(target, catalog, field_id, errors)
        status = "mapped" if targets else "unmapped"
        mappings.append(
            {
                "field_id": field_id,
                "field_name": field.get("name"),
                "role": field.get("role"),
                "priority": field.get("priority"),
                "status": status,
                "catalog_targets": targets,
                "alternative_targets": alternatives,
                "reason": str(
                    item.get("reason")
                    or (
                        "No defensible runtime catalog target was identified."
                        if status == "unmapped"
                        else ""
                    )
                ),
            }
        )
        if status == "unmapped":
            warnings.append(f"{field_id} ({field.get('name')}) is unmapped")

    unknown_ids = sorted(set(returned) - {str(f.get("field_id")) for f in possible_fields})
    if unknown_ids:
        errors.append(f"DA1 returned unknown possible field IDs: {unknown_ids}")

    # Cross-mapping duplicate check: warn when two fields map to the same
    # catalog measure target without a distinguishing filter or quality_flag.
    # Targets are considered identical when (kind, name, canonical filters,
    # canonical quality_flags) match — different filters make them distinct.
    measure_target_fields: defaultdict[str, list[str]] = defaultdict(list)
    for mapping in mappings:
        if mapping["status"] != "mapped":
            continue
        for target in mapping.get("catalog_targets", []):
            if target.get("kind") != "measure":
                continue
            key = _duplicate_measure_key(target)
            measure_target_fields[key].append(mapping["field_id"])
    for key, field_ids in measure_target_fields.items():
        if len(field_ids) > 1:
            name = key.split("|", 1)[0]
            warnings.append(
                f"Duplicate measure target '{name}' mapped by {field_ids} with no "
                f"distinguishing filter or quality_flag — only one value will be fetched"
            )

    suggested = normalize_suggested_request(
        raw.get("suggested_request") or {}, envelope, catalog, errors
    )
    return {
        "summary": str(raw.get("summary") or "Catalog mapping report."),
        "field_mappings": mappings,
        "suggested_request": suggested,
        "notes": [str(item) for item in raw.get("notes", [])],
        "validation": {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
        },
    }


def normalize_suggested_request(
    request: dict[str, Any],
    envelope: CaseEnvelope,
    catalog: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    allowed_entities = set(catalog["allowed_grains"]["entity_grain"])
    allowed_times = set(catalog["allowed_grains"]["time_grain"])
    quality_policies = set(catalog["quality_policies"])
    valid_measures = {
        name for group in catalog["measures"].values() for name in group
    }
    entity = request.get("entity_grain")
    time_grain = request.get("time_grain")
    source = request.get("source_domain")
    quality = request.get("quality_policy") or "exclude_inconsistent"
    measures = [
        str(item) for item in request.get("measures", []) if str(item) in valid_measures
    ]
    if entity is not None and entity not in allowed_entities:
        errors.append(f"Suggested entity grain does not exist: {entity}")
        entity = None
    if time_grain is not None and time_grain not in allowed_times:
        errors.append(f"Suggested time grain does not exist: {time_grain}")
        time_grain = None
    if source not in {None, "energy", "kpi", "energy_and_kpi"}:
        errors.append(f"Suggested source domain does not exist: {source}")
        source = None
    if quality not in quality_policies:
        errors.append(f"Suggested quality policy does not exist: {quality}")
        quality = "exclude_inconsistent"
    return {
        "source_domain": source,
        "entity_grain": entity,
        "time_grain": time_grain,
        "time_window": {
            "start": str(
                (request.get("time_window") or {}).get("start")
                or envelope.time_window[0]
            ),
            "end": str(
                (request.get("time_window") or {}).get("end")
                or envelope.time_window[1]
            ),
        },
        "filters": dict(request.get("filters") or {}),
        "measures": list(dict.fromkeys(measures)),
        "quality_policy": quality,
    }


def catalog_target_exists(
    target: dict[str, Any], catalog: dict[str, Any]
) -> bool:
    """Return True when the target's kind/name pair exists in the catalog.

    Also validates optional source binding fields when present:
    - source_table must exist in catalog["source_tables"] if provided.
    - source_column must exist as a column of that table if both are provided.
    These structural checks are appended to the caller's error list via the
    separate validate_source_binding() helper; this function only checks kind/name.
    """
    kind = target.get("kind")
    name = target.get("name")
    if kind == "entity_grain":
        return name in catalog["allowed_grains"]["entity_grain"]
    if kind == "time_grain":
        return name in catalog["allowed_grains"]["time_grain"]
    if kind == "measure":
        return any(name in group for group in catalog["measures"].values())
    if kind == "filter":
        return any(
            name in group for group in catalog["selectable_filters"].values()
        )
    if kind == "source_domain":
        return name in {"energy", "kpi", "energy_and_kpi"}
    return False


def validate_source_binding(
    target: dict[str, Any],
    catalog: dict[str, Any],
    field_id: str,
    errors: list[str],
) -> None:
    """Validate optional source_table / source_column binding fields.

    Appends to errors (in place) if a referenced table or column is absent from
    the catalog. Does not fail when the fields are absent or null — binding is
    optional (e.g. entity_grain and time_grain targets have no column-level binding).
    """
    source_table = target.get("source_table")
    source_column = target.get("source_column")
    if not source_table:
        return
    source_tables = catalog.get("source_tables", {})
    if source_table not in source_tables:
        errors.append(
            f"{field_id}: source_table '{source_table}' not found in catalog source_tables"
        )
        return
    if source_column:
        table_columns = {
            col["name"] for col in source_tables[source_table].get("columns", [])
        }
        if source_column not in table_columns:
            errors.append(
                f"{field_id}: source_column '{source_column}' not found in "
                f"catalog source_tables.{source_table}.columns"
            )


# Binding field names preserved from LLM output (beyond kind/name).
_BINDING_FIELDS = ("source_table", "source_column", "aggregation", "filters", "quality_flags")


def _normalize_targets(values: list[Any]) -> list[dict[str, Any]]:
    """Normalise catalog target entries, preserving optional source binding fields."""
    normalized: list[dict[str, Any]] = []
    for value in values or []:
        if isinstance(value, str) and ":" in value:
            kind, name = value.split(":", 1)
            value = {"kind": kind, "name": name}
        if not isinstance(value, dict):
            continue
        kind = str(value.get("kind", "")).strip()
        name = str(value.get("name", "")).strip()
        if not (kind and name):
            continue
        entry: dict[str, Any] = {"kind": kind, "name": name}
        for field in _BINDING_FIELDS:
            if field in value:
                entry[field] = value[field]
        normalized.append(entry)
    return normalized


def _duplicate_measure_key(target: dict[str, Any]) -> str:
    """Build a composite dedup key for a measure catalog target.

    Two targets that share the same key are considered true duplicates — they
    would fetch the exact same data. Targets with identical ``name`` but
    different ``filters`` or ``quality_flags`` produce distinct keys and are
    NOT duplicates (e.g. CO2 filtered by media type vs unfiltered CO2).
    """
    name = target.get("name", "")
    filters = sorted(
        (target.get("filters") or []),
        key=lambda f: (f.get("column", ""), f.get("op", ""), str(f.get("value", ""))),
    )
    quality_flags = sorted(target.get("quality_flags") or [])
    filters_key = json.dumps(filters, sort_keys=True, ensure_ascii=False)
    qf_key = json.dumps(quality_flags, ensure_ascii=False)
    return f"{name}|{filters_key}|{qf_key}"


# Compatibility alias for callers that still use the old function name.
normalize_data_request = normalize_mapping_report
