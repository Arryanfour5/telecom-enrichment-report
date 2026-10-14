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
INNER JOIN dim_cri c
    ON toString(if(cd.caller = cd.target_number, cd.receiver, cd.caller))
       IN (toString(c.tCallingNo), toString(c.tCalledNo))
WHERE cd.case_id = {case_id:UUID} AND cd.target_number = {msisdn:String}
GROUP BY cri_id, crii_id, target_no, calling_no, called_no,
         call_date, call_type, direction, officer, matched_party
ORDER BY matched_calls DESC
