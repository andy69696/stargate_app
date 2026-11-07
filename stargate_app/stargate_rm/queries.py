"""SQL for every Kantata pull the Resource manager views need, against the Salesforce bronze tables.

Each function returns the SQL that reproduces one SOQL query from the signed-off runbooks (rmpull/tools/RUNBOOK.md and
the Resourcing & margin runbook), so the legacy builders in stargate_rm/legacy run unchanged on the lake.  Column aliases
use the SOQL relationship path (`KimbleOne__Resource__r.Name`) so db.records() can nest them exactly like a SOQL result.

Conventions
  * Objects are lower-cased table names in <catalog>.<schema>: KimbleOne__Resource__c -> kimbleone__resource__c.
  * Salesforce lookups (Foo__r.Name) are LEFT JOINs on Id.  If a reference object is not in bronze, the join returns NULL;
    every such table is listed in REFERENCE_OBJECTS so the gap is easy to spot in Databricks.
  * Dates are passed in as ISO strings.  LIMITs from SOQL are dropped: the warehouse returns everything.
  * Soft-deleted rows: bronze keeps Salesforce's IsDeleted; every query filters isdeleted = false where the column exists.
"""
from __future__ import annotations

from . import config, db
from .config import kt, MS_PRODUCT_GROUPS

# Reference objects the joins rely on (checked against the bronze column export of 6 Nov 2026).  Not in bronze:
# KimbleOne__ProductGroup__c (names come from config.PRODUCT_GROUP_NAMES until it is ingested), KimbleOne__Location__c,
# KimbleOne__TimePattern__c, KimbleOne__UnitType__c (display only: left empty), KimbleOne__ResourceActivityRole__c (the matcher
# falls back to each resource's own activity role).  Joining a missing table fails the whole query, so these are joined only
# when db.table_exists() says they are there.
REFERENCE_OBJECTS = [
    "KimbleOne__ResourceType__c", "KimbleOne__Grade__c", "KimbleOne__BusinessUnit__c", "KimbleOne__BusinessUnitGroup__c",
    "KimbleOne__ActivityRole__c", "KimbleOne__TimePeriod__c", "KimbleOne__PeriodType__c",
    "KimbleOne__ForecastStatus__c", "KimbleOne__DeliveryStage__c", "KimbleOne__ResourcedActivity__c",
    "KimbleOne__AnalysisFact__c", "KimbleOne__DeliveryElement__c", "KimbleOne__DomainClass__c", "KimbleOne__CapabilityType__c",
    "KimbleOne__CapabilityGroup__c", "KimbleOne__PerformanceAnalysis__c", "Account",
]
OPTIONAL_REFERENCE_OBJECTS = ["KimbleOne__ProductGroup__c", "KimbleOne__Location__c", "KimbleOne__TimePattern__c",
                              "KimbleOne__UnitType__c", "KimbleOne__ResourceActivityRole__c"]


def has(obj: str) -> bool:
    return db.table_exists(kt(obj))


def pg_join(dg: str = "dg") -> str:
    """Join for the product group name, or nothing when the table is not in bronze."""
    return f"\n      LEFT JOIN {kt('KimbleOne__ProductGroup__c')} pg ON pg.id = {dg}.kimbleone__productgroup__c" if has("KimbleOne__ProductGroup__c") else ""


def pg_name(dg: str = "dg") -> str:
    """Product group name: from the table when ingested, else from config.PRODUCT_GROUP_NAMES by Id, else NULL."""
    if has("KimbleOne__ProductGroup__c"):
        return "pg.name"
    if not config.PRODUCT_GROUP_NAMES:
        return "CAST(NULL AS STRING)"
    whens = " ".join(f"WHEN '{k.replace(chr(39), '')}' THEN '{v.replace(chr(39), chr(39) * 2)}'" for k, v in config.PRODUCT_GROUP_NAMES.items())
    return f"(CASE {dg}.kimbleone__productgroup__c {whens} END)"

ABSENCE_ACTIVITIES = ("Planned absence (Bamboo)", "Unplanned absence (Bamboo)", "Contracted Non-Working day", "Non Working Day(3BDS)",
                      "IOC Shift Rest Day", "Supplier Unavailability")


