-- ############################################################################################################
-- QUERIES  >  REPORT   (66 queries)
-- ############################################################################################################

-- ============================================================================================================
-- FILE app/cell_tower_source.py   (1 queries)
-- ============================================================================================================

-- ------------------------------------------------------------------------------------------------------------
-- Q001  _probe_master / rows   [ClickHouse]
-- File:    app/cell_tower_source.py:89
-- Purpose: True / False: does master.dim_cell_tower exist.
-- Tables:  system.tables
-- ------------------------------------------------------------------------------------------------------------
SELECT count() AS n FROM system.tables WHERE database = {d:String} AND name = 'dim_cell_tower';

-- ============================================================================================================
-- FILE app/pg.py   (1 queries)
-- ============================================================================================================

-- ------------------------------------------------------------------------------------------------------------
-- Q002  _HEADER_SQL   [PostgreSQL]
-- File:    app/pg.py:23
-- Purpose: One row per file kind of this number in this case.
-- Tables:  postgres: cases, postgres: units, postgres: telecom_metadata, postgres: files
-- ------------------------------------------------------------------------------------------------------------
SELECT c.reference_name, c.reference_date, c.description, c.notes, c.officer_name, u.name AS unit_name,
       f.subdomain AS kind,
       min(t.period_start) AS requested_from, max(t.period_end) AS requested_to,
       min(t.actual_start) AS actual_from,    max(t.actual_end) AS actual_to
FROM cases AS c
LEFT JOIN units AS u ON u.unit_id = c.group_unit_id
LEFT JOIN telecom_metadata AS t ON t.case_id = c.case_id AND right(t.msisdn, 10) = right(%(msisdn)s, 10)
LEFT JOIN files AS f ON f.file_id = t.file_id AND f.subdomain IN ('cdr', 'ipdr')
                    AND f.deleted_at IS NULL AND NOT f.is_trashed
WHERE c.case_id = %(case_id)s::uuid
GROUP BY c.case_id, u.unit_id, f.subdomain;

-- ============================================================================================================
-- FILE config/ipdr_report_mapping.py   (27 queries)
-- ============================================================================================================

-- ------------------------------------------------------------------------------------------------------------
-- Q003  ipdr_period   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:141   (SQL built with f-string: {...} are filled in by code)
-- Purpose: The IPDR period actually present for this number: first and last session, sessions.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT
    any(f.device_id)                                   AS msisdn,
    toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                                 AS actual_from,
    toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                                  AS actual_to,
    count()                                            AS sessions,
    uniqExactIf(f.session_date, f.start_date_time > toDateTime('2000-01-01 00:00:00'))    AS days_with_data,
    uniqExact(f.file_id)                               AS files
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10);

-- ------------------------------------------------------------------------------------------------------------
-- Q004  top_place   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:161   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Top place: the city the number spent the most sessions in, with its coordinates.
-- Tables:  analytics.fact_internet_sessions, (table name set by code: {_CELLS})
-- ------------------------------------------------------------------------------------------------------------
SELECT
    if(t.city != '', t.city, concat('cell ', f.cell_id))   AS place,
    any(t.state) AS state, any(t.country) AS country,
    any(t.latitude) AS latitude, any(t.longitude) AS longitude,
    count() AS sessions, uniqExact(f.cell_id) AS cells,
    toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen
FROM fact_internet_sessions AS f
LEFT JOIN (
    SELECT cell_id, any(ifNull(city, '')) AS city, any(ifNull(state, '')) AS state, any(country) AS country,
           nullIf(any(latitude), 0) AS latitude, nullIf(any(longitude), 0) AS longitude
    FROM (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
)
    WHERE cell_id IN (SELECT cell_id FROM fact_internet_sessions AS f WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10))
    GROUP BY cell_id
) AS t ON t.cell_id = f.cell_id
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND f.start_date_time > toDateTime('2000-01-01 00:00:00') AND ifNull(f.cell_id, '') != ''
GROUP BY place
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q005  internet_usage   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:188   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Usage of internet, FILE WISE: per ingested file, the megabytes moved per active day.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT
    toString(f.file_id)                                              AS file_id,
    toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                                               AS period_from,
    toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                                                AS period_to,
    count()                                                          AS sessions,
    uniqExactIf(toDate(f.start_date_time), f.start_date_time > toDateTime('2000-01-01 00:00:00'))       AS active_days,
    round(sum(ifNull(f.uplink_vol, 0) + ifNull(f.downlink_vol, 0)) / 1048576, 2)                                                  AS total_mb,
    round(total_mb / greatest(active_days, 1), 1)                    AS avg_daily_mb,
    multiIf(avg_daily_mb < {usage_low:Float64}, 'low', avg_daily_mb >= {usage_high:Float64}, 'high', 'medium') AS usage_level
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10)
GROUP BY f.file_id
ORDER BY period_from;

-- ------------------------------------------------------------------------------------------------------------
-- Q006  sustel_hits   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:213
-- Purpose: Which of the resolved B-party numbers are on the Sustel watchlist.
-- Tables:  scout_reference.sustel
-- ------------------------------------------------------------------------------------------------------------
SELECT
    right(toString(`telno`), 10)   AS matched_last10,
    `Case`                         AS case_name,
    `monitored`                    AS monitored,
    if(right(toString(`telno`), 10) = right({msisdn:String}, 10), 'target', 'b_party') AS match_type
FROM (SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, toInt64OrNull(nullIf(replaceRegexpAll(r.telno, '[^0-9]', ''), '')) AS telno, toInt64OrNull(nullIf(replaceRegexpAll(r.telno0, '[^0-9]', ''), '')) AS telno0, r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction FROM scout_reference.sustel AS r FINAL WHERE r._is_deleted = 0)
WHERE right(toString(`telno`), 10) = right({msisdn:String}, 10)
   OR right(toString(`telno`), 10) IN {counterparts_last10:Array(String)};

-- ------------------------------------------------------------------------------------------------------------
-- Q007  sustel_connectivity   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:229   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Connectivity with Sustel numbers: sessions whose resolved B-party is a Sustel number.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT
    right(f.b_party_number, 10)    AS number,
    count()                        AS sessions,
    toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))             AS first_seen,
    toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))              AS last_seen
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10)
  AND right(ifNull(f.b_party_number, ''), 10) IN {sustel_last10:Array(String)}
