"""
Cell towers: analytics.dim_cell_tower, with every column converted to one fixed type.

The mapping files name the tower table by the token __CELL_TOWER__; app/mapping.py's resolve_tokens puts in its place a subquery over
analytics.dim_cell_tower with the same columns (cell_id, address, city, state, country, postal_code, latitude, longitude, in_service_date,
azimuth, created_at, updated_at, mcc, mnc, lac, cell_code), so a query keeps its column references. A copy of ipdr-analytics-v2's
services/cell_tower_source.py: keep the two the same.

Only analytics.dim_cell_tower is read. master.dim_cell_tower is NOT used, not even as a fallback for a cell analytics lacks.

Every column is converted BEFORE it is compared, joined or combined (toString() first, then toUInt16OrZero / toFloat64OrNull /
toDateOrNull ...), so no table has to be altered. Whatever a deployment made of `dim_cell_tower` (cell_id String or LowCardinality(String)
or Nullable; city / state / country String, LowCardinality or Nullable; latitude / longitude Float32, Float64, Nullable or String;
mcc / mnc / lac / cell_code UInt, Nullable or String; postal_code / in_service_date / azimuth String after an old manual ALTER ... DEFAULT
'India'), the output types are always:
    cell_id String, address / city / state Nullable(String), country String (never NULL), postal_code Nullable(UInt32),
    latitude / longitude Nullable(Float64), in_service_date Nullable(Date), azimuth Nullable(UInt16), created_at / updated_at DateTime64(6),
    mcc / mnc UInt16 and lac / cell_code UInt32 (0 = unknown).
A value that is not a valid number or date (such as 'India') becomes NULL (0 for the mcc / mnc / lac / cell_code group).

The database is CH_FACTS_DATABASE (analytics), on the `facts` connection. FINAL: dim_cell_tower is a ReplacingMergeTree. The SQL has no curly
braces and no double underscore.
"""
import re
from typing import Optional

TOKEN = "__CELL_TOWER__"

_SQL = """(SELECT toString(cell_id) AS cell_id, CAST(toString(address) AS Nullable(String)) AS address,
               CAST(toString(city) AS Nullable(String)) AS city, CAST(toString(state) AS Nullable(String)) AS state,
               ifNull(toString(country), '') AS country, toUInt32OrNull(toString(postal_code)) AS postal_code,
               toFloat64OrNull(toString(latitude)) AS latitude, toFloat64OrNull(toString(longitude)) AS longitude,
               toDateOrNull(toString(in_service_date)) AS in_service_date, toUInt16OrNull(toString(azimuth)) AS azimuth,
               CAST(created_at AS DateTime64(6)) AS created_at, CAST(updated_at AS DateTime64(6)) AS updated_at,
               toUInt16OrZero(toString(mcc)) AS mcc, toUInt16OrZero(toString(mnc)) AS mnc,
               toUInt32OrZero(toString(lac)) AS lac, toUInt32OrZero(toString(cell_code)) AS cell_code
        FROM @ANALYTICS@."dim_cell_tower" FINAL)"""

assert "master" not in _SQL and "{" not in _SQL and "__" not in _SQL


def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", name or ""):
        raise ValueError(f"{name!r} is not a plain database name")
    return '"' + name + '"'


def subquery(analytics_db: Optional[str] = None) -> str:
    """The subquery that replaces the token; the database is CH_FACTS_DATABASE (analytics)."""
    from app.config import settings
    return _SQL.replace("@ANALYTICS@", _ident(analytics_db or settings.ch_facts_database))


def expand(query: str) -> str:
    """`query` with __CELL_TOWER__ replaced; unchanged when it has no token."""
    return query.replace(TOKEN, subquery()) if TOKEN in query else query
