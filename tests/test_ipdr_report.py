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
    "msisdn", "case_id", "limit", "events_limit", "locations_limit", "day_start", "day_end", "home_country_name", "usage_low", "usage_high",
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
        # roaming_numbers is case-wide on purpose: one row per number of the case (this number is flagged `this_number`)
        if block.source == "facts" and "fact_internet_sessions" in block.sql and block.key not in ("other_cases_cdr", "roaming_numbers"):
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
        if "AS subscriber_name" in sql:                                             # the SDR look-up of the B-party numbers
            return [{"b_party_number": "9886142073", "subscriber_name": "Karthik Menon"}]
        if "arrayJoin([right(toString(`telno`)" in sql:                             # the Sustel look-up of the B-party numbers
            return [{"b_party_number": "9886142073", "case_name": "OP KESTREL", "monitored": "Y"}]
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
    monkeypatch.setattr(service, "fetch_case_header", lambda case_id, msisdn: {})        # no Postgres in these tests
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


# ────────────────────────────────────────────────────────
# B Party Resolution: the SDR and Sustel match of each resolved number
# ────────────────────────────────────────────────────────

def _numbers(report):
    return {r["b_party_number"]: r for r in report["b_party_resolution"]["numbers"]}


def test_b_party_resolution_looks_the_numbers_up_in_sdr_and_sustel_on_the_dims_connection():
    section = mapping.section("b_party_resolution")
    assert [(b.key, b.source) for b in section.blocks] == [("numbers", "facts"), ("sdr", "dims"), ("sustel", "dims")]
    sdr, sustel = (next(b for b in section.blocks if b.key == k).sql for k in ("sdr", "sustel"))
    assert "FROM dim_sdr" in sdr and "`SUBSCRIBER_NAME`" in sdr and "{counterparts_last10:Array(String)}" in sdr
    assert "FROM scout_reference.sustel AS r FINAL" in sustel and "`telno0`" in sustel and "{counterparts_last10:Array(String)}" in sustel   # either of the record's numbers


def test_a_b_party_number_in_sdr_and_on_sustel_gets_its_name_and_the_sustel_match_on_its_own_row(fake):
    report = service.run_ipdr_report(mapping, "919812345678", CASE, only=["b_party_resolution"])
    row = _numbers(report)["919886142073"]
    assert row["b_party_name"] == "Karthik Menon"
    assert row["sustel_match"] is True and row["b_party_sustel_match"] is True
    assert row["sustel"] == [{"case_name": "OP KESTREL", "monitored": "Y"}]
    assert not {"statement", "sdr", "sdr_match"} & set(row)                        # only the name and the Sustel match are added
    assert set(report["b_party_resolution"]) == {"numbers"}                          # the two look-up blocks are merged and dropped


def test_a_number_with_no_sdr_and_no_sustel_record_has_no_name_and_no_match(fake):
    row = _numbers(service.run_ipdr_report(mapping, "919812345678", CASE, only=["b_party_resolution"]))["919000000001"]
    assert row["b_party_name"] is None and row["sustel_match"] is False and row["b_party_sustel_match"] is False and row["sustel"] is None


def test_sustel_records_in_several_cases_are_all_listed(monkeypatch):
    class TwoCases(FakeClient):
        def query(self, sql, params):
            if "arrayJoin([right(toString(`telno`)" in sql:
                return [{"b_party_number": "9886142073", "case_name": "OP A", "monitored": "Y"},
                        {"b_party_number": "9886142073", "case_name": "OP B", "monitored": "N"}]
            return super().query(sql, params)
    monkeypatch.setattr(service, "get_client", lambda source: TwoCases())
    row = _numbers(service.run_ipdr_report(mapping, "919812345678", CASE, only=["b_party_resolution"]))["919886142073"]
    assert row["sustel"] == [{"case_name": "OP A", "monitored": "Y"}, {"case_name": "OP B", "monitored": "N"}]