GROUP BY number
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q008  international_roaming   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:251   (SQL built with f-string: {...} are filled in by code)
-- Purpose: International roaming: the countries the serving cell was in.
-- Tables:  analytics.fact_internet_sessions, (table name set by code: {_CELLS})
-- ------------------------------------------------------------------------------------------------------------
SELECT
    t.country                                       AS country,
    count()                                         AS sessions,
    uniqExact(f.cell_id)                            AS serving_cells,
    arraySlice(groupUniqArray(t.city), 1, 5)        AS cities,
    toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                              AS first_seen,
    toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                               AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN (
    SELECT cell_id, any(ifNull(city, '')) AS city, any(ifNull(state, '')) AS state, any(country) AS country,
           nullIf(any(latitude), 0) AS latitude, nullIf(any(longitude), 0) AS longitude
    FROM (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
)
    WHERE cell_id IN (SELECT cell_id FROM fact_internet_sessions AS f WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10))
    GROUP BY cell_id
) AS t ON t.cell_id = f.cell_id
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND t.country != '' AND t.country != {home_country_name:String}
GROUP BY country
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q009  frequent_roaming   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:275   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Frequent roaming across states / countries.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT
    home_circle, roaming_circle, count() AS sessions, uniqExact(session_day) AS days,
    toString(min(ts)) AS first_seen, toString(max(ts)) AS last_seen
FROM (
    SELECT trimBoth(ifNull(f.home_circle, '')) AS home_circle,
           trimBoth(ifNull(f.home_roaming_circle, '')) AS roaming_circle,
           upper(trimBoth(ifNull(f.roaming_network_indicator, ''))) AS ind,
           f.session_date AS session_day, f.start_date_time AS ts
    FROM fact_internet_sessions AS f
    WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10)
)
WHERE (ind NOT IN ('', 'HOME')) OR (roaming_circle != '' AND home_circle != '' AND roaming_circle != home_circle)
GROUP BY home_circle, roaming_circle
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q010  city_visits   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:298   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Visits to cities with their count: the serving cell id resolved to a city through the.
-- Tables:  analytics.fact_internet_sessions, (table name set by code: {_CELLS})
-- ------------------------------------------------------------------------------------------------------------
SELECT
    t.city AS city, t.state AS state, t.country AS country,
    count()                                          AS sessions,
    uniqExact(f.session_date)                        AS visit_days,
    uniqExact(f.cell_id)                             AS cells,
    toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                               AS first_seen,
    toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                                AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN (
    SELECT cell_id, any(ifNull(city, '')) AS city, any(ifNull(state, '')) AS state, any(country) AS country,
           nullIf(any(latitude), 0) AS latitude, nullIf(any(longitude), 0) AS longitude
    FROM (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
)
    WHERE cell_id IN (SELECT cell_id FROM fact_internet_sessions AS f WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10))
    GROUP BY cell_id
) AS t ON t.cell_id = f.cell_id
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND t.city != ''
GROUP BY city, state, country
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q011  last_ipdr_date   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:319   (SQL built with f-string: {...} are filled in by code)
-- Purpose: The last date of the IPDR period for this number.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT toString(toDate(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))) AS last_ipdr_date
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10);

-- ------------------------------------------------------------------------------------------------------------
-- Q012  _locations   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:332   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Cells used by the number with their city, state and country.
-- Tables:  analytics.fact_internet_sessions, (table name set by code: {_CELLS})
-- ------------------------------------------------------------------------------------------------------------
SELECT
    f.cell_id AS cell_id, any(t.city) AS city, any(t.state) AS state, any(t.country) AS country,
    any(t.latitude) AS latitude, any(t.longitude) AS longitude,
    count()                                          AS sessions,
    toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                               AS first_seen,
    toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00')))                                AS last_seen
FROM fact_internet_sessions AS f
LEFT JOIN (
    SELECT cell_id, any(ifNull(city, '')) AS city, any(ifNull(state, '')) AS state, any(country) AS country,
           nullIf(any(latitude), 0) AS latitude, nullIf(any(longitude), 0) AS longitude
    FROM (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
)
    WHERE cell_id IN (SELECT cell_id FROM fact_internet_sessions AS f WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10))
    GROUP BY cell_id
) AS t ON t.cell_id = f.cell_id
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND f.start_date_time > toDateTime('2000-01-01 00:00:00') AND {period_filter}
GROUP BY f.cell_id
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q013  locations_international_cities   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:374   (SQL built with f-string: {...} are filled in by code)
-- Purpose: In a particular city: city name, latitude and longitude of the serving cells abroad.
-- Tables:  analytics.fact_internet_sessions, (table name set by code: {_CELLS})
-- ------------------------------------------------------------------------------------------------------------
SELECT
    t.city AS city, t.country AS country, any(t.latitude) AS latitude, any(t.longitude) AS longitude,
    count() AS sessions, toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN (
    SELECT cell_id, any(ifNull(city, '')) AS city, any(ifNull(state, '')) AS state, any(country) AS country,
           nullIf(any(latitude), 0) AS latitude, nullIf(any(longitude), 0) AS longitude
    FROM (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
)
    WHERE cell_id IN (SELECT cell_id FROM fact_internet_sessions AS f WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10))
    GROUP BY cell_id
) AS t ON t.cell_id = f.cell_id
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND t.country != '' AND t.country != {home_country_name:String} AND t.city != ''
GROUP BY city, country
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q014  imei_devices   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:398   (SQL built with f-string: {...} are filled in by code)
-- Purpose: IMEI usage in time order: one row per continuous stretch of the same IMEI.
-- Tables:  analytics.fact_internet_sessions, master.Tac_DB_master
-- ------------------------------------------------------------------------------------------------------------
SELECT
    s.imei AS imei, s.first_seen AS first_seen, s.last_seen AS last_seen, s.sessions AS sessions,
    any(d.manufacturer) AS brand, any(d.Model) AS model, any(d.Type) AS device_type, any(d.OS) AS os