def _in(vals) -> str:
    return "(" + ", ".join("'" + v.replace("'", "''") + "'" for v in vals) + ")"


# ---- shared join blocks ------------------------------------------------------------------------------------------------
def _resource_joins(alias="r"):
    return f"""
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = {alias}.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__Grade__c')} gr ON gr.id = {alias}.kimbleone__grade__c
      LEFT JOIN {kt('KimbleOne__BusinessUnit__c')} bu ON bu.id = {alias}.kimbleone__businessunit__c
      LEFT JOIN {kt('KimbleOne__BusinessUnitGroup__c')} bug ON bug.id = bu.kimbleone__businessunitgroup__c
      LEFT JOIN {kt('KimbleOne__ActivityRole__c')} rrole ON rrole.id = {alias}.kimbleone__activityrole__c"""


def _apa_joins():
    """ActivityPeriodAssignment with its time period, resource (+type), assignment and delivery group (+status, account, product group, activity)."""
    return f"""
      FROM {kt('KimbleOne__ActivityPeriodAssignment__c')} apa
      JOIN {kt('KimbleOne__TimePeriod__c')} tp ON tp.id = apa.kimbleone__timeperiod__c
      LEFT JOIN {kt('KimbleOne__PeriodType__c')} pt ON pt.id = tp.kimbleone__periodtype__c
      LEFT JOIN {kt('KimbleOne__Resource__c')} r ON r.id = apa.kimbleone__resource__c
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__ActivityAssignment__c')} aa ON aa.id = apa.kimbleone__activityassignment__c
      LEFT JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = aa.kimbleone__deliverygroup__c
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
      LEFT JOIN Account_ acc ON acc.id = dg.kimbleone__account__c{pg_join()}
      LEFT JOIN {kt('KimbleOne__ResourcedActivity__c')} ra ON ra.id = aa.kimbleone__resourcedactivity__c""".replace("Account_", kt("Account"))


_CLIENT_ENG = """rt.name IN ('Employee','Supplier','Partner') AND aa.kimbleone__deliverygroup__c IS NOT NULL
      AND NOT dg.name LIKE 'UBDS-%' AND COALESCE(fs.name, '') <> 'Lost Closed'"""


# ---- 1a resources (RM runbook) and match/resources.json (same object, superset of fields) ---------------------------------
def resources(prev_monday: str) -> str:
    return f"""
    SELECT r.id AS `Id`, r.name AS `Name`, rt.name AS `KimbleOne__ResourceType__r.Name`, gr.name AS `KimbleOne__Grade__r.Name`,
           bu.name AS `KimbleOne__BusinessUnit__r.Name`, bug.name AS `KimbleOne__BusinessUnit__r.KimbleOne__BusinessUnitGroup__r.Name`,
           bus.name AS `KimbleOne__BusinessUnitSecondary__r.Name`, rrole.name AS `KimbleOne__ActivityRole__r.Name`,
           r.budget_role__c AS `Budget_role__c`, CAST(NULL AS STRING) AS `KimbleOne__Location__r.Name`,
           r.kimbleone__startdate__c AS `KimbleOne__StartDate__c`, r.kimbleone__enddate__c AS `KimbleOne__EndDate__c`,
           r.ubds_contract_end_date__c AS `UBDS_Contract_end_date__c`, CAST(NULL AS STRING) AS `KimbleOne__TimePattern__r.Name`,
           r.kimbleone__standardcost__c AS `KimbleOne__StandardCost__c`, CAST(NULL AS STRING) AS `KimbleOne__StandardCostUnitType__r.Name`,
           r.kimbleone__standardrevenue__c AS `KimbleOne__StandardRevenue__c`,
           r.kimbleone__latestp1assignmentenddate__c AS `KimbleOne__LatestP1AssignmentEndDate__c`,
           r.suppress_utilisation_reporting__c AS `Suppress_Utilisation_reporting__c`
      FROM {kt('KimbleOne__Resource__c')} r
      {_resource_joins('r')}
      LEFT JOIN {kt('KimbleOne__BusinessUnit__c')} bus ON bus.id = r.kimbleone__businessunitsecondary__c
     WHERE rt.name IN ('Employee','Supplier','Partner') AND COALESCE(r.isdeleted, false) = false
       AND (r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{prev_monday}')
     ORDER BY bu.name, r.name"""


