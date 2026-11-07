"""Column names the gold queries need, checked against the bronze and HubSpot column export of 6 Nov 2026.

A value of None means the lake has no such column: the field reads as empty and /api/status lists it under gaps.  Every
column here is also checked at run time, so a renamed column degrades to empty rather than failing the query.
"""
import os

# ---- Kantata delivery group (engagement)
DG = {
    "contract_revenue": "kimbleone__contractrevenue__c",
    "contract_cost": "kimbleone__contractcost__c",
    "baseline_revenue": "kimbleone__baselinecontractrevenue__c",   # the sold baseline; X01 and variance compare against it
    "baseline_cost": "kimbleone__baselinecontractcost__c",
    "offering": None,              # no offering on the delivery group: the family comes from its products' business unit
    "pre_mob": "pre_mob__c",
    "hubspot_deal": None,          # no HubSpot deal id on the delivery group
}

# ---- Kantata milestones.  Milestones hang off a delivery element, which carries the delivery group.
MILESTONE_OBJECT = "KimbleOne__Milestone__c"
MILESTONE = {
    "delivery_element": "kimbleone__deliveryelement__c",
    "description": "kimbleone__narrative__c",
    "forecast": "forecast_delivery_date__c",
    "contracted": "ubds_contracted_due_date__c",
    "original_contracted": "ubds_previous_contracted_due_date__c",   # set when the contracted date has been changed (X26)
    "rag": "ubds_rag_status__c",
    "rag_updated": "lastmodifieddate",          # no RAG-changed date: the milestone's last edit stands in (X04)
    "delivered": "ubds_milestone_delivered_date__c",
    "status": "kimbleone__milestonestatus__c",
}
MILESTONE_DONE_STATUSES = {"complete", "completed", "delivered", "closed", "invoiced", "approved"}   # see /api/diagnose for the values in use

# ---- Activity assignment custom field (also used by the Resourcing pull)
ASSIGNMENT_CLIENT_ROLE = "client_role__c"

# ---- HubSpot deals (open deals become role cards for the matcher)
HUBSPOT_DEALS_TABLE = os.environ.get("HUBSPOT_DEALS_TABLE", "hubspot_dev.silver.deals")
HUBSPOT = {
    "id": "hs_object_id",
    "name": "dealname",
    "pipeline": "pipeline",
    "stage": "dealstage",
    "prob": "hs_deal_stage_probability",
    "amount": "amount",
    "close": "closedate",
    "start": "start_date",
    "end": "end_date",
    "bu": "business_unit",
    "offering": "service_offering",
    "fstatus": "forecast_status",          # compared ignoring case, spaces and underscores (commit, most_likely, ...)
    "margin": "final_margin_percentage",
    "owner": "hubspot_owner_id",           # an owner id: owner names are not in the lake
    "sf_account": "salesforce_account_id", # joined to the Salesforce account for the company name
    "closed": "archived",                  # archived deals are left out
}
HUBSPOT_OPEN_BANDS = ("Commit", "Most Likely", "Best Case")   # the snapshot's 62 deals were all in these three bands