FROM (
    SELECT dev AS imei, toString(min(ts)) AS first_seen, toString(max(ts)) AS last_seen, count() AS sessions
    FROM (
        SELECT dev, ts, sum(is_new) OVER (ORDER BY ts, rid ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS stretch
        FROM (
            SELECT dev, ts, rid,
                   dev != lagInFrame(dev, 1, '') OVER (ORDER BY ts, rid ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS is_new
            FROM (
                SELECT replaceAll(replaceAll(ifNull(f.imei, ''), '`', ''), char(39), '') AS dev, f.start_date_time AS ts, f.record_id AS rid
                FROM fact_internet_sessions AS f
                WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND f.start_date_time > toDateTime('2000-01-01 00:00:00') AND ifNull(f.imei, '') != ''
            )
        )
    )
    GROUP BY stretch, dev
) AS s
LEFT JOIN master.Tac_DB_master AS d ON d.tac_code = toUInt32OrZero(substring(s.imei, 1, 8)) AND d.is_current = 1
GROUP BY s.imei, s.first_seen, s.last_seen, s.sessions
ORDER BY s.first_seen;

-- ------------------------------------------------------------------------------------------------------------
-- Q015  imei_dim_hits   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:428
-- Purpose: Handsets of this number that an earlier case already noted, matched on the IMEI.
-- Tables:  analytics.dim_imei_legacy
-- ------------------------------------------------------------------------------------------------------------
SELECT toString(`IMEI`) AS imei, `Case Reference` AS case_reference, `Group` AS group_name,
       `Analyst Name` AS analyst, `Brief description` AS description, `timestamp`
FROM dim_imei_legacy
WHERE toString(`IMEI`) IN {imeis:Array(String)};

-- ------------------------------------------------------------------------------------------------------------
-- Q016  imsi_subscribers   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:443   (SQL built with f-string: {...} are filled in by code)
-- Purpose: IMSI usage in time order: one row per continuous stretch of the same IMSI.
-- Tables:  analytics.fact_internet_sessions, master.Tac_DB_master
-- ------------------------------------------------------------------------------------------------------------
SELECT
    s.imsi AS imsi, s.first_seen AS first_seen, s.last_seen AS last_seen, s.sessions AS sessions,
    s.imei AS imei, any(d.manufacturer) AS brand, any(d.Model) AS model
FROM (
    SELECT sim AS imsi, toString(min(ts)) AS first_seen, toString(max(ts)) AS last_seen, count() AS sessions,
           topK(1)(handset)[1] AS imei
    FROM (
        SELECT sim, ts, handset, sum(is_new) OVER (ORDER BY ts, rid ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS stretch
        FROM (
            SELECT sim, ts, rid, handset,
                   sim != lagInFrame(sim, 1, '') OVER (ORDER BY ts, rid ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS is_new
            FROM (
                SELECT replaceAll(replaceAll(ifNull(f.imsi, ''), '`', ''), char(39), '') AS sim,
                       f.start_date_time AS ts, f.record_id AS rid, replaceAll(replaceAll(ifNull(f.imei, ''), '`', ''), char(39), '') AS handset
                FROM fact_internet_sessions AS f
                WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND f.start_date_time > toDateTime('2000-01-01 00:00:00') AND ifNull(f.imsi, '') != ''
            )
            WHERE NOT endsWith(sim, '000000000')
        )
    )
    GROUP BY stretch, sim
) AS s
LEFT JOIN master.Tac_DB_master AS d ON d.tac_code = toUInt32OrZero(substring(s.imei, 1, 8)) AND d.is_current = 1
GROUP BY s.imsi, s.first_seen, s.last_seen, s.sessions, s.imei
ORDER BY s.first_seen;

-- ------------------------------------------------------------------------------------------------------------
-- Q017  b_party_locations   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:522
-- Purpose: B-party numbers with their city, resolved from the SDR address….
-- Tables:  analytics.dim_sdr
-- ------------------------------------------------------------------------------------------------------------
SELECT right(toString(`SUBSCRIBER_NUMBER`), 10) AS b_party_number,
       any(coalesce(nullIf(`LOCAL_ADDRESS`, ''), nullIf(`PERMANENT_ADDRESS`, ''))) AS location,
       any(`CIRCLE`) AS circle
FROM dim_sdr
WHERE right(toString(`SUBSCRIBER_NUMBER`), 10) IN {counterparts_last10:Array(String)}
GROUP BY b_party_number
ORDER BY b_party_number
;

-- ------------------------------------------------------------------------------------------------------------
-- Q018  applications   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:545   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Applications used with their session counts (WhatsApp, Snapchat.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT f.platform AS application, count() AS sessions, toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen,
       round(sum(ifNull(f.uplink_vol, 0) + ifNull(f.downlink_vol, 0)) / 1048576, 2) AS total_mb
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND f.platform NOT IN ('', 'unknown')
GROUP BY application
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q019  applications_totals   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:560   (SQL built with f-string: {...} are filled in by code)
-- Purpose: How many sessions were resolved to an application and how many were not.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT count() AS sessions, countIf(f.platform NOT IN ('', 'unknown')) AS resolved_sessions,
       countIf(f.platform IN ('', 'unknown')) AS unresolved_sessions
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10);

-- ------------------------------------------------------------------------------------------------------------
-- Q020  _usage_summary   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:570   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Usage summary: sessions per application / name with first and last seen.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT {name_expr} AS name, count() AS sessions, toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen,
       uniqExact(f.destination_ip) AS distinct_destination_ips, topK(3)(f.destination_port) AS top_ports
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND ({condition})
GROUP BY name
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q021  _usage_events   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:582   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Usage events: one row per session with application and destination.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT toString(f.start_date_time) AS at, {name_expr} AS name, f.destination_ip AS destination_ip,
       f.destination_port AS destination_port, f.duration AS duration_seconds
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND ({condition})
ORDER BY f.start_date_time DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q022  hotspot_indicators   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:647   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Hotspot usage.
-- Tables:  analytics.fact_internet_sessions, analytics.fact_ipdr_anomaly, analytics.dim_anomaly
-- ------------------------------------------------------------------------------------------------------------
SELECT indicator, sessions, distinct_source_ips, first_seen, last_seen, access_points FROM (
    SELECT 'concurrent_connections' AS indicator, count() AS sessions, uniqExact(f.source_ip) AS distinct_source_ips,
           toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen,
           arraySlice(groupUniqArray(ifNull(f.access_point, '')), 1, 5) AS access_points
    FROM fact_internet_sessions AS f
    WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10)
      AND f.record_id IN (
          SELECT record_id FROM fact_ipdr_anomaly
          WHERE anomaly_id IN (SELECT anomaly_id FROM dim_anomaly
                               WHERE anomaly_domain = 'ipdr' AND anomaly_type = 'multiple_concurrent_device_connections'))
    HAVING sessions > 0
    UNION ALL
    SELECT 'tethering_access_point' AS indicator, count() AS sessions, uniqExact(f.source_ip) AS distinct_source_ips,
           toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen,
           arraySlice(groupUniqArray(ifNull(f.access_point, '')), 1, 5) AS access_points
    FROM fact_internet_sessions AS f
    WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10)
      AND (positionCaseInsensitive(ifNull(f.access_point, ''), 'tether') > 0
           OR positionCaseInsensitive(ifNull(f.access_point, ''), 'hotspot') > 0
           OR positionCaseInsensitive(ifNull(f.access_point, ''), 'mobap') > 0)
    HAVING sessions > 0
);

-- ------------------------------------------------------------------------------------------------------------
-- Q023  _websites   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:679   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Websites reached: destination domains with organisation, sessions and volume.
-- Tables:  analytics.fact_internet_sessions, (table name set by code: {_DEST_IPS})
-- ------------------------------------------------------------------------------------------------------------
SELECT ip.as_domain AS site, any(ip.as_name) AS organisation, count() AS sessions,
       uniqExact(f.destination_ip) AS distinct_ips, toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN (
    SELECT address, argMax(country, created_at) AS country, argMax(city, created_at) AS city,
           argMax(organization, created_at) AS organization, argMax(as_name, created_at) AS as_name,
           argMax(as_domain, created_at) AS as_domain, argMax(asn, created_at) AS asn
    FROM dim_ip_address
    WHERE type = 'destination'
      AND address IN (SELECT destination_ip FROM fact_internet_sessions AS f WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10))
    GROUP BY address
) AS ip ON ip.address = f.destination_ip
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND has({domains_param}, lower(ip.as_domain))
GROUP BY site
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q024  b_party_numbers   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:728   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Resolved B-party numbers and the period they were in contact: the IPDR Destination IP.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT f.b_party_number AS b_party_number, f.b_party_type AS resolved_as, count() AS sessions,
       toString(minIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS first_seen, toString(maxIf(f.start_date_time, f.start_date_time > toDateTime('2000-01-01 00:00:00'))) AS last_seen
FROM fact_internet_sessions AS f
WHERE f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10) AND ifNull(f.b_party_number, '') != ''
GROUP BY b_party_number, resolved_as
ORDER BY sessions DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q025  b_party_sdr   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:746
-- Purpose: SDR subscriber name of each resolved B-party number, when there is an SDR record.
-- Tables:  analytics.dim_sdr
-- ------------------------------------------------------------------------------------------------------------
SELECT right(toString(`SUBSCRIBER_NUMBER`), 10) AS b_party_number,
       any(`SUBSCRIBER_NAME`) AS subscriber_name
FROM dim_sdr
WHERE right(toString(`SUBSCRIBER_NUMBER`), 10) IN {counterparts_last10:Array(String)}
GROUP BY b_party_number
ORDER BY b_party_number
;

-- ------------------------------------------------------------------------------------------------------------
-- Q026  b_party_sustel   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:765
-- Purpose: Sustel watchlist records of each resolved B-party number: the case and whether it is….
-- Tables:  scout_reference.sustel
-- ------------------------------------------------------------------------------------------------------------
SELECT number AS b_party_number, `Case` AS case_name, `monitored` AS monitored
FROM (
    SELECT arrayJoin([right(toString(`telno`), 10), right(toString(`telno0`), 10)]) AS number, `Case`, `monitored`
    FROM (SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, toInt64OrNull(nullIf(replaceRegexpAll(r.telno, '[^0-9]', ''), '')) AS telno, toInt64OrNull(nullIf(replaceRegexpAll(r.telno0, '[^0-9]', ''), '')) AS telno0, r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction FROM scout_reference.sustel AS r FINAL WHERE r._is_deleted = 0)
)
WHERE number IN {counterparts_last10:Array(String)}
GROUP BY number, case_name, monitored
ORDER BY number, case_name
;

-- ------------------------------------------------------------------------------------------------------------
-- Q027  sustel_matches   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:782
-- Purpose: Full Sustel watchlist record for this number or any resolved B-party number.
-- Tables:  scout_reference.sustel
-- ------------------------------------------------------------------------------------------------------------
SELECT
    s.IDNo AS idno, s.SSSID AS sssid, toString(s.telno) AS telno, toString(s.telno0) AS telno_alt,
    s.orgcode, s.`Case` AS case_name, s.Orggroup AS orggroup, s.username,
    s.teladdress, s.country AS country, s.Other_Information AS other_information,
    s.reference, s.refdate, s.monitored,
    s.DateOf1stSanction AS first_sanction, s.validtill AS valid_till,
    s.Monitorby AS monitored_by, s.`Group` AS group_name,
    s.OfficerConcerned AS officer, s.remarks,
    if(right(toString(s.telno), 10) = right({msisdn:String}, 10), 'target', 'b_party') AS match_type
FROM (SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, toInt64OrNull(nullIf(replaceRegexpAll(r.telno, '[^0-9]', ''), '')) AS telno, toInt64OrNull(nullIf(replaceRegexpAll(r.telno0, '[^0-9]', ''), '')) AS telno0, r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction FROM scout_reference.sustel AS r FINAL WHERE r._is_deleted = 0) AS s
WHERE right(toString(s.telno), 10) = right({msisdn:String}, 10)
   OR right(toString(s.telno0), 10) = right({msisdn:String}, 10)
   OR right(toString(s.telno), 10) IN {counterparts_last10:Array(String)}
   OR right(toString(s.telno0), 10) IN {counterparts_last10:Array(String)};

-- ------------------------------------------------------------------------------------------------------------
-- Q028  cross_reference_other_cases   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:804
-- Purpose: This number's IPDR in other cases.
-- Tables:  analytics.fact_internet_sessions
-- ------------------------------------------------------------------------------------------------------------
SELECT toString(f.case_id) AS case_id, toString(f.file_id) AS file_id, f.data_source AS data_source,
       count() AS records, min(f.session_date) AS first_seen, max(f.session_date) AS last_seen
FROM fact_internet_sessions AS f
WHERE right(f.device_id, 10) = right({msisdn:String}, 10) AND f.case_id != {case_id:UUID}
GROUP BY case_id, file_id, data_source
ORDER BY records DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q029  cross_reference_other_cases_cdr   [ClickHouse]
-- File:    config/ipdr_report_mapping.py:819
-- Purpose: This number's CDR in any case: the same subscriber seen in call records.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
SELECT toString(c.case_id) AS case_id, toString(c.file_id) AS file_id, c.data_source AS data_source,
       count() AS records, min(c.call_date) AS first_seen, max(c.call_date) AS last_seen
FROM fact_call_detail AS c
WHERE right(c.target_number, 10) = right({msisdn:String}, 10)
GROUP BY case_id, file_id, data_source
ORDER BY records DESC
;

-- ============================================================================================================
-- FILE config/report_mapping.py   (37 queries)
-- ============================================================================================================

-- ------------------------------------------------------------------------------------------------------------
-- Q030  subscriber_details   [ClickHouse]
-- File:    config/report_mapping.py:96
-- Purpose: Core subscriber record from the SDR table, with the Truecaller-reported name.
-- Tables:  analytics.dim_sdr, analytics.dim_truecaller
-- ------------------------------------------------------------------------------------------------------------
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
;

-- ------------------------------------------------------------------------------------------------------------
-- Q031  subscriber_photo   [ClickHouse]
-- File:    config/report_mapping.py:129
-- Purpose: Subscriber photo from dim_sdr, matched on the target's number: base64-encoded image plus.
-- Tables:  analytics.dim_sdr
-- ------------------------------------------------------------------------------------------------------------
SELECT nullIf(photo1_base64, '') AS photo_base64,
       'image/jpeg' AS photo_mime_type
FROM dim_sdr
WHERE right(toString(SUBSCRIBER_NUMBER), 10) = right({msisdn:String}, 10)
  AND ifNull(photo1_base64, '') != ''
;

-- ------------------------------------------------------------------------------------------------------------
-- Q032  name_alias_sources   [ClickHouse]
-- File:    config/report_mapping.py:143
-- Purpose: Every name/alias seen for the number, unioned across SDR, Truecaller, phonebook.
-- Tables:  analytics.dim_sdr, analytics.dim_truecaller, scout_reference.phonebook, analytics.dim_profiler, scout_reference.sustel
-- ------------------------------------------------------------------------------------------------------------
SELECT 'SDR' AS source, `SUBSCRIBER_NAME` AS value
FROM dim_sdr WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String} AND `SUBSCRIBER_NAME` != ''
UNION ALL
SELECT 'Truecaller', `name`
FROM dim_truecaller WHERE toString(`phone number`) = {msisdn:String} AND `name` != ''
UNION ALL
SELECT 'Phonebook', `User`
FROM (SELECT r.phonebook_id AS id, toInt64OrZero(replaceRegexpAll(r.telephone, '[^0-9]', '')) AS Telephone, r.user_name AS `User`, r.place AS Place, r.district AS District, r.state AS State, r.country AS Country, r.remarks AS Remarks, r.reference AS Reference, toDate(r.ref_date) AS `Date`, toDateTime(r.entry_date) AS `Date of entry` FROM scout_reference.phonebook AS r FINAL WHERE r._is_deleted = 0) WHERE toString(`Telephone`) = {msisdn:String} AND `User` != ''
UNION ALL
SELECT 'Profiler', `names`
FROM dim_profiler WHERE toString(`phone number`) = {msisdn:String} AND `names` != ''
UNION ALL
SELECT 'Sustel', `username`
FROM (SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, toInt64OrNull(nullIf(replaceRegexpAll(r.telno, '[^0-9]', ''), '')) AS telno, toInt64OrNull(nullIf(replaceRegexpAll(r.telno0, '[^0-9]', ''), '')) AS telno0, r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction FROM scout_reference.sustel AS r FINAL WHERE r._is_deleted = 0) WHERE toString(`telno`) = {msisdn:String} AND `username` != '';

