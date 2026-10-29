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
| GET | `/api/v1/full-report/{case_id}/{msisdn}` | The CDR report and the IPDR report in one response: `{msisdn, case_id, generated_at, cdr, ipdr}` |

Optional query params: `limit` (row cap for list blocks, all of the above), and
on the full report `sections=<id>` repeated to select a subset.

## Sections

Left panel: `subscriber`, `name_alias`, `sim_source`, `alternate_numbers`.

Right panel: `ai_insights` (1), `locations` (2), `imei` (3), `last_activity` (4, with `last_call`, `last_sms`, `last_p2p_sms` and `recent_activity`),
`financial_locators` (5), `ecommerce_locators` (6), `connectivity` (7, incl. `top_b_party_locations` / `least_b_party_locations` -- 2 rows each, set by `b_party_locations_limit`),
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

## Report header (Postgres)

The CDR report (full report and right panel) and the IPDR report (right panel and full report) start with a `header`:
Ref, Ref date, Input gist, Analysed by, Analysed on and the period requested / actual (`cdr_period` or `ipdr_period`).
These are not in ClickHouse; `app/pg.py` reads them from the case-management Postgres (`PG_HOST`, `PG_PORT` 5432,
`PG_USER`, `PG_PASSWORD`, `PG_DATABASE`):

| Header field | Source |
|---|---|
| Ref / Ref date | `cases.reference_name` / `cases.reference_date` |
| Input gist | `cases.description` (`cases.notes` when empty) |
| Analysed by | `cases.officer_name` |
| Analysed on | the time the report was generated |
| Period requested | `telecom_metadata.period_start` / `period_end` of the number's files |
| Period actual | `telecom_metadata.actual_start` / `actual_end` of the number's files |

The files of a number are told apart by `files.subdomain` (`cdr` / `ipdr`); several files of one kind give the earliest
start and the latest end. The number is matched on its last 10 digits. If Postgres is down the report still comes back, the
header fields are null and a `warnings` entry (`section: header, block: postgres`) says why. On the IPDR endpoints a
`ref`, `ref_date`, `input_gist`, `analysed_by`, `period_requested_from` / `period_requested_to` query parameter overrides
the Postgres value.

## IPDR report

