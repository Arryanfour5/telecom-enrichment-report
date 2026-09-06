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

# Tunables referenced by the queries below.
SETTINGS: Dict[str, Any] = {
    "day_window_start": 6,
    "day_window_end": 20,
    "top_n": 10,
    "home_country_code": '91',
}

# Every section of the report, in order. To add one, append a dict here -- that
# is the only edit needed; the API picks it up with no code change.
#
#   id     -> URL segment for /api/v1/report/{case}/{msisdn}/section/{id}
#   panel  -> "left" or "right" (also selects which /left or /right call it joins)
#   blocks -> one entry per value in the section's JSON:
#       key    -> the JSON key in the response
#       source -> "facts" or "dims" (which ClickHouse connection to run on)
#       kind   -> many  : list of rows
#                 one   : single row object, or null  (add LIMIT 1)
#                 value : first column of the first row, as a scalar
#                 static: no SQL, returns `value` as-is
#       sql    -> plain ClickHouse SQL. Bare table names resolve against the
#                 connection's database; {name:Type} are bind params.
SECTIONS: List[Dict[str, Any]] = [
    {
        "id": "subscriber",
        "title": "Subscriber",
        "panel": "left",
        "blocks": [
            {
                # Core subscriber record from the SDR (subscriber data record) table
                "key": "subscriber",
                "source": "dims",
                "kind": "one",
                "sql": """
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
""",
            },
            {
                # Truecaller-reported name/operator/location/spam flag for the number
                "key": "truecaller",
                "source": "dims",
                "kind": "one",
                "sql": """
SELECT `name` AS truecaller_name, `operator`, `location`, `spam`
FROM dim_truecaller
WHERE toString(`phone number`) = {msisdn:String}
LIMIT 1
""",
            },
        ],
    },
    {
        "id": "name_alias",
        "title": "Name / Alias",
        "panel": "left",
        "blocks": [
            {
                # Every name/alias seen for the number, unioned across SDR, Truecaller,
                # phonebook, profiler and Sustel
                "key": "names",
                "source": "dims",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "sim_source",
        "title": "SIM Brought From",
        "panel": "left",
        "blocks": [
            {
                # Point-of-sale details for where the SIM was bought, from SDR
                "key": "point_of_sale",
                "source": "dims",
                "kind": "one",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "alternate_numbers",
        "title": "Alternate Numbers",
        "panel": "left",
        "blocks": [
            {
                # Alternate numbers linked to this subscriber, from SDR and Sustel
                "key": "numbers",
                "source": "dims",
                "kind": "many",
                "sql": """
SELECT 'SDR' AS source, toString(`ALTERNATE_NUMBER`) AS number
FROM dim_sdr WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String} AND toString(`ALTERNATE_NUMBER`) NOT IN ('', '0')
UNION ALL
SELECT 'Sustel', toString(`telnoO`)
FROM dim_sustel WHERE toString(`telno`) = {msisdn:String} AND toString(`telnoO`) NOT IN ('', '0')
""",
            },
        ],
    },
    {
        "id": "ai_insights",
        "title": "Section 1 - AI Insights",
        "panel": "right",
        "blocks": [
            {
                # Sustel watchlist row(s) matching this number, with the matched number
                # and case
                "key": "sustel_match",
                "source": "dims",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # Profiler row matching this number, with names and social handles
                "key": "profiler_match",
                "source": "dims",
                "kind": "one",
                "sql": """
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
""",
            },
            {
                # CRI row(s) matching this number (full or last-10-digit), with the
                # counterpart (b-party) resolved
                "key": "cri_match",
                "source": "dims",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # Call-forwarding related anomalies flagged by the shared anomaly
                # pipeline (fact_call_anomaly/dim_anomaly), matched dynamically by
                # name/category/type. There is no call-forwarding anomaly type in the
                # platform's current dim_anomaly catalog, so this returns empty today --
                # it starts reporting automatically if one is added.
                "key": "call_forwarding",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # How many distinct circles/records show this number roaming. A record
                # counts as roaming only when roaming_circle is set AND differs from the
                # subscriber's home circle (dim_sdr, passed in as {home_circle:String}
                # -- see app/service.py's _lookup_home_circle) -- a CDR that just
                # repeats the home circle in roaming_circle is not roaming.
                "key": "roaming_summary",
                "source": "facts",
                "kind": "one",
                "sql": """
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
""",
            },
            {
                # Subscriber's home circle from SDR, for comparison against the
                # roaming_summary/city_visits circles. dim_sdr lives on the `dims`
                # connection (a different ClickHouse server than fact_call_detail's
                # `facts` connection), so this is its own block rather than a SQL join
                # -- results are merged in Python, never joined cross-server.
                "key": "home_circle",
                "source": "dims",
                "kind": "value",
                "sql": """
SELECT `CIRCLE` AS circle
FROM dim_sdr
WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String}
LIMIT 1
""",
            },
            {
                # Roaming circles actually visited (roaming_circle set AND different
                # from the subscriber's home circle), with record counts and first/last
                # seen dates.
                "key": "city_visits",
                "source": "facts",
                "kind": "many",
                "sql": """
SELECT roaming_circle AS circle, count() AS records,
       min(call_date) AS first_seen, max(call_date) AS last_seen
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
  AND roaming_circle != ''
  AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
GROUP BY circle ORDER BY records DESC
""",
            },
            {
                # Distinct cities visited, resolved from every cell tower hit -- both
                # start_cell_id and end_cell_id, so a call that started in one cell and
                # ended in another counts both -- joined to dim_cell_tower for the full
                # address/city/state/country/lat/long.
                "key": "cities_visited",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # First/last activity dates, active days and silent days over the
                # observed span
                "key": "activity_span",
                "source": "facts",
                "kind": "one",
                "sql": """
SELECT
    min(call_date)                       AS first_activity,
    max(call_date)                       AS last_activity,
    countDistinct(call_date)             AS active_days,
    dateDiff('day', min(call_date), max(call_date)) + 1 AS span_days,
    dateDiff('day', min(call_date), max(call_date)) + 1 - countDistinct(call_date) AS silent_days
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
""",
            },
            {
                # Longest gaps with no activity between consecutive active days. Every
                # date-diff gap is still returned (nothing is dropped), but a gap whose
                # resumption day the shared anomaly pipeline actually flagged as a
                # silence-related anomaly is marked anomaly_flagged and sorted first.
                "key": "silent_gaps",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "locations",
        "title": "Section 2 - Locations",
        "panel": "right",
        "blocks": [
            {
                # Top cell towers hit during day-window hours -- both start_cell_id and
                # end_cell_id, so a call that moved between cells counts both -- with
                # full tower detail from dim_cell_tower.
                "key": "day",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # Top cell towers hit outside day-window hours -- both start_cell_id and
                # end_cell_id, so a call that moved between cells counts both -- with
                # full tower detail from dim_cell_tower.
                "key": "night",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # Top cities visited, aggregated -- geo-resolved via dim_cell_tower
                # (both start_cell_id and end_cell_id), not the raw roaming_circle CDR
                # field, which is often blank. Same signal and same aggregation level as
                # ai_insights.cities_visited; kept here too since Section 2 (Locations)
                # is where a 'during visits' city ranking is expected.
                "key": "during_visits",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "imei",
        "title": "Section 3 - IMEI",
        "panel": "right",
        "blocks": [
            {
                # Devices (IMEIs) used by this number, resolved to brand/model/type/os
                # via TAC
                "key": "devices",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # IMEI watchlist hits from the LEA dim_imei table
                "key": "imei_dim_hits",
                "source": "dims",
                "kind": "many",
                "sql": """
SELECT toString(`IMEI`) AS imei, `Case` AS case_name, `Reference` AS reference,
       `Group` AS group_name, `Analyst Name` AS analyst,
       `Brief description` AS description, `timestamp`
FROM dim_imei
""",
            },
        ],
    },
    {
        "id": "last_activity",
        "title": "Section 4 - Last Call / SMS",
        "panel": "right",
        "blocks": [
            {
                # The single most recent call (voice) record for this number, with
                # resolved tower location
                "key": "last_call",
                "source": "facts",
                "kind": "one",
                "sql": """
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
""",
            },
            {
                # The single most recent SMS record for this number, with resolved tower
                # location
                "key": "last_sms",
                "source": "facts",
                "kind": "one",
                "sql": """
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
""",
            },
            {
                # Top N most recent call/SMS records combined (context around
                # last_call/last_sms), with resolved tower location
                "key": "recent_activity",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "financial_locators",
        "title": "Section 5 - Financial Locators",
        "panel": "right",
        "blocks": [
            {
                # SMS from financial/bank senders, resolved via dim_sms_sender's DLT
                # categorization (categorization = 'Transactional') rather than a
                # hardcoded sender-name list.
                "key": "bank_sms",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "ecommerce_locators",
        "title": "Section 6 - E-Commerce Locators",
        "panel": "right",
        "blocks": [
            {
                # Call/SMS traffic from e-commerce senders, resolved via
                # dim_sms_sender's DLT categorization (categorization = 'Promotional')
                # rather than a hardcoded sender-name list.
                "key": "ecommerce_sms",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "connectivity",
        "title": "Section 7 - Connectivity",
        "panel": "right",
        "blocks": [
            {
                # Aggregate outgoing/incoming call and SMS totals, durations and
                # distinct counterparts
                "key": "totals",
                "source": "facts",
                "kind": "one",
                "sql": """
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
""",
            },
            {
                # Record counts per roaming circle -- same logic as
                # ai_insights.roaming_summary/city_visits: a record counts as roaming
                # only when roaming_circle is set AND differs from the subscriber's home
                # circle ({home_circle:String}).
                "key": "roaming",
                "source": "facts",
                "kind": "many",
                "sql": """
SELECT roaming_circle AS circle, count() AS records
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
  AND roaming_circle != ''
  AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
GROUP BY circle ORDER BY records DESC
""",
            },
            {
                # Top counterparts by total call/SMS volume, with duration and
                # first/last contact
                "key": "top_counterparts",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # Counterparts resolved to a non-home country, i.e. international
                # contacts
                "key": "international",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "sustel",
        "title": "Section 8 - Sustel",
        "panel": "right",
        "blocks": [
            {
                # Full Sustel watchlist record(s) for this number
                "key": "matches",
                "source": "dims",
                "kind": "many",
                "sql": """
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
""",
            },
        ],
    },
    {
        "id": "social_media",
        "title": "Section 9 - Social Media",
        "panel": "right",
        "blocks": [
            {
                # Profiler-reported social handles (Telegram/Facebook/Twitter/UPI)
                "key": "profile",
                "source": "dims",
                "kind": "one",
                "sql": """
SELECT
    `names` AS names, `gender`, `addresses`,
    `telegram`, `facebook`, `twitter`, `upi`, `MNP` AS mnp_flag
FROM dim_profiler
WHERE toString(`phone number`) = {msisdn:String}
LIMIT 1
""",
            },
            {
                # Truecaller leak fields
                # (name/email/operator/location/country/address/spam)
                "key": "truecaller_leak",
                "source": "dims",
                "kind": "one",
                "sql": """
SELECT `name`, `email`, `operator`, `location`, `country`, `address`, `spam`
FROM dim_truecaller
WHERE toString(`phone number`) = {msisdn:String}
LIMIT 1
""",
            },
        ],
    },
    {
        "id": "cross_reference",
        "title": "Section 10 - Cross Reference",
        "panel": "right",
        "blocks": [
            {
                # This number's activity in other cases (excludes the current case_id)
                "key": "other_cases",
                "source": "facts",
                "kind": "many",
                "sql": """
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
""",
            },
            {
                # Phonebook entries referencing this number
                "key": "phonebook_hits",
                "source": "dims",
                "kind": "many",
                "sql": """
SELECT toString(`Telephone`) AS telephone, `User` AS user_name, `Place` AS place,
       `District` AS district, `State` AS state, `Remarks` AS remarks,
       `Reference` AS reference
FROM dim_phonebook
WHERE toString(`Telephone`) = {msisdn:String}
""",
            },
            {
                # CRI records where this number is target, caller or called party
                # CRI records tied to this case's actual calls: the call's
                # counterpart (whichever of caller/receiver is not the target)
                # is compared against the CRI's calling and called numbers, so
                # matched_calls counts the calls with that watchlisted contact
                # rather than every call the target made.
                #
                # NOTE this block joins fact_call_detail (facts) to dim_cri
                # (dims) in one statement, so it needs both on the same
                # connection -- hence source "facts", where dim_cri is
                # currently reachable. If an offline deployment splits dims
                # onto a separate server this one block fails with a warning
                # (the rest of the report is unaffected); the dims-only
                # ai_insights.cri_match block still works there.
                "key": "cri_hits",
                "source": "facts",
                "kind": "many",
                "sql": """
SELECT
    c.tCRID                AS cri_id,
    c.tCRIID               AS crii_id,
    toString(c.tTargetNo)  AS target_no,
    toString(c.tcallingNo) AS calling_no,
    toString(c.tCalledNo)  AS called_no,
    c.tDate                AS call_date,
    c.tCallType            AS call_type,
    c.tCallDirection       AS direction,
    c.OfficerConcerned     AS officer,
    if(toString(cd.caller) IN (toString(c.tcallingNo), toString(c.tCalledNo)),
       'caller', 'receiver')                            AS matched_party,
    count()                                             AS matched_calls,
    min(cd.call_date)                                   AS first_seen,
    max(cd.call_date)                                   AS last_seen
FROM fact_call_detail cd
INNER JOIN dim_cri c
    ON toString(if(cd.caller = cd.target_number, cd.receiver, cd.caller))
       IN (toString(c.tcallingNo), toString(c.tCalledNo))
WHERE cd.case_id = {case_id:UUID} AND cd.target_number = {msisdn:String}
GROUP BY cri_id, crii_id, target_no, calling_no, called_no,
         call_date, call_type, direction, officer, matched_party
ORDER BY matched_calls DESC
""",
            },
        ],
    },
]

# app/mapping.py imports this file by path and reads MAPPING off it.
MAPPING: Dict[str, Any] = {
    "version": 1,
    "settings": SETTINGS,
    "sections": SECTIONS,
}