-- ------------------------------------------------------------------------------------------------------------
-- Q033  point_of_sale   [ClickHouse]
-- File:    config/report_mapping.py:165
-- Purpose: Point-of-sale address for where the SIM was bought, from SDR.
-- Tables:  analytics.dim_sdr
-- ------------------------------------------------------------------------------------------------------------
SELECT `POS_ADDRESS` AS pos_address
FROM dim_sdr
WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String}
;

-- ------------------------------------------------------------------------------------------------------------
-- Q034  alternate_numbers   [ClickHouse]
-- File:    config/report_mapping.py:177
-- Purpose: Alternate numbers linked to this subscriber, from SDR and Sustel.
-- Tables:  analytics.dim_sdr, scout_reference.sustel
-- ------------------------------------------------------------------------------------------------------------
SELECT 'SDR' AS source, toString(`ALTERNATE_NUMBER`) AS number
FROM dim_sdr WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String} AND toString(`ALTERNATE_NUMBER`) NOT IN ('', '0')
UNION ALL
SELECT 'Sustel', toString(`telno0`)
FROM (SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, toInt64OrNull(nullIf(replaceRegexpAll(r.telno, '[^0-9]', ''), '')) AS telno, toInt64OrNull(nullIf(replaceRegexpAll(r.telno0, '[^0-9]', ''), '')) AS telno0, r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction FROM scout_reference.sustel AS r FINAL WHERE r._is_deleted = 0) WHERE toString(`telno`) = {msisdn:String} AND toString(`telno0`) NOT IN ('', '0');

