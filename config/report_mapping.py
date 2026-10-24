"""
Telecom Enrichment Report - schema mapping.

This file is the ONLY place that knows what the database looks like. Mount it
over /app/config/report_mapping.py in an offline deployment whose schema
differs and the API adapts without a code change or rebuild -- same pattern
as the CDR/IPDR query_templates.py modules: a plain Python module the app
imports by file path, edit it and restart (or hot-reload) to change behavior.

Placeholders inside sql:
  ${table_key}      -> resolved from TelecomReportMapping.get_tables()
  {name:Type}       -> ClickHouse server-side bind param (NOT touched by us).
                       Available: {msisdn:String}, {case_id:UUID},
                       {limit:UInt32}, {day_start:UInt8}, {day_end:UInt8}

Every block declares which `source` (connection) it runs on, so facts and dims
may live on entirely separate ClickHouse servers.
"""
from typing import Any, Dict, List


class TelecomReportMapping:
    """Repository for the telecom enrichment report's schema mapping and queries."""

    # ─────────────────────────────────────────────────────────────────────
    # Connections and tables. Repoint these when the offline schema differs.
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_databases() -> Dict[str, str]:
        """Logical source -> database name on that connection.
        Connection host/port/credentials come from env (see .env.example)."""
        return {
            "facts": "analytics",       # source: facts   -> CH_FACTS_*
            # The LEA dim_* tables (dim_imei_legacy, dim_mnp, dim_profiler,
            # dim_sdr, dim_truecaller) live in `analytics`
            # on :8123, laid out per the CREATE TABLE statements for `pi-scout-sit`.
            # Point this at another database/server if an offline deployment keeps
            # them split -- `source` per block still supports that.
            "dims": "analytics",        # source: dims    -> CH_DIMS_*
            "master": "master",         # source: facts (same server as facts by default)
            "enriched": "scout_enriched",   # CAF: the real name is CH_ENRICHED_DATABASE (see __CAF__)
            # Sustel, phone book, CRI (scout_reference): the real name is
            # CH_REFERENCE_DATABASE; queries write __SUSTEL__ / __PHONEBOOK__ / __CRI__ (app/lea_reference.py).
            "reference": "scout_reference",
        }

    @staticmethod
    def get_tables() -> Dict[str, str]:
        """Fully qualified table names, keyed by the ${table_key} used in queries below."""
        return {
            "call_detail": "{facts}.fact_call_detail",
            "sdr": "{dims}.dim_sdr",
            "cri": "{reference}.cri",
            "imei": "{dims}.dim_imei_legacy",
            "mnp": "{dims}.dim_mnp",
            # MSC code ranges (coai.com), loaded by the ingestion service's deployment_scripts/2026-10-21/ingest_msc_codes.py
            "msc": "{master}.dim_msc_master",
            "phonebook": "{reference}.phonebook",
            "profiler": "{dims}.dim_profiler",
            "sustel": "{reference}.sustel",
            "truecaller": "{dims}.dim_truecaller",
            # The operator CAF now lives in the enriched layer; queries write it as __CAF__ and app/mapping.py fills in
            # CH_ENRICHED_DATABASE (default scout_enriched).
            "caf": "{enriched}.customer_application_form",
            "tac": "master.Tac_DB_master",
            "cell_tower": "{facts}.dim_cell_tower",
            "sms_sender": "{master}.dim_sms_sender",
            "country": "{master}.dim_country_master",
            # Shared with ipdr-analytics-v2's anomaly pipeline: per-call anomaly
            # flags (fact_call_anomaly) and their catalog (dim_anomaly), on the
            # same "analytics" database as call_detail.
            "call_anomaly": "{facts}.fact_call_anomaly",
            "anomaly_dim": "{facts}.dim_anomaly",
        }

    @staticmethod
    def get_settings() -> Dict[str, Any]:
        """Tunables referenced by the queries below."""
        return {
            "day_window_start": 6,        # 06:00 - 20:00 counts as "day"
            "day_window_end": 20,
            "top_n": 10,
            "b_party_locations_limit": 2,   # rows in each of the most/least contacted B-party location lists
            "home_country_code": "91",
        }

    # ─────────────────────────────────────────────────────────────────────
    # LEFT PANEL queries
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def subscriber_details() -> Dict[str, Any]:
        return {
            "description": "Core subscriber record from the SDR (subscriber data record) table, with the Truecaller-reported name",
            "clickhouse_query": """
SELECT
    s.`SUBSCRIBER_NAME`   AS name,
    toDate(s.`DOB`)       AS dob,  -- DOB is DateTime64(9); return the date only
    s.`SIM_ACTV_DATE`     AS doa,
    s.`SERVICE_PROVIDER`  AS operator,
    s.`CIRCLE`            AS circle,
    s.`CON_TYPE`          AS connection_type,
    t.`name`              AS truecaller_name
FROM dim_sdr AS s
LEFT JOIN dim_truecaller AS t ON toString(t.`phone number`) = toString(s.`SUBSCRIBER_NUMBER`)
WHERE toString(s.`SUBSCRIBER_NUMBER`) = {msisdn:String}
LIMIT 1
            """
        }

    @staticmethod
    def subscriber_photo() -> Dict[str, Any]:
        return {
            "description": (
                "Subscriber photo from dim_sdr (the SDR record), matched on the "
                "target's number: base64-encoded image plus its MIME type, so a "
                "caller can render it as "
                "data:<photo_mime_type>;base64,<photo_base64>. Null when the "
                "number has no dim_sdr row or the row has no photo. It is "
                "dim_sdr.photo1_base64 (compared on the last 10 digits of "
                "SUBSCRIBER_NUMBER, which some sources store with the 91 prefix "
                "and some without); dim_sdr carries no MIME type, so it is "
                "image/jpeg. It used to be the CAF's photo (customer_application_form). "
                "Looked up on the target number rather than through "
                "sub_fact_mapping_a_party: the bridge is keyed by call_id, which "
                "lives in the facts connection, and this block runs on the dims one."
            ),
            "clickhouse_query": """
SELECT nullIf(photo1_base64, '') AS photo_base64,
       'image/jpeg' AS photo_mime_type
FROM dim_sdr
WHERE right(toString(SUBSCRIBER_NUMBER), 10) = right({msisdn:String}, 10)
  AND ifNull(photo1_base64, '') != ''
LIMIT 1
            """
        }

    @staticmethod
    def name_alias_sources() -> Dict[str, Any]:
        return {
            "description": "Every name/alias seen for the number, unioned across SDR, Truecaller, phonebook, profiler and Sustel",
            "clickhouse_query": """
SELECT 'SDR' AS source, `SUBSCRIBER_NAME` AS value
FROM dim_sdr WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String} AND `SUBSCRIBER_NAME` != ''
UNION ALL
SELECT 'Truecaller', `name`
FROM dim_truecaller WHERE toString(`phone number`) = {msisdn:String} AND `name` != ''
UNION ALL
SELECT 'Phonebook', `User`
FROM __PHONEBOOK__ WHERE toString(`Telephone`) = {msisdn:String} AND `User` != ''
UNION ALL
SELECT 'Profiler', `names`
FROM dim_profiler WHERE toString(`phone number`) = {msisdn:String} AND `names` != ''
UNION ALL
SELECT 'Sustel', `username`
FROM __SUSTEL__ WHERE toString(`telno`) = {msisdn:String} AND `username` != ''
            """
        }

    @staticmethod
    def point_of_sale() -> Dict[str, Any]:
        return {
            "description": "Point-of-sale address for where the SIM was bought, from SDR",
            "clickhouse_query": """
SELECT `POS_ADDRESS` AS pos_address
FROM dim_sdr
WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String}
LIMIT 1
            """
        }

    @staticmethod
    def alternate_numbers() -> Dict[str, Any]:
        return {
            "description": "Alternate numbers linked to this subscriber, from SDR and Sustel",
            "clickhouse_query": """
SELECT 'SDR' AS source, toString(`ALTERNATE_NUMBER`) AS number
FROM dim_sdr WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String} AND toString(`ALTERNATE_NUMBER`) NOT IN ('', '0')
UNION ALL
SELECT 'Sustel', toString(`telno0`)
FROM __SUSTEL__ WHERE toString(`telno`) = {msisdn:String} AND toString(`telno0`) NOT IN ('', '0')
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 1: AI Insights
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def target_counterparts() -> Dict[str, Any]:
        return {
            "description": (
                "Distinct b-party numbers this target was in contact with "
                "(fact_call_detail). Feeds sustel_match's b-party check below --"
                "dims and facts are separate ClickHouse connections, so that "
                "check can't be a single SQL join; app/service.py's "
                "_lookup_counterparts runs this first and passes the result in "
                "as the {counterparts:Array(String)} bind param, same pattern as "
                "home_circle."
            ),
            "clickhouse_query": """
SELECT DISTINCT if(caller = {msisdn:String}, receiver, caller) AS counterpart
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
LIMIT 500
            """
        }

    @staticmethod
    def sustel_match() -> Dict[str, Any]:
        return {
            "description": (
                "Whether this number -- or any b-party (counterpart) it was in "
                "contact with -- is on the Sustel watchlist, with the case "
                "name, monitored flag, and the actual number that matched "
                "(the target itself or a b-party)."
            ),
            "clickhouse_query": """
SELECT
    `Case`                  AS case_name,
    `monitored`             AS monitored,
    multiIf(
        toString(`telno`) = {msisdn:String} OR toString(`telno0`) = {msisdn:String}, {msisdn:String},
        toString(`telno`) IN {counterparts:Array(String)}, toString(`telno`),
        toString(`telno0`)
    )                       AS matched_number
FROM __SUSTEL__
WHERE toString(`telno`) = {msisdn:String} OR toString(`telno0`) = {msisdn:String}
   OR toString(`telno`) IN {counterparts:Array(String)} OR toString(`telno0`) IN {counterparts:Array(String)}
            """
        }

    @staticmethod
    def profiler_match() -> Dict[str, Any]:
        return {
            "description": (
                "Profiler row(s) matching this number OR any b-party "
                "(counterpart) it was in contact with, with names/social "
                "handles and whether the match was the target itself or a "
                "b-party."
            ),
            "clickhouse_query": """
SELECT
    toString(`phone number`) AS matched_number,
    if(toString(`phone number`) = {msisdn:String}, 'target', 'b_party') AS match_type,
    `names`        AS names,
    `gender`       AS gender,
    `telegram`     AS telegram,
    `facebook`     AS facebook,
    `twitter`      AS twitter,
    `upi`          AS upi
FROM dim_profiler
WHERE toString(`phone number`) = {msisdn:String}
   OR toString(`phone number`) IN {counterparts:Array(String)}
            """
        }

    @staticmethod
    def cri_match() -> Dict[str, Any]:
        return {
            "description": (
                "CRI row(s) matching this number OR any b-party (counterpart) "
                "it was in contact with (full or last-10-digit target match), "
                "with the actual matched number and whether that match was the "
                "target itself or a b-party."
            ),
            "clickhouse_query": """
SELECT
    `tCDRID`         AS cri_id,
    `tDate`          AS call_date,
    `tCallType`      AS call_type,
    `tCallDirection` AS direction,
    multiIf(
        toString(`tTargetNo`) = {msisdn:String}
            OR toString(`tCallingNo`) = {msisdn:String}
            OR toString(`tCalledNo`) = {msisdn:String}
            OR toString(`tRight10Target`) = right({msisdn:String}, 10),
        {msisdn:String},
        toString(`tTargetNo`) IN {counterparts:Array(String)}, toString(`tTargetNo`),
        toString(`tCallingNo`) IN {counterparts:Array(String)}, toString(`tCallingNo`),
        toString(`tCalledNo`)
    )                AS matched_number,
    if(
        toString(`tTargetNo`) = {msisdn:String}
            OR toString(`tCallingNo`) = {msisdn:String}
            OR toString(`tCalledNo`) = {msisdn:String}
            OR toString(`tRight10Target`) = right({msisdn:String}, 10),
        'target', 'b_party'
    )                AS match_type,
    `Officerconcerned` AS officer
FROM __CRI__
WHERE toString(`tTargetNo`) = {msisdn:String}
   OR toString(`tCallingNo`) = {msisdn:String}
   OR toString(`tCalledNo`) = {msisdn:String}
   OR toString(`tRight10Target`) = right({msisdn:String}, 10)
   OR toString(`tTargetNo`) IN {counterparts:Array(String)}
   OR toString(`tCallingNo`) IN {counterparts:Array(String)}
   OR toString(`tCalledNo`) IN {counterparts:Array(String)}
            """
        }

    @staticmethod
    def call_forwarding_anomaly() -> Dict[str, Any]:
        return {
            "description": (
                "Whether any call-forwarding related anomaly has been flagged by "
                "the shared anomaly pipeline (fact_call_anomaly/dim_anomaly), "
                "matched dynamically by name/category/type -- reduced to a single "
                "yes/no rather than the underlying anomaly rows. There is no "
                "call-forwarding anomaly type in the platform's current "
                "dim_anomaly catalog, so this reports false today -- it starts "
                "reporting automatically if one is added."
            ),
            "clickhouse_query": """
SELECT count() > 0 AS has_call_forwarding
FROM fact_call_anomaly AS fca
INNER JOIN dim_anomaly AS da ON da.anomaly_id = fca.anomaly_id
INNER JOIN fact_call_detail AS c ON c.call_id = fca.call_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND (da.name ILIKE '%forward%' OR da.category ILIKE '%forward%' OR da.anomaly_type ILIKE '%forward%')
            """
        }

    @staticmethod
    def cities_visited() -> Dict[str, Any]:
        return {
            "description": (
                "Distinct cities visited, resolved from every cell tower hit -- "
                "both start_cell_id and end_cell_id, so a call that started in one "
                "cell and ended in another counts toward BOTH cities -- joined to "
                "dim_cell_tower for the full address/city/state/country/lat/long. "
                "`records` is countDistinct(call_id), not count(): a call whose "
                "start and end cell are different towers in the SAME city must "
                "still count once, or a city's total can exceed the case's actual "
                "record count (seen live: 2056 'records' in a city out of a "
                "1673-row case) -- a number nobody reading the report can make "
                "sense of."
            ),
            "clickhouse_query": """
WITH cell_hits AS (
    SELECT call_id, call_date, start_cell_id AS cell_id
    FROM fact_call_detail
    WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
      AND start_cell_id != ''
    UNION ALL
    SELECT call_id, call_date, end_cell_id AS cell_id
    FROM fact_call_detail
    WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
      AND end_cell_id != '' AND end_cell_id != start_cell_id
)
SELECT
    t.city                    AS city,
    t.state                   AS state,
    t.country                 AS country,
    any(t.address)            AS address,
    any(t.latitude)           AS latitude,
    any(t.longitude)          AS longitude,
    countDistinct(h.call_id)  AS records,
    countDistinct(h.cell_id)  AS distinct_towers,
    min(h.call_date)          AS first_seen,
    max(h.call_date)          AS last_seen
FROM cell_hits AS h
INNER JOIN __CELL_TOWER__ AS t ON t.cell_id = h.cell_id
WHERE t.city != ''
GROUP BY city, state, country
ORDER BY records DESC
            """
        }

    @staticmethod
    def roaming_summary() -> Dict[str, Any]:
        return {
            "description": (
                "How many distinct circles/records show this number roaming. "
                "A record counts as roaming only when roaming_circle is set AND "
                "differs from the subscriber's home circle (dim_sdr, passed in as "
                "{home_circle:String} -- see app/service.py's _lookup_home_circle) "
                "-- a CDR that just repeats the home circle in roaming_circle is "
                "not roaming."
            ),
            "clickhouse_query": """
SELECT
    countDistinctIf(roaming_circle,
        roaming_circle != '' AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
    ) AS distinct_circles,
    countIf(
        roaming_circle != '' AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
    ) AS roaming_records,
    count() AS total_records
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
            """
        }

    @staticmethod
    def home_circle() -> Dict[str, Any]:
        return {
            "description": (
                "Subscriber's home circle from SDR, for comparison against the "
                "roaming_summary/city_visits circles. dim_sdr lives on the `dims` "
                "connection (a different ClickHouse server than fact_call_detail's "
                "`facts` connection), so this is its own block rather than a SQL "
                "join -- results are merged in Python, never joined cross-server."
            ),
            "clickhouse_query": """
SELECT `CIRCLE` AS circle
FROM dim_sdr
WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String}
LIMIT 1
            """
        }

    @staticmethod
    def city_visits() -> Dict[str, Any]:
        return {
            "description": (
                "Roaming circles actually visited (roaming_circle set AND "
                "different from the subscriber's home circle), with record "
                "counts and first/last seen dates."
            ),
            "clickhouse_query": """