def test_a_failing_sdr_look_up_leaves_the_name_empty_and_a_failing_sustel_look_up_leaves_its_match_unknown(monkeypatch):
    from app.db import ClickHouseError

    class Broken(FakeClient):
        def query(self, sql, params):
            if "AS subscriber_name" in sql:
                raise ClickHouseError("dim_sdr missing")
            if "arrayJoin([right(toString(`telno`)" in sql:
                raise ClickHouseError("scout_reference.sustel missing")
            return super().query(sql, params)
    monkeypatch.setattr(service, "get_client", lambda source: Broken())
    report = service.run_ipdr_report(mapping, "919812345678", CASE, only=["b_party_resolution"])
    row = _numbers(report)["919886142073"]
    assert row["b_party_name"] is None
    assert row["sustel_match"] is None and row["b_party_sustel_match"] is None and row["sustel"] is None     # unknown, not false
    assert [w["block"] for w in report["warnings"]] == ["sdr", "sustel"]


def test_the_one_section_endpoint_returns_the_merged_rows(client):
    body = client.get(f"/api/v1/ipdr/report/{CASE}/919812345678/section/b_party_resolution").json()
    row = next(r for r in body["b_party_resolution"]["numbers"] if r["b_party_number"] == "919886142073")
    assert row["b_party_name"] == "Karthik Menon" and row["b_party_sustel_match"] is True


# ────────────────────────────────────────────────────────
# Report header from Postgres (cases + telecom_metadata), for the CDR and the IPDR report
# ────────────────────────────────────────────────────────

CASE_HEADER = {
    "ref": "FIR 112/11 Case Satwari", "ref_date": "2026-10-14T05:54:00Z", "input_gist": "IPDR demo", "analysed_by": "Insp R.Sharma",
    "concerned_officer": "Insp R.Sharma", "concerned_unit": "Counter Terrorism Unit",
    "periods": {
        "cdr": {"requested": {"from": "2026-06-01 00:00:00", "to": "2026-06-30 23:59:59"},
                "actual": {"from": "2026-06-02 08:00:00", "to": "2026-06-29 20:00:00"}},
        "ipdr": {"requested": {"from": "2026-07-01 00:00:00", "to": "2026-09-21 23:59:59"},
                 "actual": {"from": "2026-07-01 21:29:00", "to": "2026-09-21 13:58:21"}},
    },
}


@pytest.fixture
def with_case(fake, monkeypatch):
    monkeypatch.setattr(service, "fetch_case_header", lambda case_id, msisdn: CASE_HEADER)


def test_the_ipdr_header_takes_the_case_fields_and_the_period_from_postgres(with_case):
    header = service.run_ipdr_report(mapping, "919812345678", CASE, only=["header"])["header"]
    assert (header["ref"], header["ref_date"], header["input_gist"], header["analysed_by"]) == (
        "FIR 112/11 Case Satwari", "2026-10-14T05:54:00Z", "IPDR demo", "Insp R.Sharma")
    assert header["ipdr_period"]["requested"] == {"from": "2026-07-01 00:00:00", "to": "2026-09-21 23:59:59"}
    actual = header["ipdr_period"]["actual"]
    assert actual["actual_from"] == "2026-07-01 21:29:00" and actual["actual_to"] == "2026-09-21 13:58:21"
    assert actual["sessions"] == 202                                                      # the ClickHouse counts are kept
    assert header["top_place"]["place"] == "Karachi"


def test_a_query_parameter_wins_over_postgres_in_the_ipdr_header(with_case):
    header = service.run_ipdr_report(mapping, "919812345678", CASE, only=["header"], header_extra={"ref": "OVERRIDE"})["header"]
    assert header["ref"] == "OVERRIDE" and header["analysed_by"] == "Insp R.Sharma"


def test_the_cdr_report_has_a_header_with_the_cdr_period(with_case):
    report = service.run_report(load_mapping(MAPPING_FILE.with_name("report_mapping.py")), "919812345678", CASE)
    assert list(report)[:4] == ["msisdn", "case_id", "generated_at", "header"]
    header = report["header"]
    assert header["ref"] == "FIR 112/11 Case Satwari" and header["analysed_on"] == report["generated_at"]
    assert header["cdr_period"] == {"requested": {"from": "2026-06-01 00:00:00", "to": "2026-06-30 23:59:59"},
                                    "actual": {"actual_from": "2026-06-02 08:00:00", "actual_to": "2026-06-29 20:00:00"}}


