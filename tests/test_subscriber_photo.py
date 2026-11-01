"""The report's subscriber photo is dim_sdr.photo1_base64 (matched on SUBSCRIBER_NUMBER), not the CAF's. No database."""
import config.report_mapping as rm


def _photo_sql():
    return rm.TelecomReportMapping.subscriber_photo()["clickhouse_query"]


def test_the_photo_query_reads_dim_sdr_on_its_sort_key():
    """dim_sdr's country-aware number key (FIX-03): 9812345678 and 919812345678 meet, and the read is an index read, not a pass over
    every SDR row as `right(toString(SUBSCRIBER_NUMBER), 10) = ...` was."""
    from config.report_mapping import _MSISDN_PROBE, _SDR_KEY
    sql = _photo_sql()
    assert "FROM dim_sdr" in sql and "photo1_base64" in sql
    assert _SDR_KEY + " = " + _MSISDN_PROBE in sql
    assert "ifNull(photo1_base64, '') != ''" in sql


def test_the_photo_query_no_longer_reads_the_caf():
    sql = _photo_sql()
    assert "__CAF__" not in sql and "customer_application_form" not in sql


def test_both_reports_use_the_same_photo_block():
    import inspect
    from config import ipdr_report_mapping
    assert "subscriber_photo()" in inspect.getsource(ipdr_report_mapping)
    assert "subscriber_photo()" in inspect.getsource(rm)
