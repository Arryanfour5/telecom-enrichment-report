"""__CELL_TOWER__ (app/cell_tower_source.py, a copy of ipdr-analytics-v2's): analytics.dim_cell_tower with every column converted to one
type; master.dim_cell_tower is not read. No database."""
from app import cell_tower_source as cts


def test_the_subquery_is_safe_inside_format_templates():
    q = cts.subquery()
    assert "{" not in q and "__" not in q and "%" not in q


def test_it_reads_analytics_dim_cell_tower_and_nothing_from_master():
    q = cts.subquery()
    assert 'FROM "analytics"."dim_cell_tower" FINAL)' in q
    assert "master" not in q.lower() and "UNION" not in q and "JOIN" not in q


def test_every_column_is_converted_to_one_type_whatever_the_deployments_table_looks_like():
    """postal_code / in_service_date / azimuth as String (an old ALTER ... DEFAULT 'India'), text mcc / mnc / lac, or plain String
    city / country / cell_id must give the same columns and types as the standard table."""
    q = cts.subquery()
    for fragment in ("toString(cell_id) AS cell_id",
                     "CAST(toString(city) AS Nullable(String)) AS city",
                     "ifNull(toString(country), '') AS country",
                     "toUInt32OrNull(toString(postal_code)) AS postal_code",
                     "toDateOrNull(toString(in_service_date)) AS in_service_date",
                     "toUInt16OrNull(toString(azimuth)) AS azimuth",
                     "CAST(created_at AS DateTime64(6)) AS created_at",
                     "toFloat64OrNull(toString(latitude)) AS latitude",
                     "toUInt16OrZero(toString(mcc)) AS mcc"):
        assert fragment in q, fragment
    assert "LowCardinality" not in q


def test_expand_replaces_only_the_token():
    assert cts.expand("SELECT 1") == "SELECT 1"
    out = cts.expand("SELECT ct.city FROM __CELL_TOWER__ ct")
    assert cts.TOKEN not in out and out.startswith("SELECT ct.city FROM (SELECT ") and out.endswith(") ct")
