"""The report's subscriber photo is dim_sdr.photo1_base64 (matched on SUBSCRIBER_NUMBER), not the CAF's. No database."""
import config.report_mapping as rm


def _photo_sql():
    return rm.TelecomReportMapping.subscriber_photo()["clickhouse_query"]


def test_the_photo_query_reads_dim_sdr_on_the_last_ten_digits_of_the_subscriber_number():
    sql = " ".join(_photo_sql().split())
    assert "FROM dim_sdr" in sql and "photo1_base64" in sql
    assert "right(toString(SUBSCRIBER_NUMBER), 10) = right({msisdn:String}, 10)" in sql
    assert "ifNull(photo1_base64, '') != ''" in sql


def test_the_photo_query_no_longer_reads_the_caf():
    sql = _photo_sql()
    assert "__CAF__" not in sql and "customer_application_form" not in sql


def test_both_reports_use_the_same_photo_block():
    import inspect
    from config import ipdr_report_mapping
    assert "subscriber_photo()" in inspect.getsource(ipdr_report_mapping)
    assert "subscriber_photo()" in inspect.getsource(rm)
