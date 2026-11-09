"""Deterministic D1 executor for regulation CLI handoff flow.

Takes an approved handoff_data_request from DA1's approved scope,
normalizes and validates it against the runtime catalog, then executes
via DataRequestExecutor. No LLM planning in this mode.

Agent prompt: epoch_switch/agents/data/d1_loader/D1.md.
Upstream: DA1 approved_mapping_scope.handoff_data_request.
Downstream: D2 quality preflight, derived_case_facts_builder.py, and CP1.
"""
from __future__ import annotations

from typing import Any

from epoch_switch.core.envelope import CaseEnvelope

from epoch_switch.core.data_catalog import DataCatalogBuilder
from .d1_data_executor import DataRequestExecutor
from .d1_data_request_planner import normalize_data_request, validate_data_request


class HandoffDataExecutor:
    """Deterministic D1 executor fed by approved handoff_data_request.

    Regulation CLI mode only. No LLM planning.
    handoff_data_request comes from DA1+human approval,
    is normalized and validated, then executed via DataRequestExecutor.
    """

    def __init__(self, executor: DataRequestExecutor | None = None):
        self._executor = executor or DataRequestExecutor()

    def execute_from_handoff(
        self,
        registry_id: str,
        handoff_request: dict[str, Any],
        envelope: CaseEnvelope,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Execute D1 from an approved handoff_data_request.

        1. Build runtime catalog via DataCatalogBuilder
        2. Normalize handoff_request into executor-compatible DataRequest
        3. Validate against catalog — raise ValueError if invalid
        4. Execute via DataRequestExecutor
        5. Build summary for shelf storage
        6. Return (data_request, data_product, summary)

        Args:
            registry_id: The registry identifier (e.g. "uc1_ets1_monitoring")
            handoff_request: The approved handoff_data_request from DA1
            envelope: The CaseEnvelope for this run

        Returns:
            Tuple of (normalized_data_request, data_product, summary)

        Raises:
            ValueError: If handoff_request is missing or validation fails
        """
        if not handoff_request:
            raise ValueError(
                "handoff_data_request is missing. "
                "DA1 approved scope must contain a valid handoff_data_request."
            )

        catalog = DataCatalogBuilder().build()

        # Capture the DA1-approved time window before normalization/clamping so we
        # can detect and surface any divergence as a CP1 limitation downstream.
        raw_tw = handoff_request.get("time_window")
        requested_time_window: dict[str, Any] = dict(raw_tw) if raw_tw else {
            "start": envelope.time_window[0],
            "end": envelope.time_window[1],
        }

        data_request = self._normalize_handoff(handoff_request, envelope, catalog)

        validation = validate_data_request(data_request, catalog)
        if not validation.get("ok", False):
            errors = validation.get("errors", [])
            raise ValueError(
                f"handoff_data_request validation failed: {'; '.join(errors)}"
            )

        data_product = self._executor.execute(data_request)

        summary = self._build_summary(
            data_request, data_product, validation, requested_time_window
        )

        return data_request, data_product, summary

    def _normalize_handoff(
        self,
        handoff_request: dict[str, Any],
        envelope: CaseEnvelope,
        catalog: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize handoff_request into executor-compatible DataRequest.

        Merges envelope context (time_window, site_filter) and applies
        the existing normalize_data_request() function.
        """
        request = dict(handoff_request)

        if "time_window" not in request or not request["time_window"]:
            request["time_window"] = {
                "start": envelope.time_window[0],
                "end": envelope.time_window[1],
            }

        if "filters" not in request:
            request["filters"] = {}

        # Envelope-to-filter backfill is delegated entirely to normalize_data_request
        # → _filters_from_envelope, which is the single EMEA-aware backfill source.
        # A parallel inline alias_map here would diverge from _filters_from_envelope
        # and re-inject composite bucket labels (e.g. "EMEA") as raw cdp_region
        # values, defeating the expansion and violating "DA1 approval is final":
        # the approved handoff is authoritative and must not be silently overwritten
        # with raw registry scope values.
        return normalize_data_request(request, envelope, catalog)

    @staticmethod
    def _build_summary(
        data_request: dict[str, Any],
        data_product: dict[str, Any],
        validation: dict[str, Any],
        requested_time_window: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a compact summary for shelf storage.

        When ``requested_time_window`` differs from the effective window in
        ``data_request``, the summary includes ``requested_time_range``,
        ``effective_time_range``, and ``time_window_clamped: True`` so that
        downstream agents (derived_case_facts → CP1) can surface the divergence
        from the DA1-approved handoff as a CP1 limitation.
        """
        tables_info = {}
        total_rows = 0
        all_measures = set()

        for name, table in data_product.get("tables", {}).items():
            row_count = table.get("row_count", 0)
            columns = table.get("columns", [])
            quality_summary = table.get("quality_summary", {})
            tables_info[name] = {
                "row_count": row_count,
                "column_count": len(columns),
                "columns": columns,
                "quality_summary": quality_summary,
            }
            total_rows += row_count

            for col in columns:
                if col not in {
                    # Real column names (Batch 2a)
                    "location_id", "location_name", "country", "country_code",
                    "country_name", "cdp_region", "bu_rc_name", "bu_rc_group",
                    # Legacy names (kept for backward compat with older shelf records)
                    "iso2_code", "region_name", "bu_rc_code",
                    # Derived dimensions (Batch 2b: materialised, still dim not measure)
                    "size_tier", "emea", "scope",
                    "time_period",
                }:
                    all_measures.add(col)

        time_window = data_request.get("time_window", {})
        effective_start = time_window.get("start", "")
        effective_end = time_window.get("end", "")

        req_start = (requested_time_window or {}).get("start", effective_start)
        req_end = (requested_time_window or {}).get("end", effective_end)
        clamped = (req_start != effective_start) or (req_end != effective_end)

        summary: dict[str, Any] = {
            "tables": list(data_product.get("tables", {}).keys()),
            "total_rows": total_rows,
            "measures": sorted(all_measures),
            "grain": f"{data_request.get('entity_grain', 'site')} × {data_request.get('time_grain', 'year')}",
            "time_range": [effective_start, effective_end],
            "requested_time_range": [req_start, req_end],
            "effective_time_range": [effective_start, effective_end],
            "time_window_clamped": clamped,
            "source_domain": data_request.get("source_domain", "energy"),
            "quality_policy": data_request.get("quality_policy", "exclude_inconsistent"),
            "tables_detail": tables_info,
            "validation": validation,
        }
        return summary
