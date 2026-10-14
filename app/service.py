"""Runs sections against ClickHouse and shapes the response."""

import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.db import ClickHouseError, get_client
from app.mapping import Block, Mapping, Section


def _lookup_home_circle(mapping: Mapping, msisdn: str) -> str:
    """Subscriber's home circle (dim_sdr), so roaming_summary/city_visits can
    tell an actual roaming circle apart from a CDR that just repeats the home
    circle in roaming_circle. Reuses the ai_insights.home_circle block's SQL
    rather than duplicating it; dims and facts are separate ClickHouse
    connections so this can't be a single SQL join -- it's one extra query up
    front instead. Best-effort: if it can't be resolved, "" is returned and
    every non-empty roaming_circle counts as roaming, same as before this."""
    section = mapping.section("ai_insights")
    block = next((b for b in section.blocks if b.key == "home_circle"), None) if section else None
    if not block or not block.sql:
        return ""
    try:
        rows = get_client(block.source).query(block.sql, {"msisdn": msisdn})
    except ClickHouseError:
        return ""
    return (rows[0].get("circle") or "") if rows else ""


def _lookup_counterparts(mapping: Mapping, msisdn: str, case_id: str) -> List[str]:
    """Distinct b-party numbers this target was in contact with
    (fact_call_detail), so sustel_match/profiler_match/cri_match can flag a
    watchlist hit on a b-party too, not just the target number itself. Reuses
    the ai_insights.counterparts block's SQL, same pattern as home_circle.
    Best-effort: an empty list on failure just means no b-party is checked."""
    section = mapping.section("ai_insights")
    block = next((b for b in section.blocks if b.key == "counterparts"), None) if section else None
    if not block or not block.sql:
        return []
    try:
        rows = get_client(block.source).query(block.sql, {"msisdn": msisdn, "case_id": case_id})
    except ClickHouseError:
        return []
    return [r["counterpart"] for r in rows if r.get("counterpart")]


def build_params(mapping: Mapping, msisdn: str, case_id: str,
                 limit: Optional[int] = None) -> Dict[str, Any]:
    """Bind params offered to every query, whether or not it uses them."""
    cfg = mapping.settings
    return {
        "msisdn": msisdn,
        "case_id": case_id,
        "limit": limit or cfg.get("top_n", settings.default_limit),
        "b_party_locations_limit": cfg.get("b_party_locations_limit", 2),
        "day_start": cfg.get("day_window_start", 6),
        "day_end": cfg.get("day_window_end", 20),
        "home_country": cfg.get("home_country_code", "91"),
        "home_circle": _lookup_home_circle(mapping, msisdn),
        "counterparts": _lookup_counterparts(mapping, msisdn, case_id),
    }


def _run_block(block: Block, params: Dict[str, Any], errors: List[Dict[str, str]],
              section_id: str) -> Any:
    """Returns just the block's value -- a dict, a list, a scalar, or None.
    A failed block contributes nothing to the payload; its error is appended
    to `errors` instead of wrapping every single value in a status envelope."""
    if block.kind == "static":
        return block.value

    try:
        rows = get_client(block.source).query(block.sql, params)
    except ClickHouseError as e:
        # A section that cannot run must not sink the rest of the report -- in an
        # offline deployment a single mismapped table is expected, and the
        # message is what tells the operator which mapping to fix.
        errors.append({"section": section_id, "block": block.key, "error": str(e)})
        return None

    if block.kind == "one":
        return rows[0] if rows else None
    if block.kind == "value":
        return list(rows[0].values())[0] if rows else None
    return rows


