"""Repeated rows in the name / alias, alternate number, IMEI dossier, Sustel and Profiler lists are shown once.

    PYTHONPATH=. python -m pytest tests/test_dedupe.py -q
"""
from pathlib import Path

import pytest

from app import service
from app.mapping import load_mapping

CONFIG = Path(__file__).resolve().parent.parent / "config"
CDR = load_mapping(CONFIG / "report_mapping.py")
IPDR = load_mapping(CONFIG / "ipdr_report_mapping.py")
CASE = "11111111-2222-3333-4444-555555555555"


class Rows:
    """A fake ClickHouse that answers the blocks under test by a marker in their SQL."""

    NAMES = [{"source": "Profiler", "value": "Ashraf Ali"}, {"source": "Phonebook", "value": "Ashraf Ali"},
             {"source": "Truecaller", "value": "ashraf  ali "}, {"source": "SDR", "value": "Ashraf Ali"},
             {"source": "SDR", "value": "Ashraf Ali"}, {"source": "Sustel", "value": "h.singh"}]
    NUMBERS = [{"source": "SDR", "number": "919812000011"}, {"source": "SDR", "number": "919812000011"},
               {"source": "Sustel", "number": "919812000011"}, {"source": "Sustel", "number": "919812000022"}]
    HITS = [{"imei": "1", "case_reference": "A"}, {"imei": "1", "case_reference": "A"}, {"imei": "1", "case_reference": "B"}]

    def query(self, sql, params):
        if "'Profiler'" in sql and "'Phonebook'" in sql:
            return [dict(r) for r in self.NAMES]
        if "AS number" in sql and "'SDR'" in sql:
            return [dict(r) for r in self.NUMBERS]
        if "FROM dim_imei_legacy" in sql:
            return [dict(r) for r in self.HITS]
        return []

    def ping(self):
        return True


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    monkeypatch.setattr(service, "get_client", lambda source: Rows())
    monkeypatch.setattr(service, "fetch_case_header", lambda case_id, msisdn: {})


def test_a_name_repeated_within_one_source_is_shown_once_and_each_source_keeps_its_own_row():
    for run, mapping in ((service.run_report, CDR), (service.run_ipdr_report, IPDR)):
        names = run(mapping, "919812345678", CASE, only=["name_alias"])["name_alias"]["names"]
        assert names == [{"source": "Profiler", "value": "Ashraf Ali"}, {"source": "Phonebook", "value": "Ashraf Ali"},
                         {"source": "Truecaller", "value": "ashraf  ali "}, {"source": "SDR", "value": "Ashraf Ali"},
                         {"source": "Sustel", "value": "h.singh"}]


def test_an_alternate_number_repeated_within_one_source_is_shown_once():
    for run, mapping in ((service.run_report, CDR), (service.run_ipdr_report, IPDR)):
        numbers = run(mapping, "919812345678", CASE, only=["alternate_numbers"])["alternate_numbers"]["numbers"]
        assert numbers == [{"source": "SDR", "number": "919812000011"}, {"source": "Sustel", "number": "919812000011"},
                           {"source": "Sustel", "number": "919812000022"}]


def test_two_different_values_from_one_source_are_both_kept():
    rows = [{"source": "SDR", "value": "Ashraf Ali"}, {"source": "SDR", "value": "A. Ali"}, {"source": "SDR", "value": "ashraf ali"}]
    assert service._dedupe("name_alias", "names", rows) == rows[:2]


def test_a_dossier_row_is_dropped_only_when_every_column_is_the_same():
    hits = service.run_report(CDR, "919812345678", CASE, only=["imei"])["imei"]["imei_dim_hits"]
    assert hits == [{"imei": "1", "case_reference": "A"}, {"imei": "1", "case_reference": "B"}]


def test_other_blocks_keep_their_repeated_rows():
    rows = [{"a": 1}, {"a": 1}]
    assert service._dedupe("connectivity", "voip_calls", rows) == rows
    assert service._dedupe("name_alias", "names", []) == []


def test_a_name_with_no_value_is_not_merged_into_another():
    rows = [{"source": "SDR", "value": None, "x": 1}, {"source": "SDR", "value": None, "x": 2}]
    assert service._dedupe("name_alias", "names", rows) == rows
