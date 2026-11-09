"""D1 DataRequest normalization and validation helpers."""
from __future__ import annotations

from typing import Any

from epoch_switch.core.data_catalog import expand_region_bucket, resolve_source_domains
from epoch_switch.core.envelope import CaseEnvelope


def normalize_data_request(
    request: dict[str, Any],
    envelope: CaseEnvelope,
    catalog: dict[str, Any],
) -> dict[str, Any]:
    """Normalize aliases and enforce minimum fields without doing calculations."""
    req = dict(request or {})
    req.setdefault("request_id", f"data_request_{envelope.case_id}")
    req.setdefault("intent", envelope.natural_request)
    req.setdefault("entity_grain", "site")
    req.setdefault("time_grain", "year")
    req.setdefault("filters", {})
    req.setdefault("measures", ["energy_mwh", "co2_t"])
    req.setdefault("quality_policy", "exclude_inconsistent")
    req.setdefault("regulation_field_mappings", [])

    if "time_window" not in req or not isinstance(req["time_window"], dict):
        req["time_window"] = {"start": envelope.time_window[0], "end": envelope.time_window[1]}

    req["filters"] = _normalize_filter_keys(req.get("filters") or {})
    req["filters"].update(_filters_from_envelope(envelope.site_filter, only_missing=True, current=req["filters"]))
    req["time_window"] = _clamp_time_window(req["time_window"], catalog)
    req["measures"] = _normalize_measures(req.get("measures") or [])

    # source_domain and source_domains are always derived from the catalog using
    # the DA1-approved measures and filters — the R1 suggestion (if any) is
    # overridden here so D1 never depends on R1's label.
    _domains = resolve_source_domains(req["measures"], req.get("filters"))
    req["source_domains"] = _domains
    req["source_domain"] = _domains[0]  # primary for validation/metadata

    req["regulation_field_mappings"] = _normalize_regulation_mappings(
        req.get("regulation_field_mappings") or [],
        envelope.regulation_profile.get("required_data_fields", [])
        if envelope.regulation_profile
        else [],
    )
    req["validation"] = validate_data_request(req, catalog)
    return req