def run_section(section: Section, params: Dict[str, Any],
                errors: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """{block_key: value, ...} for one section. Pass a shared `errors` list to
    collect failures across multiple sections instead of one per call."""
    errs = errors if errors is not None else []
    return {b.key: _run_block(b, params, errs, section.id) for b in section.blocks}


def run_report(mapping: Mapping, msisdn: str, case_id: str,
               limit: Optional[int] = None,
               only: Optional[List[str]] = None) -> Dict[str, Any]:
    params = build_params(mapping, msisdn, case_id, limit)
    sections = [s for s in mapping.sections if not only or s.id in only]
    errors: List[Dict[str, str]] = []
    result: Dict[str, Any] = {
        "msisdn": msisdn,
        "case_id": case_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for s in sections:
        result[s.id] = run_section(s, params, errors)
    if errors:
        result["warnings"] = errors
    return result


def run_panel(mapping: Mapping, msisdn: str, case_id: str, panel: str,
             limit: Optional[int] = None) -> Dict[str, Any]:
    """Every section belonging to one panel ('left' or 'right'), run and
    returned together -- for a caller that renders one panel at a time
    instead of the whole report."""
    params = build_params(mapping, msisdn, case_id, limit)
    sections = [s for s in mapping.sections if s.panel == panel]
    errors: List[Dict[str, str]] = []
    result: Dict[str, Any] = {
        "msisdn": msisdn,
        "case_id": case_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for s in sections:
        result[s.id] = run_section(s, params, errors)
    if errors:
        result["warnings"] = errors
    return result


# ─────────────────────────────────────────────────────────────────────────────
# IPDR report. Same runners as the CDR report; only the bind parameters differ.
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_list(mapping: Mapping, section_id: str, block_key: str, params: Dict[str, Any], column: str,
                 override: Optional[Dict[str, Any]] = None) -> List[str]:
    """One column of a block's rows as a de-duplicated list of strings: the first leg of a two-step lookup (facts ->
    dims), because the two connections may be different servers and so cannot be joined in SQL. Best-effort: [] on
    failure, which just means nothing is looked up in the next leg."""
    section = mapping.section(section_id)
    block = next((b for b in section.blocks if b.key == block_key), None) if section else None
    if not block or not block.sql:
        return []
    try:
        rows = get_client(block.source).query(block.sql, {**params, **(override or {})})
    except ClickHouseError:
        return []
    return list(dict.fromkeys(str(r[column]) for r in rows if r.get(column)))


def build_ipdr_params(mapping: Mapping, msisdn: str, case_id: str, limit: Optional[int] = None) -> Dict[str, Any]:
    """Bind params offered to every IPDR query, whether or not it uses them. Three legs are looked up first:
    the resolved B-party numbers (facts), which of them are on Sustel (dims), and the handsets' IMEIs (facts)."""
    cfg = mapping.settings
    params: Dict[str, Any] = {
        "msisdn": msisdn,
        "case_id": case_id,
        "limit": limit or cfg.get("top_n", settings.default_limit),
        "events_limit": cfg.get("events_limit", 25),
        "day_start": cfg.get("day_window_start", 6),
        "day_end": cfg.get("day_window_end", 20),
        "home_country_name": cfg.get("home_country_name", "India"),
        "usage_low": cfg.get("usage_low_mb_per_day", 100.0),
        "usage_high": cfg.get("usage_high_mb_per_day", 1024.0),
        "ecommerce_domains": cfg.get("ecommerce_domains", []),
        "financial_domains": cfg.get("financial_domains", []),
        "shared_hosting_domains": cfg.get("shared_hosting_domains", []),
        "counterparts": [], "counterparts_last10": [], "sustel_last10": [], "imeis": [],
    }
    params["counterparts"] = _lookup_list(mapping, "b_party_resolution", "numbers", params, "b_party_number",
                                          {"limit": 500})
    params["counterparts_last10"] = list(dict.fromkeys(n[-10:] for n in params["counterparts"] if len(n) >= 10))
    params["sustel_last10"] = _lookup_list(mapping, "ai_insights", "sustel_hits", params, "matched_last10")
    params["imeis"] = _lookup_list(mapping, "imei", "devices", params, "imei")
    return params


def _ipdr_header(result: Dict[str, Any], extra: Optional[Dict[str, Any]]) -> None:
    """Shape the header the way the report lays it out: Ref / Ref date / Input gist / Analysed by / Analysed on /
    IPDR period requested and actual / Top place. Ref, date, gist, analyst and the requested period are not in
    ClickHouse: the caller supplies them (null when it does not)."""
    extra = extra or {}
    block = result.get("header") or {}
    actual = block.get("ipdr_period")
    result["header"] = {
        "top_place": block.get("top_place"),
        "ref": extra.get("ref"),
        "ref_date": extra.get("ref_date"),
        "input_gist": extra.get("input_gist"),
        "analysed_by": extra.get("analysed_by"),
        "analysed_on": result.get("generated_at"),
        "ipdr_period": {
            "requested": {"from": extra.get("period_requested_from"), "to": extra.get("period_requested_to")},
            "actual": actual,
        },
    }


def run_ipdr_report(mapping: Mapping, msisdn: str, case_id: str, limit: Optional[int] = None,
                    only: Optional[List[str]] = None, panel: Optional[str] = None,
                    header_extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The IPDR report (optionally one panel, or only the named sections)."""
    params = build_ipdr_params(mapping, msisdn, case_id, limit)
    sections = [s for s in mapping.sections
                if (not only or s.id in only) and (not panel or s.panel == panel)]
    errors: List[Dict[str, str]] = []
    result: Dict[str, Any] = {
        "msisdn": msisdn,
        "case_id": case_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for s in sections:
        result[s.id] = run_section(s, params, errors)
    if "header" in result:
        _ipdr_header(result, header_extra)
    if errors:
        result["warnings"] = errors
    return result