# ---- 1b weekly demand on client engagements by resource x week -----------------------------------------------------------
def wk_billable(d_from: str, d_to: str) -> str:
    return f"""
    SELECT apa.kimbleone__resource__c AS rid, tp.kimbleone__startdate__c AS sd,
           SUM(apa.kimbleone__forecastp1usage__c) AS u1, SUM(apa.kimbleone__forecastp2usage__c) AS u2, SUM(apa.kimbleone__forecastp3usage__c) AS u3,
           SUM(apa.kimbleone__forecastp2revenue__c) AS r2, SUM(apa.kimbleone__forecastp2cost__c) AS c2, COUNT(apa.id) AS n
      {_apa_joins()}
     WHERE pt.name = 'Week' AND tp.kimbleone__startdate__c BETWEEN DATE '{d_from}' AND DATE '{d_to}'
       AND {_CLIENT_ENG}
     GROUP BY apa.kimbleone__resource__c, tp.kimbleone__startdate__c"""


# ---- 1c weekly absence by resource x week x activity ----------------------------------------------------------------------
def wk_absence(d_from: str, d_to: str) -> str:
    return f"""
    SELECT apa.kimbleone__resource__c AS rid, tp.kimbleone__startdate__c AS sd, ra.name AS act,
           SUM(apa.kimbleone__forecastp3usage__c) AS u3, COUNT(apa.id) AS n
      {_apa_joins()}
     WHERE pt.name = 'Week' AND tp.kimbleone__startdate__c BETWEEN DATE '{d_from}' AND DATE '{d_to}'
       AND rt.name IN ('Employee','Supplier','Partner') AND aa.kimbleone__deliverygroup__c IS NULL
       AND ra.name IN {_in(ABSENCE_ACTIVITIES)} AND apa.kimbleone__forecastp3usage__c > 0
     GROUP BY apa.kimbleone__resource__c, tp.kimbleone__startdate__c, ra.name"""


# ---- 1h weekly demand by engagement (hover on each week cell) --------------------------------------------------------------
def wk_eng(d_from: str, d_to: str) -> str:
    return f"""
    SELECT apa.kimbleone__resource__c AS rid, tp.kimbleone__startdate__c AS sd, dg.name AS dg,
           SUM(apa.kimbleone__forecastp1usage__c) AS u1, SUM(apa.kimbleone__forecastp2usage__c) AS u2, SUM(apa.kimbleone__forecastp3usage__c) AS u3, COUNT(apa.id) AS n
      {_apa_joins()}
     WHERE pt.name = 'Week' AND tp.kimbleone__startdate__c BETWEEN DATE '{d_from}' AND DATE '{d_to}'
       AND {_CLIENT_ENG} AND apa.kimbleone__forecastp3usage__c > 0
     GROUP BY apa.kimbleone__resource__c, tp.kimbleone__startdate__c, dg.name"""


# ---- 1d monthly cost and revenue by resource x month x engagement ------------------------------------------------------------
def mo_margin(m_from: str, m_to: str) -> str:
    return f"""
    SELECT apa.kimbleone__resource__c AS rid, tp.kimbleone__startdate__c AS sd, dg.name AS dg,
           SUM(apa.kimbleone__forecastp2revenue__c) AS r2, SUM(apa.kimbleone__forecastp2cost__c) AS c2,
           SUM(apa.kimbleone__forecastp1revenue__c) AS r1, SUM(apa.kimbleone__forecastp1cost__c) AS c1,
           SUM(apa.kimbleone__forecastp2usage__c) AS u2, SUM(apa.kimbleone__forecastp3revenue__c) AS r3, SUM(apa.kimbleone__forecastp3cost__c) AS c3, COUNT(apa.id) AS n
      {_apa_joins()}
     WHERE pt.name = 'Month' AND tp.kimbleone__startdate__c BETWEEN DATE '{m_from}' AND DATE '{m_to}'
       AND {_CLIENT_ENG}
     GROUP BY apa.kimbleone__resource__c, tp.kimbleone__startdate__c, dg.name"""