-- ------------------------------------------------------------------------------------------------------------
-- Q035  target_counterparts   [ClickHouse]
-- File:    config/report_mapping.py:202
-- Purpose: Distinct b-party numbers this target was in contact with.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
SELECT DISTINCT if(caller = {msisdn:String}, receiver, caller) AS counterpart
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
;

-- ------------------------------------------------------------------------------------------------------------
-- Q036  sustel_match   [ClickHouse]
-- File:    config/report_mapping.py:219
-- Purpose: Whether this number.
-- Tables:  scout_reference.sustel
-- ------------------------------------------------------------------------------------------------------------
SELECT
    `Case`                  AS case_name,
    `monitored`             AS monitored,
    multiIf(
        toString(`telno`) = {msisdn:String} OR toString(`telno0`) = {msisdn:String}, {msisdn:String},
        toString(`telno`) IN {counterparts:Array(String)}, toString(`telno`),
        toString(`telno0`)
    )                       AS matched_number
FROM (SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, toInt64OrNull(nullIf(replaceRegexpAll(r.telno, '[^0-9]', ''), '')) AS telno, toInt64OrNull(nullIf(replaceRegexpAll(r.telno0, '[^0-9]', ''), '')) AS telno0, r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction FROM scout_reference.sustel AS r FINAL WHERE r._is_deleted = 0)
WHERE toString(`telno`) = {msisdn:String} OR toString(`telno0`) = {msisdn:String}
   OR toString(`telno`) IN {counterparts:Array(String)} OR toString(`telno0`) IN {counterparts:Array(String)};

