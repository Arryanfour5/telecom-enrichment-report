"""dim_truecaller is sorted on the normalised number key (scoutfeed-sdis-v2 scripts/migrate_dim_truecaller_sort_key.py): the report must
write the identical expression, or its lookups read the whole table."""
import hashlib

from config.report_mapping import TelecomReportMapping as M, _MSISDN_PROBE, _TRUECALLER_KEY


def test_the_key_is_the_one_the_table_is_sorted_on():
    assert hashlib.sha256(_TRUECALLER_KEY.encode()).hexdigest()[:16] == "7c2969352ccea89b"


def test_every_truecaller_lookup_filters_on_the_key():
    for sql in (M.subscriber_details(), M.name_alias_sources(), M.social_truecaller_leak()):
        q = sql["clickhouse_query"]
        assert _TRUECALLER_KEY + " = " + _MSISDN_PROBE in q
