"""SQL for the gold build, against the same Kantata bronze tables as the rest of the app.

Forecasts are read from Kantata's MONTH periods only.  (The 4 Nov snapshot summed week and month periods together, which
doubled every revenue, cost and usage figure.)  Custom or unconfirmed columns come from fields.py and are passed in as
`opt`, a function returning the column expression or NULL when the column is missing.
"""
from __future__ import annotations

from ..config import kt
from ..queries import pg_join, pg_name

INTERNAL_DG = "NOT dg.name LIKE 'UBDS-%'"


def engagements(floor: str, opt) -> str:
    """Delivery groups that are not lost and end on or after `floor` (the first day of last month), or have no end date."""
    return f"""
    SELECT dg.id AS id, dg.name AS name, acc.name AS client, fs.name AS fstatus, ds.name AS stage,
           dg.kimbleone__expectedstartdate__c AS start, dg.kimbleone__expectedenddate__c AS `end`,
           {pg_name()} AS product_group, dg.kimbleone__forecastatthislevel__c AS at_level, pbu.bu AS product_bu,
           {opt('dg', 'contract_revenue')} AS rev, {opt('dg', 'contract_cost')} AS cost,
           {opt('dg', 'baseline_revenue')} AS brev, {opt('dg', 'baseline_cost')} AS bcost,
           {opt('dg', 'offering')} AS offering, {opt('dg', 'pre_mob')} AS pre_mob, {opt('dg', 'hubspot_deal')} AS hs_deal
      FROM {kt('KimbleOne__DeliveryGroup__c')} dg
      LEFT JOIN {kt('Account')} acc ON acc.id = dg.kimbleone__account__c
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
      LEFT JOIN {kt('KimbleOne__DeliveryStage__c')} ds ON ds.id = dg.kimbleone__deliverystage__c{pg_join()}
      LEFT JOIN (SELECT de.kimbleone__deliverygroup__c AS dg_id, MIN(bu.name) AS bu      -- business unit of the engagement's products
                   FROM {kt('KimbleOne__DeliveryElement__c')} de
                   JOIN {kt('KimbleOne__Product__c')} p ON p.id = de.kimbleone__product__c
                   JOIN {kt('KimbleOne__BusinessUnit__c')} bu ON bu.id = p.kimbleone__businessunit__c
                  WHERE COALESCE(de.isdeleted, false) = false
                  GROUP BY de.kimbleone__deliverygroup__c) pbu ON pbu.dg_id = dg.id
     WHERE COALESCE(fs.name, '') <> 'Lost Closed' AND {INTERNAL_DG} AND COALESCE(dg.isdeleted, false) = false
       AND (dg.kimbleone__expectedenddate__c >= DATE '{floor}' OR dg.kimbleone__expectedenddate__c IS NULL)"""


def monthly(m_from: str, m_to: str, opt) -> str:
    """Forecast and actuals by assignment x month (month periods only), for every resource type including generics."""
    return f"""
    SELECT aa.id AS aid, apa.kimbleone__resource__c AS rid, r.name AS rname, rt.name AS rtype, gr.name AS grade,
           aa.kimbleone__deliverygroup__c AS dg, arole.name AS role, {opt('aa', 'client_role')} AS client_role,
           aa.kimbleone__startdate__c AS start, DATE_FORMAT(tp.kimbleone__startdate__c, 'yyyy-MM') AS month,
           SUM(apa.kimbleone__forecastp1usage__c) AS u1, SUM(apa.kimbleone__forecastp2usage__c) AS u2, SUM(apa.kimbleone__forecastp3usage__c) AS u3,
           SUM(apa.kimbleone__forecastp1revenue__c) AS r1, SUM(apa.kimbleone__forecastp2revenue__c) AS r2, SUM(apa.kimbleone__forecastp3revenue__c) AS r3,
           SUM(apa.kimbleone__forecastp1cost__c) AS c1, SUM(apa.kimbleone__forecastp2cost__c) AS c2, SUM(apa.kimbleone__forecastp3cost__c) AS c3,
           SUM(apa.kimbleone__actualusage__c) AS act
      FROM {kt('KimbleOne__ActivityPeriodAssignment__c')} apa
      JOIN {kt('KimbleOne__TimePeriod__c')} tp ON tp.id = apa.kimbleone__timeperiod__c
      LEFT JOIN {kt('KimbleOne__PeriodType__c')} pt ON pt.id = tp.kimbleone__periodtype__c
      JOIN {kt('KimbleOne__ActivityAssignment__c')} aa ON aa.id = apa.kimbleone__activityassignment__c
      LEFT JOIN {kt('KimbleOne__Resource__c')} r ON r.id = apa.kimbleone__resource__c
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__Grade__c')} gr ON gr.id = r.kimbleone__grade__c
      LEFT JOIN {kt('KimbleOne__ActivityRole__c')} arole ON arole.id = aa.kimbleone__activityrole__c
      JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = aa.kimbleone__deliverygroup__c
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
     WHERE pt.name = 'Month' AND tp.kimbleone__startdate__c BETWEEN DATE '{m_from}' AND DATE '{m_to}'
       AND COALESCE(fs.name, '') <> 'Lost Closed' AND {INTERNAL_DG} AND COALESCE(aa.isdeleted, false) = false
     GROUP BY aa.id, apa.kimbleone__resource__c, r.name, rt.name, gr.name, aa.kimbleone__deliverygroup__c, arole.name,
              {opt('aa', 'client_role')}, aa.kimbleone__startdate__c, DATE_FORMAT(tp.kimbleone__startdate__c, 'yyyy-MM')"""


