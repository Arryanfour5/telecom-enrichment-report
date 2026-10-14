"""Tests for the IPDR enrichment report. No database: a fake ClickHouse answers by SQL text.

    PYTHONPATH=. python -m pytest tests -q           (needs fastapi, requests, pydantic-settings, httpx, pytest)
"""
import re
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app import service
from app.main import app
from app.mapping import load_mapping

MAPPING_FILE = Path(__file__).resolve().parent.parent / "config" / "ipdr_report_mapping.py"
mapping = load_mapping(MAPPING_FILE)

CASE = "11111111-2222-3333-4444-555555555555"
KNOWN_BINDS = {
    "msisdn", "case_id", "limit", "events_limit", "day_start", "day_end", "home_country_name", "usage_low", "usage_high",
    "ecommerce_domains", "financial_domains", "shared_hosting_domains",
    "counterparts", "counterparts_last10", "sustel_last10", "imeis",
}
# The client's report structure, in order.
EXPECTED_RIGHT = ["header", "ai_insights", "locations", "imei", "imsi", "connectivity", "applications_used",
                  "vpn_usage", "tor_usage", "bittorrent_usage", "hotspot_usage", "websites_used", "b_party_resolution",
                  "sustel", "social_media", "cross_reference"]
EXPECTED_LEFT = ["subscriber", "name_alias", "sim_brought_from", "alternate_numbers"]


def _blocks():
    return [(s.id, b) for s in mapping.sections for b in s.blocks]


def test_mapping_has_the_clients_sections_in_order():
    assert [s.id for s in mapping.sections if s.panel == "right"] == EXPECTED_RIGHT
    assert [s.id for s in mapping.sections if s.panel == "left"] == EXPECTED_LEFT


def test_ai_insights_has_the_six_points():
    keys = [b.key for b in mapping.section("ai_insights").blocks]
    for needed in ("internet_usage", "sustel_connectivity", "international_roaming", "frequent_roaming", "city_visits",
                   "last_ipdr_date"):
        assert needed in keys


def test_connectivity_has_the_voip_calls_from_the_inferred_call_type():
    blocks = mapping.section("connectivity").blocks
    assert [b.key for b in blocks] == ["voip_by_country", "voip_calls", "b_party_locations"]
    for b in blocks[:2]:
        assert "f.voip_call_type != ''" in b.sql and "VoIP Media" not in b.sql      # not the Application Database tag
    assert not any("VoIP Media" in b.sql for _, b in _blocks())


def test_locations_has_day_night_international_and_city():
    assert [b.key for b in mapping.section("locations").blocks] == ["day", "night", "international", "international_cities"]


@pytest.mark.parametrize("section_id,block", _blocks(), ids=lambda v: v if isinstance(v, str) else v.key)
def test_every_block_has_sql_that_binds_only_known_parameters(section_id, block):
    assert block.sql and block.kind in ("one", "many", "value")
    assert set(re.findall(r"\{(\w+):[\w()]+\}", block.sql)) <= KNOWN_BINDS, f"{section_id}.{block.key}"
    assert block.sql.count("(") == block.sql.count(")")
    assert block.source in ("facts", "dims")


def test_fact_blocks_read_internet_sessions_not_call_records():
    for section_id, block in _blocks():
        if block.key == "other_cases_cdr":
            continue
        assert "fact_call_detail" not in block.sql, f"{section_id}.{block.key} reads CDR facts"
        assert "sub_fact_mapping_a_party" not in block.sql, f"{section_id}.{block.key} reads a CDR bridge"


def test_cdr_cross_reference_is_the_only_cdr_reader_and_is_labelled():
    readers = [(s, b.key) for s, b in _blocks() if "fact_call_detail" in b.sql]
    assert readers == [("cross_reference", "other_cases_cdr")]


def test_the_number_is_matched_on_its_last_ten_digits_on_the_fact_side():
    for section_id, block in _blocks():
        if block.source == "facts" and "fact_internet_sessions" in block.sql and block.key != "other_cases_cdr":
            assert "right(f.device_id, 10) = right({msisdn:String}, 10)" in block.sql, f"{section_id}.{block.key}"


def test_left_panel_reuses_the_cdr_mapping_blocks():
    from importlib import util
    spec = util.spec_from_file_location("cdr_mapping_under_test", MAPPING_FILE.with_name("report_mapping.py"))
    cdr = util.module_from_spec(spec)
    spec.loader.exec_module(cdr)
    expected = cdr.TelecomReportMapping.subscriber_details()["clickhouse_query"]
    assert next(b for b in mapping.section("subscriber").blocks if b.key == "subscriber").sql == expected


def test_thresholds_and_domain_lists_are_settings_not_code():
    cfg = mapping.settings
    assert cfg["usage_low_mb_per_day"] < cfg["usage_high_mb_per_day"]
    assert "amazon.in" in cfg["ecommerce_domains"] and "sbi.co.in" in cfg["financial_domains"]
    assert "amazonaws.com" in cfg["shared_hosting_domains"] and "amazonaws.com" not in cfg["ecommerce_domains"]


