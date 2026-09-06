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


def build_params(mapping: Mapping, msisdn: str, case_id: str,
                 limit: Optional[int] = None) -> Dict[str, Any]:
    """Bind params offered to every query, whether or not it uses them."""
    cfg = mapping.settings
    return {
        "msisdn": msisdn,
        "case_id": case_id,
        "limit": limit or cfg.get("top_n", settings.default_limit),
        "day_start": cfg.get("day_window_start", 6),
        "day_end": cfg.get("day_window_end", 20),
        "home_country": cfg.get("home_country_code", "91"),
        "home_circle": _lookup_home_circle(mapping, msisdn),
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