def test_a_postgres_failure_leaves_the_header_empty_with_a_warning(fake, monkeypatch):
    from app.pg import PostgresError

    def down(case_id, msisdn):
        raise PostgresError("connection refused")
    monkeypatch.setattr(service, "fetch_case_header", down)
    report = service.run_ipdr_report(mapping, "919812345678", CASE, only=["header"])
    assert report["header"]["ref"] is None and report["header"]["ipdr_period"]["requested"] == {"from": None, "to": None}
    assert report["warnings"] == [{"section": "header", "block": "postgres", "error": "connection refused"}]


def test_the_full_report_endpoint_returns_the_cdr_and_the_ipdr_report(client, with_case):
    body = client.get(f"/api/v1/full-report/{CASE}/919812345678").json()
    assert set(body) == {"msisdn", "case_id", "generated_at", "cdr", "ipdr"}
    assert body["cdr"]["header"]["ref"] == body["ipdr"]["header"]["ref"] == "FIR 112/11 Case Satwari"
    assert "cdr_period" in body["cdr"]["header"] and "ipdr_period" in body["ipdr"]["header"]
    assert "ai_insights" in body["cdr"] and "ai_insights" in body["ipdr"]


# ────────────────────────────────────────────────────────
# Locations: day / night are city-wise and capped at the top 5
# ────────────────────────────────────────────────────────

def test_day_and_night_locations_are_city_wise_with_the_same_columns():
    for key in ("day", "night"):
        sql = next(b for b in mapping.section("locations").blocks if b.key == key).sql
        assert "GROUP BY c.place, c.state, c.country" in sql and "LIMIT {locations_limit:UInt32}" in sql
        for column in ("cell_id", "city", "state", "country", "latitude", "longitude", "sessions", "first_seen", "last_seen"):
            assert f"AS {column}" in sql, column


def test_the_locations_limit_is_five_unless_the_caller_asks_for_fewer(fake):
    assert mapping.settings["locations_top_n"] == 5
    assert service.build_ipdr_params(mapping, "919812345678", CASE)["locations_limit"] == 5
    assert service.build_ipdr_params(mapping, "919812345678", CASE, limit=3)["locations_limit"] == 3
    assert service.build_ipdr_params(mapping, "919812345678", CASE, limit=50)["locations_limit"] == 5


def test_the_cdr_day_and_night_locations_are_city_wise_top_five():
    cdr = load_mapping(MAPPING_FILE.with_name("report_mapping.py"))
    for key in ("day", "night"):
        sql = next(b for b in cdr.section("locations").blocks if b.key == key).sql
        assert "GROUP BY c.place, c.state, c.country" in sql and "{locations_limit:UInt32}" in sql
    assert service.build_params(cdr, "919812345678", CASE, None)["locations_limit"] == 5


def test_the_header_carries_the_concerned_officer_and_unit_in_both_reports(with_case):
    ipdr_header = service.run_ipdr_report(mapping, "919812345678", CASE, only=["header"])["header"]
    cdr_header = service.run_report(load_mapping(MAPPING_FILE.with_name("report_mapping.py")), "919812345678", CASE)["header"]
    for header in (ipdr_header, cdr_header):
        assert header["concerned_officer"] == "Insp R.Sharma" and header["concerned_unit"] == "Counter Terrorism Unit"


def test_the_concerned_officer_and_unit_are_null_when_postgres_has_none(fake):
    header = service.run_ipdr_report(mapping, "919812345678", CASE, only=["header"])["header"]
    assert header["concerned_officer"] is None and header["concerned_unit"] is None


# ────────────────────────────────────────────────────────
# States visited, and the distinct numbers of the case that roamed (CDR and IPDR); from the serving cell id only
# ────────────────────────────────────────────────────────