# ---- 1e / 1f Kantata forecast ledger (PerformanceAnalysis, fact Revenue) ----------------------------------------------------
# "period revenue" in the lake request: the revenue Kantata itself attributes per person and period.
# TODO(databricks): confirm KimbleOne__PerformanceAnalysis__c is ingested; if the lake exposes a separate period-revenue
#      object instead, point these two queries at it (fields: resource, delivery group, delivery element, domain class,
#      time period, P1/P2/P3 forecast).
def _pa_joins():
    return f"""
      FROM {kt('KimbleOne__PerformanceAnalysis__c')} pa
      JOIN {kt('KimbleOne__TimePeriod__c')} tp ON tp.id = pa.kimbleone__timeperiod__c
      LEFT JOIN {kt('KimbleOne__AnalysisFact__c')} af ON af.id = pa.kimbleone__analysisfact__c
      LEFT JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = pa.kimbleone__deliverygroup__c
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c"""


def mo_ledger_rev(m_from: str, m_to: str) -> str:
    return f"""
    SELECT pa.kimbleone__resource__c AS rid, YEAR(tp.kimbleone__startdate__c) AS y, MONTH(tp.kimbleone__startdate__c) AS m, dg.name AS dg,
           SUM(pa.kimbleone__p2forecast__c) AS p2, SUM(pa.kimbleone__p1forecast__c) AS p1, SUM(pa.kimbleone__p3forecast__c) AS p3, COUNT(pa.id) AS n
      {_pa_joins()}
     WHERE af.name = 'Revenue' AND pa.kimbleone__resource__c IS NOT NULL
       AND tp.kimbleone__startdate__c BETWEEN DATE '{m_from}' AND DATE '{m_to}' AND COALESCE(fs.name, '') <> 'Lost Closed'
     GROUP BY pa.kimbleone__resource__c, YEAR(tp.kimbleone__startdate__c), MONTH(tp.kimbleone__startdate__c), dg.name"""


def mo_ledger_fixed(m_from: str, m_to: str) -> str:
    return f"""
    SELECT dg.name AS dg, de.name AS el, dc.name AS dc, YEAR(tp.kimbleone__startdate__c) AS y, MONTH(tp.kimbleone__startdate__c) AS m,
           SUM(pa.kimbleone__p2forecast__c) AS p2, SUM(pa.kimbleone__p1forecast__c) AS p1, SUM(pa.kimbleone__p3forecast__c) AS p3, COUNT(pa.id) AS n
      {_pa_joins()}
      LEFT JOIN {kt('KimbleOne__DeliveryElement__c')} de ON de.id = pa.kimbleone__deliveryelement__c
      LEFT JOIN {kt('KimbleOne__DomainClass__c')} dc ON dc.id = pa.kimbleone__domainclass__c
     WHERE af.name = 'Revenue' AND pa.kimbleone__resource__c IS NULL AND pa.kimbleone__domainclass__c IS NOT NULL
       AND tp.kimbleone__startdate__c BETWEEN DATE '{m_from}' AND DATE '{m_to}' AND COALESCE(fs.name, '') <> 'Lost Closed'
     GROUP BY dg.name, de.name, dc.name, YEAR(tp.kimbleone__startdate__c), MONTH(tp.kimbleone__startdate__c)"""


# ---- 1g last assignment dates per resource x engagement --------------------------------------------------------------------
def _aa_joins():
    return f"""
      FROM {kt('KimbleOne__ActivityAssignment__c')} aa
      LEFT JOIN {kt('KimbleOne__Resource__c')} r ON r.id = aa.kimbleone__resource__c
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = aa.kimbleone__deliverygroup__c
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
      LEFT JOIN {kt('Account')} acc ON acc.id = dg.kimbleone__account__c
      LEFT JOIN {kt('KimbleOne__ActivityRole__c')} arole ON arole.id = aa.kimbleone__activityrole__c"""