def validate_data_request(request: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []

    valid_entity_grains = set(catalog["allowed_grains"]["entity_grain"])
    valid_time_grains = set(catalog["allowed_grains"]["time_grain"])
    if request.get("entity_grain") not in valid_entity_grains:
        errors.append(f"Unsupported entity_grain: {request.get('entity_grain')}")
    if request.get("time_grain") not in valid_time_grains:
        errors.append(f"Unsupported time_grain: {request.get('time_grain')}")
    if request.get("source_domain") not in {"energy", "emissions", "kpi", "energy_and_kpi"}:
        errors.append(f"Unsupported source_domain: {request.get('source_domain')}")
    if request.get("quality_policy") not in set(catalog["quality_policies"]):
        errors.append(f"Unsupported quality_policy: {request.get('quality_policy')}")

    allowed_filters = _allowed_filter_values(catalog)
    for column, values in (request.get("filters") or {}).items():
        if column not in allowed_filters:
            warnings.append(f"Unknown filter column ignored by executor if absent: {column}")
            continue
        if values in (None, [], ""):
            continue
        value_list = values if isinstance(values, list) else [values]
        invalid = [v for v in value_list if v not in allowed_filters[column]]
        if invalid:
            errors.append(f"Invalid value(s) for {column}: {invalid}")

    valid_measures = {
        name
        for group in catalog["measures"].values()
        for name in group
    }
    invalid_measures = [
        measure
        for measure in request.get("measures", [])
        if measure not in valid_measures
    ]
    if invalid_measures:
        errors.append(f"Unsupported measure(s): {invalid_measures}")

    for mapping in request.get("regulation_field_mappings", []):
        field_name = mapping["required_field"]
        optional = mapping["optional"]
        if mapping["status"] == "unmapped":
            message = f"Regulation field '{field_name}' is unmapped"
            (warnings if optional else errors).append(message)
            continue
        target_errors = _mapping_target_errors(mapping, request, catalog)
        if target_errors:
            message = (
                f"Regulation field '{field_name}' has invalid mapping: "
                + "; ".join(target_errors)
            )
            (warnings if optional else errors).append(message)

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _normalize_regulation_mappings(
    mappings: list[dict[str, Any]],
    required_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_name = {
        str(item.get("required_field", "")).strip(): item
        for item in mappings
        if isinstance(item, dict) and item.get("required_field")
    }
    normalized: list[dict[str, Any]] = []
    for field in required_fields:
        name = str(field.get("name", "")).strip()
        item = by_name.get(name, {})
        targets = item.get("catalog_targets") or []
        normalized_targets = []
        for target in targets:
            if isinstance(target, str) and ":" in target:
                kind, target_name = target.split(":", 1)
                target = {"kind": kind, "name": target_name}
            if not isinstance(target, dict):
                continue
            kind = str(target.get("kind", "")).strip()
            target_name = str(target.get("name", "")).strip()
            if kind and target_name:
                normalized_targets.append({"kind": kind, "name": target_name})
        status = item.get("status")
        if status not in {"mapped", "unmapped"}:
            status = "mapped" if normalized_targets else "unmapped"
        if status == "mapped" and not normalized_targets:
            status = "unmapped"
        normalized.append(
            {
                "required_field": name,
                "role": field.get("role"),
                "optional": bool(field.get("optional", False)),
                "status": status,
                "catalog_targets": normalized_targets,
                "reason": str(
                    item.get("reason")
                    or (
                        "No explicit catalog mapping was returned by DA1."
                        if status == "unmapped"
                        else ""
                    )
                ),
            }
        )
    return normalized


def _mapping_target_errors(
    mapping: dict[str, Any],
    request: dict[str, Any],
    catalog: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    valid_filters = set(_allowed_filter_values(catalog))
    valid_measures = {
        name
        for group in catalog["measures"].values()
        for name in group
    }
    for target in mapping["catalog_targets"]:
        kind = target["kind"]
        name = target["name"]
        if kind == "entity_grain":
            if name != request.get("entity_grain"):
                errors.append(f"entity_grain '{name}' is not selected")
        elif kind == "time_grain":
            if name != request.get("time_grain"):
                errors.append(f"time_grain '{name}' is not selected")
        elif kind == "measure":
            if name not in valid_measures or name not in request.get("measures", []):
                errors.append(f"measure '{name}' is not selected")
        elif kind == "filter":
            if name not in valid_filters or name not in request.get("filters", {}):
                errors.append(f"filter '{name}' is not selected")
        elif kind == "source_domain":
            if name != request.get("source_domain"):
                errors.append(f"source_domain '{name}' is not selected")
        else:
            errors.append(f"unsupported target kind '{kind}'")
    return errors


def _allowed_filter_values(catalog: dict[str, Any]) -> dict[str, set[Any]]:
    """Build a column → allowed-value-set map from the catalog's selectable_filters.

    Bare-list filters (e.g. cdp_region: ["Europe", ...]) are loaded directly.
    Derived/virtual filters declared as dicts (e.g. emea: {"values": [True, False]})
    are also included — this strengthens validation for derived filters without
    weakening the cdp_region literal guard (cdp_region stays a bare list).
    Dict entries without a "values" list key (e.g. deferred stubs) are skipped.
    """
    out: dict[str, set[Any]] = {}
    for group in catalog["selectable_filters"].values():
        for column, spec in group.items():
            if isinstance(spec, list):
                out[column] = set(spec)
            elif isinstance(spec, dict) and isinstance(spec.get("values"), list):
                out[column] = set(spec["values"])
    return out


def _normalize_filter_keys(filters: dict[str, Any]) -> dict[str, Any]:
    # Source column names (Batch 2a). Legacy names kept as fallback aliases.
    aliases = {
        # Country
        "country": "country_code",
        "iso2_code": "country_code",         # legacy alias
        # Region
        "region": "cdp_region",
        "region_name": "cdp_region",         # legacy alias
        # BU/RC
        "bu": "bu_rc_group",
        "business_unit": "bu_rc_group",
        "bu_rc": "bu_rc_group",
        "bu_rc_code": "bu_rc_name",          # legacy alias
        # Site
        "site": "location_name",
        "site_name": "location_name",
        # Media
        "media": "media_type",
        "media_name": "media_type",          # legacy alias
        # Scope (emissions source — Batch 2b)
        "emission_scope": "scope",
        "emissions_scope": "scope",
        # Quality (deferred — kept for validator compat)
        "quality": "status",
        "data_quality_flag": "status",       # legacy alias
    }
    normalized: dict[str, Any] = {}
    for key, value in filters.items():
        normalized[aliases.get(key, key)] = value
    return normalized


def _filters_from_envelope(
    site_filter: dict[str, Any],
    *,
    only_missing: bool = False,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive executor filter entries from the registry site_filter.

    Composite region bucket labels (e.g. "EMEA") are expanded to their derived
    filter (e.g. {"emea": [True]}) instead of being aliased to cdp_region — that
    alias would carry a literal the validator correctly rejects.  Literal cdp_region
    values ("Europe", "Africa", etc.) pass through expand_region_bucket unchanged
    (returns None) and fall back to the cdp_region alias as before.
    """
    current = current or {}
    # Keys are envelope site_filter keys; values are real executor frame column names.
    # This is the single authoritative envelope→executor-filter mapping; do not
    # add a parallel alias map elsewhere (see "DA1 approval is final" boundary).
    mapping = {
        "country": "country_code",
        "region": "cdp_region",
        "bu_rc": "bu_rc_group",
        # Batch 2b: size_tier and emea are now materialised in-pipeline.
        "size_tier": "size_tier",
        "emea": "emea",
        # Scope (emissions source — folded in from _normalize_handoff's
        # former inline alias_map to keep this the single backfill source).
        "scope": "scope",
        "emission_scope": "scope",
    }
    out: dict[str, Any] = {}
    for source, target in mapping.items():
        if source not in site_filter:
            continue
        value = site_filter[source]
        if source == "region":
            bucket = expand_region_bucket(value)
            if bucket:
                # Composite bucket label — emit derived filter entries individually.
                for bk, bv in bucket.items():
                    bk_empty = current.get(bk) in (None, [], "")
                    if not only_missing or bk not in current or bk_empty:
                        out[bk] = bv
                continue
        current_empty = current.get(target) in (None, [], "")
        if not only_missing or target not in current or current_empty:
            out[target] = value
    return out


def _clamp_time_window(time_window: dict[str, Any], catalog: dict[str, Any]) -> dict[str, str]:
    adr = catalog["available_date_range"]
    min_date = adr.get("min_period_date") or adr.get("min_week_start", "2019-01-01")
    max_date = adr.get("max_period_date") or adr.get("max_week_start", "2026-12-31")
    start = str(time_window.get("start") or min_date)
    end = str(time_window.get("end") or max_date)
    return {"start": max(start, min_date), "end": min(end, max_date)}


def _normalize_measures(measures: list[Any]) -> list[str]:
    aliases = {
        "annual_energy_mwh": "energy_mwh",
        "energy_consumption_total": "energy_mwh",
        "annual_co2_t": "co2_t",
        "co2": "co2_t",
        # Real column names → executor alias
        "amount_consumed_mwh": "energy_mwh",
        "co2e_t": "co2_t",
        "kpi_value": "value",
    }
    normalized: list[str] = []
    for measure in measures:
        name = measure.get("name") if isinstance(measure, dict) else str(measure)
        normalized.append(aliases.get(name, name))
    return list(dict.fromkeys(normalized))