The IPDR quick report (`/api/v1/ipdr/...`) is the same service with its own mapping file,
`config/ipdr_report_mapping.py` (`IPDR_MAPPING`-style contract: a module-level `MAPPING` dict; the path is
`IPDR_MAPPING_FILE`, default `config/ipdr_report_mapping.py`). An IPDR record is an internet session
(`fact_internet_sessions`) and the number it belongs to is its `device_id`; facts are matched on the **last 10 digits**
of the number.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/ipdr/sections` | Configured sections and block keys |
| GET | `/api/v1/ipdr/mapping` | The mapping's settings (thresholds, domain lists) |
| GET | `/api/v1/ipdr/report/{case_id}/{msisdn}` | Full report. `sections=<id>` (repeat) picks sections |
| GET | `/api/v1/ipdr/report/{case_id}/{msisdn}/left` | Subscriber, Name / Alias, SIM bought from, Alternate numbers |
| GET | `/api/v1/ipdr/report/{case_id}/{msisdn}/right` | Header and every right-panel section |
| GET | `/api/v1/ipdr/report/{case_id}/{msisdn}/section/{id}` | One section |

`ref`, `ref_date`, `input_gist`, `analysed_by`, `period_requested_from` and `period_requested_to` are query parameters
on the full report and `/right`: they are not in ClickHouse, so they are echoed in `header` (null when not given).
`header` also carries `analysed_on`, `ipdr_period.actual` (first / last session, sessions, days with data, files) and
`top_place` (the city with the most sessions).

Right-panel sections, in the client's order: `ai_insights` (file-wise internet usage level, Sustel connectivity,
international roaming, frequent roaming by circle, city visits, last IPDR date), `locations` (day, night, international,
international cities - with latitude / longitude and date range), `imei`, `imsi`, `connectivity` (B-party numbers with
their SDR city), `applications_used`, `vpn_usage`, `tor_usage`, `bittorrent_usage`, `hotspot_usage`, `websites_used`,
`b_party_resolution`, `sustel`, `social_media`, `cross_reference`.

**Reuse.** The left panel, the social-media blocks and the Sustel records read the LEA reference tables by number
(`dim_sdr`, `dim_truecaller`, `dim_profiler`, Sustel, ...), which the CDR and IPDR reports share; the IPDR
mapping loads `report_mapping.py` from the same folder and takes those blocks from it. Everything that reads call
records is written again against `fact_internet_sessions`.

**Sustel, phone book, CRI, MSC.** Sustel, phone book and CRI are `scout_reference.sustel` / `phonebook` / `cri` (the ingestion
service's Access pipeline), not `analytics.dim_sustel` / `dim_phonebook` / `dim_cri`. Queries write `__SUSTEL__` / `__PHONEBOOK__` /
`__CRI__`; `app/mapping.py` swaps in the subqueries of `app/lea_reference.py`, which show those tables under the legacy column names,
so the queries are unchanged. Database: `CH_REFERENCE_DATABASE` (default `scout_reference`, on the `dims` server). MSC codes are
`master.dim_msc_master` (code ranges), not `analytics.dim_msc`.

**Two-step lookups.** `app/service.py` (`build_ipdr_params`) first reads the resolved B-party numbers (facts), then which
of them are on Sustel (dims), then the handsets' IMEIs, and passes them to the dims queries as bind parameters:
facts and dims may be on different servers, so they are never joined in SQL.

**What decides each section**

- *B party resolution*: each resolved B-party number (`numbers`: how it resolved, `P2P Session` / `P2A Session`, the sessions and when) also carries
  `b_party_name` (the SDR subscriber name, null when the number has no SDR record), `sustel_match` / `b_party_sustel_match` (true when the number is on
  Sustel, matched on the last 10 digits of either of the record's two numbers; null when the Sustel look-up failed) and `sustel` (the case and whether it is
  monitored, null when there is none). The full Sustel record is in the Sustel Details section (`sustel.matches`, `match_type` `b_party`). The two look-ups are
  dims queries merged into the facts rows in Python (`app/service.py`), because dims and facts may be on different servers.
- *Applications used / VoIP-tagged apps*: the application of the IP range the session's destination IP is in (the port plays no part), in
  `master.dim_application_master` (IP ranges; loaded by `deployment_scripts/<date>/ingest_dim_application_master.py` in the ingestion repo).
- *VPN*: `vpn_name`, set by the ingestion from the community VPN list, else IP2Proxy, on a VPN port (`Unidentified VPN` when neither names the provider).
- *TOR*: `is_tor`, set by the ingestion when the destination IP is a Tor exit node (exact IP) or an IP2Proxy TOR address. *BitTorrent*: `master.dim_bittorrent`.
  MaxMind is not used for VPN / TOR (the ingestion's `deployment_scripts/2026-10-21/ingest_anonymizer_masters.py` loads the lists).
- *Internet usage level*: megabytes per ACTIVE day of each file against `usage_low_mb_per_day` (100) and
  `usage_high_mb_per_day` (1024). **Provisional thresholds - confirm with the client.**
- *Hotspot (tethering)*: an IPDR record does not say "tethering", so `hotspot_usage.indicators` lists indicators only:
  sessions the concurrent-connections detector flagged, and access-point names containing tether / hotspot / mobap.
- *Websites used*: IPDR has no URLs, so a site is recognised by the destination IP's AS domain (exact match against
  `ecommerce_domains` / `financial_domains`). Cloud and CDN domains (AWS, CloudFront, Akamai, Google ...) cannot be told
  apart from the shops hosted on them, so they are listed apart in `shared_hosting` and never counted as e-commerce.
- *Repeated rows* (CDR and IPDR): `name_alias.names` and `alternate_numbers.numbers` show a value once **per source** -- when one source (e.g. the SDR, which can hold
  several records of a number) returns the same name or number twice, the first row is kept; the Profiler, Phonebook, Truecaller, SDR and Sustel each still show their
  own row. `imei.imei_dim_hits`, `ai_insights.sustel_match` / `profiler_match` / `sustel_hits` and `sustel.matches` drop only rows identical in every column.
  Done in `_dedupe` (`app/service.py`); other lists are untouched.
- *Day / night locations* (`locations.day`, `locations.night`, CDR and IPDR): city-wise -- the cells of one city are added up, a cell with no city stays its
  own place -- and capped at the top `locations_top_n` (5, in the mapping's settings; a smaller `limit` query parameter lowers it). The columns are unchanged;
  `cell_id`, address and coordinates are those of the city's busiest cell.
- *International roaming / cities / locations*: the serving cell id resolved through `dim_cell_tower` (country, city,
  latitude, longitude); "international" is any country other than `home_country_name`.
- *VoIP calls* (`connectivity.voip_by_country`, `connectivity.voip_calls`): sessions with `fact_internet_sessions.voip_call_type` set (`whatsapp_call`, `telegram_call`, `instagram_call`, ...), inferred by the ingestion's AppInferenceEngine. Needs a database that has ingested an IPDR file since the column shipped.

The test suite runs without a database: `PYTHONPATH=. python -m pytest tests -q` (needs `httpx` and `pytest`).

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