def assignments(prev_monday: str) -> str:
    return f"""
    SELECT aa.kimbleone__resource__c AS rid, dg.name AS dg, fs.name AS st, MAX(aa.kimbleone__forecastp3enddate__c) AS endp3,
           MIN(aa.kimbleone__startdate__c) AS startd, SUM(aa.kimbleone__forecastremainingusage__c) AS remaining, COUNT(aa.id) AS n
      {_aa_joins()}
     WHERE rt.name IN ('Employee','Supplier','Partner') AND aa.kimbleone__deliverygroup__c IS NOT NULL AND NOT dg.name LIKE 'UBDS-%'
       AND COALESCE(fs.name, '') <> 'Lost Closed' AND aa.kimbleone__forecastp3enddate__c >= DATE '{prev_monday}' AND COALESCE(aa.isdeleted, false) = false
     GROUP BY aa.kimbleone__resource__c, dg.name, fs.name"""


# ---- Resourcing & margin: generic placeholders and named assignments --------------------------------------------------------
_AA_FIELDS = """aa.id AS `Id`, r.name AS `KimbleOne__Resource__r.Name`, rt.name AS `KimbleOne__Resource__r.KimbleOne__ResourceType__r.Name`,
           dg.name AS `KimbleOne__DeliveryGroup__r.Name`, acc.name AS `KimbleOne__DeliveryGroup__r.KimbleOne__Account__r.Name`,
           fs.name AS `KimbleOne__DeliveryGroup__r.KimbleOne__ForecastStatus__r.Name`, arole.name AS `KimbleOne__ActivityRole__r.Name`,
           aa.kimbleone__startdate__c AS `KimbleOne__StartDate__c`, aa.kimbleone__forecastp3enddate__c AS `KimbleOne__ForecastP3EndDate__c`,
           aa.kimbleone__margin__c AS `KimbleOne__Margin__c`, aa.kimbleone__forecastrevenue__c AS `KimbleOne__ForecastRevenue__c`,
           aa.kimbleone__forecastcost__c AS `KimbleOne__ForecastCost__c`, aa.kimbleone__forecastusage__c AS `KimbleOne__ForecastUsage__c`,
           aa.kimbleone__forecastremainingusage__c AS `KimbleOne__ForecastRemainingUsage__c`,
           aa.kimbleone__totalactualrevenue__c AS `KimbleOne__TotalActualRevenue__c`, aa.kimbleone__totalactualcost__c AS `KimbleOne__TotalActualCost__c`"""


def generic(floor: str) -> str:
    # Recruitment__c and Client_Role__c are UBDS custom fields on the assignment.  TODO(databricks): confirm both are in bronze.
    return f"""
    SELECT {_AA_FIELDS}, aa.client_role__c AS `Client_Role__c`, aa.recruitment__c AS `Recruitment__c`
      {_aa_joins()}
     WHERE rt.name = 'Generic Resource' AND aa.kimbleone__forecastp3enddate__c >= DATE '{floor}'
       AND COALESCE(fs.name, '') <> 'Lost Closed' AND COALESCE(aa.isdeleted, false) = false
     ORDER BY aa.kimbleone__startdate__c"""


def named(floor: str) -> str:
    return f"""
    SELECT {_AA_FIELDS}
      {_aa_joins()}
     WHERE rt.name IN ('Employee','Supplier','Partner') AND aa.kimbleone__forecastp3enddate__c >= DATE '{floor}'
       AND COALESCE(fs.name, '') <> 'Lost Closed' AND aa.kimbleone__margin__c IS NOT NULL AND COALESCE(aa.isdeleted, false) = false
     ORDER BY aa.kimbleone__startdate__c"""


def months(m_from: str, m_to: str) -> str:
    """Monthly billable series by engagement x resource type (Margin watch trend and By engagement)."""
    return f"""
    SELECT dg.name AS dg, acc.name AS acc, fs.name AS st, tp.kimbleone__startdate__c AS sd, rt.name AS rt, COUNT(apa.id) AS n,
           SUM(apa.kimbleone__forecastp3revenue__c) AS rev, SUM(apa.kimbleone__forecastp3cost__c) AS cost, SUM(apa.kimbleone__forecastp3usage__c) AS `usage`,
           SUM(apa.kimbleone__forecastp1revenue__c) AS p1rev, SUM(apa.kimbleone__forecastp2revenue__c) AS p2rev,
           SUM(apa.kimbleone__actualrevenue__c) AS arev, SUM(apa.kimbleone__actualcost__c) AS acost, SUM(apa.kimbleone__actualusage__c) AS ausage
      {_apa_joins()}
     WHERE pt.name = 'Month' AND tp.kimbleone__startdate__c BETWEEN DATE '{m_from}' AND DATE '{m_to}'
       AND COALESCE(fs.name, '') <> 'Lost Closed' AND aa.kimbleone__margin__c IS NOT NULL
     GROUP BY dg.name, acc.name, fs.name, tp.kimbleone__startdate__c, rt.name"""


