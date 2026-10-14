"""Telecom Enrichment Report API.

One endpoint per report section plus one that assembles the whole report, so a
caller can render a single panel or hand the full JSON to an LLM.
"""

from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query

from app.config import settings
from app.db import get_client
from app.mapping import Mapping, MappingError, load_mapping
from app.service import build_ipdr_params, build_params, run_ipdr_report, run_panel, run_report, run_section

app = FastAPI(title=settings.api_title, root_path=settings.api_root_path, version="1.0.0")

_mapping: Optional[Mapping] = None
_ipdr_mapping: Optional[Mapping] = None


def get_mapping() -> Mapping:
    global _mapping
    if _mapping is None:
        try:
            _mapping = load_mapping(settings.mapping_file)
        except MappingError as e:
            raise HTTPException(500, f"mapping file invalid: {e}")
    return _mapping


def get_ipdr_mapping() -> Mapping:
    global _ipdr_mapping
    if _ipdr_mapping is None:
        try:
            _ipdr_mapping = load_mapping(settings.ipdr_mapping_file)
        except MappingError as e:
            raise HTTPException(500, f"IPDR mapping file invalid: {e}")
    return _ipdr_mapping


@app.on_event("startup")
def _warm() -> None:
    try:
        load_mapping(settings.mapping_file)
    except MappingError:
        # Surfaced per-request instead; the process should still start so the
        # operator can hit /health and see what is wrong.
        pass


@app.get("/health")
def health():
    checks = {source: get_client(source).ping() for source in ("facts", "dims")}
    try:
        mapping = load_mapping(settings.mapping_file)
        mapping_status = {"ok": True, "version": mapping.version,
                          "sections": len(mapping.sections)}
    except MappingError as e:
        mapping_status = {"ok": False, "error": str(e)}
    try:
        ipdr = load_mapping(settings.ipdr_mapping_file)
        ipdr_status = {"ok": True, "version": ipdr.version, "sections": len(ipdr.sections)}
    except MappingError as e:
        ipdr_status = {"ok": False, "error": str(e)}
    healthy = all(checks.values()) and mapping_status["ok"] and ipdr_status["ok"]
    return {"status": "ok" if healthy else "degraded",
            "clickhouse": checks, "mapping": mapping_status, "ipdr_mapping": ipdr_status}


@app.get("/api/v1/sections")
def list_sections():
    mapping = get_mapping()
    return {
        "mapping_version": mapping.version,
        "sections": [
            {"id": s.id, "title": s.title, "panel": s.panel,
             "blocks": [b.key for b in s.blocks]}
            for s in mapping.sections
        ],
    }


@app.get("/api/v1/mapping")
def show_mapping():
    """What the API resolved the mapping file to -- for debugging a mount.
    Queries use bare table names, so the database each one resolves against is
    the connection's, shown here alongside the mapping file's own settings."""
    mapping = get_mapping()
    return {
        "version": mapping.version,
        "databases": {
            "facts": settings.ch_facts_database,
            "dims": settings.ch_dims_database,
        },
        "settings": mapping.settings,
    }


@app.get("/api/v1/report/{case_id}/{msisdn}/section/{section_id}")
def get_section(case_id: str, msisdn: str, section_id: str,
                limit: Optional[int] = Query(None, ge=1, le=1000)):
    mapping = get_mapping()
    section = mapping.section(section_id)
    if section is None:
        raise HTTPException(404, f"unknown section '{section_id}'. "
                                 f"See GET /api/v1/sections")
    params = build_params(mapping, msisdn, case_id, limit)
    errors: List[dict] = []
    result = {"msisdn": msisdn, "case_id": case_id,
             section_id: run_section(section, params, errors)}
    if errors:
        result["warnings"] = errors
    return result


@app.get("/api/v1/report/{case_id}/{msisdn}/left")
def get_left_panel(case_id: str, msisdn: str,
                   limit: Optional[int] = Query(None, ge=1, le=1000)):
    """Every left-panel section (subscriber, name_alias, sim_brought_from,
    alternate_numbers) in one call, instead of one request per section."""
    return run_panel(get_mapping(), msisdn, case_id, "left", limit)


@app.get("/api/v1/report/{case_id}/{msisdn}/right")
def get_right_panel(case_id: str, msisdn: str,
                    limit: Optional[int] = Query(None, ge=1, le=1000)):
    """Every right-panel section (ai_insights, locations, imei, ... ,
    cross_reference) in one call, instead of one request per section."""
    return run_panel(get_mapping(), msisdn, case_id, "right", limit)


