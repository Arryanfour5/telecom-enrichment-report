SELECT
    c.tCDRID               AS cri_id,
    c.tCRIID               AS crii_id,
    toString(c.tTargetNo)  AS target_no,
    toString(c.tCallingNo) AS calling_no,
    toString(c.tCalledNo)  AS called_no,
    c.tDate                AS call_date,
    c.tCallType            AS call_type,
    c.tCallDirection       AS direction,
    c.Officerconcerned     AS officer,
    if(toString(cd.caller) IN (toString(c.tCallingNo), toString(c.tCalledNo)),
       'caller', 'receiver')                            AS matched_party,
    count()                                             AS matched_calls,
    min(cd.call_date)                                   AS first_seen,
    max(cd.call_date)                                   AS last_seen
FROM fact_call_detail cd
-- CRI is scout_reference.cri (the Access ingestion's table), not analytics.dim_cri; the columns read here under their legacy names.
INNER JOIN (
    SELECT cdr_id AS tCDRID, cri_id AS tCRIID,
           toInt64OrNull(nullIf(replaceRegexpAll(target_no, '[^0-9]', ''), ''))  AS tTargetNo,
           toInt64OrNull(nullIf(replaceRegexpAll(calling_no, '[^0-9]', ''), '')) AS tCallingNo,
           toInt64OrNull(nullIf(replaceRegexpAll(called_no, '[^0-9]', ''), ''))  AS tCalledNo,
           toDateTime(call_date) AS tDate, call_type AS tCallType, call_direction AS tCallDirection, '' AS Officerconcerned
    FROM scout_reference.cri FINAL WHERE `_is_deleted` = 0
) c
    ON toString(if(cd.caller = cd.target_number, cd.receiver, cd.caller))
       IN (toString(c.tCallingNo), toString(c.tCalledNo))
WHERE cd.case_id = {case_id:UUID} AND cd.target_number = {msisdn:String}
GROUP BY cri_id, crii_id, target_no, calling_no, called_no,
         call_date, call_type, direction, officer, matched_party
ORDER BY matched_calls DESC