def assignments(end_floor: str, opt) -> str:
    """Live assignments (named and generic) on client engagements, with whole-assignment forecast figures."""
    return f"""
    SELECT aa.id AS id, aa.kimbleone__deliverygroup__c AS dg, r.id AS rid, r.name AS name, rt.name AS type, gr.name AS grade, bu.name AS bu,
           arole.name AS role, {opt('aa', 'client_role')} AS client_role,
           aa.kimbleone__forecastrevenue__c AS rev, aa.kimbleone__forecastcost__c AS cost, aa.kimbleone__forecastusage__c AS `usage`,
           aa.kimbleone__forecastremainingusage__c AS remaining, aa.kimbleone__startdate__c AS start, aa.kimbleone__forecastp3enddate__c AS `end`,
           r.kimbleone__standardcost__c AS std_cost, r.ubds_contract_end_date__c AS contract_end
      FROM {kt('KimbleOne__ActivityAssignment__c')} aa
      JOIN {kt('KimbleOne__DeliveryGroup__c')} dg ON dg.id = aa.kimbleone__deliverygroup__c
      LEFT JOIN {kt('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
      LEFT JOIN {kt('KimbleOne__Resource__c')} r ON r.id = aa.kimbleone__resource__c
      LEFT JOIN {kt('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {kt('KimbleOne__Grade__c')} gr ON gr.id = r.kimbleone__grade__c
      LEFT JOIN {kt('KimbleOne__BusinessUnit__c')} bu ON bu.id = r.kimbleone__businessunit__c
      LEFT JOIN {kt('KimbleOne__ActivityRole__c')} arole ON arole.id = aa.kimbleone__activityrole__c
     WHERE rt.name IN ('Employee', 'Supplier', 'Partner', 'Generic Resource') AND COALESCE(fs.name, '') <> 'Lost Closed' AND {INTERNAL_DG}
       AND aa.kimbleone__forecastp3enddate__c >= DATE '{end_floor}' AND COALESCE(aa.isdeleted, false) = false"""


def milestones(obj: str, de_col: str, opt) -> str:
    """Milestones with their delivery group (through the delivery element they belong to)."""
    return f"""
    SELECT m.id AS id, m.name AS name, de.kimbleone__deliverygroup__c AS dg, {opt('m', 'description')} AS description,
           {opt('m', 'forecast')} AS forecast, {opt('m', 'contracted')} AS contracted, {opt('m', 'original_contracted')} AS original_contracted,
           {opt('m', 'rag')} AS rag, {opt('m', 'rag_updated')} AS rag_updated, {opt('m', 'delivered')} AS delivered, {opt('m', 'status')} AS status
      FROM {kt(obj)} m
      JOIN {kt('KimbleOne__DeliveryElement__c')} de ON de.id = m.{de_col}
     WHERE COALESCE(m.isdeleted, false) = false"""


def norm(expr: str) -> str:
    """Forecast status compared ignoring case, spaces and underscores ('most_likely' = 'Most Likely')."""
    return f"LOWER(REPLACE(REPLACE(CAST({expr} AS STRING), ' ', ''), '_', ''))"


def deals(table: str, opt, bands: tuple) -> str:
    band = opt("hs", "fstatus")
    closed = opt("hs", "closed")
    in_bands = ", ".join("'" + b.lower().replace(" ", "") + "'" for b in bands)
    return f"""
    SELECT {opt('hs', 'id')} AS id, {opt('hs', 'name')} AS name, {opt('hs', 'pipeline')} AS pipeline, {opt('hs', 'stage')} AS stage,
           {opt('hs', 'prob')} AS prob, {opt('hs', 'amount')} AS amount, {opt('hs', 'close')} AS close, {opt('hs', 'start')} AS start,
           {opt('hs', 'end')} AS `end`, {opt('hs', 'bu')} AS bu, {opt('hs', 'offering')} AS offering, {band} AS fstatus,
           {opt('hs', 'margin')} AS margin, {opt('hs', 'owner')} AS owner, {opt('hs', 'sf_account')} AS sf_account, acc.name AS company
      FROM {table} hs
      LEFT JOIN {kt('Account')} acc ON acc.id = {opt('hs', 'sf_account')}
     WHERE COALESCE(CAST({closed} AS BOOLEAN), false) = false AND {norm(band)} IN ({in_bands})"""