-- ------------------------------------------------------------------------------------------------------------
-- Q037  profiler_match   [ClickHouse]
-- File:    config/report_mapping.py:243
-- Purpose: Profiler row matching this number OR any b-party it was in contact with.
-- Tables:  analytics.dim_profiler
-- ------------------------------------------------------------------------------------------------------------
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
   OR toString(`phone number`) IN {counterparts:Array(String)};

-- ------------------------------------------------------------------------------------------------------------
-- Q038  cri_match   [ClickHouse]
-- File:    config/report_mapping.py:268
-- Purpose: CRI row matching this number OR any b-party it was in contact with.
-- Tables:  scout_reference.cri
-- ------------------------------------------------------------------------------------------------------------
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
FROM (SELECT r.cdr_id AS id, r.cdr_id AS tCDRID, r.cri_id AS tCRIID, toInt64OrNull(nullIf(replaceRegexpAll(r.target_no, '[^0-9]', ''), '')) AS tTargetNo, toInt64OrNull(nullIf(replaceRegexpAll(r.calling_no, '[^0-9]', ''), '')) AS tCallingNo, toInt64OrNull(nullIf(replaceRegexpAll(r.called_no, '[^0-9]', ''), '')) AS tCalledNo, toDateTime(r.call_date) AS tDate, r.call_type AS tCallType, r.call_direction AS tCallDirection, toInt64OrNull(nullIf(replaceRegexpAll(r.right10_target, '[^0-9]', ''), '')) AS tRight10Target, toInt64OrNull(nullIf(replaceRegexpAll(r.right10_calling_no, '[^0-9]', ''), '')) AS tRight10CallingNo, toInt64OrNull(nullIf(replaceRegexpAll(r.right10_called_no, '[^0-9]', ''), '')) AS tRight10CalledNo, toInt64OrNull(nullIf(replaceRegexpAll(r.right10_other_no, '[^0-9]', ''), '')) AS tRight10OtherNo, toInt64OrNull(nullIf(replaceRegexpAll(r.other_no, '[^0-9]', ''), '')) AS tOtherNo, '' AS Officerconcerned, parseDateTimeBestEffortOrNull(r.entered_on) AS EnterON FROM scout_reference.cri AS r FINAL WHERE r._is_deleted = 0)
WHERE toString(`tTargetNo`) = {msisdn:String}
   OR toString(`tCallingNo`) = {msisdn:String}
   OR toString(`tCalledNo`) = {msisdn:String}
   OR toString(`tRight10Target`) = right({msisdn:String}, 10)
   OR toString(`tTargetNo`) IN {counterparts:Array(String)}
   OR toString(`tCallingNo`) IN {counterparts:Array(String)}
   OR toString(`tCalledNo`) IN {counterparts:Array(String)};

-- ------------------------------------------------------------------------------------------------------------
-- Q039  call_forwarding_anomaly   [ClickHouse]
-- File:    config/report_mapping.py:315
-- Purpose: Whether any call-forwarding related anomaly has been flagged by the shared anomaly.
-- Tables:  analytics.fact_call_anomaly, analytics.dim_anomaly, analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
SELECT count() > 0 AS has_call_forwarding
FROM fact_call_anomaly AS fca
INNER JOIN dim_anomaly AS da ON da.anomaly_id = fca.anomaly_id
INNER JOIN fact_call_detail AS c ON c.call_id = fca.call_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND (da.name ILIKE '%forward%' OR da.category ILIKE '%forward%' OR da.anomaly_type ILIKE '%forward%');

-- ------------------------------------------------------------------------------------------------------------
-- Q040  cities_visited   [ClickHouse]
-- File:    config/report_mapping.py:340
-- Purpose: Distinct cities visited, resolved from every cell tower hit.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
INNER JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = h.cell_id
WHERE t.city != ''
GROUP BY city, state, country
ORDER BY records DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q041  roaming_summary   [ClickHouse]
-- File:    config/report_mapping.py:382
-- Purpose: How many distinct circles/records show this number roaming.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
SELECT
    countDistinctIf(roaming_circle,
        roaming_circle != '' AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
    ) AS distinct_circles,
    countIf(
        roaming_circle != '' AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
    ) AS roaming_records,
    count() AS total_records
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String};

-- ------------------------------------------------------------------------------------------------------------
-- Q042  home_circle   [ClickHouse]
-- File:    config/report_mapping.py:406
-- Purpose: Subscriber's home circle from SDR, for comparison against the roaming_summary/city_visits.
-- Tables:  analytics.dim_sdr
-- ------------------------------------------------------------------------------------------------------------
SELECT `CIRCLE` AS circle
FROM dim_sdr
WHERE toString(`SUBSCRIBER_NUMBER`) = {msisdn:String}
;

-- ------------------------------------------------------------------------------------------------------------
-- Q043  city_visits   [ClickHouse]
-- File:    config/report_mapping.py:422
-- Purpose: Roaming circles actually visited, with record counts and first/last seen dates.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
SELECT roaming_circle AS circle, count() AS records,
       min(call_date) AS first_seen, max(call_date) AS last_seen
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
  AND roaming_circle != ''
  AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
