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


# dim_sdr is sorted on the same normalised key over SUBSCRIBER_NUMBER (scoutfeed-sdis-v2 app/interfaces/msisdn_norm.py SDR_KEY,
# scripts/migrate_dim_sdr_sort_key.py; ipdr-analytics-v2 msisdn_norm.SDR_KEY): FIX-03, a lookup on it reads only the asked numbers.
def test_the_sdr_key_is_the_one_dim_sdr_is_sorted_on():
    from config.report_mapping import _SDR_KEY
    assert hashlib.sha256(_SDR_KEY.encode()).hexdigest()[:16] == "f0e2b0a5122e6613"


def test_every_sdr_lookup_filters_on_the_key():
    import re

    from config.ipdr_report_mapping import IPDRReportMapping
    from config.report_mapping import _SDR_KEY
    sqls = [v["clickhouse_query"] for cls in (M, IPDRReportMapping) for name, fn in vars(cls).items()
            if isinstance(fn, staticmethod) and not name.startswith("_")
            for v in [_call(fn)] if isinstance(v, dict) and "clickhouse_query" in v]
    sqls.append(M._b_party_locations_sql("DESC"))                      # the B-party location blocks' shared body
    reads = [(sql, m.end()) for sql in sqls for m in re.finditer(r"FROM dim_sdr\b", sql)]
    assert reads
    for sql, at in reads:
        assert _SDR_KEY in sql[at:at + 4000].split("GROUP BY")[0].split("LIMIT")[0], sql[at:at + 200]


def _call(fn):
    try:
        return fn.__func__()
    except TypeError:
        return None
