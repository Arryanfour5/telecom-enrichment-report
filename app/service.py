"""Runs sections against ClickHouse and shapes the response."""

import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.db import ClickHouseError, get_client
from app.mapping import Block, Mapping, Section
from app.pg import PostgresError, fetch_case_header


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


def _locations_limit(cfg: Dict[str, Any], limit: Optional[int]) -> int:
    """Rows in the day / night location lists: `locations_top_n` (5), or fewer when the caller asks for a smaller `limit`."""
    top = cfg.get("locations_top_n", 5)
    return min(top, limit) if limit else top


def build_params(mapping: Mapping, msisdn: str, case_id: str,
                 limit: Optional[int] = None) -> Dict[str, Any]:
    """Bind params offered to every query, whether or not it uses them."""
    cfg = mapping.settings
    return {
        "msisdn": msisdn,
        "case_id": case_id,
        "limit": limit or cfg.get("top_n", settings.default_limit),
        "b_party_locations_limit": cfg.get("b_party_locations_limit", 2),
        "locations_limit": _locations_limit(cfg, limit),
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
    return _dedupe(section_id, block.key, rows)


# Blocks that can list the same fact more than once: the SDR holds several records of a number, so a source can return the
# same name or number twice. Only these are de-duplicated; a list of sessions or calls may legitimately hold two identical rows.
#   per source: the same value twice from ONE source (Profiler, Phonebook, Truecaller, SDR, Sustel) is shown once, the first
#               row kept; the same value from different sources stays, one row per source
#   whole row:  only a row identical in every column is dropped
_DEDUPE_PER_SOURCE = {("name_alias", "names"): "value", ("alternate_numbers", "numbers"): "number"}
_DEDUPE_WHOLE_ROW = {("imei", "imei_dim_hits"), ("ai_insights", "sustel_match"), ("ai_insights", "profiler_match"),
                      ("ai_insights", "sustel_hits"), ("sustel", "matches")}


def _norm(value: Any) -> str:
    return " ".join(str(value).split()).casefold()


def _dedupe(section_id: str, block_key: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`rows` without the repeats described above, in their original order; any other block is returned as it is."""
    column = _DEDUPE_PER_SOURCE.get((section_id, block_key))
    if column is None and (section_id, block_key) not in _DEDUPE_WHOLE_ROW:
        return rows
    seen, kept = set(), []
    for row in rows:
        if column and row.get(column) not in (None, ""):
            key = (_norm(row.get("source")), _norm(row[column]))
        else:
            key = repr(sorted(row.items(), key=str))
        if key not in seen:
            seen.add(key)
            kept.append(row)
    return kept


def run_section(section: Section, params: Dict[str, Any],
                errors: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """{block_key: value, ...} for one section. Pass a shared `errors` list to
    collect failures across multiple sections instead of one per call."""
    errs = errors if errors is not None else []
    return {b.key: _run_block(b, params, errs, section.id) for b in section.blocks}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _case_header(case_id: str, msisdn: str, errors: List[Dict[str, str]]) -> Dict[str, Any]:
    """The case's header fields from Postgres. Best-effort like every other block: {} plus a warning when Postgres is
    down, so the report still comes back with the header fields empty."""
    try:
        return fetch_case_header(case_id, msisdn)
    except PostgresError as e:
        errors.append({"section": "header", "block": "postgres", "error": str(e)})
        return {}


def _header_fields(kind: str, case: Dict[str, Any], extra: Optional[Dict[str, Any]], generated_at: str) -> Dict[str, Any]:
    """Ref / Ref date / Input gist / Analysed by / Analysed on / `<kind>_period` requested and actual, `kind` being
    'cdr' or 'ipdr'. Postgres supplies the values; a caller-supplied value (the query parameters of the IPDR endpoints)
    wins over it."""
    extra = extra or {}
    period = (case.get("periods") or {}).get(kind) or {}
    requested, actual = period.get("requested") or {}, period.get("actual") or {}
    return {
        "ref": extra.get("ref") or case.get("ref"),
        "ref_date": extra.get("ref_date") or case.get("ref_date"),
        "input_gist": extra.get("input_gist") or case.get("input_gist"),
        "analysed_by": extra.get("analysed_by") or case.get("analysed_by"),
        "analysed_on": generated_at,
        f"{kind}_period": {
            "requested": {"from": extra.get("period_requested_from") or requested.get("from"),
                          "to": extra.get("period_requested_to") or requested.get("to")},
            "actual": {"actual_from": actual.get("from"), "actual_to": actual.get("to")} if actual else None,
        },
    }


def _cdr_header(result: Dict[str, Any], case: Dict[str, Any]) -> None:
    """The CDR report's header, put right after `generated_at` (the CDR mapping has no header section)."""
    header = _header_fields("cdr", case, None, result["generated_at"])
    ordered = {k: v for k, v in result.items() if k in ("msisdn", "case_id", "generated_at")}
    ordered["header"] = header
    ordered.update({k: v for k, v in result.items() if k not in ordered})
    result.clear()
    result.update(ordered)


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
    if not only:
        _cdr_header(result, _case_header(case_id, msisdn, errors))
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
    if panel == "right":
        _cdr_header(result, _case_header(case_id, msisdn, errors))
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
        "locations_limit": _locations_limit(cfg, limit),
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


def _ipdr_header(result: Dict[str, Any], case: Dict[str, Any], extra: Optional[Dict[str, Any]]) -> None:
    """Shape the header the way the report lays it out: Ref / Ref date / Input gist / Analysed by / Analysed on /
    IPDR period requested and actual / Top place. The case fields and the period come from Postgres (a value the caller
    passes as a query parameter wins); the top place and the session counts of the actual period are from ClickHouse."""
    block = result.get("header") or {}
    header = _header_fields("ipdr", case, extra, result["generated_at"])
    period = header["ipdr_period"]
    measured = block.get("ipdr_period") or {}                         # ClickHouse: the sessions actually present
    if measured:
        period["actual"] = {**measured, **{k: v for k, v in (period["actual"] or {}).items() if v}}
    header["top_place"] = block.get("top_place")
    result["header"] = header


# ─────────────────────────────────────────────────────────
# B Party Resolution: each resolved number with its SDR name and Sustel match
# ─────────────────────────────────────────────────────────

def _enrich_b_party_resolution(section: Dict[str, Any]) -> None:
    """Merge the `sdr` and `sustel` look-ups (dims) into the `numbers` rows (facts) by the number's last 10 digits, in Python: the two
    connections may be different servers, so they cannot be joined in SQL. Each row gains `b_party_name` (the SDR subscriber name, null when
    there is no SDR record), `sustel_match` / `b_party_sustel_match` (true / false) and `sustel` (the case and whether it is monitored, null when
    there is none); the two helper blocks are removed. A Sustel look-up that failed leaves the match null (unknown), never false."""
    sdr, sustel = section.pop("sdr", None), section.pop("sustel", None)
    rows = section.get("numbers")
    if not isinstance(rows, list):
        return
    name_by = {str(r.get("b_party_number")): r.get("subscriber_name") for r in (sdr or [])}
    sustel_by: Dict[str, List[Dict[str, Any]]] = {}
    for r in sustel or []:
        sustel_by.setdefault(str(r.get("b_party_number")), []).append({"case_name": r.get("case_name"), "monitored": r.get("monitored")})
    for row in rows:
        last10 = str(row.get("b_party_number") or "")[-10:]
        row["b_party_name"] = name_by.get(last10) or None
        row["sustel_match"] = None if sustel is None else last10 in sustel_by
        row["b_party_sustel_match"] = row["sustel_match"]
        row["sustel"] = sustel_by.get(last10) or None


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
    if isinstance(result.get("b_party_resolution"), dict):
        _enrich_b_party_resolution(result["b_party_resolution"])
    if "header" in result:
        _ipdr_header(result, _case_header(case_id, msisdn, errors), header_extra)
    if errors:
        result["warnings"] = errors
    return result


def run_combined_report(cdr_mapping: Mapping, ipdr_mapping: Mapping, msisdn: str, case_id: str,
                        limit: Optional[int] = None) -> Dict[str, Any]:
    """The CDR report and the IPDR report of one number in one response: {msisdn, case_id, generated_at, cdr, ipdr}.
    Each part is exactly what its own endpoint returns, header included."""
    return {
        "msisdn": msisdn,
        "case_id": case_id,
        "generated_at": _now(),
        "cdr": run_report(cdr_mapping, msisdn, case_id, limit),
        "ipdr": run_ipdr_report(ipdr_mapping, msisdn, case_id, limit),
    }