SELECT roaming_circle AS circle, count() AS records,
       min(call_date) AS first_seen, max(call_date) AS last_seen
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
  AND roaming_circle != ''
  AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
GROUP BY circle ORDER BY records DESC
            """
        }

    @staticmethod
    def activity_span() -> Dict[str, Any]:
        return {
            "description": "First/last activity dates, active days and silent days over the observed span",
            "clickhouse_query": """
SELECT
    min(call_date)                       AS first_activity,
    max(call_date)                       AS last_activity,
    countDistinct(call_date)             AS active_days,
    dateDiff('day', min(call_date), max(call_date)) + 1 AS span_days,
    dateDiff('day', min(call_date), max(call_date)) + 1 - countDistinct(call_date) AS silent_days
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
            """
        }

    @staticmethod
    def silent_gaps() -> Dict[str, Any]:
        return {
            "description": (
                "Longest gaps with no activity between consecutive active days. "
                "Every date-diff gap is still returned (nothing is dropped), but a "
                "gap whose resumption day the shared anomaly pipeline actually "
                "flagged as a silence-related anomaly is marked anomaly_flagged "
                "and sorted first."
            ),
            "clickhouse_query": """
-- lagInFrame defaults the first row to the zero Date (1970-01-01) rather than
-- NULL, so that row is excluded explicitly or it reports a ~20000 day "gap"
-- before the first ever record.
WITH gaps AS (
    SELECT call_date,
           lagInFrame(call_date) OVER (ORDER BY call_date) AS prev_day
    FROM (
        SELECT DISTINCT call_date FROM fact_call_detail
        WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
        ORDER BY call_date
    )
),
flagged_days AS (
    SELECT DISTINCT toDate(c.start_date_time) AS activity_date, da.name AS anomaly_name
    FROM fact_call_anomaly AS fca
    INNER JOIN dim_anomaly AS da ON da.anomaly_id = fca.anomaly_id
    INNER JOIN fact_call_detail AS c ON c.call_id = fca.call_id
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND (da.name ILIKE '%silence%' OR da.category ILIKE '%silence%' OR da.anomaly_type ILIKE '%silence%')
)
SELECT
    g.prev_day AS gap_start,
    g.call_date AS gap_end,
    dateDiff('day', g.prev_day, g.call_date) - 1 AS silent_days,
    f.anomaly_name IS NOT NULL AS anomaly_flagged,
    f.anomaly_name AS anomaly_name