def test_states_visited_and_roaming_numbers_are_in_ai_insights_of_both_reports():
    cdr = load_mapping(MAPPING_FILE.with_name("report_mapping.py"))
    for m in (mapping, cdr):
        blocks = {b.key: b for b in m.section("ai_insights").blocks}
        assert "states_visited" in blocks and "roaming_numbers" in blocks
        assert "GROUP BY place, place_type, country" in blocks["states_visited"].sql
        numbers = blocks["roaming_numbers"].sql
        assert "GROUP BY right(" in numbers and "{case_id:UUID}" in numbers and "this_number" in numbers   # one row per number, whichever file it is in
        assert "{msisdn:String}" in numbers and "places_count" in numbers and "file_id" in numbers


def test_roaming_is_read_from_the_cell_id_not_from_the_operators_circle_columns():
    cdr = load_mapping(MAPPING_FILE.with_name("report_mapping.py"))
    for m in (mapping, cdr):
        for key in ("states_visited", "roaming_numbers"):
            sql = next(b for b in m.section("ai_insights").blocks if b.key == key).sql
            assert "dim_cell_tower" in sql and "cell_id" in sql                                   # the token is resolved when the mapping loads
            assert "roaming_circle" not in sql and "home_circle" not in sql and "roaming_network_indicator" not in sql


def test_the_states_of_a_number_are_counted_across_its_files_not_per_file():
    cdr = load_mapping(MAPPING_FILE.with_name("report_mapping.py"))
    for m in (mapping, cdr):
        sql = next(b for b in m.section("ai_insights").blocks if b.key == "states_visited").sql
        assert "GROUP BY place, place_type, country" in sql and "GROUP BY file_id" not in sql
        assert "uniqExact(f.file_id)" in sql or "countDistinct(h.file_id)" in sql              # how many files, not one row per file


def test_a_tower_with_no_state_falls_back_to_its_city_then_its_country_and_the_most_distinct_towers_come_first():
    cdr = load_mapping(MAPPING_FILE.with_name("report_mapping.py"))
    for m, towers in ((mapping, "ORDER BY cells DESC"), (cdr, "ORDER BY distinct_towers DESC")):
        sql = next(b for b in m.section("ai_insights").blocks if b.key == "states_visited").sql
        assert "AS place_type" in sql and "'state'" in sql and "'city'" in sql and "'country'" in sql
        assert "t.state != ''" in sql or "ifNull(t.state, '') != ''" in sql
        assert towers in sql                                                       # not only rows that have a state
        assert "(t.state != '' OR t.city != '' OR t.country != '')" in sql or "ifNull(t.country, '') != ''" in sql


# ────────────────────────────────────────────────────────
# Cross reference: the other cases of the number from case_cross_reference, as the MSISDN Intelligence Panel lists them
# ────────────────────────────────────────────────────────

OTHER_A, OTHER_B, OTHER_C = ("aaaaaaaa-0000-0000-0000-00000000000a", "bbbbbbbb-0000-0000-0000-00000000000b",
                             "cccccccc-0000-0000-0000-00000000000c")


def _permission(case_id, action, file_id="f"):
    import json
    return json.dumps({"action": action, "case_id": case_id, "file_id": file_id})


class RefRows(FakeClient):
    """case_cross_reference: A has a request row and a view row (so it is view), B only a request, C a row that is not JSON."""

    def query(self, sql, params):
        if "FROM case_cross_reference" in sql:
            return [{"other_case": OTHER_B, "access_permission": _permission(OTHER_B, "request")},
                    {"other_case": OTHER_A, "access_permission": _permission(OTHER_A, "request", "f1")},
                    {"other_case": OTHER_A, "access_permission": _permission(OTHER_A, "view", "f2")},
                    {"other_case": OTHER_C, "access_permission": "not json"}]
        return super().query(sql, params)


def _details(**by_case):
    return {cid: {"name": "n", "case_no": "D", "concerned_unit": "U", "owner": "O", "date_time": "19-10-2026 14:05", **d}
            for cid, d in by_case.items()}