def ms_engagements(today: str) -> str:
    return f"""
    SELECT dg.name AS `Name`, {pg_name()} AS `KimbleOne__ProductGroup__r.Name`
      FROM {kt('KimbleOne__DeliveryGroup__c')} dg{pg_join()}
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
     WHERE {pg_name()} IN {_in(MS_PRODUCT_GROUPS)} AND (dg.kimbleone__expectedenddate__c >= DATE '{today}' OR dg.kimbleone__expectedenddate__c IS NULL)
       AND COALESCE(fs.name, '') <> 'Lost Closed'"""


def eng_pg(today: str) -> str:
    return f"""
    SELECT dg.name AS `Name`, {pg_name()} AS `KimbleOne__ProductGroup__r.Name`, fs.name AS `KimbleOne__ForecastStatus__r.Name`,
           dg.kimbleone__expectedenddate__c AS `KimbleOne__ExpectedEndDate__c`
      FROM {kt('KimbleOne__DeliveryGroup__c')} dg{pg_join()}
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
     WHERE (dg.kimbleone__expectedenddate__c >= DATE '{today}' OR dg.kimbleone__expectedenddate__c IS NULL) AND COALESCE(fs.name, '') <> 'Lost Closed'
     ORDER BY dg.name"""


def not_detailed(today: str) -> str:
    return f"""
    SELECT dg.name AS `Name`, acc.name AS `KimbleOne__Account__r.Name`, fs.name AS `KimbleOne__ForecastStatus__r.Name`,
           dg.kimbleone__forecastatthislevel__c AS `KimbleOne__ForecastAtThisLevel__c`, dg.kimbleone__contractrevenue__c AS `KimbleOne__ContractRevenue__c`,
           dg.kimbleone__expectedstartdate__c AS `KimbleOne__ExpectedStartDate__c`, dg.kimbleone__expectedenddate__c AS `KimbleOne__ExpectedEndDate__c`,
           {pg_name()} AS `KimbleOne__ProductGroup__r.Name`, ds.name AS `KimbleOne__DeliveryStage__r.Name`
      FROM {kt('KimbleOne__DeliveryGroup__c')} dg
      LEFT JOIN {kt('Account')} acc ON acc.id = dg.kimbleone__account__c
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c{pg_join()}
      LEFT JOIN {kt('KimbleOne__DeliveryStage__c')} ds ON ds.id = dg.kimbleone__deliverystage__c
     WHERE COALESCE(fs.name, '') <> 'Lost Closed' AND (dg.kimbleone__expectedenddate__c >= DATE '{today}' OR dg.kimbleone__expectedenddate__c IS NULL)
       AND COALESCE(dg.kimbleone__forecastatthislevel__c, 'Y') <> 'N'"""


# ---- matcher inputs ---------------------------------------------------------------------------------------------------------
def res_roles(today: str) -> str:
    if not has("KimbleOne__ResourceActivityRole__c"):     # not in bronze: each resource's own activity role
        return f"""
    SELECT r.id AS `KimbleOne__Resource__c`, arole.name AS `KimbleOne__ActivityRole__r.Name`
      FROM {kt('KimbleOne__Resource__c')} r
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__ActivityRole__c')} arole ON arole.id = r.kimbleone__activityrole__c
     WHERE rt.name IN ('Employee','Supplier','Partner') AND arole.name IS NOT NULL
       AND (r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{today}')"""
    return f"""
    SELECT rar.kimbleone__resource__c AS `KimbleOne__Resource__c`, arole.name AS `KimbleOne__ActivityRole__r.Name`
      FROM {kt('KimbleOne__ResourceActivityRole__c')} rar
      JOIN {kt('KimbleOne__Resource__c')} r ON r.id = rar.kimbleone__resource__c
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__ActivityRole__c')} arole ON arole.id = rar.kimbleone__activityrole__c
     WHERE rt.name IN ('Employee','Supplier','Partner') AND (r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{today}')"""


