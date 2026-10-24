"""
Sustel, phone book and CRI come from scout_reference (CH_REFERENCE_DATABASE): scout_reference.sustel / phonebook / cri, the tables the
ingestion service's Access (.accdb) pipeline writes, read with FINAL and without tombstoned rows. They are the one source of Sustel, phone book and CRI for every reader. The mapping files name them by token (__SUSTEL__, __PHONEBOOK__, __CRI__); app/mapping.py's
resolve_tokens puts these subqueries in their place. A copy of the ingestion service's app/services/lea_reference.py (and of
ipdr-analytics-v2's): keep them the same.

Each function returns a subquery that presents the reference table under the LEGACY column names and types (IDNo, telno, `Case`,
Telephone, `User`, tCRIID, tRight10Target, ...), so a query swaps its table name for the subquery and keeps its column references.
The Access layout keeps numbers as digit strings; here they are numbers again (NULL when blank), as the legacy columns were. CRI has no
officer column in the Access layout: Officerconcerned is ''. A phone book entry's id is its phonebook_id (UInt64, the bridges store it).
"""
from typing import Optional


def _ref(ref: Optional[str]) -> str:
    if ref:
        return ref
    from app.config import settings
    return settings.ch_reference_database


def resolve(sql: str, ref: Optional[str] = None) -> str:
    """`sql` with __SUSTEL__ / __PHONEBOOK__ / __CRI__ replaced by the subqueries."""
    if "__" not in sql:
        return sql
    return (sql.replace("__SUSTEL__", sustel_sql(ref)).replace("__PHONEBOOK__", phonebook_sql(ref))
               .replace("__CRI__", cri_sql(ref)))


def _num(col: str) -> str:
    return f"toInt64OrNull(nullIf(replaceRegexpAll(r.{col}, '[^0-9]', ''), ''))"


def sustel_sql(ref: Optional[str] = None) -> str:
    return (
        "(SELECT r.id_no AS IDNo, toString(r.sssid) AS SSSID, " + _num("telno") + " AS telno, " + _num("telno0") + " AS telno0, "
        "r.orgcode AS orgcode, r.case_name AS `Case`, r.orggroup AS Orggroup, r.username AS username, r.telcode AS telcode, "
        "r.teladdress AS teladdress, r.country AS country, r.other_information AS Other_Information, r.reference AS reference, "
        "toDateTime(r.refdate) AS refdate, r.monitored AS monitored, toDateTime(r.date_of_1st_sanction) AS DateOf1stSanction, "
        "toDateTime(r.renew_dt) AS RenewDt, toDateTime(r.validtill) AS validtill, toDateTime(r.last_san_dt) AS LastSanDt, "
        "toString(r.renew_before) AS RenewBefore, r.sanlist AS Sanlist, r.group_name AS `Group`, r.monitorby_old AS MonitorbyOld, "
        "r.monitorby AS Monitorby, r.migration_action_taken AS MigrationActionTaken, toDateTime(r.oprt_since) AS Oprt_since, "
        "r.ectel AS ECTEL, toInt64OrNull(r.proposal_no) AS `Propsal no`, toDateTime(r.ltr_dt) AS LTR_DT, r.da AS DA, r.re AS RE, "
        "r.remarks AS remarks, toDateTime(r.dt419) AS Dt419, toDateTime(r.dt_proposal) AS DtProposal, toString(r.dt_delete) AS DtDelete, "
        "r.officer_concerned AS OfficerConcerned, toDateTime(r.date_of_2nd_sanction) AS DateOf2ndSanction "
        f"FROM {_ref(ref)}.sustel AS r FINAL WHERE r._is_deleted = 0)"
    )


def phonebook_sql(ref: Optional[str] = None) -> str:
    return (
        "(SELECT r.phonebook_id AS id, toInt64OrZero(replaceRegexpAll(r.telephone, '[^0-9]', '')) AS Telephone, "
        "r.user_name AS `User`, r.place AS Place, r.district AS District, r.state AS State, r.country AS Country, r.remarks AS Remarks, "
        "r.reference AS Reference, toDate(r.ref_date) AS `Date`, toDateTime(r.entry_date) AS `Date of entry` "
        f"FROM {_ref(ref)}.phonebook AS r FINAL WHERE r._is_deleted = 0)"
    )


def cri_sql(ref: Optional[str] = None) -> str:
    return (
        "(SELECT r.cdr_id AS id, r.cdr_id AS tCDRID, r.cri_id AS tCRIID, " + _num("target_no") + " AS tTargetNo, "
        + _num("calling_no") + " AS tCallingNo, " + _num("called_no") + " AS tCalledNo, toDateTime(r.call_date) AS tDate, "
        "r.call_type AS tCallType, r.call_direction AS tCallDirection, " + _num("right10_target") + " AS tRight10Target, "
        + _num("right10_calling_no") + " AS tRight10CallingNo, " + _num("right10_called_no") + " AS tRight10CalledNo, "
        + _num("right10_other_no") + " AS tRight10OtherNo, " + _num("other_no") + " AS tOtherNo, '' AS Officerconcerned, "
        "parseDateTimeBestEffortOrNull(r.entered_on) AS EnterON "
        f"FROM {_ref(ref)}.cri AS r FINAL WHERE r._is_deleted = 0)"
    )
