"""Postgres access for the report header: the case row and the period of the number's CDR / IPDR files.

Ref, Ref date, Input gist and Analysed by are on `cases`; the requested and actual period of a file are on
`telecom_metadata`, and `files.subdomain` says whether that file is a CDR or an IPDR.
"""

import datetime as dt
from typing import Any, Dict, Optional

import psycopg
from psycopg.rows import dict_row

from app.config import settings


class PostgresError(RuntimeError):
    pass


# One row per file kind ('cdr' / 'ipdr') of this number in this case; the case columns repeat on every row. A case with
# no file of the number still returns one row (kind null), so the case fields are not lost. A number is matched on its
# last 10 digits, like the ClickHouse facts.
_HEADER_SQL = """
SELECT c.reference_name, c.reference_date, c.description, c.notes, c.officer_name,
       f.subdomain AS kind,
       min(t.period_start) AS requested_from, max(t.period_end) AS requested_to,
       min(t.actual_start) AS actual_from,    max(t.actual_end) AS actual_to
FROM cases AS c
LEFT JOIN telecom_metadata AS t ON t.case_id = c.case_id AND right(t.msisdn, 10) = right(%(msisdn)s, 10)
LEFT JOIN files AS f ON f.file_id = t.file_id AND f.subdomain IN ('cdr', 'ipdr')
                    AND f.deleted_at IS NULL AND NOT f.is_trashed
WHERE c.case_id = %(case_id)s::uuid
GROUP BY c.case_id, f.subdomain
"""


def _stamp(value: Optional[dt.datetime]) -> Optional[str]:
    """A period bound the way the ClickHouse sessions print: 'YYYY-MM-DD HH:MM:SS' (naive times, as stored)."""
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


def _utc(value: Optional[dt.datetime]) -> Optional[str]:
    """A timestamptz the way `generated_at` prints."""
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def fetch_case_header(case_id: str, msisdn: str) -> Dict[str, Any]:
    """{ref, ref_date, input_gist, analysed_by, periods: {cdr|ipdr: {requested: {from, to}, actual: {from, to}}}},
    or {} when the case is not in Postgres. Raises PostgresError when Postgres cannot be queried."""
    try:
        with psycopg.connect(
            host=settings.pg_host, port=settings.pg_port, user=settings.pg_user, password=settings.pg_password,
            dbname=settings.pg_database, connect_timeout=settings.pg_connect_timeout_seconds,
            options=f"-c statement_timeout={settings.query_timeout_seconds * 1000}", row_factory=dict_row,
        ) as conn:
            rows = conn.execute(_HEADER_SQL, {"case_id": case_id, "msisdn": msisdn}).fetchall()
    except psycopg.Error as e:
        raise PostgresError(f"postgres {settings.pg_host}:{settings.pg_port}: {str(e).strip()[:300]}") from e

    if not rows:
        return {}
    first = rows[0]
    return {
        "ref": first["reference_name"],
        "ref_date": _utc(first["reference_date"]),
        "input_gist": first["description"] or first["notes"],
        "analysed_by": first["officer_name"],
        "periods": {
            r["kind"]: {"requested": {"from": _stamp(r["requested_from"]), "to": _stamp(r["requested_to"])},
                        "actual": {"from": _stamp(r["actual_from"]), "to": _stamp(r["actual_to"])}}
            for r in rows if r["kind"]
        },
    }
