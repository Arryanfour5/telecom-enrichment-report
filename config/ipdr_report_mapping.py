"""
IPDR Enrichment Report - schema mapping (the IPDR counterpart of report_mapping.py).

Same contract as the CDR mapping: this file is the ONLY place that knows what the IPDR report reads. Mount a different one
over /app/config/ipdr_report_mapping.py in an offline deployment and the API adapts without a rebuild. It defines a
module-level MAPPING dict in the shape app/mapping.py loads.

WHAT IS REUSED, WHAT IS NOT
---------------------------
The LEA reference tables (dim_sdr, dim_truecaller, dim_profiler, ...; Sustel / phone book / CRI are scout_reference's, written __SUSTEL__ /
__PHONEBOOK__ / __CRI__; the CAF is scout_enriched.customer_application_form) are shared by
the CDR and IPDR reports: they are keyed by phone number / IMEI, not by the kind of record. The blocks that only read
them by `{msisdn}` (subscriber, photo, names, point of sale, alternate numbers, social profile, Truecaller leak) are taken
from report_mapping.py itself, loaded by path from the same folder, so a corrected query there is corrected here too.
Everything that reads call records is written again below against `fact_internet_sessions` (an IPDR record is an
internet session; the number it belongs to is `device_id`).

REPORT STRUCTURE (the client's IPDR quick report)
-------------------------------------------------
  header   Ref / Ref date / Input gist / Analysed by / Analysed on / IPDR period requested and actual
  left     Subscriber detail, Alias/Names, SIM bought from, Alternate numbers
  right    AI Insights, Locations, IMEI, IMSI, Connectivity (VoIP calls, B-party cities), Applications used, VPN usage, TOR usage,
           BitTorrent usage, Hotspot (tethering) usage, Websites visited, B-party resolution, Sustel details,
           Social media, Cross reference

Placeholders inside sql are ClickHouse bind parameters `{name:Type}` (never interpolated). Available: {msisdn:String},
{case_id:UUID}, {limit:UInt32}, {day_start:UInt8}, {day_end:UInt8}, {home_country_name:String}, {usage_low:Float64},
{usage_high:Float64}, {events_limit:UInt32}, {ecommerce_domains:Array(String)}, {financial_domains:Array(String)},
{shared_hosting_domains:Array(String)} and, looked up first by app/service.py: {counterparts:Array(String)} (the resolved
B-party numbers), {counterparts_last10:Array(String)}, {sustel_last10:Array(String)}, {imeis:Array(String)}.

The number is matched on its LAST 10 DIGITS on the fact side (device_id is stored in several forms) and as given on the
dims side, exactly like the CDR report.
"""
import importlib.util
from pathlib import Path
from typing import Any, Dict, List


