"""
Cell towers: analytics.dim_cell_tower, with every column converted to one fixed type.

The mapping files name the tower table by the token __CELL_TOWER__; app/mapping.py's resolve_tokens puts in its place a subquery over
analytics.dim_cell_tower with the same columns (cell_id, address, city, state, country, postal_code, latitude, longitude, in_service_date,
azimuth, created_at, updated_at, mcc, mnc, lac, cell_code), so a query keeps its column references. A copy of ipdr-analytics-v2's
services/cell_tower_source.py: keep the two the same (the scoped __CELL_TOWER_OF__ form is this repo's).

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
# __CELL_TOWER_OF__(<a query returning cell ids>): the same table, read only for those cells (a case uses a few thousand of the ~500k).
# `cell_id` is dim_cell_tower's ORDER BY key, so the filter reads only the matching granules even with FINAL.
SCOPED_TOKEN = "__CELL_TOWER_OF__("

_CASTS = """toString(cell_id) AS cell_id, CAST(toString(address) AS Nullable(String)) AS address,
               CAST(toString(city) AS Nullable(String)) AS city, CAST(toString(state) AS Nullable(String)) AS state,
               ifNull(toString(country), '') AS country, toUInt32OrNull(toString(postal_code)) AS postal_code,
               toFloat64OrNull(toString(latitude)) AS latitude, toFloat64OrNull(toString(longitude)) AS longitude,
               toDateOrNull(toString(in_service_date)) AS in_service_date, toUInt16OrNull(toString(azimuth)) AS azimuth,
               CAST({created} AS DateTime64(6)) AS created_at, CAST({updated} AS DateTime64(6)) AS updated_at,
               toUInt16OrZero(toString(mcc)) AS mcc, toUInt16OrZero(toString(mnc)) AS mnc,
               toUInt32OrZero(toString(lac)) AS lac, toUInt32OrZero(toString(cell_code)) AS cell_code"""

_SQL = "(SELECT " + _CASTS.format(created="created_at", updated="updated_at") + """
        FROM @ANALYTICS@."dim_cell_tower" FINAL)"""

# The columns whose latest value the scoped form takes per cell.
_COLUMNS = ("address", "city", "state", "country", "postal_code", "latitude", "longitude", "in_service_date", "azimuth", "mcc", "mnc", "lac", "cell_code")

assert "master" not in _SQL and "{" not in _SQL and "__" not in _SQL


def _ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", name or ""):
        raise ValueError(f"{name!r} is not a valid database name (letters, digits, _ and -)")
    return '"' + name + '"'


def subquery(analytics_db: Optional[str] = None, cells: Optional[str] = None) -> str:
    """The subquery that replaces the token; the database is CH_FACTS_DATABASE (analytics).

    `cells` (the __CELL_TOWER_OF__ form): a query returning cell ids; only those cells are read, each as its LATEST row (the row with the
    highest updated_at, then created_at), taken with argMax instead of FINAL. On a table of 1-2 crore towers, FINAL merges the whole
    table's rows first (about 0.5 GiB and 200 ms for a case's 3,000 cells); argMax over the filtered rows is about 30 MiB and 45 ms and
    gives the same row per cell. tuple(): argMax skips a NULL argument, which would take an older row's value; the tuple is never NULL.
    The inner query names its time columns latest_*: an alias called updated_at would shadow the column argMax reads."""
    from app.config import settings
    db = _ident(analytics_db or settings.ch_facts_database)
    if not cells:
        return _SQL.replace("@ANALYTICS@", db)
    picked = ",\n               ".join(f"argMax(tuple({c}), (updated_at, created_at)).1 AS {c}" for c in _COLUMNS)
    latest = f"""(
            SELECT cell_id,
               {picked},
               max(created_at) AS latest_created_at, max(updated_at) AS latest_updated_at
            FROM {db}."dim_cell_tower"
            WHERE cell_id IN ({cells})
            GROUP BY cell_id
        )"""
    return "(SELECT " + _CASTS.format(created="latest_created_at", updated="latest_updated_at") + "\n        FROM " + latest + ")"


def _matching_paren(text: str, start: int) -> int:
    """Index of the ')' closing the '(' at text[start - 1] (start is just after it)."""
    depth = 1
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("__CELL_TOWER_OF__( is not closed")


def expand(query: str) -> str:
    """`query` with __CELL_TOWER_OF__(...) and __CELL_TOWER__ replaced; unchanged when it has neither."""
    while SCOPED_TOKEN in query:
        start = query.index(SCOPED_TOKEN) + len(SCOPED_TOKEN)
        end = _matching_paren(query, start)
        query = query[:start - len(SCOPED_TOKEN)] + subquery(cells=query[start:end].strip()) + query[end + 1:]
    return query.replace(TOKEN, subquery()) if TOKEN in query else query
