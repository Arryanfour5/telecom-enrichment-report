# Telecom Enrichment Report API

Section-wise enrichment report for a mobile number in an ingested case. Call one
section at a time to render a panel, or call the report endpoint once and hand
the whole JSON to an LLM.

## The point: schema lives in config, not in code

Every table name and every query sits in `config/report_mapping.py`, as a
`TelecomReportMapping` class with one static method per query — the same
pattern as the `CDRQueryTemplates`/`query_templates.py` modules used
elsewhere in this codebase. No SQL is scattered through the rest of the app.
In an offline deployment whose schema differs, edit that one file — the
queries are plain ClickHouse SQL, so a renamed table or column is changed
directly in the query that uses it. It is loaded by file path (`importlib`,
not a Python `import`), so a mounted replacement only has to define a
module-level `MAPPING` dict in the same shape (`get_mapping()` at the bottom
of the file builds it).

```python
class TelecomReportMapping:
    @staticmethod
    def subscriber_details() -> Dict[str, Any]:
        return {
            "description": "Core subscriber record from SDR",
            "clickhouse_query": """
                SELECT `SUBSCRIBER_NAME` AS name, `CIRCLE` AS circle
                FROM dim_sdr
                WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String}
                LIMIT 1
            """
        }

    @staticmethod
    def get_sections() -> List[Dict[str, Any]]:
        return [
            {
                "id": "subscriber",
                "blocks": [
                    {
                        "key": "subscriber",
                        "source": "dims",   # which connection this block runs on
                        "kind": "one",      # one | many | value | static
                        "sql": TelecomReportMapping.subscriber_details()["clickhouse_query"],
                    },
                ],
            },
        ]

# app/mapping.py imports this file by path and reads MAPPING off it.
MAPPING = TelecomReportMapping.get_mapping()
```

- **Table names are bare** (`dim_sdr`, `fact_call_detail`). They resolve against
  the database set on the connection, so a deployment whose tables live under a
  different database name changes `CH_FACTS_DATABASE` / `CH_DIMS_DATABASE` in the
  environment instead of editing every query. The two `master.*` tables are
  written fully qualified because they sit in a different database.
- `{name:Type}` is a ClickHouse bind param and is left alone. Values are never
  interpolated into SQL, so they cannot alter the statement.
- Params available to any query: `{msisdn:String}`, `{case_id:UUID}`,
  `{limit:UInt32}`, `{day_start:UInt8}`, `{day_end:UInt8}`,
  `{home_country:String}`, `{home_circle:String}`.
- **Comparing a phone number against a dim table**: wrap both sides in
  `toString()`. The column is `Int64` in some deployments and `String` in
  others, and an uncast comparison is a hard type error, not a silent miss.

Facts and dims are separate connections so they *can* live on separate
ClickHouse servers/databases; they currently both point at `analytics` on
:8123. A block declares its `source`; results are merged in Python, so no
cross-server SQL join is ever attempted.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Both ClickHouse connections + mapping file validity |
| GET | `/api/v1/sections` | List configured sections and their block keys |
| GET | `/api/v1/mapping` | What the mapping file resolved to (debug a mount) |
| GET | `/api/v1/report/{case_id}/{msisdn}/section/{section_id}` | One section |
| GET | `/api/v1/report/{case_id}/{msisdn}/left` | All left-panel sections (subscriber, name_alias, sim_source, alternate_numbers) |
| GET | `/api/v1/report/{case_id}/{msisdn}/right` | All right-panel sections (ai_insights through cross_reference) |
| GET | `/api/v1/report/{case_id}/{msisdn}` | Full report (left + right panel) |

Optional query params: `limit` (row cap for list blocks, all of the above), and
on the full report `sections=<id>` repeated to select a subset.

## Sections

Left panel: `subscriber`, `name_alias`, `sim_source`, `alternate_numbers`.

Right panel: `ai_insights` (1), `locations` (2), `imei` (3), `last_activity` (4),
`financial_locators` (5), `ecommerce_locators` (6), `connectivity` (7),
`sustel` (8), `social_media` (9), `cross_reference` (10).

## Response shape

Section id is a top-level key, block key holds the value directly — no status
envelope or timing metadata wrapping every field, so the JSON can be handed to
an LLM as-is.

```json
{
  "msisdn": "919812345678",
  "case_id": "21943083-fffd-4570-81e3-a016b13f03fd",
  "generated_at": "2026-09-06T11:25:22Z",
  "subscriber": {
    "subscriber": {"name": "Harjindar Singh", "circle": "MADHYA PRADESH", ...},
    "truecaller": {"truecaller_name": "Harjindar S", "spam": "not_spam", ...}
  },
  "imei": {
    "devices": [ ... ],
    "imei_dim_hits": [ ... ]
  }
}
```

One mismapped table degrades a single block rather than failing the report: the
block's value is `null` and a top-level `warnings` array appears naming the
section, block and ClickHouse error. It is absent entirely when nothing failed.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # fill in the ClickHouse credentials
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8199

curl "http://127.0.0.1:8199/api/v1/report/<case_id>/<msisdn>"
curl "http://127.0.0.1:8199/api/v1/report/<case_id>/<msisdn>/section/connectivity"
```

## Samples

`samples/` holds real responses captured against case
`21943083-fffd-4570-81e3-a016b13f03fd`, msisdn `919812345678` — the full report
plus nine individual sections. These are the payloads to feed an LLM.

## Sample-case seed data

The sample case originally left sections 5/6, roaming, city visits, silent gaps,
call forwarding, international and IMEI brand/model empty, because its ingested
CDR carried no SMS-sender IDs, no `roaming_circle`, no foreign counterparts, no
anomaly rows, and cell/TAC rows that did not resolve. 17 synthetic
`fact_call_detail` rows, 3 `dim_cell_tower` rows and 3 `fact_call_anomaly` rows
were added so every section returns data.

The anomaly rows reference **existing** `dim_anomaly` types — `silence_periods`
(id 4) and `call_forwarding` (id 25), both `anomaly_domain = 'cdr'`. Nothing was
added to the anomaly catalogue itself: it is shared, and drives
ipdr-analytics-v2's filter panel.

Note `anomaly_id` is unique only *within* a domain (id 25 is `call_forwarding`
in `cdr` and `exfiltration_over_steganographic_channels` in `ipdr`), while
`fact_call_anomaly` joins on `anomaly_id` alone — so any query over it should
filter `anomaly_domain` or match on the name, as the blocks here do.

The CDR rows are all tagged `file_id = dddddddd-dddd-dddd-dddd-dddddddddddd`, so
the seed set is identifiable and removable on its own:

```sql
-- inspect
SELECT * FROM analytics.fact_call_detail
WHERE file_id = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
-- remove
ALTER TABLE analytics.fact_call_detail
  DELETE WHERE file_id = 'dddddddd-dddd-dddd-dddd-dddddddddddd';
ALTER TABLE analytics.dim_cell_tower
  DELETE WHERE cell_id IN ('770101', '770202', '770303');
```

No pre-existing row was modified — the five original cell towers are shared by
53 other cases, so new towers were added rather than backfilling those.

## Known data gaps (data, not code)

- **Two of the three sample IMEIs still show blank brand/model** — their TACs
  genuinely are not in `tac_db`. The third (`0112450...`) resolves to Apple
  iPhone, which confirms the TAC join itself works.
- **`samples/*.json` predate the seed data** and no longer match live responses.