def _cdr_mapping():
    """The CDR mapping class, loaded from the sibling file (see the module docstring)."""
    path = Path(__file__).with_name("report_mapping.py")
    spec = importlib.util.spec_from_file_location("cdr_report_mapping_reused_by_ipdr", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TelecomReportMapping


# One session of this number in this case. `f` is fact_internet_sessions.
_OF_NUMBER = """f.case_id = {case_id:UUID} AND right(f.device_id, 10) = right({msisdn:String}, 10)"""
_VALID = "f.start_date_time > toDateTime('2000-01-01 00:00:00')"     # 12-hour times that failed to parse are 1970-01-01
_FIRST = "toString(minIf(f.start_date_time, " + _VALID + "))"
_LAST = "toString(maxIf(f.start_date_time, " + _VALID + "))"
_MB = "round(sum(ifNull(f.uplink_vol, 0) + ifNull(f.downlink_vol, 0)) / 1048576, 2)"
_IMEI = "replaceAll(replaceAll(ifNull(f.imei, ''), '`', ''), char(39), '')"

# The serving-cell dimension for THIS number's cells only (dim_cell_tower is ~500k rows).
_CELLS = """(
    SELECT cell_id, any(ifNull(city, '')) AS city, any(ifNull(state, '')) AS state, any(country) AS country,
           nullIf(any(latitude), 0) AS latitude, nullIf(any(longitude), 0) AS longitude
    FROM __CELL_TOWER__
    WHERE cell_id IN (SELECT cell_id FROM fact_internet_sessions AS f WHERE """ + _OF_NUMBER + """)
    GROUP BY cell_id
)"""

# The destination IP's knowledge (country, organisation, AS) for THIS number's destinations only.
_DEST_IPS = """(
    SELECT address, argMax(country, created_at) AS country, argMax(city, created_at) AS city,
           argMax(organization, created_at) AS organization, argMax(as_name, created_at) AS as_name,
           argMax(as_domain, created_at) AS as_domain, argMax(asn, created_at) AS asn
    FROM dim_ip_address
    WHERE type = 'destination'
      AND address IN (SELECT destination_ip FROM fact_internet_sessions AS f WHERE """ + _OF_NUMBER + """)
    GROUP BY address
)"""


class IPDRReportMapping:
    """Repository for the IPDR enrichment report's schema mapping and queries."""

    @staticmethod
    def get_databases() -> Dict[str, str]:
        return {"facts": "analytics", "dims": "analytics", "master": "master", "reference": "scout_reference"}

    @staticmethod
    def get_tables() -> Dict[str, str]:
        return {
            "sessions": "{facts}.fact_internet_sessions",
            "ipdr_anomaly": "{facts}.fact_ipdr_anomaly",
            "anomaly_dim": "{facts}.dim_anomaly",
            "ip_dim": "{facts}.dim_ip_address",
            "cell_tower": "{facts}.dim_cell_tower",
            "call_detail": "{facts}.fact_call_detail",
            "application": "{master}.dim_application_master",    # destination IP range -> application (ip_start..ip_end); the port plays no part
            "tac": "master.Tac_DB_master",
            "sdr": "{dims}.dim_sdr", "sustel": "{reference}.sustel", "profiler": "{dims}.dim_profiler",
            "truecaller": "{dims}.dim_truecaller", "imei": "{dims}.dim_imei_legacy",
        }

    @staticmethod
    def get_settings() -> Dict[str, Any]:
        return {
            "day_window_start": 6,             # 06:00 - 20:00 counts as "day"
            "day_window_end": 20,
            "top_n": 10,
            "events_limit": 25,                # individual sessions listed under VPN / TOR / BitTorrent / VoIP
            "home_country_name": "India",      # as dim_cell_tower.country spells it
            # File-wise internet usage (AI Insights, point 1): megabytes per ACTIVE day of the file. PROVISIONAL:
            # below `low` is low, from `high` is high, between is medium. Confirm the thresholds with the client.
            "usage_low_mb_per_day": 100.0,
            "usage_high_mb_per_day": 1024.0,
            # Websites: IPDR has no URLs, so a site is recognised by the destination IP's AS domain (exact match).
            "ecommerce_domains": [
                "amazon.in", "amazon.com", "flipkart.com", "myntra.com", "meesho.com", "snapdeal.com", "ajio.com",
                "nykaa.com", "bigbasket.com", "ebay.com", "alibaba.com", "aliexpress.com", "olx.in",
            ],
            "financial_domains": [
                "sbi.co.in", "onlinesbi.sbi", "onlinesbi.com", "hdfcbank.com", "icicibank.com", "axisbank.com",
                "kotak.com", "pnb.co.in", "bankofbaroda.in", "canarabank.com", "yesbank.in", "indusind.com",
                "idfcfirstbank.com", "federalbank.co.in", "paytm.com", "phonepe.com", "razorpay.com",
                "npci.org.in", "rbi.org.in", "paypal.com",
            ],
            # Cloud / CDN domains: traffic to them cannot be told apart from the shops hosted on them.
            "shared_hosting_domains": [
                "amazonaws.com", "cloudfront.net", "akamaitechnologies.com", "akaquill.net", "edgekey.net",
                "googleusercontent.com", "1e100.net", "cloudflare.com", "fastly.net", "azureedge.net",
            ],
        }

    # ─────────────────────────────────────────────────────────────────────
    # HEADER
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def ipdr_period() -> Dict[str, Any]:
        return {
            "description": (
                "The IPDR period actually present for this number: first and last session, sessions, days with data and "
                "files. 'IPDR Period Requested' is not in ClickHouse; the endpoint takes it as a query parameter."
            ),
            "clickhouse_query": """
SELECT
    any(f.device_id)                                   AS msisdn,
    """ + _FIRST + """                                 AS actual_from,
    """ + _LAST + """                                  AS actual_to,
    count()                                            AS sessions,
    uniqExactIf(f.session_date, """ + _VALID + """)    AS days_with_data,
    uniqExact(f.file_id)                               AS files
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + """
            """
        }

    @staticmethod
    def top_place() -> Dict[str, Any]:
        return {
            "description": (
                "Top place: the city the number spent the most sessions in (day and night together), with its coordinates; "
                "a cell the tower master has no city for counts as its own place ('cell <id>')."
            ),
            "clickhouse_query": """
SELECT
    if(t.city != '', t.city, concat('cell ', f.cell_id))   AS place,
    any(t.state) AS state, any(t.country) AS country,
    any(t.latitude) AS latitude, any(t.longitude) AS longitude,
    count() AS sessions, uniqExact(f.cell_id) AS cells,
    """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen
FROM fact_internet_sessions AS f
LEFT JOIN """ + _CELLS + """ AS t ON t.cell_id = f.cell_id
WHERE """ + _OF_NUMBER + " AND " + _VALID + """ AND ifNull(f.cell_id, '') != ''
GROUP BY place
ORDER BY sessions DESC
LIMIT 1
            """,
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 1: AI Insights
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def internet_usage() -> Dict[str, Any]:
        return {
            "description": (
                "Usage of internet, FILE WISE (not month wise): per ingested file, the megabytes moved per active day and "
                "the level - low / medium / high - against the settings' usage_low_mb_per_day / usage_high_mb_per_day."
            ),
            "clickhouse_query": """
SELECT
    toString(f.file_id)                                              AS file_id,
    """ + _FIRST + """                                               AS period_from,
    """ + _LAST + """                                                AS period_to,
    count()                                                          AS sessions,
    uniqExactIf(toDate(f.start_date_time), """ + _VALID + """)       AS active_days,
    """ + _MB + """                                                  AS total_mb,
    round(total_mb / greatest(active_days, 1), 1)                    AS avg_daily_mb,
    multiIf(avg_daily_mb < {usage_low:Float64}, 'low', avg_daily_mb >= {usage_high:Float64}, 'high', 'medium') AS usage_level
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + """
GROUP BY f.file_id
ORDER BY period_from
            """
        }

    @staticmethod
    def sustel_hits() -> Dict[str, Any]:
        return {
            "description": (
                "Which of the resolved B-party numbers (or the number itself) are on the Sustel watchlist, matched on the "
                "last 10 digits. app/service.py runs this first and passes the matched numbers on as {sustel_last10} so the "
                "facts side can count the sessions (dims and facts may be on different servers, so no single join)."
            ),
            "clickhouse_query": """
SELECT
    right(toString(`telno`), 10)   AS matched_last10,
    `Case`                         AS case_name,
    `monitored`                    AS monitored,
    if(right(toString(`telno`), 10) = right({msisdn:String}, 10), 'target', 'b_party') AS match_type
FROM __SUSTEL__
WHERE right(toString(`telno`), 10) = right({msisdn:String}, 10)
   OR right(toString(`telno`), 10) IN {counterparts_last10:Array(String)}
            """
        }

    @staticmethod
    def sustel_connectivity() -> Dict[str, Any]:
        return {
            "description": "Connectivity with Sustel numbers: sessions whose resolved B-party is a Sustel number, with the count.",
            "clickhouse_query": """
SELECT
    right(f.b_party_number, 10)    AS number,
    count()                        AS sessions,
    """ + _FIRST + """             AS first_seen,
    """ + _LAST + """              AS last_seen
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + """
  AND right(ifNull(f.b_party_number, ''), 10) IN {sustel_last10:Array(String)}
GROUP BY number
ORDER BY sessions DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def international_roaming() -> Dict[str, Any]:
        return {
            "description": (
                "International roaming: the countries the serving cell (CGI) was in, other than the home country, with the "
                "date range - the serving cell id resolved through the tower master."
            ),
            "clickhouse_query": """
SELECT
    t.country                                       AS country,
    count()                                         AS sessions,
    uniqExact(f.cell_id)                            AS serving_cells,
    arraySlice(groupUniqArray(t.city), 1, 5)        AS cities,
    """ + _FIRST + """                              AS first_seen,
    """ + _LAST + """                               AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN """ + _CELLS + """ AS t ON t.cell_id = f.cell_id
WHERE """ + _OF_NUMBER + """ AND t.country != '' AND t.country != {home_country_name:String}
GROUP BY country
ORDER BY sessions DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def frequent_roaming() -> Dict[str, Any]:
        return {
            "description": (
                "Frequent roaming across states / countries, read from the operator's own columns: Home_Circle against "
                "Home_Roaming_Circle and the Roaming_Network_Indicator ('HOME' when on the home network)."
            ),
            "clickhouse_query": """
SELECT
    home_circle, roaming_circle, count() AS sessions, uniqExact(session_day) AS days,
    toString(min(ts)) AS first_seen, toString(max(ts)) AS last_seen
FROM (
    SELECT trimBoth(ifNull(f.home_circle, '')) AS home_circle,
           trimBoth(ifNull(f.home_roaming_circle, '')) AS roaming_circle,
           upper(trimBoth(ifNull(f.roaming_network_indicator, ''))) AS ind,
           f.session_date AS session_day, f.start_date_time AS ts
    FROM fact_internet_sessions AS f
    WHERE """ + _OF_NUMBER + """
)
WHERE (ind NOT IN ('', 'HOME')) OR (roaming_circle != '' AND home_circle != '' AND roaming_circle != home_circle)
GROUP BY home_circle, roaming_circle
ORDER BY sessions DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def city_visits() -> Dict[str, Any]:
        return {
            "description": "Visits to cities with their count: the serving cell id resolved to a city through the tower master.",
            "clickhouse_query": """
SELECT
    t.city AS city, t.state AS state, t.country AS country,
    count()                                          AS sessions,
    uniqExact(f.session_date)                        AS visit_days,
    uniqExact(f.cell_id)                             AS cells,
    """ + _FIRST + """                               AS first_seen,
    """ + _LAST + """                                AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN """ + _CELLS + """ AS t ON t.cell_id = f.cell_id
WHERE """ + _OF_NUMBER + """ AND t.city != ''
GROUP BY city, state, country
ORDER BY sessions DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def last_ipdr_date() -> Dict[str, Any]:
        return {
            "description": "The last date of the IPDR period for this number.",
            "clickhouse_query": """
SELECT toString(toDate(maxIf(f.start_date_time, """ + _VALID + """))) AS last_ipdr_date
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + """
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 2: Locations
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _locations(period_filter: str) -> str:
        return """
SELECT
    f.cell_id AS cell_id, any(t.city) AS city, any(t.state) AS state, any(t.country) AS country,
    any(t.latitude) AS latitude, any(t.longitude) AS longitude,
    count()                                          AS sessions,
    """ + _FIRST + """                               AS first_seen,
    """ + _LAST + """                                AS last_seen
FROM fact_internet_sessions AS f
LEFT JOIN """ + _CELLS + """ AS t ON t.cell_id = f.cell_id
WHERE """ + _OF_NUMBER + " AND " + _VALID + " AND " + period_filter + """
GROUP BY f.cell_id
ORDER BY sessions DESC
LIMIT {limit:UInt32}
        """

    @staticmethod
    def locations_day() -> Dict[str, Any]:
        return {
            "description": "Top DAY locations (serving cells inside the day window) with latitude / longitude and the date range.",
            "clickhouse_query": IPDRReportMapping._locations(
                "toHour(f.start_date_time) >= {day_start:UInt8} AND toHour(f.start_date_time) < {day_end:UInt8}"),
        }

    @staticmethod
    def locations_night() -> Dict[str, Any]:
        return {
            "description": "Top NIGHT locations (serving cells outside the day window) with latitude / longitude and the date range.",
            "clickhouse_query": IPDRReportMapping._locations(
                "(toHour(f.start_date_time) < {day_start:UInt8} OR toHour(f.start_date_time) >= {day_end:UInt8})"),
        }

    @staticmethod
    def locations_international() -> Dict[str, Any]:
        return {
            "description": "International: the top countries the serving cell was in outside the home country, with the date range.",
            "clickhouse_query": IPDRReportMapping.international_roaming()["clickhouse_query"],
        }

    @staticmethod
    def locations_international_cities() -> Dict[str, Any]:
        return {
            "description": "In a particular city: city name, latitude and longitude of the serving cells abroad, with the date range.",
            "clickhouse_query": """
SELECT
    t.city AS city, t.country AS country, any(t.latitude) AS latitude, any(t.longitude) AS longitude,
    count() AS sessions, """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN """ + _CELLS + """ AS t ON t.cell_id = f.cell_id
WHERE """ + _OF_NUMBER + """ AND t.country != '' AND t.country != {home_country_name:String} AND t.city != ''
GROUP BY city, country
ORDER BY sessions DESC
LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 3 / 3A: IMEI and IMSI
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def imei_devices() -> Dict[str, Any]:
        return {
            "description": (
                "IMEI (handset) usage in time order: one row per continuous stretch of the same IMEI, with the date range "
                "and the brand / model from the TAC database (master.Tac_DB_master)."
            ),
            "clickhouse_query": """
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
                SELECT """ + _IMEI + """ AS dev, f.start_date_time AS ts, f.record_id AS rid
                FROM fact_internet_sessions AS f
                WHERE """ + _OF_NUMBER + " AND " + _VALID + """ AND ifNull(f.imei, '') != ''
            )
        )
    )
    GROUP BY stretch, dev
) AS s
LEFT JOIN master.Tac_DB_master AS d ON d.tac_code = toUInt32OrZero(substring(s.imei, 1, 8)) AND d.is_current = 1
GROUP BY s.imei, s.first_seen, s.last_seen, s.sessions
ORDER BY s.first_seen
            """
        }

    @staticmethod
    def imei_dim_hits() -> Dict[str, Any]:
        return {
            "description": "Handsets of this number that an earlier case already noted (LEA dim_imei_legacy), matched on the IMEI.",
            "clickhouse_query": """
SELECT toString(`IMEI`) AS imei, `Case Reference` AS case_reference, `Group` AS group_name,
       `Analyst Name` AS analyst, `Brief description` AS description, `timestamp`
FROM dim_imei_legacy
WHERE toString(`IMEI`) IN {imeis:Array(String)}
            """
        }

    @staticmethod
    def imsi_subscribers() -> Dict[str, Any]:
        return {
            "description": (
                "IMSI (SIM) usage in time order: one row per continuous stretch of the same IMSI, with the date range and "
                "the handset (most used IMEI, brand, model) it was in. Placeholder IMSIs ending in nine zeros are dropped."
            ),
            "clickhouse_query": """
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
                       f.start_date_time AS ts, f.record_id AS rid, """ + _IMEI + """ AS handset
                FROM fact_internet_sessions AS f
                WHERE """ + _OF_NUMBER + " AND " + _VALID + """ AND ifNull(f.imsi, '') != ''
            )
            WHERE NOT endsWith(sim, '000000000')
        )
    )
    GROUP BY stretch, sim
) AS s
LEFT JOIN master.Tac_DB_master AS d ON d.tac_code = toUInt32OrZero(substring(s.imei, 1, 8)) AND d.is_current = 1
GROUP BY s.imsi, s.first_seen, s.last_seen, s.sessions, s.imei
ORDER BY s.first_seen
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Section 4: Connectivity / VoIP calls
    # ─────────────────────────────────────────────────────────────────────

    # VoIP calls: a session is a VoIP call when the ingestion's AppInferenceEngine recognised one on it and wrote
    # fact_internet_sessions.voip_call_type ('whatsapp_call', 'telegram_call', 'instagram_call', ...; '' = not a call).
    # The column is added by the IPDR ingestion; a database that has not ingested a file since shows these two blocks as
    # an error and leaves the rest of the report alone.
    _VOIP = """
FROM fact_internet_sessions AS f
LEFT JOIN """ + _DEST_IPS + """ AS ip ON ip.address = f.destination_ip
WHERE """ + _OF_NUMBER + """ AND f.voip_call_type != ''"""

    @staticmethod
    def voip_by_country() -> Dict[str, Any]:
        return {
            "description": "VoIP calls: which kind of call (whatsapp_call, telegram_call, ...) and to which country, with the date range.",
            "clickhouse_query": """
SELECT f.voip_call_type AS voip_call_type, replaceRegexpOne(f.voip_call_type, '_call$', '') AS application,
       if(ip.country = '' OR ip.country = '-', 'Unknown', ip.country) AS country,
       count() AS calls, """ + _FIRST + """ AS first_call, """ + _LAST + """ AS last_call
""" + IPDRReportMapping._VOIP + """
GROUP BY voip_call_type, application, country
ORDER BY calls DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def voip_calls() -> Dict[str, Any]:
        return {
            "description": "The most recent VoIP calls one by one: call type, country, destination, date and time.",
            "clickhouse_query": """
SELECT toString(f.start_date_time) AS at, f.voip_call_type AS voip_call_type,
       replaceRegexpOne(f.voip_call_type, '_call$', '') AS application,
       if(ip.country = '' OR ip.country = '-', 'Unknown', ip.country) AS country,
       f.destination_ip AS destination_ip, f.destination_port AS destination_port, f.duration AS duration_seconds
""" + IPDRReportMapping._VOIP + """
ORDER BY f.start_date_time DESC
LIMIT {events_limit:UInt32}
            """
        }

    @staticmethod
    def b_party_locations() -> Dict[str, Any]:
        return {
            "description": (
                "B-party numbers (resolved from the IPDR Destination IP file) with their city, resolved from the SDR "
                "address (LOCAL_ADDRESS, else PERMANENT_ADDRESS) and circle. Only B-parties that match an SDR record appear."
            ),
            "clickhouse_query": """
SELECT right(toString(`SUBSCRIBER_NUMBER`), 10) AS b_party_number,
       any(coalesce(nullIf(`LOCAL_ADDRESS`, ''), nullIf(`PERMANENT_ADDRESS`, ''))) AS location,
       any(`CIRCLE`) AS circle
FROM dim_sdr
WHERE right(toString(`SUBSCRIBER_NUMBER`), 10) IN {counterparts_last10:Array(String)}
GROUP BY b_party_number
ORDER BY b_party_number
LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Applications used, VPN / TOR / BitTorrent / Hotspot
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def applications() -> Dict[str, Any]:
        return {
            "description": (
                "Applications used with their session counts (WhatsApp (10), Snapchat (50), ...): the application the "
                "destination IP + port belongs to in the Application Database. Sessions that match no entry are not listed."
            ),
            "clickhouse_query": """
SELECT f.platform AS application, count() AS sessions, """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen,
       """ + _MB + """ AS total_mb
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + """ AND f.platform NOT IN ('', 'unknown')
GROUP BY application
ORDER BY sessions DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def applications_totals() -> Dict[str, Any]:
        return {
            "description": "How many sessions were resolved to an application and how many were not.",
            "clickhouse_query": """
SELECT count() AS sessions, countIf(f.platform NOT IN ('', 'unknown')) AS resolved_sessions,
       countIf(f.platform IN ('', 'unknown')) AS unresolved_sessions
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + """
            """
        }

    @staticmethod
    def _usage_summary(condition: str, name_expr: str) -> str:
        return """
SELECT """ + name_expr + """ AS name, count() AS sessions, """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen,
       uniqExact(f.destination_ip) AS distinct_destination_ips, topK(3)(f.destination_port) AS top_ports
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + " AND (" + condition + """)
GROUP BY name
ORDER BY sessions DESC
LIMIT {limit:UInt32}
        """

    @staticmethod
    def _usage_events(condition: str, name_expr: str) -> str:
        return """
SELECT toString(f.start_date_time) AS at, """ + name_expr + """ AS name, f.destination_ip AS destination_ip,
       f.destination_port AS destination_port, f.duration AS duration_seconds
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + " AND (" + condition + """)
ORDER BY f.start_date_time DESC
LIMIT {events_limit:UInt32}
        """

    _VPN_COND = "f.vpn_name != '' OR f.proxy_type = 'VPN'"
    _VPN_NAME = "if(f.vpn_name != '', f.vpn_name, 'VPN (provider not named)')"
    _TOR_COND = "f.is_tor = 1 OR f.proxy_type = 'TOR'"
    _TOR_NAME = "if(f.is_tor = 1, 'Tor-Network', 'TOR (exit-node address)')"
    _BT_COND = "f.platform = 'BitTorrent'"

    @staticmethod
    def vpn_summary() -> Dict[str, Any]:
        return {
            "description": "VPN usage: the VPN provider the ingestion stored (vpn_name: community VPN list, else IP2Proxy; 'Unidentified VPN' when neither names it), on a VPN port, with first / last use and sessions.",
            "clickhouse_query": IPDRReportMapping._usage_summary(IPDRReportMapping._VPN_COND, IPDRReportMapping._VPN_NAME),
        }

    @staticmethod
    def vpn_sessions() -> Dict[str, Any]:
        return {
            "description": "The most recent VPN sessions: name and date / time.",
            "clickhouse_query": IPDRReportMapping._usage_events(IPDRReportMapping._VPN_COND, IPDRReportMapping._VPN_NAME),
        }

    @staticmethod
    def tor_summary() -> Dict[str, Any]:
        return {
            "description": "TOR usage: the destination IP is a Tor exit node (exact IP, any port) or an IP2Proxy TOR address, as the ingestion stored it (is_tor), with first / last use.",
            "clickhouse_query": IPDRReportMapping._usage_summary(IPDRReportMapping._TOR_COND, IPDRReportMapping._TOR_NAME),
        }

    @staticmethod
    def tor_sessions() -> Dict[str, Any]:
        return {
            "description": "The most recent TOR sessions: name and date / time.",
            "clickhouse_query": IPDRReportMapping._usage_events(IPDRReportMapping._TOR_COND, IPDRReportMapping._TOR_NAME),
        }

    @staticmethod
    def bittorrent_summary() -> Dict[str, Any]:
        return {
            "description": "BitTorrent usage: destination IP + port in the BitTorrent database (master.dim_bittorrent), with first / last use.",
            "clickhouse_query": IPDRReportMapping._usage_summary(IPDRReportMapping._BT_COND, "'BitTorrent'"),
        }

    @staticmethod
    def bittorrent_sessions() -> Dict[str, Any]:
        return {
            "description": "The most recent BitTorrent sessions: name and date / time.",
            "clickhouse_query": IPDRReportMapping._usage_events(IPDRReportMapping._BT_COND, "'BitTorrent'"),
        }

    @staticmethod
    def hotspot_indicators() -> Dict[str, Any]:
        return {
            "description": (
                "Hotspot (tethering) usage. An IPDR record does not say 'tethering', so these are INDICATORS: sessions the "
                "concurrent-connections detector flagged (several source addresses behind the subscriber at once) and "
                "sessions on an access-point name that names tethering / hotspot."
            ),
            "clickhouse_query": """
SELECT indicator, sessions, distinct_source_ips, first_seen, last_seen, access_points FROM (
    SELECT 'concurrent_connections' AS indicator, count() AS sessions, uniqExact(f.source_ip) AS distinct_source_ips,
           """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen,
           arraySlice(groupUniqArray(ifNull(f.access_point, '')), 1, 5) AS access_points
    FROM fact_internet_sessions AS f
    WHERE """ + _OF_NUMBER + """
      AND f.record_id IN (
          SELECT record_id FROM fact_ipdr_anomaly
          WHERE anomaly_id IN (SELECT anomaly_id FROM dim_anomaly
                               WHERE anomaly_domain = 'ipdr' AND anomaly_type = 'multiple_concurrent_device_connections'))
    HAVING sessions > 0
    UNION ALL
    SELECT 'tethering_access_point' AS indicator, count() AS sessions, uniqExact(f.source_ip) AS distinct_source_ips,
           """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen,
           arraySlice(groupUniqArray(ifNull(f.access_point, '')), 1, 5) AS access_points
    FROM fact_internet_sessions AS f
    WHERE """ + _OF_NUMBER + """
      AND (positionCaseInsensitive(ifNull(f.access_point, ''), 'tether') > 0
           OR positionCaseInsensitive(ifNull(f.access_point, ''), 'hotspot') > 0
           OR positionCaseInsensitive(ifNull(f.access_point, ''), 'mobap') > 0)
    HAVING sessions > 0
)
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - Websites visited
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _websites(domains_param: str) -> str:
        return """
SELECT ip.as_domain AS site, any(ip.as_name) AS organisation, count() AS sessions,
       uniqExact(f.destination_ip) AS distinct_ips, """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen
FROM fact_internet_sessions AS f
INNER JOIN """ + _DEST_IPS + """ AS ip ON ip.address = f.destination_ip
WHERE """ + _OF_NUMBER + " AND has(" + domains_param + """, lower(ip.as_domain))
GROUP BY site
ORDER BY sessions DESC
LIMIT {limit:UInt32}
        """

    @staticmethod
    def websites_ecommerce() -> Dict[str, Any]:
        return {
            "description": (
                "E-commerce locators. IPDR carries no URLs, so a site is recognised by the destination IP's AS domain "
                "(exact match against the settings' ecommerce_domains). Best effort: see shared_hosting."
            ),
            "clickhouse_query": IPDRReportMapping._websites("{ecommerce_domains:Array(String)}"),
        }

    @staticmethod
    def websites_financial() -> Dict[str, Any]:
        return {
            "description": "Financial locators (banks, UPI, payments): same AS-domain recognition against the settings' financial_domains.",
            "clickhouse_query": IPDRReportMapping._websites("{financial_domains:Array(String)}"),
        }

    @staticmethod
    def websites_shared_hosting() -> Dict[str, Any]:
        return {
            "description": (
                "Sessions to cloud / CDN providers (AWS, CloudFront, Akamai, Google, ...). A shop or bank hosted there cannot be "
                "told apart from any other tenant, so these are listed apart instead of being counted as e-commerce."
            ),
            "clickhouse_query": IPDRReportMapping._websites("{shared_hosting_domains:Array(String)}"),
        }

    # ─────────────────────────────────────────────────────────────────────
    # RIGHT PANEL - B-party resolution, Sustel, Cross reference
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def b_party_numbers() -> Dict[str, Any]:
        return {
            "description": (
                "Resolved B-party numbers and the period they were in contact: the IPDR Destination IP file paired with the "
                "sessions (b_party_type says how: 'P2P Session' = a subscriber's session, 'P2A Session' = a session to an address)."
            ),
            "clickhouse_query": """
SELECT f.b_party_number AS b_party_number, f.b_party_type AS resolved_as, count() AS sessions,
       """ + _FIRST + """ AS first_seen, """ + _LAST + """ AS last_seen
FROM fact_internet_sessions AS f
WHERE """ + _OF_NUMBER + """ AND ifNull(f.b_party_number, '') != ''
GROUP BY b_party_number, resolved_as
ORDER BY sessions DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def b_party_sdr() -> Dict[str, Any]:
        return {
            "description": (
                "SDR subscriber name of each resolved B-party number (last 10 digits), when there is an SDR record. app/service.py merges it into "
                "B Party Resolution's `numbers` rows as `b_party_name` and drops this block."
            ),
            "clickhouse_query": """
SELECT right(toString(`SUBSCRIBER_NUMBER`), 10) AS b_party_number,
       any(`SUBSCRIBER_NAME`) AS subscriber_name
FROM dim_sdr
WHERE right(toString(`SUBSCRIBER_NUMBER`), 10) IN {counterparts_last10:Array(String)}
GROUP BY b_party_number
ORDER BY b_party_number
LIMIT 500
            """
        }

    @staticmethod
    def b_party_sustel() -> Dict[str, Any]:
        return {
            "description": (
                "Sustel watchlist records of each resolved B-party number (last 10 digits, on either of the record's two numbers): the case and "
                "whether it is monitored. app/service.py merges it into B Party Resolution's `numbers` rows as `sustel` / `sustel_match` / `b_party_sustel_match` and drops this "
                "block; the full record is in the Sustel Details section (`sustel.matches`, match_type 'b_party'), which reads the same numbers."
            ),
            "clickhouse_query": """
SELECT number AS b_party_number, `Case` AS case_name, `monitored` AS monitored
FROM (
    SELECT arrayJoin([right(toString(`telno`), 10), right(toString(`telno0`), 10)]) AS number, `Case`, `monitored`
    FROM __SUSTEL__
)
WHERE number IN {counterparts_last10:Array(String)}
GROUP BY number, case_name, monitored
ORDER BY number, case_name
LIMIT 500
            """
        }

    @staticmethod
    def sustel_matches() -> Dict[str, Any]:
        return {
            "description": "Full Sustel watchlist record(s) for this number or any resolved B-party number (last 10 digits).",
            "clickhouse_query": """
SELECT
    s.IDNo AS idno, s.SSSID AS sssid, toString(s.telno) AS telno, toString(s.telno0) AS telno_alt,
    s.orgcode, s.`Case` AS case_name, s.Orggroup AS orggroup, s.username,
    s.teladdress, s.country AS country, s.Other_Information AS other_information,
    s.reference, s.refdate, s.monitored,
    s.DateOf1stSanction AS first_sanction, s.validtill AS valid_till,
    s.Monitorby AS monitored_by, s.`Group` AS group_name,
    s.OfficerConcerned AS officer, s.remarks,
    if(right(toString(s.telno), 10) = right({msisdn:String}, 10), 'target', 'b_party') AS match_type
FROM __SUSTEL__ AS s
WHERE right(toString(s.telno), 10) = right({msisdn:String}, 10)
   OR right(toString(s.telno0), 10) = right({msisdn:String}, 10)
   OR right(toString(s.telno), 10) IN {counterparts_last10:Array(String)}
   OR right(toString(s.telno0), 10) IN {counterparts_last10:Array(String)}
            """
        }

    @staticmethod
    def cross_reference_other_cases() -> Dict[str, Any]:
        return {
            "description": "This number's IPDR in other cases (excludes the current case_id).",
            "clickhouse_query": """
SELECT toString(f.case_id) AS case_id, toString(f.file_id) AS file_id, f.data_source AS data_source,
       count() AS records, min(f.session_date) AS first_seen, max(f.session_date) AS last_seen
FROM fact_internet_sessions AS f
WHERE right(f.device_id, 10) = right({msisdn:String}, 10) AND f.case_id != {case_id:UUID}
GROUP BY case_id, file_id, data_source
ORDER BY records DESC
LIMIT {limit:UInt32}
            """
        }

    @staticmethod
    def cross_reference_other_cases_cdr() -> Dict[str, Any]:
        return {
            "description": "This number's CDR in any case (as target): the same subscriber seen in call records.",
            "clickhouse_query": """
SELECT toString(c.case_id) AS case_id, toString(c.file_id) AS file_id, c.data_source AS data_source,
       count() AS records, min(c.call_date) AS first_seen, max(c.call_date) AS last_seen
FROM fact_call_detail AS c
WHERE right(c.target_number, 10) = right({msisdn:String}, 10)
GROUP BY case_id, file_id, data_source
ORDER BY records DESC
LIMIT {limit:UInt32}
            """
        }

    # ─────────────────────────────────────────────────────────────────────
    # Assembly
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def get_sections() -> List[Dict[str, Any]]:
        m = IPDRReportMapping
        c = _cdr_mapping()          # number-only blocks, shared with the CDR report

        def block(key: str, source: str, kind: str, query: Dict[str, Any]) -> Dict[str, Any]:
            return {"key": key, "source": source, "kind": kind, "sql": query["clickhouse_query"]}

        return [
            {"id": "header", "title": "Report header", "panel": "right", "blocks": [
                block("ipdr_period", "facts", "one", m.ipdr_period()),
                block("top_place", "facts", "one", m.top_place()),
            ]},
            # ── left panel: the shared dims blocks ─────────────────────────────────────
            {"id": "subscriber", "title": "Subscriber", "panel": "left", "blocks": [
                block("photo", "dims", "one", c.subscriber_photo()),
                block("subscriber", "dims", "one", c.subscriber_details()),
            ]},
            {"id": "name_alias", "title": "Name / Alias", "panel": "left", "blocks": [
                block("names", "dims", "many", c.name_alias_sources()),
            ]},
            {"id": "sim_brought_from", "title": "SIM Brought From", "panel": "left", "blocks": [
                block("point_of_sale", "dims", "one", c.point_of_sale()),
            ]},
            {"id": "alternate_numbers", "title": "Alternate Numbers", "panel": "left", "blocks": [
                block("numbers", "dims", "many", c.alternate_numbers()),
            ]},
            # ── right panel ───────────────────────────────────────────────────────────
            {"id": "ai_insights", "title": "Section 1 - AI Insights", "panel": "right", "blocks": [
                block("internet_usage", "facts", "many", m.internet_usage()),
                block("sustel_hits", "dims", "many", m.sustel_hits()),
                block("sustel_connectivity", "facts", "many", m.sustel_connectivity()),
                block("international_roaming", "facts", "many", m.international_roaming()),
                block("frequent_roaming", "facts", "many", m.frequent_roaming()),
                block("city_visits", "facts", "many", m.city_visits()),
                block("last_ipdr_date", "facts", "value", m.last_ipdr_date()),
            ]},
            {"id": "locations", "title": "Section 2 - Locations", "panel": "right", "blocks": [
                block("day", "facts", "many", m.locations_day()),
                block("night", "facts", "many", m.locations_night()),
                block("international", "facts", "many", m.locations_international()),
                block("international_cities", "facts", "many", m.locations_international_cities()),
            ]},
            {"id": "imei", "title": "Section 3 - IMEI", "panel": "right", "blocks": [
                block("devices", "facts", "many", m.imei_devices()),
                block("imei_dim_hits", "dims", "many", m.imei_dim_hits()),
            ]},
            {"id": "imsi", "title": "Section 3A - IMSI", "panel": "right", "blocks": [
                block("subscribers", "facts", "many", m.imsi_subscribers()),
            ]},
            {"id": "connectivity", "title": "Section 4 - Connectivity", "panel": "right", "blocks": [
                block("voip_by_country", "facts", "many", m.voip_by_country()),
                block("voip_calls", "facts", "many", m.voip_calls()),
                block("b_party_locations", "dims", "many", m.b_party_locations()),
            ]},
            {"id": "applications_used", "title": "Section 5 - Applications Used", "panel": "right", "blocks": [
                block("applications", "facts", "many", m.applications()),
                block("totals", "facts", "one", m.applications_totals()),
            ]},
            {"id": "vpn_usage", "title": "Section 6 - VPN Usage", "panel": "right", "blocks": [
                block("names", "facts", "many", m.vpn_summary()),
                block("sessions", "facts", "many", m.vpn_sessions()),
            ]},
            {"id": "tor_usage", "title": "Section 7 - TOR Usage", "panel": "right", "blocks": [
                block("names", "facts", "many", m.tor_summary()),
                block("sessions", "facts", "many", m.tor_sessions()),
            ]},
            {"id": "bittorrent_usage", "title": "Section 8 - BitTorrent Usage", "panel": "right", "blocks": [
                block("names", "facts", "many", m.bittorrent_summary()),
                block("sessions", "facts", "many", m.bittorrent_sessions()),
            ]},
            {"id": "hotspot_usage", "title": "Section 9 - Hotspot (Tethering) Usage", "panel": "right", "blocks": [
                block("indicators", "facts", "many", m.hotspot_indicators()),
            ]},
            {"id": "websites_used", "title": "Section 10 - Websites Used", "panel": "right", "blocks": [
                block("ecommerce_locators", "facts", "many", m.websites_ecommerce()),
                block("financial_locators", "facts", "many", m.websites_financial()),
                block("shared_hosting", "facts", "many", m.websites_shared_hosting()),
            ]},
            {"id": "b_party_resolution", "title": "Section 11 - B Party Resolution", "panel": "right", "blocks": [
                block("numbers", "facts", "many", m.b_party_numbers()),
                # looked up on the dims connection and merged into `numbers` by app/service.py (sdr_match / sdr, sustel_match / sustel)
                block("sdr", "dims", "many", m.b_party_sdr()),
                block("sustel", "dims", "many", m.b_party_sustel()),
            ]},
            {"id": "sustel", "title": "Section 12 - Sustel Details", "panel": "right", "blocks": [
                block("matches", "dims", "many", m.sustel_matches()),
            ]},
            {"id": "social_media", "title": "Section 13 - Social Media", "panel": "right", "blocks": [
                block("profile", "dims", "one", c.social_profile()),
                block("truecaller_leak", "dims", "one", c.social_truecaller_leak()),
            ]},
            {"id": "cross_reference", "title": "Section 14 - Cross Reference", "panel": "right", "blocks": [
                block("other_cases", "facts", "many", m.cross_reference_other_cases()),
                block("other_cases_cdr", "facts", "many", m.cross_reference_other_cases_cdr()),
            ]},
        ]

    @staticmethod
    def get_mapping() -> Dict[str, Any]:
        return {
            "version": 1,
            "databases": IPDRReportMapping.get_databases(),
            "tables": IPDRReportMapping.get_tables(),
            "settings": IPDRReportMapping.get_settings(),
            "sections": IPDRReportMapping.get_sections(),
        }


# app/mapping.py imports this file by path and reads MAPPING off it.
MAPPING = IPDRReportMapping.get_mapping()