def booked(m_from: str, m_to: str) -> str:
    return f"""
    SELECT apa.kimbleone__resource__c AS res, tp.kimbleone__startdate__c AS sd, SUM(apa.kimbleone__forecastp3usage__c) AS u3,
           SUM(apa.kimbleone__forecastp1usage__c) AS u1, SUM(apa.kimbleone__forecastp3revenue__c) AS rev
      {_apa_joins()}
     WHERE pt.name = 'Month' AND tp.kimbleone__startdate__c BETWEEN DATE '{m_from}' AND DATE '{m_to}'
       AND rt.name IN ('Employee','Supplier','Partner') AND COALESCE(fs.name, '') <> 'Lost Closed'
     GROUP BY apa.kimbleone__resource__c, tp.kimbleone__startdate__c"""


def booked_type(m_from: str, m_to: str) -> str:
    return f"""
    SELECT apa.kimbleone__resource__c AS res, tp.kimbleone__startdate__c AS sd, {pg_name()} AS pg, SUM(apa.kimbleone__forecastp3usage__c) AS u3
      {_apa_joins()}
     WHERE pt.name = 'Month' AND tp.kimbleone__startdate__c BETWEEN DATE '{m_from}' AND DATE '{m_to}'
       AND rt.name IN ('Employee','Supplier','Partner') AND COALESCE(fs.name, '') <> 'Lost Closed'
     GROUP BY apa.kimbleone__resource__c, tp.kimbleone__startdate__c, {pg_name()}"""


def capabilities(today: str) -> str:
    """Skills ratings (ResourceCapability -> CapabilityType -> CapabilityGroup); one query replaces the six group-set pulls."""
    return f"""
    SELECT rc.kimbleone__resource__c AS `KimbleOne__Resource__c`, cg.name AS `KimbleOne__CapabilityType__r.KimbleOne__CapabilityGroup__r.Name`,
           ct.name AS `KimbleOne__CapabilityType__r.Name`, rc.kimbleone__numericrating__c AS `KimbleOne__NumericRating__c`,
           rc.kimbleone__booleanrating__c AS `KimbleOne__BooleanRating__c`
      FROM {kt('KimbleOne__ResourceCapability__c')} rc
      JOIN {kt('KimbleOne__Resource__c')} r ON r.id = rc.kimbleone__resource__c
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__CapabilityType__c')} ct ON ct.id = rc.kimbleone__capabilitytype__c
      LEFT JOIN {kt('KimbleOne__CapabilityGroup__c')} cg ON cg.id = ct.kimbleone__capabilitygroup__c
     WHERE rt.name IN ('Employee','Supplier','Partner') AND (r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{today}')"""


# ---- additional lake objects (new in salesforce_dev.bronze) used to enrich the views and the Explain facts -------------------
def required_capabilities() -> str:
    """Skills a generic role asks for (RequiredCapability on the activity assignment)."""
    return f"""
    SELECT rq.kimbleone__activityassignment__c AS aid, ct.name AS capability, cg.name AS capability_group, rq.kimbleone__minimumnumericrating__c AS min_rating
      FROM {kt('KimbleOne__RequiredCapability__c')} rq
      LEFT JOIN {kt('KimbleOne__CapabilityType__c')} ct ON ct.id = rq.kimbleone__capabilitytype__c
      LEFT JOIN {kt('KimbleOne__CapabilityGroup__c')} cg ON cg.id = ct.kimbleone__capabilitygroup__c
     WHERE COALESCE(rq.isdeleted, false) = false"""


def dg_actors() -> str:
    """Who is who on each engagement (DeliveryGroupActor): used so Explain can name the account lead / delivery principal."""
    return f"""
    SELECT dg.name AS dg, ar.name AS actor_role, u.name AS actor
      FROM {kt('KimbleOne__DeliveryGroupActor__c')} a
      JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = a.kimbleone__deliverygroup__c
      LEFT JOIN {kt('KimbleOne__ActorRole__c')} ar ON ar.id = a.kimbleone__actorrole__c
      LEFT JOIN {kt('User')} u ON u.id = a.kimbleone__user__c
     WHERE COALESCE(a.isdeleted, false) = false"""