GROUP BY circle ORDER BY records DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q044  activity_span   [ClickHouse]
-- File:    config/report_mapping.py:437
-- Purpose: First/last activity dates, active days and silent days over the observed span.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
SELECT
    min(call_date)                       AS first_activity,
    max(call_date)                       AS last_activity,
    countDistinct(call_date)             AS active_days,
    dateDiff('day', min(call_date), max(call_date)) + 1 AS span_days,
    dateDiff('day', min(call_date), max(call_date)) + 1 - countDistinct(call_date) AS silent_days
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String};

-- ------------------------------------------------------------------------------------------------------------
-- Q045  silent_gaps   [ClickHouse]
-- File:    config/report_mapping.py:459
-- Purpose: Longest gaps with no activity between consecutive active days.
-- Tables:  analytics.fact_call_detail, analytics.fact_call_anomaly, analytics.dim_anomaly
-- ------------------------------------------------------------------------------------------------------------
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
;

-- ------------------------------------------------------------------------------------------------------------
-- Q046  locations_day   [ClickHouse]
-- File:    config/report_mapping.py:507
-- Purpose: Top cell towers hit during day-window hours.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
LEFT JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = h.cell_id
GROUP BY cell_id ORDER BY records DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q047  locations_night   [ClickHouse]
-- File:    config/report_mapping.py:546
-- Purpose: Top cell towers hit outside day-window hours.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
LEFT JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = h.cell_id
GROUP BY cell_id ORDER BY records DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q048  locations_during_visits   [ClickHouse]
-- File:    config/report_mapping.py:590
-- Purpose: Top cities visited, aggregated.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
INNER JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = h.cell_id
WHERE t.city != ''
GROUP BY city, state, country
ORDER BY records DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q049  imei_devices   [ClickHouse]
-- File:    config/report_mapping.py:637
-- Purpose: IMEI usage timeline: one row per continuous stretch of the same IMEI, in time order.
-- Tables:  analytics.fact_call_detail, master.Tac_DB_master
-- ------------------------------------------------------------------------------------------------------------
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
ORDER BY s.first_seen;

-- ------------------------------------------------------------------------------------------------------------
-- Q050  imei_dim_hits   [ClickHouse]
-- File:    config/report_mapping.py:684
-- Purpose: IMEI watchlist hits from the LEA dim_imei_legacy table.
-- Tables:  analytics.dim_imei_legacy
-- ------------------------------------------------------------------------------------------------------------
SELECT toString(`IMEI`) AS imei, `Case Reference` AS case_reference,
       `Group` AS group_name, `Analyst Name` AS analyst,
       `Brief description` AS description, `timestamp`
FROM dim_imei_legacy;

-- ------------------------------------------------------------------------------------------------------------
-- Q051  imsi_subscribers   [ClickHouse]
-- File:    config/report_mapping.py:710
-- Purpose: IMSI usage timeline: one row per continuous stretch of the same IMSI, in time order.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
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
ORDER BY first_seen;

-- ------------------------------------------------------------------------------------------------------------
-- Q052  last_call   [ClickHouse]
-- File:    config/report_mapping.py:748
-- Purpose: The single most recent call record for this number, with resolved tower location.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
LEFT JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.call_type NOT LIKE '%sms%'
GROUP BY call_type, direction, counterpart, start_time, end_time,
         duration_seconds, cell_id
ORDER BY start_time DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q053  last_sms   [ClickHouse]
-- File:    config/report_mapping.py:776
-- Purpose: The single most recent SMS record for this number, with resolved tower location.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
LEFT JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.call_type LIKE '%sms%'
GROUP BY call_type, direction, counterpart, start_time, end_time,
         duration_seconds, cell_id
ORDER BY start_time DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q054  last_p2p_sms   [ClickHouse]
-- File:    config/report_mapping.py:813
-- Purpose: The most recent person-to-person SMS for this number.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
LEFT JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
  AND c.call_type LIKE '%sms%'
  AND c.sms_sender_id = 0
  AND match(if(c.caller = {msisdn:String}, c.receiver, c.caller), '^[0-9]{10,}$')
  AND NOT startsWith(right(if(c.caller = {msisdn:String}, c.receiver, c.caller), 10), '140')
GROUP BY call_type, direction, counterpart, start_time, cell_id
ORDER BY start_time DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q055  recent_activity   [ClickHouse]
-- File:    config/report_mapping.py:841
-- Purpose: Top N most recent call/SMS records combined, with resolved tower location.
-- Tables:  analytics.fact_call_detail, analytics.dim_cell_tower (+ master.dim_cell_tower when it exists)
-- ------------------------------------------------------------------------------------------------------------
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
LEFT JOIN (SELECT CAST(a.cell_id AS LowCardinality(String)) AS cell_id,
        CAST(if(ifNull(a.address, '') = '' AND ifNull(m.site_address, '') != '', m.site_address, a.address) AS Nullable(String)) AS address,
        CAST(a.city AS Nullable(String)) AS city, CAST(a.state AS Nullable(String)) AS state,
        CAST(ifNull(toString(a.country), '') AS LowCardinality(String)) AS country, toUInt32OrNull(toString(a.postal_code)) AS postal_code,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.latitude, a.latitude) AS Nullable(Float64)) AS latitude,
        CAST(if(ifNull(a.latitude, 0) = 0 AND ifNull(a.longitude, 0) = 0 AND (ifNull(m.latitude, 0) != 0 OR ifNull(m.longitude, 0) != 0), m.longitude, a.longitude) AS Nullable(Float64)) AS longitude,
        toDateOrNull(toString(a.in_service_date)) AS in_service_date, toUInt16OrNull(toString(a.azimuth)) AS azimuth,
        CAST(a.created_at AS DateTime64(6)) AS created_at, CAST(a.updated_at AS DateTime64(6)) AS updated_at,
        if(a.mcc = 0, ifNull(m.mcc, 0), a.mcc) AS mcc, if(a.mnc = 0, ifNull(m.mnc, 0), a.mnc) AS mnc,
        if(a.lac = 0, ifNull(m.lac, 0), a.lac) AS lac, if(a.cell_code = 0, ifNull(m.ci, 0), a.cell_code) AS cell_code
    FROM "analytics"."dim_cell_tower" AS a FINAL
    LEFT JOIN (SELECT CAST('' AS String) AS cell_id, CAST(NULL AS Nullable(String)) AS site_address, CAST(NULL AS Nullable(Float64)) AS latitude, CAST(NULL AS Nullable(Float64)) AS longitude, CAST(NULL AS Nullable(UInt16)) AS mcc, CAST(NULL AS Nullable(UInt16)) AS mnc, CAST(NULL AS Nullable(UInt32)) AS lac, CAST(NULL AS Nullable(UInt32)) AS ci WHERE 0) AS m ON m.cell_id = a.cell_id
) AS t ON t.cell_id = c.start_cell_id
WHERE c.case_id = {case_id:UUID} AND c.target_number = {msisdn:String}
GROUP BY call_type, direction, counterpart, start_time, end_time,
         duration_seconds, cell_id
