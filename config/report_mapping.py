"""
Telecom Enrichment Report - schema mapping.

This file is the ONLY place that knows what the database looks like. Mount it
over /app/config/report_mapping.py in an offline deployment whose schema
differs and the API adapts without a code change or rebuild -- same pattern
as the CDR/IPDR query_templates.py modules: a plain Python module the app
imports by file path, edit it and restart (or hot-reload) to change behavior.

The SQL below is plain ClickHouse SQL -- edit a table or column name directly in
the query that uses it. Table names are bare (fact_call_detail, dim_sustel, ...)
and resolve against the database set on the connection, so a deployment whose
tables live under a different database name changes CH_FACTS_DATABASE /
CH_DIMS_DATABASE in the environment rather than editing every query. The two
master.* tables are written fully qualified because they sit in a different
database from the rest.

The only non-SQL syntax in a query is {name:Type} -- a ClickHouse server-side
bind param, passed as a value and never interpolated into the statement:
  {msisdn:String}, {case_id:UUID}, {limit:UInt32}, {day_start:UInt8},
  {day_end:UInt8}, {home_country:String}, {home_circle:String}

Every block declares which `source` (connection) it runs on, so facts and dims
may live on entirely separate ClickHouse servers.
"""
from typing import Any, Dict, List


class TelecomReportMapping:
    """Repository for the telecom enrichment report's schema mapping and queries."""

    @staticmethod
    def get_settings() -> Dict[str, Any]:
        """Tunables referenced by the queries below."""
        return {
            "day_window_start": 6,        # 06:00 - 20:00 counts as "day"
            "day_window_end": 20,
            "top_n": 10,
            "home_country_code": "91",
        }

    # ─────────────────────────────────────────────────────────────────────
    # LEFT PANEL queries
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def subscriber_details() -> Dict[str, Any]:
        return {
            "description": "Core subscriber record from the SDR (subscriber data record) table",
            "clickhouse_query": """
SELECT
    `SUBSCRIBER_NAME`   AS name,
    `DOB`               AS dob,
    `SIM_ACTV_DATE`     AS doa,
    `SERVICE_PROVIDER`  AS operator,
    `CIRCLE`            AS circle,
    `CON_TYPE`          AS connection_type,
    `SUB_STATUS`        AS subscriber_status,
    `CON_STATUS`        AS connection_status,
    `GENDER`            AS gender,
    `F_H_NAME`          AS father_husband_name,
    `LOCAL_ADDRESS`     AS local_address,
    `PERMANENT_ADDRESS` AS permanent_address,
    `EMAIL ID`          AS email,
    `IMSINO`            AS imsi
FROM dim_sdr
WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String}
LIMIT 1
            """
        }

    @staticmethod
    def truecaller_profile() -> Dict[str, Any]:
        return {
            "description": "Truecaller-reported name/operator/location/spam flag for the number",
            "clickhouse_query": """
SELECT `name` AS truecaller_name, `operator`, `location`, `spam`
FROM dim_truecaller
WHERE toString(`phone number`) = {msisdn:String}
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
FROM dim_phonebook WHERE toString(`Telephone`) = {msisdn:String} AND `User` != ''
UNION ALL
SELECT 'Profiler', `names`
FROM dim_profiler WHERE toString(`phone number`) = {msisdn:String} AND `names` != ''
UNION ALL
SELECT 'Sustel', `username`
FROM dim_sustel WHERE toString(`telno`) = {msisdn:String} AND `username` != ''
            """
        }

    @staticmethod
    def point_of_sale() -> Dict[str, Any]:
        return {
            "description": "Point-of-sale details for where the SIM was bought, from SDR",
            "clickhouse_query": """
SELECT
    `POS_CD`         AS pos_code,
    `POS_NAME`       AS pos_name,
    `POS_AGENT_NAME` AS pos_agent_name,
    `POS_ADDRESS`    AS pos_address,
    `CAF_NO`         AS caf_no,
    `PID_NO`         AS pid_no
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
SELECT 'Sustel', toString(`telnoO`)
FROM dim_sustel WHERE toString(`telno`) = {msisdn:String} AND toString(`telnoO`) NOT IN ('', '0')
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 1: AI Insights
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def sustel_match() -> Dict[str, Any]:
        return {
            "description": "Sustel watchlist row(s) matching this number, with the matched number and case",
            "clickhouse_query": """
SELECT
    toString(`telno`)       AS matched_number,
    toString(`telnoO`)      AS matched_number_alt,
    `Case`                  AS case_name,
    `orggroup`              AS org_group,
    `teladdress`            AS address,
    `Country`               AS country,
    `monitored`             AS monitored,
    `OfficerConcerned`      AS officer
FROM dim_sustel
WHERE toString(`telno`) = {msisdn:String} OR toString(`telnoO`) = {msisdn:String}
            """
        }

    @staticmethod
    def profiler_match() -> Dict[str, Any]:
        return {
            "description": "Profiler row matching this number, with names and social handles",
            "clickhouse_query": """
SELECT
    toString(`phone number`) AS matched_number,
    `names`        AS names,
    `gender`       AS gender,
    `telegram`     AS telegram,
    `facebook`     AS facebook,
    `twitter`      AS twitter,
    `upi`          AS upi
FROM dim_profiler
WHERE toString(`phone number`) = {msisdn:String}
LIMIT 1
            """
        }

    @staticmethod
    def cri_match() -> Dict[str, Any]:
        return {
            "description": "CRI row(s) matching this number (full or last-10-digit), with the counterpart (b-party) resolved",
            "clickhouse_query": """
SELECT
    `tCRID`         AS cri_id,
    `tDate`         AS call_date,
    `tCallType`     AS call_type,
    `tCallDirection` AS direction,
    if(toString(`tcallingNo`) = {msisdn:String}, toString(`tCalledNo`), toString(`tcallingNo`)) AS counterpart,
    `OfficerConcerned` AS officer
FROM dim_cri
WHERE toString(`tTargetNo`) = {msisdn:String}
   OR toString(`tcallingNo`) = {msisdn:String}
   OR toString(`tCalledNo`) = {msisdn:String}
   OR toString(`tRight10Target`) = right({msisdn:String}, 10)
            """
        }

    @staticmethod
    def call_forwarding_anomaly() -> Dict[str, Any]:
        return {
            "description": (
                "Call-forwarding related anomalies flagged by the shared anomaly "
                "pipeline (fact_call_anomaly/dim_anomaly), matched dynamically by "
                "name/category/type. There is no call-forwarding anomaly type in "
                "the platform's current dim_anomaly catalog, so this returns empty "
                "today -- it starts reporting automatically if one is added."
            ),
            "clickhouse_query": """
SELECT
    da.name        AS anomaly_name,
    da.category    AS anomaly_category,
    da.description AS anomaly_description,
    fca.anomaly_score AS anomaly_score,
    fca.risk_level    AS risk_level,
    c.start_date_time AS detected_at,
    if(c.caller = {msisdn:String}, c.receiver, c.caller) AS counterpart
FROM fact_call_anomaly AS fca
INNER JOIN dim_anomaly AS da ON da.anomaly_id = fca.anomaly_id
INNER JOIN fact_call_detail AS c ON c.call_id = fca.call_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND (da.name ILIKE '%forward%' OR da.category ILIKE '%forward%' OR da.anomaly_type ILIKE '%forward%')
ORDER BY c.start_date_time DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def cities_visited() -> Dict[str, Any]:
        return {
            "description": (
                "Distinct cities visited, resolved from every cell tower hit -- "
                "both start_cell_id and end_cell_id, so a call that started in one "
                "cell and ended in another counts both -- joined to dim_cell_tower "
                "for the full address/city/state/country/lat/long."
            ),
            "clickhouse_query": """
WITH cell_hits AS (
    SELECT call_date, start_cell_id AS cell_id
    FROM fact_call_detail
    WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
      AND start_cell_id != ''
    UNION ALL
    SELECT call_date, end_cell_id AS cell_id
    FROM fact_call_detail
    WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
      AND end_cell_id != '' AND end_cell_id != start_cell_id
)
SELECT
    t.city               AS city,
    t.state              AS state,
    t.country            AS country,
    any(t.address)       AS address,
    any(t.latitude)      AS latitude,
    any(t.longitude)     AS longitude,
    count()              AS records,
    countDistinct(h.cell_id) AS distinct_towers,
    min(h.call_date)     AS first_seen,
    max(h.call_date)     AS last_seen
FROM cell_hits AS h
INNER JOIN dim_cell_tower AS t ON t.cell_id = h.cell_id
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
LEFT JOIN dim_cell_tower AS t ON t.cell_id = h.cell_id
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
LEFT JOIN dim_cell_tower AS t ON t.cell_id = h.cell_id
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
                "(Locations) is where a 'during visits' city ranking is expected."
            ),
            "clickhouse_query": """
WITH cell_hits AS (
    SELECT c.call_date, c.start_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.start_cell_id != ''
    UNION ALL
    SELECT c.call_date, c.end_cell_id AS cell_id
    FROM fact_call_detail AS c
    WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
      AND c.end_cell_id != '' AND c.end_cell_id != c.start_cell_id
)
SELECT
    t.city                   AS city,
    t.state                  AS state,
    t.country                AS country,
    count()                  AS records,
    countDistinct(h.cell_id) AS distinct_towers,
    min(h.call_date)         AS first_seen,
    max(h.call_date)         AS last_seen,
    any(t.address)           AS address,
    any(t.latitude)          AS latitude,
    any(t.longitude)         AS longitude
FROM cell_hits AS h
INNER JOIN dim_cell_tower AS t ON t.cell_id = h.cell_id
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
            "description": "Devices (IMEIs) used by this number, resolved to brand/model/type/os via TAC",
            "clickhouse_query": """
SELECT
    c.imei                       AS imei,
    count()                      AS records,
    min(c.start_date_time)       AS first_seen,
    max(c.start_date_time)       AS last_seen,
    any(d.brand)                 AS brand,
    any(d.model)                 AS model,
    any(d.type)                  AS device_type,
    any(d.os)                    AS os
FROM fact_call_detail AS c
LEFT JOIN tac_db AS d ON d.code = toUInt32OrZero(substring(c.imei, 1, 8))
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.imei != ''
GROUP BY imei ORDER BY records DESC
            """
        }

    @staticmethod
    def imei_dim_hits() -> Dict[str, Any]:
        return {
            "description": "IMEI watchlist hits from the LEA dim_imei table",
            "clickhouse_query": """
SELECT toString(`IMEI`) AS imei, `Case` AS case_name, `Reference` AS reference,
       `Group` AS group_name, `Analyst Name` AS analyst,
       `Brief description` AS description, `timestamp`
FROM dim_imei
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
    any(t.city)       AS city,
    any(t.latitude)   AS latitude,
    any(t.longitude)  AS longitude
FROM fact_call_detail AS c
LEFT JOIN dim_cell_tower AS t ON t.cell_id = c.start_cell_id
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
    any(t.city)       AS city,
    any(t.latitude)   AS latitude,
    any(t.longitude)  AS longitude
FROM fact_call_detail AS c
LEFT JOIN dim_cell_tower AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.call_type LIKE '%sms%'
GROUP BY call_type, direction, counterpart, start_time, end_time,
         duration_seconds, cell_id
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
    any(t.city)       AS city,
    any(t.latitude)   AS latitude,
    any(t.longitude)  AS longitude
FROM fact_call_detail AS c
LEFT JOIN dim_cell_tower AS t ON t.cell_id = c.start_cell_id
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
            "description": "Full Sustel watchlist record(s) for this number",
            "clickhouse_query": """
SELECT
    `IDNO` AS idno, `SSSID` AS sssid, toString(`telno`) AS telno, toString(`telnoO`) AS telno_alt,
    `orgcode`, `Case` AS case_name, `orggroup`, `username`,
    `teladdress`, `Country` AS country, `Other_Information` AS other_information,
    `reference`, `refdate`, `monitored`,
    `Dateof1stSanction` AS first_sanction, `Validtill` AS valid_till,
    `Monitorby` AS monitored_by, `Group` AS group_name,
    `OfficerConcerned` AS officer, `remarks`
FROM dim_sustel
WHERE toString(`telno`) = {msisdn:String} OR toString(`telnoO`) = {msisdn:String}
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
            "description": "Truecaller leak fields (name/email/operator/location/country/address/spam)",
            "clickhouse_query": """
SELECT `name`, `email`, `operator`, `location`, `country`, `address`, `spam`
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

    @staticmethod
    def cross_reference_phonebook_hits() -> Dict[str, Any]:
        return {
            "description": "Phonebook entries referencing this number",
            "clickhouse_query": """
SELECT toString(`Telephone`) AS telephone, `User` AS user_name, `Place` AS place,
       `District` AS district, `State` AS state, `Remarks` AS remarks,
       `Reference` AS reference
FROM dim_phonebook
WHERE toString(`Telephone`) = {msisdn:String}
            """
        }

    @staticmethod
    def cross_reference_cri_hits() -> Dict[str, Any]:
        return {
            "description": "CRI records where this number is target, caller or called party",
            "clickhouse_query": """
SELECT `tCRID` AS cri_id, `tCRIID` AS crii_id, toString(`tTargetNo`) AS target_no,
       toString(`tcallingNo`) AS calling_no, toString(`tCalledNo`) AS called_no,
       `tDate` AS call_date, `tCallType` AS call_type,
       `tCallDirection` AS direction, `OfficerConcerned` AS officer
FROM dim_cri
WHERE toString(`tTargetNo`) = {msisdn:String}
   OR toString(`tcallingNo`) = {msisdn:String}
   OR toString(`tCalledNo`) = {msisdn:String}
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
                    # No photo source is configured yet. Map a table here and
                    # uncomment when one exists.
                    # {"key": "photo", "kind": "static", "value": None,
                    #  "note": "No photo source is configured. Map a table here when one exists."},
                    {"key": "subscriber", "source": "dims", "kind": "one",
                     "sql": m.subscriber_details()["clickhouse_query"]},
                    {"key": "truecaller", "source": "dims", "kind": "one",
                     "sql": m.truecaller_profile()["clickhouse_query"]},
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
                "id": "sim_source", "title": "SIM Brought From", "panel": "left",
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
                    {"key": "sustel_match", "source": "dims", "kind": "many",
                     "sql": m.sustel_match()["clickhouse_query"]},
                    {"key": "profiler_match", "source": "dims", "kind": "one",
                     "sql": m.profiler_match()["clickhouse_query"]},
                    {"key": "cri_match", "source": "dims", "kind": "many",
                     "sql": m.cri_match()["clickhouse_query"]},
                    {"key": "call_forwarding", "source": "facts", "kind": "many",
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
                "id": "last_activity", "title": "Section 4 - Last Call / SMS", "panel": "right",
                "blocks": [
                    {"key": "last_call", "source": "facts", "kind": "one",
                     "sql": m.last_call()["clickhouse_query"]},
                    {"key": "last_sms", "source": "facts", "kind": "one",
                     "sql": m.last_sms()["clickhouse_query"]},
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
                    {"key": "phonebook_hits", "source": "dims", "kind": "many",
                     "sql": m.cross_reference_phonebook_hits()["clickhouse_query"]},
                    {"key": "cri_hits", "source": "dims", "kind": "many",
                     "sql": m.cross_reference_cri_hits()["clickhouse_query"]},
                ],
            },
        ]

    @staticmethod
    def get_mapping() -> Dict[str, Any]:
        """Load the full mapping: settings and sections."""
        return {
            "version": 1,
            "settings": TelecomReportMapping.get_settings(),
            "sections": TelecomReportMapping.get_sections(),
        }


# app/mapping.py imports this file by path and reads MAPPING off it.
MAPPING = TelecomReportMapping.get_mapping()
