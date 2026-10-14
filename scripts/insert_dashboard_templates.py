#!/usr/bin/env python3
"""Insert the dim-enrichment queries into public."dashboard-template".

Six templates, one per dim table, each joining fact_call_detail to a dim via
sub_fact_mapping_a_party / _b_party.

Run:
    python insert_dashboard_templates.py            # skip names that exist
    python insert_dashboard_templates.py --replace  # delete those names, reinsert
    python insert_dashboard_templates.py --dry-run  # print, touch nothing
"""
import asyncio
import json
import os
import sys

import asyncpg

CASE_ID = "21943083-fffd-4570-81e3-a016b13f03fd"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public."dashboard-template" (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    type VARCHAR(10) NOT NULL,
    query TEXT NOT NULL,
    template_sql TEXT,
    executed_sql TEXT,
    owner VARCHAR(255) NOT NULL,
    parameters JSONB,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    filter_sql TEXT,
    domain VARCHAR(10),

    CONSTRAINT "dashboard-template_type_check"
        CHECK (
            type IN ('system', 'user', 'case')
        ),

    CONSTRAINT "dashboard-template_pkey"
        PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."saved-analysis" (
    id UUID DEFAULT gen_random_uuid() NOT NULL,
    case_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    query_text TEXT,
    executed_sql TEXT NOT NULL,
    result_data JSONB,
    result_summary TEXT,
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    domain VARCHAR(10),

    CONSTRAINT "saved-analysis_pkey"
        PRIMARY KEY (id)
);
"""

PHONEBOOK_SQL = """SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    pb.id, pb.Telephone, pb.User, pb.Place, pb.District, pb.State, pb.Country, pb.Remarks, pb.Reference,
    pb.Date, pb.`Date of entry`
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_a_party m ON m.call_id = cd.call_id
INNER JOIN dim_phonebook pb ON pb.id = toInt64(m.dim_phonebook_id)
WHERE cd.case_id = '{case_id}'
UNION ALL
SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    pb.id, pb.Telephone, pb.User, pb.Place, pb.District, pb.State, pb.Country, pb.Remarks, pb.Reference,
    pb.Date, pb.`Date of entry`
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_b_party m ON m.call_id = cd.call_id
INNER JOIN dim_phonebook pb ON pb.id = toInt64(m.dim_phonebook_id)
WHERE cd.case_id = '{case_id}'"""

# Fixed: the b_party half as supplied also selected cd.case_id and cd.call_id,
# giving 18 columns against the a_party half's 16. ClickHouse rejects that with
# UNION_ALL_RESULT_STRUCTURES_MISMATCH, so both halves now select the same six
# fact columns the a_party half did.
PROFILER_SQL = """SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    pr.id, pr.`phone number`, pr.addresses, pr.names, pr.gender, pr.telegram, pr.facebook, pr.twitter, pr.upi, pr.MNP
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_a_party m ON m.call_id = cd.call_id
INNER JOIN dim_profiler pr ON pr.id = toInt64(m.dim_profiler_id)
WHERE cd.case_id = '{case_id}'
UNION ALL
SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    pr.id, pr.`phone number`, pr.addresses, pr.names, pr.gender, pr.telegram, pr.facebook, pr.twitter, pr.upi, pr.MNP
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_b_party m ON m.call_id = cd.call_id
INNER JOIN dim_profiler pr ON pr.id = toInt64(m.dim_profiler_id)
WHERE cd.case_id = '{case_id}'"""

# Fixed: same column-count mismatch as the profiler query.
TRUECALLER_SQL = """SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    tc.id, tc.`phone number`, tc.name, tc.email, tc.operator, tc.location, tc.country, tc.address, tc.`spam score`
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_a_party m ON m.call_id = cd.call_id
INNER JOIN dim_truecaller tc ON tc.id = toInt64(m.dim_truecaller_id)
WHERE cd.case_id = '{case_id}'
UNION ALL
SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    tc.id, tc.`phone number`, tc.name, tc.email, tc.operator, tc.location, tc.country, tc.address, tc.`spam score`
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_b_party m ON m.call_id = cd.call_id
INNER JOIN dim_truecaller tc ON tc.id = toInt64(m.dim_truecaller_id)
WHERE cd.case_id = '{case_id}'"""

# Joined on m.dim_imei_legacy_id (the mapping tables' bridge column for
# dim_imei_legacy). a_party only, as supplied.
IMEI_SQL = """SELECT
    cd.case_id, cd.call_id, cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number, cd.imei,
    im.id, im.IMEI, im.`Case Reference`, im.`Group`, im.`Analyst Name`, im.`Brief description`, im.timestamp
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_a_party m ON m.call_id = cd.call_id
INNER JOIN dim_imei_legacy im ON im.id = toInt64(m.dim_imei_legacy_id)
WHERE cd.case_id = '{case_id}'"""

MNP_SQL = """SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    n.id, n.code, n.`service provider`, n.circle, n.`date of MNP`
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_a_party m ON m.call_id = cd.call_id
INNER JOIN dim_mnp n ON n.id = toInt64(m.dim_mnp_id)
WHERE cd.case_id = '{case_id}'
UNION ALL
SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    n.id, n.code, n.`service provider`, n.circle, n.`date of MNP`
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_b_party m ON m.call_id = cd.call_id
INNER JOIN dim_mnp n ON n.id = toInt64(m.dim_mnp_id)
WHERE cd.case_id = '{case_id}'"""

MSC_SQL = """SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    ms.id, ms.code, ms.`service provider`, ms.circle
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_a_party m ON m.call_id = cd.call_id
INNER JOIN dim_msc ms ON ms.id = toInt64(m.dim_msc_id)
WHERE cd.case_id = '{case_id}'
UNION ALL
SELECT
    cd.caller, cd.receiver, cd.call_type, cd.call_date, cd.duration, cd.target_number,
    ms.id, ms.code, ms.`service provider`, ms.circle
FROM fact_call_detail cd
INNER JOIN sub_fact_mapping_b_party m ON m.call_id = cd.call_id
INNER JOIN dim_msc ms ON ms.id = toInt64(m.dim_msc_id)
WHERE cd.case_id = '{case_id}'"""

TEMPLATES = [
    {
        "name": "Phonebook Enrichment for Case Calls",
        "description": (
            "Every call in the case whose A party or B party matches a dim_phonebook "
            "entry, with the phonebook record attached -- name, place, district, state, "
            "remarks and reference. Shows which parties in the case are already known "
            "contacts and where they are recorded."
        ),
        "sql": PHONEBOOK_SQL,
    },
    {
        "name": "Profiler Enrichment for Case Calls",
        "description": (
            "Case calls joined to dim_profiler for either party, exposing the profiled "
            "identity behind a number -- known addresses, names, gender, Telegram, "
            "Facebook, Twitter, UPI handle and MNP flag."
        ),
        "sql": PROFILER_SQL,
    },
    {
        "name": "Truecaller Enrichment for Case Calls",
        "description": (
            "Case calls joined to dim_truecaller for either party, giving the "
            "crowd-sourced caller name, email, operator, location, country, address and "
            "spam classification for the numbers involved."
        ),
        "sql": TRUECALLER_SQL,
    },
    {
        "name": "IMEI Enrichment for Case Calls",
        "description": (
            "Case calls whose A party maps to a dim_imei_legacy record, pairing the handset "
            "IMEI seen in the CDR with the analyst's IMEI dossier -- case, reference, "
            "group, analyst and description."
        ),
        "sql": IMEI_SQL,
    },
    {
        "name": "MNP Enrichment for Case Calls",
        "description": (
            "Case calls joined to dim_mnp for either party, showing number-portability "
            "records -- the code, the service provider ported to, the circle and the "
            "porting date."
        ),
        "sql": MNP_SQL,
    },
    {
        "name": "MSC Enrichment for Case Calls",
        "description": (
            "Case calls joined to dim_msc for either party, resolving the mobile "
            "switching centre code to its service provider and circle."
        ),
        "sql": MSC_SQL,
    },
]

PARAMETERS = {
    "case_id": {
        "type": "UUID",
        "description": "The active case ID",
        "original_value": CASE_ID,
    }
}

TYPE = "system"
DOMAIN = "cdr"
OWNER = "0"
CREATED_BY = "test_user"


def build_rows():
    """(name, description, type, query, template_sql, owner, parameters, created_by, domain)."""
    rows = []
    for t in TEMPLATES:
        rows.append((
            t["name"],
            t["description"],
            TYPE,
            t["sql"].replace("{case_id}", CASE_ID),        # query: concrete, runnable
            t["sql"].replace("{case_id}", "{{ case_id }}"),  # template_sql: parameterised
            OWNER,
            json.dumps(PARAMETERS),
            CREATED_BY,
            DOMAIN,
        ))
    return rows


async def main() -> None:
    replace = "--replace" in sys.argv
    dry_run = "--dry-run" in sys.argv

    host: str = os.getenv("PGHOST", "postgres")
    port: str = os.getenv("PGPORT", "5432")
    user: str = os.getenv("PGUSER", "postgres")
    password: str = os.getenv("PGPASSWORD", "kHsdDElpWlxN")
    database: str = os.getenv("PGDATABASE", "pi-scout-db-test")
    table: str = "dashboard-template"

    rows = build_rows()

    if dry_run:
        for r in rows:
            print(f"\n{'=' * 70}\n{r[0]}  [{r[2]}/{r[8]}]\n{'=' * 70}\n{r[4]}")
        print(f"\n{len(rows)} template(s) would be inserted into "
              f'"{table}" on {database}')
        return

    conn = await asyncpg.connect(host=host, port=int(port), user=user,
                                 password=password, database=database)
    try:
        async with conn.transaction():
            await conn.execute(CREATE_TABLE_SQL)

            names = [r[0] for r in rows]
            existing = {
                rec["name"] for rec in await conn.fetch(
                    f'SELECT name FROM public."{table}" WHERE name = ANY($1::text[])',
                    names,
                )
            }

            if existing and replace:
                await conn.execute(
                    f'DELETE FROM public."{table}" WHERE name = ANY($1::text[])',
                    list(existing),
                )
                print(f"  replaced {len(existing)} existing template(s)")
            elif existing:
                rows = [r for r in rows if r[0] not in existing]
                for name in sorted(existing):
                    print(f"  skip (exists): {name}")

            if rows:
                await conn.executemany(
                    f'INSERT INTO public."{table}" '
                    "(name, description, type, query, template_sql, owner, "
                    " parameters, created_by, domain) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)",
                    rows,
                )
            for r in rows:
                print(f"  + {r[0]}")

        print(f'✓ Inserted {len(rows)} template(s) → "{table}" on {database}')
    except Exception as e:
        print(f"✗ Insert failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