def _cross(monkeypatch, details, run="ipdr", mapping_=None):
    monkeypatch.setattr(service, "get_client", lambda source: RefRows())
    monkeypatch.setattr(service, "fetch_case_header", lambda case_id, msisdn: {})
    asked = []

    def fake_cases(ids):
        asked.append(list(ids))
        if isinstance(details, Exception):
            raise details
        return details
    monkeypatch.setattr(service, "fetch_cases", fake_cases)
    if run == "ipdr":
        return service.run_ipdr_report(mapping, "919812345678", CASE, only=["cross_reference"]), asked
    cdr = load_mapping(MAPPING_FILE.with_name("report_mapping.py"))
    return service.run_report(cdr, "919812345678", CASE, only=["cross_reference"]), asked


def test_the_cases_come_from_case_cross_reference_by_the_last_ten_digits_without_the_current_case():
    for m in (mapping, load_mapping(MAPPING_FILE.with_name("report_mapping.py"))):
        sql = next(b for b in m.section("cross_reference").blocks if b.key == "case_references").sql
        assert "FROM case_cross_reference" in sql and "right(toString(msisdn), 10) = right({msisdn:String}, 10)" in sql
        assert "case_id != {case_id:UUID}" in sql and "AS other_case" in sql      # not `AS case_id`: the alias would shadow the column in the WHERE


def test_each_case_is_listed_once_with_its_postgres_details_and_viewable_cases_come_first(monkeypatch):
    report, asked = _cross(monkeypatch, _details(**{
        OTHER_A: {"name": "Zulu", "case_no": "D001", "concerned_unit": "Unit A", "owner": "Officer A"},
        OTHER_B: {"name": "Alpha", "case_no": "D002", "concerned_unit": "Unit B", "owner": "Officer B", "date_time": "01-01-2026 09:30"}}))
    section = report["cross_reference"]
    assert asked == [[OTHER_B, OTHER_A]]                                    # C's row is not JSON: skipped
    assert [c["case_id"] for c in section["cases"]] == [OTHER_A, OTHER_B]   # view (A, though named Zulu) before request (B)
    assert section["cases"][0] == {"case_no": "D001", "concerned_unit": "Unit A", "owner": "Officer A", "date_time": "19-10-2026 14:05",
                                   "case_id": OTHER_A, "action": "view",
                                   "display": "Case No: D001 | Concerned Unit: Unit A | Owner: Officer A | Date and Time: 19-10-2026 14:05"}
    assert section["cases"][1]["action"] == "request"
    assert "case_references" not in section and "data_sources" not in section
    assert "other_cases" in section and "other_cases_cdr" in section        # the existing blocks are unchanged


def test_a_case_postgres_has_no_name_for_is_left_out(monkeypatch):
    report, _ = _cross(monkeypatch, _details(**{OTHER_A: {"name": "Alpha"}, OTHER_B: {"name": None}}))
    assert [c["case_id"] for c in report["cross_reference"]["cases"]] == [OTHER_A]
    report, _ = _cross(monkeypatch, {})                                     # Postgres read, but it has none of the cases
    assert report["cross_reference"]["cases"] == []


def test_when_postgres_cannot_be_read_every_case_is_listed_with_empty_details(monkeypatch):
    from app.pg import PostgresError
    report, _ = _cross(monkeypatch, PostgresError("connection refused"))
    cases = report["cross_reference"]["cases"]
    assert [(c["case_id"], c["action"], c["owner"]) for c in cases] == [(OTHER_A, "view", None), (OTHER_B, "request", None)]
    assert {"section": "cross_reference", "block": "postgres", "error": "connection refused"} in report["warnings"]


def test_the_cdr_cross_reference_has_the_same_cases_list(monkeypatch):
    report, _ = _cross(monkeypatch, _details(**{OTHER_A: {"case_no": "D001"}, OTHER_B: {"case_no": "D002"}}), run="cdr")
    assert [(c["case_no"], c["action"]) for c in report["cross_reference"]["cases"]] == [("D001", "view"), ("D002", "request")]