# ─────────────────────────────────────────────────────────────────────────────
# Fake ClickHouse
# ─────────────────────────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def query(self, sql: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.calls.append({"sql": sql, "params": dict(params)})
        if "AS actual_from" in sql:
            return [{"msisdn": params["msisdn"], "actual_from": "2025-05-01 08:00:00", "actual_to": "2025-05-14 13:23:00",
                     "sessions": 202, "days_with_data": 11, "files": 1}]
        if "AS place" in sql:
            return [{"place": "Karachi", "state": "Sindh", "country": "Pakistan", "latitude": 24.8, "longitude": 67.0,
                     "sessions": 2, "cells": 2}]
        if "f.b_party_number AS b_party_number" in sql:
            return [{"b_party_number": "919886142073", "resolved_as": "P2P Session", "sessions": 3},
                    {"b_party_number": "919000000001", "resolved_as": "P2P Session", "sessions": 1}]
        if "AS matched_last10" in sql:
            return [{"matched_last10": "9886142073", "case_name": "X", "monitored": "Y", "match_type": "b_party"}]
        if "AS imei, s.first_seen" in sql:
            return [{"imei": "351000000000000", "sessions": 5}]
        return []

    def ping(self):
        return True


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(service, "get_client", lambda source: client)
    return client


def test_params_are_looked_up_in_two_legs(fake):
    params = service.build_ipdr_params(mapping, "919812345678", CASE)
    assert params["counterparts"] == ["919886142073", "919000000001"]
    assert params["counterparts_last10"] == ["9886142073", "9000000001"]
    assert params["sustel_last10"] == ["9886142073"]
    assert params["imeis"] == ["351000000000000"]
    # the B-party leg is not capped at the page size
    first = next(c for c in fake.calls if "f.b_party_number AS b_party_number" in c["sql"])
    assert first["params"]["limit"] == 500
    # the Sustel leg was handed the B-parties found by the first
    sustel = next(c for c in fake.calls if "AS matched_last10" in c["sql"])
    assert sustel["params"]["counterparts_last10"] == ["9886142073", "9000000001"]


def test_params_survive_a_failing_lookup(monkeypatch):
    from app.db import ClickHouseError

    class Boom(FakeClient):
        def query(self, sql, params):
            raise ClickHouseError("down")
    monkeypatch.setattr(service, "get_client", lambda source: Boom())
    params = service.build_ipdr_params(mapping, "919812345678", CASE)
    assert params["counterparts"] == [] and params["sustel_last10"] == [] and params["imeis"] == []


def test_header_is_shaped_like_the_report(fake):
    report = service.run_ipdr_report(mapping, "919812345678", CASE, only=["header"], header_extra={
        "ref": "CASE-1", "analysed_by": "Insp", "period_requested_from": "2025-05-01", "period_requested_to": "2025-05-31"})
    header = report["header"]
    assert header["ref"] == "CASE-1" and header["analysed_by"] == "Insp" and header["ref_date"] is None
    assert header["analysed_on"] == report["generated_at"]
    assert header["ipdr_period"]["requested"] == {"from": "2025-05-01", "to": "2025-05-31"}
    assert header["ipdr_period"]["actual"]["sessions"] == 202
    assert header["top_place"]["place"] == "Karachi"


def test_a_failing_block_degrades_only_itself(monkeypatch):
    from app.db import ClickHouseError

    class OneBad(FakeClient):
        def query(self, sql, params):
            if "AS usage_level" in sql:
                raise ClickHouseError("bad table")
            return super().query(sql, params)
    monkeypatch.setattr(service, "get_client", lambda source: OneBad())
    report = service.run_ipdr_report(mapping, "919812345678", CASE, only=["ai_insights", "applications_used"])
    assert report["ai_insights"]["internet_usage"] is None
    assert report["warnings"] == [{"section": "ai_insights", "block": "internet_usage", "error": "bad table"}]
    assert "applications_used" in report and "applications" in report["applications_used"]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(fake):
    return TestClient(app)


def test_sections_endpoint_lists_the_structure(client):
    body = client.get("/api/v1/ipdr/sections").json()
    assert [s["id"] for s in body["sections"] if s["panel"] == "right"] == EXPECTED_RIGHT


def test_full_report_has_every_section_and_the_header_params(client):
    body = client.get(f"/api/v1/ipdr/report/{CASE}/919812345678", params={"ref": "R-9", "analysed_by": "Insp"}).json()
    for section_id in EXPECTED_RIGHT + EXPECTED_LEFT:
        assert section_id in body, section_id
    assert body["header"]["ref"] == "R-9" and "warnings" not in body


def test_panels_split_left_and_right(client):
    left = client.get(f"/api/v1/ipdr/report/{CASE}/919812345678/left").json()
    right = client.get(f"/api/v1/ipdr/report/{CASE}/919812345678/right").json()
    assert set(EXPECTED_LEFT) <= set(left) and "ai_insights" not in left
    assert set(EXPECTED_RIGHT) <= set(right) and "subscriber" not in right


def test_one_section_and_unknown_section(client):
    one = client.get(f"/api/v1/ipdr/report/{CASE}/919812345678/section/vpn_usage").json()
    assert "vpn_usage" in one and "ai_insights" not in one
    assert client.get(f"/api/v1/ipdr/report/{CASE}/919812345678/section/nope").status_code == 404


def test_sections_filter_on_the_full_report(client):
    body = client.get(f"/api/v1/ipdr/report/{CASE}/919812345678", params=[("sections", "tor_usage")]).json()
    assert "tor_usage" in body and "vpn_usage" not in body


def test_limit_is_validated(client):
    assert client.get(f"/api/v1/ipdr/report/{CASE}/919812345678", params={"limit": 0}).status_code == 422


def test_the_cdr_report_is_untouched(client):
    assert client.get("/api/v1/sections").status_code == 200