FROM gaps g
LEFT JOIN flagged_days f ON f.activity_date = g.call_date
WHERE g.prev_day > toDate('1970-01-02')
  AND dateDiff('day', g.prev_day, g.call_date) > 1
ORDER BY anomaly_flagged DESC, silent_days DESC
LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 2: Locations
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def locations_day() -> Dict[str, Any]:
        return {
            "description": (
                "Top cell towers hit during day-window hours -- both start_cell_id "
                "and end_cell_id, so a call that moved between cells counts both -- "
                "with full tower detail from dim_cell_tower."
            ),
            "clickhouse_query": """
WITH cell_hits AS (
    SELECT c.call_date, c.start_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.start_cell_id != ''
      AND toHour(c.start_date_time) >= {day_start:UInt8}
      AND toHour(c.start_date_time) <  {day_end:UInt8}
    UNION ALL
    SELECT c.call_date, c.end_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.end_cell_id != '' AND c.end_cell_id != c.start_cell_id
      AND toHour(c.start_date_time) >= {day_start:UInt8}
      AND toHour(c.start_date_time) <  {day_end:UInt8}
)
SELECT
    h.cell_id        AS cell_id,
    count()          AS records,
    any(t.address)   AS address,
    any(t.city)      AS city,
    any(t.state)     AS state,
    any(t.country)   AS country,
    any(t.latitude)  AS latitude,
    any(t.longitude) AS longitude
FROM cell_hits AS h
LEFT JOIN __CELL_TOWER__ AS t ON t.cell_id = h.cell_id
GROUP BY cell_id ORDER BY records DESC LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def locations_night() -> Dict[str, Any]:
        return {
            "description": (
                "Top cell towers hit outside day-window hours -- both start_cell_id "
                "and end_cell_id, so a call that moved between cells counts both -- "
                "with full tower detail from dim_cell_tower."
            ),
            "clickhouse_query": """
WITH cell_hits AS (
    SELECT c.call_date, c.start_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.start_cell_id != ''
      AND (toHour(c.start_date_time) < {day_start:UInt8}
           OR toHour(c.start_date_time) >= {day_end:UInt8})
    UNION ALL
    SELECT c.call_date, c.end_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.end_cell_id != '' AND c.end_cell_id != c.start_cell_id
      AND (toHour(c.start_date_time) < {day_start:UInt8}
           OR toHour(c.start_date_time) >= {day_end:UInt8})
)
SELECT
    h.cell_id        AS cell_id,
    count()          AS records,
    any(t.address)   AS address,
    any(t.city)      AS city,
    any(t.state)     AS state,
    any(t.country)   AS country,
    any(t.latitude)  AS latitude,
    any(t.longitude) AS longitude
FROM cell_hits AS h
LEFT JOIN __CELL_TOWER__ AS t ON t.cell_id = h.cell_id
GROUP BY cell_id ORDER BY records DESC LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def locations_during_visits() -> Dict[str, Any]:
        return {
            "description": (
                "Top cities visited, aggregated -- geo-resolved via dim_cell_tower "
                "(both start_cell_id and end_cell_id), not the raw roaming_circle "
                "CDR field, which is often blank. Same signal and same aggregation "
                "level as ai_insights.cities_visited; kept here too since Section 2 "
                "(Locations) is where a 'during visits' city ranking is expected. "
                "`records` is countDistinct(call_id) -- see cities_visited()'s "
                "docstring for why count() over cell_hits is wrong here (a call "
                "with two different towers in the same city must not count twice)."
            ),
            "clickhouse_query": """
WITH cell_hits AS (
    SELECT c.call_id, c.call_date, c.start_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.start_cell_id != ''
    UNION ALL
    SELECT c.call_id, c.call_date, c.end_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.end_cell_id != '' AND c.end_cell_id != c.start_cell_id
)
SELECT
    t.city                    AS city,
    t.state                   AS state,
    t.country                 AS country,
    countDistinct(h.call_id)  AS records,
    countDistinct(h.cell_id)  AS distinct_towers,
    min(h.call_date)          AS first_seen,
    max(h.call_date)          AS last_seen,
    any(t.address)            AS address,
    any(t.latitude)           AS latitude,
    any(t.longitude)          AS longitude
FROM cell_hits AS h
INNER JOIN __CELL_TOWER__ AS t ON t.cell_id = h.cell_id
WHERE t.city != ''
GROUP BY city, state, country
ORDER BY records DESC LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 3: IMEI
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def imei_devices() -> Dict[str, Any]:
        return {
            "description": (
                "IMEI (handset) usage timeline: one row per continuous stretch "
                "of the same IMEI, in time order -- a new row starts every time "
                "the IMEI changes, so A -> B -> A -> C -> B yields five rows -- "
                "each resolved to brand/model/type/os via TAC "
                "(master.Tac_DB_master). Same logic as imsi.subscribers (this replaced the old one-row-per-IMEI summary). Stray "
                "single quotes are stripped from the CDR value (the ingest "
                "stores some quoted), which also lets the TAC lookup match."
            ),
            "clickhouse_query": """
SELECT
    s.imei                 AS imei,
    s.first_seen           AS first_seen,
    s.last_seen            AS last_seen,
    s.records              AS records,
    any(d.manufacturer)    AS brand,
    any(d.Model)           AS model,
    any(d.Type)            AS device_type,
    any(d.OS)              AS os
FROM (
    SELECT
        dev                    AS imei,
        min(start_date_time)   AS first_seen,
        max(start_date_time)   AS last_seen,
        count()                AS records
    FROM (
        SELECT dev, start_date_time,
               sum(is_new) OVER (ORDER BY start_date_time, call_id
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS stretch
        FROM (
            SELECT dev, start_date_time, call_id,
                   dev != lagInFrame(dev, 1, '') OVER (ORDER BY start_date_time, call_id
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS is_new
            FROM (
                SELECT replaceAll(c.imei, char(39), '') AS dev,
                       c.start_date_time AS start_date_time,
                       c.call_id AS call_id
                FROM fact_call_detail AS c
                WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
                  AND c.imei != ''
            )
        )
    )
    GROUP BY stretch, dev
) AS s
LEFT JOIN master.Tac_DB_master AS d
    ON d.tac_code = toUInt32OrZero(substring(s.imei, 1, 8)) AND d.is_current = 1
GROUP BY s.imei, s.first_seen, s.last_seen, s.records
ORDER BY s.first_seen
            """
        }

    @staticmethod
    def imei_dim_hits() -> Dict[str, Any]:
        return {
            "description": "IMEI watchlist hits from the LEA dim_imei_legacy table",
            "clickhouse_query": """
SELECT toString(`IMEI`) AS imei, `Case Reference` AS case_reference,
       `Group` AS group_name, `Analyst Name` AS analyst,
       `Brief description` AS description, `timestamp`
FROM dim_imei_legacy
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 3A: IMSI
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def imsi_subscribers() -> Dict[str, Any]:
        return {
            "description": (
                "IMSI (SIM) usage timeline: one row per continuous stretch of "
                "the same IMSI, in time order, so a number that used IMSI A, "
                "switched to B, then back to A yields three rows (A, B, A) "
                "rather than one row per distinct IMSI. Two cleanups on the "
                "CDR values: stray single quotes are stripped (the ingest "
                "stores them quoted), and placeholder IMSIs ending in nine "
                "zeros (e.g. 405864000000000, an unresolved-IMSI filler on "
                "outgoing records) are dropped -- otherwise they interleave "
                "with the real IMSI and split every stretch."
            ),
            "clickhouse_query": """
SELECT
    sim                    AS imsi,
    min(start_date_time)   AS first_seen,
    max(start_date_time)   AS last_seen,
    count()                AS records
FROM (
    SELECT sim, start_date_time,
           sum(is_new) OVER (ORDER BY start_date_time, call_id
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS stretch
    FROM (
        SELECT sim, start_date_time, call_id,
               sim != lagInFrame(sim, 1, '') OVER (ORDER BY start_date_time, call_id
                             ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS is_new
        FROM (
            SELECT replaceAll(c.imsi, char(39), '') AS sim,
                   c.start_date_time AS start_date_time,
                   c.call_id AS call_id
            FROM fact_call_detail AS c
            WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
              AND c.imsi != ''
        )
        WHERE NOT endsWith(sim, '000000000')
    )
)
GROUP BY stretch, sim
ORDER BY first_seen
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 4: Last Call / SMS
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def last_call() -> Dict[str, Any]:
        return {
            "description": "The single most recent call (voice) record for this number, with resolved tower location",
            "clickhouse_query": """
SELECT
    c.call_type       AS call_type,
    if(c.caller = {msisdn:String}, 'outgoing', 'incoming') AS direction,
    if(c.caller = {msisdn:String}, c.receiver, c.caller)   AS counterpart,
    c.start_date_time AS start_time,
    c.end_date_time   AS end_time,
    c.duration        AS duration_seconds,
    c.start_cell_id   AS cell_id,
    any(t.address)    AS address,
    any(t.city)       AS city,
    any(t.latitude)   AS latitude,
    any(t.longitude)  AS longitude
FROM fact_call_detail AS c
LEFT JOIN __CELL_TOWER__ AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.call_type NOT LIKE '%sms%'
GROUP BY call_type, direction, counterpart, start_time, end_time,
         duration_seconds, cell_id
ORDER BY start_time DESC
LIMIT 1
            """
        }

    @staticmethod
    def last_sms() -> Dict[str, Any]:
        return {
            "description": "The single most recent SMS record for this number, with resolved tower location",
            "clickhouse_query": """
SELECT
    c.call_type       AS call_type,
    if(c.caller = {msisdn:String}, 'outgoing', 'incoming') AS direction,
    if(c.caller = {msisdn:String}, c.receiver, c.caller)   AS counterpart,
    c.start_date_time AS start_time,
    c.end_date_time   AS end_time,
    c.duration        AS duration_seconds,
    c.start_cell_id   AS cell_id,
    any(t.address)    AS address,
    any(t.city)       AS city,
    any(t.latitude)   AS latitude,
    any(t.longitude)  AS longitude
FROM fact_call_detail AS c
LEFT JOIN __CELL_TOWER__ AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.call_type LIKE '%sms%'
GROUP BY call_type, direction, counterpart, start_time, end_time,
         duration_seconds, cell_id
ORDER BY start_time DESC
LIMIT 1
            """
        }

    @staticmethod
    def last_p2p_sms() -> Dict[str, Any]:
        return {
            "description": (
                "The most recent person-to-person SMS for this number, with the "
                "resolved location of the tower it was on. Person-to-person = the "
                "other party is a real phone number: numeric, at least 10 digits "
                "(so 5-digit short codes are out), not matched to a DLT sender in "
                "master.dim_sms_sender (sms_sender_id = 0), and not a 140-series "
                "telemarketing number. Alphanumeric headers like AT-AIRTEL-S are "
                "therefore never P2P. Direction says whether the target sent or "
                "received it."
            ),
            "clickhouse_query": """
SELECT
    c.call_type       AS call_type,
    if(c.caller = {msisdn:String}, 'outgoing', 'incoming') AS direction,
    if(c.caller = {msisdn:String}, c.receiver, c.caller)   AS counterpart,
    c.start_date_time AS start_time,
    c.start_cell_id   AS cell_id,
    any(t.address)    AS address,
    any(t.city)       AS city,
    any(t.latitude)   AS latitude,
    any(t.longitude)  AS longitude
FROM fact_call_detail AS c
LEFT JOIN __CELL_TOWER__ AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.call_type LIKE '%sms%'
  AND c.sms_sender_id = 0
  AND match(if(c.caller = {msisdn:String}, c.receiver, c.caller), '^[0-9]{10,}$')
  AND NOT startsWith(right(if(c.caller = {msisdn:String}, c.receiver, c.caller), 10), '140')
GROUP BY call_type, direction, counterpart, start_time, cell_id
ORDER BY start_time DESC
LIMIT 1
            """
        }

    @staticmethod
    def recent_activity() -> Dict[str, Any]:
        return {
            "description": "Top N most recent call/SMS records combined (context around last_call/last_sms), with resolved tower location",
            "clickhouse_query": """
SELECT
    c.call_type       AS call_type,
    if(c.caller = {msisdn:String}, 'outgoing', 'incoming') AS direction,
    if(c.caller = {msisdn:String}, c.receiver, c.caller)   AS counterpart,
    c.start_date_time AS start_time,
    c.end_date_time   AS end_time,
    c.duration        AS duration_seconds,
    c.start_cell_id   AS cell_id,
    any(t.address)    AS address,
    any(t.city)       AS city,
    any(t.latitude)   AS latitude,
    any(t.longitude)  AS longitude
FROM fact_call_detail AS c
LEFT JOIN __CELL_TOWER__ AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
GROUP BY call_type, direction, counterpart, start_time, end_time,
         duration_seconds, cell_id
ORDER BY start_time DESC
LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 5/6: Financial & E-Commerce Locators
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def financial_sms() -> Dict[str, Any]:
        return {
            "description": (
                "SMS from financial/bank senders, resolved via dim_sms_sender's "
                "DLT categorization (categorization = 'Transactional') rather than "
                "a hardcoded sender-name list."
            ),
            "clickhouse_query": """
SELECT
    s.sender_name    AS sender,
    s.sender_id      AS sender_id,
    s.categorization AS category,
    count()          AS messages,
    min(c.start_date_time) AS first_seen,
    max(c.start_date_time) AS last_seen
FROM fact_call_detail AS c
INNER JOIN master.dim_sms_sender AS s ON s.sms_sender_id = c.sms_sender_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND s.categorization = 'Transactional'
GROUP BY sender, sender_id, category
ORDER BY messages DESC
            """
        }

    @staticmethod
    def ecommerce_sms() -> Dict[str, Any]:
        return {
            "description": (
                "Call/SMS traffic from e-commerce senders, resolved via "
                "dim_sms_sender's DLT categorization (categorization = "
                "'Promotional') rather than a hardcoded sender-name list."
            ),
            "clickhouse_query": """
SELECT
    s.sender_name    AS sender,
    s.sender_id      AS sender_id,
    s.categorization AS category,
    countIf(c.call_type LIKE '%sms%')  AS messages,
    countIf(c.call_type NOT LIKE '%sms%') AS calls,
    min(c.start_date_time) AS first_seen,
    max(c.start_date_time) AS last_seen
FROM fact_call_detail AS c
INNER JOIN master.dim_sms_sender AS s ON s.sms_sender_id = c.sms_sender_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND s.categorization = 'Promotional'
GROUP BY sender, sender_id, category
ORDER BY messages DESC
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 7: Connectivity
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def connectivity_totals() -> Dict[str, Any]:
        return {
            "description": "Aggregate outgoing/incoming call and SMS totals, durations and distinct counterparts",
            "clickhouse_query": """
SELECT
    countIf(caller = {msisdn:String} AND call_type NOT LIKE '%sms%')   AS outgoing_calls,
    countIf(receiver = {msisdn:String} AND call_type NOT LIKE '%sms%') AS incoming_calls,
    countIf(caller = {msisdn:String} AND call_type LIKE '%sms%')       AS outgoing_sms,
    countIf(receiver = {msisdn:String} AND call_type LIKE '%sms%')     AS incoming_sms,
    sumIf(duration, caller = {msisdn:String})                          AS outgoing_duration_seconds,
    sumIf(duration, receiver = {msisdn:String})                        AS incoming_duration_seconds,
    countDistinct(if(caller = {msisdn:String}, receiver, caller))       AS distinct_counterparts,
    count()                                                            AS total_records
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
            """
        }

    @staticmethod
    def connectivity_roaming() -> Dict[str, Any]:
        return {
            "description": (
                "Record counts per roaming circle -- same logic as "
                "ai_insights.roaming_summary/city_visits: a record counts as "
                "roaming only when roaming_circle is set AND differs from the "
                "subscriber's home circle ({home_circle:String})."
            ),
            "clickhouse_query": """
SELECT roaming_circle AS circle, count() AS records
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
  AND roaming_circle != ''
  AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
GROUP BY circle ORDER BY records DESC
            """
        }

    @staticmethod
    def connectivity_top_counterparts() -> Dict[str, Any]:
        return {
            "description": "Top counterparts by total call/SMS volume, with duration and first/last contact",
            "clickhouse_query": """
SELECT
    if(caller = {msisdn:String}, receiver, caller) AS counterpart,
    count()                                        AS total,
    countIf(caller = {msisdn:String})              AS outgoing,
    countIf(receiver = {msisdn:String})            AS incoming,
    countIf(call_type LIKE '%sms%')                AS sms,
    sum(duration)                                  AS duration_seconds,
    min(start_date_time)                           AS first_contact,
    max(start_date_time)                           AS last_contact
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
GROUP BY counterpart ORDER BY total DESC LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def _b_party_locations_sql(order: str) -> str:
        """Shared body for the most/least contacted B-party location blocks.
        `order` is 'DESC' (most contacted) or 'ASC' (least contacted)."""
        return """
SELECT
    {msisdn:String}                        AS target_number,
    s.location                             AS b_party_location,
    any(s.circle)                          AS b_party_circle,
    count()                                AS records,
    uniqExact(c.b_party)                   AS distinct_b_parties,
    groupUniqArray(c.b_party)              AS b_parties,
    arrayElement(topK(1)(c.imei), 1)       AS imei,
    groupUniqArray(c.imei)                 AS imeis
FROM (
    SELECT
        if(caller = {msisdn:String}, receiver, caller)          AS b_party,
        right(if(caller = {msisdn:String}, receiver, caller), 10) AS b_last10,
        replaceAll(imei, char(39), '')                          AS imei
    FROM fact_call_detail
    WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
) AS c
INNER JOIN (
    SELECT
        right(toString(SUBSCRIBER_NUMBER), 10) AS last10,
        any(coalesce(nullIf(LOCAL_ADDRESS, ''), nullIf(PERMANENT_ADDRESS, ''))) AS location,
        any(CIRCLE)                            AS circle
    FROM dim_sdr
    WHERE SUBSCRIBER_NUMBER IS NOT NULL
    GROUP BY last10
    HAVING location IS NOT NULL
) AS s ON s.last10 = c.b_last10
WHERE c.imei != ''
GROUP BY s.location
ORDER BY records """ + order + """, s.location
LIMIT {b_party_locations_limit:UInt32}
        """

    @staticmethod
    def connectivity_top_b_party_locations() -> Dict[str, Any]:
        return {
            "description": (
                "Most-contacted B-party locations. The location is the B-party's "
                "SDR address (LOCAL_ADDRESS, else PERMANENT_ADDRESS), so only "
                "B-parties that match an SDR record (last 10 digits) appear. One "
                "row per location, with the target number, the IMEI most used to "
                "contact it (`imei`) and every IMEI seen (`imeis`)."
            ),
            "clickhouse_query": TelecomReportMapping._b_party_locations_sql("DESC"),
        }

    @staticmethod
    def connectivity_least_b_party_locations() -> Dict[str, Any]:
        return {
            "description": (
                "Least-contacted B-party locations -- same rows and fields as "
                "top_b_party_locations, ordered ascending. When the target has "
                "few SDR-matched locations, the two lists overlap."
            ),
            "clickhouse_query": TelecomReportMapping._b_party_locations_sql("ASC"),
        }

    @staticmethod
    def connectivity_international() -> Dict[str, Any]:
        return {
            "description": "Counterparts resolved to a non-home country, i.e. international contacts",
            "clickhouse_query": """
SELECT
    counterpart,
    count() AS records,
    any(m.country_name) AS country,
    any(m.country_code) AS country_code
FROM (
    SELECT if(caller = {msisdn:String}, receiver, caller) AS counterpart,
           dim_country_master_id
    FROM fact_call_detail
    WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
) AS c
LEFT JOIN master.dim_country_master AS m ON m.dim_country_master_id = c.dim_country_master_id
WHERE m.country_code != {home_country:String} AND m.country_code != ''
GROUP BY counterpart ORDER BY records DESC LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 8: Sustel
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def sustel_matches() -> Dict[str, Any]:
        return {
            "description": (
                "Full Sustel watchlist record(s) for this number OR for any "
                "b-party (counterpart) it was in contact with on a case call. "
                "Runs on the `dims` connection with the b-party list passed in as "
                "{counterparts:Array(String)} (app/service.py's "
                "_lookup_counterparts), so it works when dims live in a different "
                "database/server than fact_call_detail. No field distinguishes a "
                "target hit from a b-party hit -- both are just Sustel matches for "
                "this case."
            ),
            "clickhouse_query": """
SELECT
    s.IDNo AS idno, s.SSSID AS sssid, toString(s.telno) AS telno, toString(s.telno0) AS telno_alt,
    s.orgcode, s.`Case` AS case_name, s.Orggroup AS orggroup, s.username,
    s.teladdress, s.country AS country, s.Other_Information AS other_information,
    s.reference, s.refdate, s.monitored,
    s.DateOf1stSanction AS first_sanction, s.validtill AS valid_till,
    s.Monitorby AS monitored_by, s.`Group` AS group_name,
    s.OfficerConcerned AS officer, s.remarks
FROM __SUSTEL__ AS s
WHERE toString(s.telno) = {msisdn:String} OR toString(s.telno0) = {msisdn:String}
   OR toString(s.telno) IN {counterparts:Array(String)} OR toString(s.telno0) IN {counterparts:Array(String)}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 9: Social Media
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def social_profile() -> Dict[str, Any]:
        return {
            "description": "Profiler-reported social handles (Telegram/Facebook/Twitter/UPI)",
            "clickhouse_query": """
SELECT
    `names` AS names, `gender`, `addresses`,
    `telegram`, `facebook`, `twitter`, `upi`, `MNP` AS mnp_flag
FROM dim_profiler
WHERE toString(`phone number`) = {msisdn:String}
LIMIT 1
            """
        }

    @staticmethod
    def social_truecaller_leak() -> Dict[str, Any]:
        return {
            "description": "Truecaller leak fields (name/email/operator/location/country/address/spam score) for the target number",
            "clickhouse_query": """
SELECT `name`, `email`, `operator`, `location`, `country`, `address`, `spam score` AS spam_score
FROM dim_truecaller
WHERE toString(`phone number`) = {msisdn:String}
LIMIT 1
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 10: Cross Reference
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def cross_reference_other_cases() -> Dict[str, Any]:
        return {
            "description": "This number's activity in other cases (excludes the current case_id)",
            "clickhouse_query": """
-- Columns are qualified with `c.` on purpose: ClickHouse resolves SELECT
-- aliases inside WHERE, so a bare `case_id` there would refer to the String
-- alias below and fail to compare against the UUID param.
SELECT
    toString(c.case_id) AS case_id,
    toString(c.file_id) AS file_id,
    c.data_source       AS data_source,
    c.subdomain         AS subdomain,
    count()             AS records,
    min(c.call_date)    AS first_seen,
    max(c.call_date)    AS last_seen
FROM fact_call_detail AS c
WHERE (c.caller = {msisdn:String} OR c.receiver = {msisdn:String}
       OR c.target_number = {msisdn:String})
  AND c.case_id != {case_id:UUID}
GROUP BY case_id, file_id, data_source, subdomain
ORDER BY records DESC LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # Assembly: wires the queries above into the sections/blocks shape the
    # app (app/mapping.py) consumes.
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_sections() -> List[Dict[str, Any]]:
        m = TelecomReportMapping
        return [
            {
                "id": "subscriber", "title": "Subscriber", "panel": "left",
                "blocks": [
                    {"key": "photo", "source": "dims", "kind": "one",
                     "sql": m.subscriber_photo()["clickhouse_query"]},
                    {"key": "subscriber", "source": "dims", "kind": "one",
                     "sql": m.subscriber_details()["clickhouse_query"]},
                ],
            },
            {
                "id": "name_alias", "title": "Name / Alias", "panel": "left",
                "blocks": [
                    {"key": "names", "source": "dims", "kind": "many",
                     "sql": m.name_alias_sources()["clickhouse_query"]},
                ],
            },
            {
                "id": "sim_brought_from", "title": "SIM Brought From", "panel": "left",
                "blocks": [
                    {"key": "point_of_sale", "source": "dims", "kind": "one",
                     "sql": m.point_of_sale()["clickhouse_query"]},
                ],
            },
            {
                "id": "alternate_numbers", "title": "Alternate Numbers", "panel": "left",
                "blocks": [
                    {"key": "numbers", "source": "dims", "kind": "many",
                     "sql": m.alternate_numbers()["clickhouse_query"]},
                ],
            },
            {
                "id": "ai_insights", "title": "Section 1 - AI Insights", "panel": "right",
                "blocks": [
                    {"key": "counterparts", "source": "facts", "kind": "many",
                     "sql": m.target_counterparts()["clickhouse_query"]},
                    {"key": "sustel_match", "source": "dims", "kind": "many",
                     "sql": m.sustel_match()["clickhouse_query"]},
                    {"key": "profiler_match", "source": "dims", "kind": "many",
                     "sql": m.profiler_match()["clickhouse_query"]},
                    {"key": "cri_match", "source": "dims", "kind": "many",
                     "sql": m.cri_match()["clickhouse_query"]},
                    {"key": "call_forwarding", "source": "facts", "kind": "value",
                     "sql": m.call_forwarding_anomaly()["clickhouse_query"]},
                    {"key": "roaming_summary", "source": "facts", "kind": "one",
                     "sql": m.roaming_summary()["clickhouse_query"]},
                    {"key": "home_circle", "source": "dims", "kind": "value",
                     "sql": m.home_circle()["clickhouse_query"]},
                    {"key": "city_visits", "source": "facts", "kind": "many",
                     "sql": m.city_visits()["clickhouse_query"]},
                    {"key": "cities_visited", "source": "facts", "kind": "many",
                     "sql": m.cities_visited()["clickhouse_query"]},
                    {"key": "activity_span", "source": "facts", "kind": "one",
                     "sql": m.activity_span()["clickhouse_query"]},
                    {"key": "silent_gaps", "source": "facts", "kind": "many",
                     "sql": m.silent_gaps()["clickhouse_query"]},
                ],
            },
            {
                "id": "locations", "title": "Section 2 - Locations", "panel": "right",
                "blocks": [
                    {"key": "day", "source": "facts", "kind": "many",
                     "sql": m.locations_day()["clickhouse_query"]},
                    {"key": "night", "source": "facts", "kind": "many",
                     "sql": m.locations_night()["clickhouse_query"]},
                    {"key": "during_visits", "source": "facts", "kind": "many",
                     "sql": m.locations_during_visits()["clickhouse_query"]},
                ],
            },
            {
                "id": "imei", "title": "Section 3 - IMEI", "panel": "right",
                "blocks": [
                    {"key": "devices", "source": "facts", "kind": "many",
                     "sql": m.imei_devices()["clickhouse_query"]},
                    {"key": "imei_dim_hits", "source": "dims", "kind": "many",
                     "sql": m.imei_dim_hits()["clickhouse_query"]},
                ],
            },
            {
                "id": "imsi", "title": "Section 3A - IMSI", "panel": "right",
                "blocks": [
                    {"key": "subscribers", "source": "facts", "kind": "many",
                     "sql": m.imsi_subscribers()["clickhouse_query"]},
                ],
            },
            {
                "id": "last_activity", "title": "Section 4 - Last Call / SMS", "panel": "right",
                "blocks": [
                    {"key": "last_call", "source": "facts", "kind": "one",
                     "sql": m.last_call()["clickhouse_query"]},
                    {"key": "last_sms", "source": "facts", "kind": "one",
                     "sql": m.last_sms()["clickhouse_query"]},
                    {"key": "last_p2p_sms", "source": "facts", "kind": "one",
                     "sql": m.last_p2p_sms()["clickhouse_query"]},
                    {"key": "recent_activity", "source": "facts", "kind": "many",
                     "sql": m.recent_activity()["clickhouse_query"]},
                ],
            },
            {
                "id": "financial_locators", "title": "Section 5 - Financial Locators", "panel": "right",
                "blocks": [
                    {"key": "bank_sms", "source": "facts", "kind": "many",
                     "sql": m.financial_sms()["clickhouse_query"]},
                ],
            },
            {
                "id": "ecommerce_locators", "title": "Section 6 - E-Commerce Locators", "panel": "right",
                "blocks": [
                    {"key": "ecommerce_sms", "source": "facts", "kind": "many",
                     "sql": m.ecommerce_sms()["clickhouse_query"]},
                ],
            },
            {
                "id": "connectivity", "title": "Section 7 - Connectivity", "panel": "right",
                "blocks": [
                    {"key": "totals", "source": "facts", "kind": "one",
                     "sql": m.connectivity_totals()["clickhouse_query"]},
                    {"key": "roaming", "source": "facts", "kind": "many",
                     "sql": m.connectivity_roaming()["clickhouse_query"]},
                    {"key": "top_counterparts", "source": "facts", "kind": "many",
                     "sql": m.connectivity_top_counterparts()["clickhouse_query"]},
                    {"key": "international", "source": "facts", "kind": "many",
                     "sql": m.connectivity_international()["clickhouse_query"]},
                    {"key": "top_b_party_locations", "source": "facts", "kind": "many",
                     "sql": m.connectivity_top_b_party_locations()["clickhouse_query"]},
                    {"key": "least_b_party_locations", "source": "facts", "kind": "many",
                     "sql": m.connectivity_least_b_party_locations()["clickhouse_query"]},
                ],
            },
            {
                "id": "sustel", "title": "Section 8 - Sustel", "panel": "right",
                "blocks": [
                    {"key": "matches", "source": "dims", "kind": "many",
                     "sql": m.sustel_matches()["clickhouse_query"]},
                ],
            },
            {
                "id": "social_media", "title": "Section 9 - Social Media", "panel": "right",
                "blocks": [
                    {"key": "profile", "source": "dims", "kind": "one",
                     "sql": m.social_profile()["clickhouse_query"]},
                    {"key": "truecaller_leak", "source": "dims", "kind": "one",
                     "sql": m.social_truecaller_leak()["clickhouse_query"]},
                ],
            },
            {
                "id": "cross_reference", "title": "Section 10 - Cross Reference", "panel": "right",
                "blocks": [
                    {"key": "other_cases", "source": "facts", "kind": "many",
                     "sql": m.cross_reference_other_cases()["clickhouse_query"]},
                ],
            },
        ]

    @staticmethod
    def get_mapping() -> Dict[str, Any]:
        """Load the full mapping: connections, tables, settings and sections."""
        return {
            "version": 1,
            "databases": TelecomReportMapping.get_databases(),
            "tables": TelecomReportMapping.get_tables(),
            "settings": TelecomReportMapping.get_settings(),
            "sections": TelecomReportMapping.get_sections(),
        }


# app/mapping.py imports this file by path and reads MAPPING off it.
MAPPING = TelecomReportMapping.get_mapping()