@app.get("/api/v1/report/{case_id}/{msisdn}")
def get_report(case_id: str, msisdn: str,
               limit: Optional[int] = Query(None, ge=1, le=1000),
               sections: Optional[List[str]] = Query(None,
                   description="Repeat to pick sections; omit for the full report")):
    return run_report(get_mapping(), msisdn, case_id, limit, sections)


# ─────────────────────────────────────────────────────────────────────────────
# IPDR report: the same layout of endpoints under /api/v1/ipdr, with its own mapping file.
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/v1/ipdr/sections")
def list_ipdr_sections():
    mapping = get_ipdr_mapping()
    return {
        "mapping_version": mapping.version,
        "sections": [
            {"id": s.id, "title": s.title, "panel": s.panel, "blocks": [b.key for b in s.blocks]}
            for s in mapping.sections
        ],
    }


@app.get("/api/v1/ipdr/mapping")
def show_ipdr_mapping():
    """What the API resolved the IPDR mapping file to - the settings (thresholds, domain lists) a deployment can tune."""
    mapping = get_ipdr_mapping()
    return {"version": mapping.version,
            "databases": {"facts": settings.ch_facts_database, "dims": settings.ch_dims_database},
            "settings": mapping.settings}


@app.get("/api/v1/ipdr/report/{case_id}/{msisdn}/section/{section_id}")
def get_ipdr_section(case_id: str, msisdn: str, section_id: str,
                     limit: Optional[int] = Query(None, ge=1, le=1000)):
    mapping = get_ipdr_mapping()
    section = mapping.section(section_id)
    if section is None:
        raise HTTPException(404, f"unknown section '{section_id}'. See GET /api/v1/ipdr/sections")
    return run_ipdr_report(mapping, msisdn, case_id, limit, only=[section_id])


@app.get("/api/v1/ipdr/report/{case_id}/{msisdn}/left")
def get_ipdr_left_panel(case_id: str, msisdn: str, limit: Optional[int] = Query(None, ge=1, le=1000)):
    """Subscriber detail, Alias/Names, SIM bought from, Alternate numbers."""
    return run_ipdr_report(get_ipdr_mapping(), msisdn, case_id, limit, panel="left")


@app.get("/api/v1/ipdr/report/{case_id}/{msisdn}/right")
def get_ipdr_right_panel(
    case_id: str, msisdn: str, limit: Optional[int] = Query(None, ge=1, le=1000),
    ref: Optional[str] = Query(None, description="Ref (shown in the header)"),
    ref_date: Optional[str] = Query(None), input_gist: Optional[str] = Query(None),
    analysed_by: Optional[str] = Query(None),
    period_requested_from: Optional[str] = Query(None), period_requested_to: Optional[str] = Query(None),
):
    """The header and every right-panel section (AI Insights ... Cross Reference)."""
    extra = {"ref": ref, "ref_date": ref_date, "input_gist": input_gist, "analysed_by": analysed_by,
             "period_requested_from": period_requested_from, "period_requested_to": period_requested_to}
    return run_ipdr_report(get_ipdr_mapping(), msisdn, case_id, limit, panel="right", header_extra=extra)


@app.get("/api/v1/ipdr/report/{case_id}/{msisdn}")
def get_ipdr_report(
    case_id: str, msisdn: str, limit: Optional[int] = Query(None, ge=1, le=1000),
    sections: Optional[List[str]] = Query(None, description="Repeat to pick sections; omit for the full report"),
    ref: Optional[str] = Query(None, description="Ref (shown in the header)"),
    ref_date: Optional[str] = Query(None), input_gist: Optional[str] = Query(None),
    analysed_by: Optional[str] = Query(None),
    period_requested_from: Optional[str] = Query(None), period_requested_to: Optional[str] = Query(None),
):
    """The full IPDR report. Ref, Ref date, Input gist, Analysed by and the requested period are not in ClickHouse:
    pass them as query parameters and they are echoed in `header`."""
    extra = {"ref": ref, "ref_date": ref_date, "input_gist": input_gist, "analysed_by": analysed_by,
             "period_requested_from": period_requested_from, "period_requested_to": period_requested_to}
    return run_ipdr_report(get_ipdr_mapping(), msisdn, case_id, limit, only=sections, header_extra=extra)