def status_summaries() -> str:
    """Current RAG status per engagement: the delivery group's status summary, its delivery status (the RAG) and narrative."""
    return f"""
    SELECT dg.name AS dg, st.name AS rag, s.kimbleone__narrative__c AS summary, s.lastmodifieddate AS modified
      FROM {kt('KimbleOne__DeliveryGroup__c')} dg
      JOIN {kt('KimbleOne__StatusSummary__c')} s ON s.id = dg.kimbleone__deliverygroupstatussummary__c
      LEFT JOIN {kt('KimbleOne__DeliveryStatus__c')} st ON st.id = s.kimbleone__deliverystatus__c
     WHERE COALESCE(s.isdeleted, false) = false"""


def purchase_orders() -> str:
    """PO cover per engagement: used as the third system check on Not detailed (Kantata value vs HubSpot value vs PO value)."""
    return f"""
    SELECT dg.name AS dg, SUM(poa.kimbleone__allocatedvalue__c) AS po_value, MIN(po.kimbleone__orderdate__c) AS po_start,
           MAX(po.kimbleone__expirydate__c) AS po_end, COUNT(DISTINCT po.id) AS po_n
      FROM {kt('KimbleOne__PurchaseOrderAllocation__c')} poa
      JOIN {kt('KimbleOne__PurchaseOrder__c')} po ON po.id = poa.kimbleone__purchaseorder__c
      JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = poa.kimbleone__deliverygroup__c
     WHERE COALESCE(poa.isdeleted, false) = false AND COALESCE(po.isdeleted, false) = false
     GROUP BY dg.name"""


def invoices() -> str:
    """Invoiced to date per engagement, used in Margin watch's engagement facts.  Invoices carry no delivery group in bronze
    (invoice items are not ingested), so this is the invoiced value of each engagement's purchase-order allocations: invoicing
    not raised against a PO is not counted."""
    return f"""
    SELECT dg.name AS dg, SUM(poa.kimbleone__invoicedvalue__c) AS invoiced, MAX(poa.kimbleone__latestinvoiceitemdate__c) AS last_invoice
      FROM {kt('KimbleOne__PurchaseOrderAllocation__c')} poa
      JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = poa.kimbleone__deliverygroup__c
     WHERE COALESCE(poa.isdeleted, false) = false
     GROUP BY dg.name"""


def time_entries(d_from: str) -> str:
    """Actual days booked per person x engagement since d_from (TimeEntry via Timesheet): the 'actual so far' behind a margin row."""
    return f"""
    SELECT te.kimbleone__resource__c AS rid, dg.name AS dg, SUM(te.kimbleone__entryunits__c) AS units, MAX(tp.kimbleone__startdate__c) AS last_entry
      FROM {kt('KimbleOne__TimeEntry__c')} te
      JOIN {kt('KimbleOne__TimePeriod__c')} tp ON tp.id = te.kimbleone__timeperiod__c
      LEFT JOIN {kt('KimbleOne__ActivityAssignment__c')} aa ON aa.id = te.kimbleone__activityassignment__c
      LEFT JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = aa.kimbleone__deliverygroup__c
     WHERE tp.kimbleone__startdate__c >= DATE '{d_from}' AND COALESCE(te.isdeleted, false) = false
     GROUP BY te.kimbleone__resource__c, dg.name"""


def hubspot_deals(table: str) -> str:
    """HubSpot deals for the Not detailed comparison.  The deals table has no engagement-code property, so the key is the
    CLIENT-PROJ prefix of the deal name (the same rule key_of() applies to Kantata delivery group names)."""
    return f"""
    SELECT COALESCE(NULLIF(regexp_extract(dealname, '^([A-Za-z0-9]+-[A-Za-z0-9]+)', 1), ''), dealname) AS key, amount,
           dealstage AS band, start_date AS start, end_date AS `end`, dealname AS name
      FROM {table}
     WHERE COALESCE(CAST(archived AS BOOLEAN), false) = false"""
