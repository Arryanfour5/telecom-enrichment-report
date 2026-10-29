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


def test_scoped_token_reads_only_the_cells_asked_for():
    from app import cell_tower_source as c
    out = c.expand("SELECT 1 FROM h LEFT JOIN __CELL_TOWER_OF__(SELECT cell_id FROM cell_hits WHERE x IN (1, 2)) AS t ON t.cell_id = h.cell_id")
    assert "__CELL_TOWER" not in out
    # the filter is on the RAW table, inside; no FINAL: the latest row of each wanted cell is taken with argMax (about 30 MiB instead of 0.5 GiB
    # on a 2-crore table); the time columns of the inner query are latest_* so the alias cannot shadow the column argMax reads
    assert 'FROM "analytics"."dim_cell_tower"\n            WHERE cell_id IN (SELECT cell_id FROM cell_hits WHERE x IN (1, 2))\n            GROUP BY cell_id' in out
    assert "FINAL" not in out
    assert "argMax(tuple(city), (updated_at, created_at)).1 AS city" in out
    assert "CAST(latest_updated_at AS DateTime64(6)) AS updated_at" in out and "CAST(latest_created_at AS DateTime64(6)) AS created_at" in out
    assert "toUInt32OrNull(toString(postal_code))" in out        # the fixed-type casts are still there


def test_plain_and_scoped_tokens_can_share_a_query():
    from app import cell_tower_source as c
    out = c.expand("SELECT * FROM __CELL_TOWER__ AS a JOIN __CELL_TOWER_OF__(SELECT 1) AS b ON 1")
    assert out.count("dim_cell_tower") == 2 and out.count("WHERE cell_id IN (SELECT 1)") == 1