ORDER BY start_time DESC
;

-- ------------------------------------------------------------------------------------------------------------
-- Q056  financial_sms   [ClickHouse]
-- File:    config/report_mapping.py:876
-- Purpose: SMS from financial/bank senders, resolved via dim_sms_sender's DLT categorization rather.
-- Tables:  analytics.fact_call_detail, master.dim_sms_sender
-- ------------------------------------------------------------------------------------------------------------
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
ORDER BY messages DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q057  ecommerce_sms   [ClickHouse]
-- File:    config/report_mapping.py:901
-- Purpose: Call/SMS traffic from e-commerce senders.
-- Tables:  analytics.fact_call_detail, master.dim_sms_sender
-- ------------------------------------------------------------------------------------------------------------
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
ORDER BY messages DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q058  connectivity_totals   [ClickHouse]
-- File:    config/report_mapping.py:927
-- Purpose: Aggregate outgoing/incoming call and SMS totals, durations and distinct counterparts.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
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
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String};

-- ------------------------------------------------------------------------------------------------------------
-- Q059  connectivity_roaming   [ClickHouse]
-- File:    config/report_mapping.py:951
-- Purpose: Record counts per roaming circle.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
SELECT roaming_circle AS circle, count() AS records
FROM fact_call_detail
WHERE case_id = {case_id:UUID} AND target_number = {msisdn:String}
  AND roaming_circle != ''
  AND upper(trim(roaming_circle)) != upper(trim({home_circle:String}))
GROUP BY circle ORDER BY records DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q060  connectivity_top_counterparts   [ClickHouse]
-- File:    config/report_mapping.py:965
-- Purpose: Top counterparts by total call/SMS volume, with duration and first/last contact.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
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
GROUP BY counterpart ORDER BY total DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q061  _b_party_locations_sql   [ClickHouse]
-- File:    config/report_mapping.py:985   (SQL built with f-string: {...} are filled in by code)
-- Purpose: Shared body for the most/least contacted B-party location blocks.
-- Tables:  analytics.fact_call_detail, analytics.dim_sdr
-- ------------------------------------------------------------------------------------------------------------
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
ORDER BY records {order}, s.location
;

-- ------------------------------------------------------------------------------------------------------------
-- Q062  connectivity_international   [ClickHouse]
-- File:    config/report_mapping.py:1047
-- Purpose: Counterparts resolved to a non-home country, i.e.
-- Tables:  analytics.fact_call_detail, master.dim_country_master
-- ------------------------------------------------------------------------------------------------------------
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
GROUP BY counterpart ORDER BY records DESC;

-- ------------------------------------------------------------------------------------------------------------
-- Q063  sustel_matches   [ClickHouse]
-- File:    config/report_mapping.py:1082
-- Purpose: Full Sustel watchlist record for this number OR.
-- Tables:  scout_reference.sustel
-- ------------------------------------------------------------------------------------------------------------
SELECT
    s.IDNo AS idno, s.SSSID AS sssid, toString(s.telno) AS telno, toString(s.telno0) AS telno_alt,
    s.orgcode, s.`Case` AS case_name, s.Orggroup AS orggroup, s.username,
    s.teladdress, s.country AS country, s.Other_Information AS other_information,
    s.reference, s.refdate, s.monitored,
    s.DateOf1stSanction AS first_sanction, s.validtill AS valid_till,
    s.Monitorby AS monitored_by, s.`Group` AS group_name,
    s.OfficerConcerned AS officer, s.remarks
FROM (SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, toInt64OrNull(nullIf(replaceRegexpAll(r.telno, '[^0-9]', ''), '')) AS telno, toInt64OrNull(nullIf(replaceRegexpAll(r.telno0, '[^0-9]', ''), '')) AS telno0, r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction FROM scout_reference.sustel AS r FINAL WHERE r._is_deleted = 0) AS s
WHERE toString(s.telno) = {msisdn:String} OR toString(s.telno0) = {msisdn:String}
   OR toString(s.telno) IN {counterparts:Array(String)} OR toString(s.telno0) IN {counterparts:Array(String)};

-- ------------------------------------------------------------------------------------------------------------
-- Q064  social_profile   [ClickHouse]
-- File:    config/report_mapping.py:1105
-- Purpose: Profiler-reported social handles.
-- Tables:  analytics.dim_profiler
-- ------------------------------------------------------------------------------------------------------------
SELECT
    `names` AS names, `gender`, `addresses`,
    `telegram`, `facebook`, `twitter`, `upi`, `MNP` AS mnp_flag
FROM dim_profiler
WHERE toString(`phone number`) = {msisdn:String}
;

-- ------------------------------------------------------------------------------------------------------------
-- Q065  social_truecaller_leak   [ClickHouse]
-- File:    config/report_mapping.py:1119
-- Purpose: Truecaller leak fields for the target number.
-- Tables:  analytics.dim_truecaller
-- ------------------------------------------------------------------------------------------------------------
SELECT `name`, `email`, `operator`, `location`, `country`, `address`, `spam score` AS spam_score
FROM dim_truecaller
WHERE toString(`phone number`) = {msisdn:String}
;

-- ------------------------------------------------------------------------------------------------------------
-- Q066  cross_reference_other_cases   [ClickHouse]
-- File:    config/report_mapping.py:1135
-- Purpose: This number's activity in other cases.
-- Tables:  analytics.fact_call_detail
-- ------------------------------------------------------------------------------------------------------------
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
ORDER BY records DESC;
