"""Column names the gold queries need that are not already used (and so proven) elsewhere in the app.

TODO(databricks): confirm each name against salesforce_dev.bronze and the HubSpot deals table.  Every column here is
optional: the build checks the table's columns first, reads NULL for any that is missing, and lists it in the build's
data gaps (/api/status) with the field that went empty.  Fix a name here and rebuild; nothing else needs to change.
"""
import os

# ---- Kantata delivery group (engagement)
DG = {
    "contract_revenue": "kimbleone__contractrevenue__c",        # used already by the Not detailed pull
    "contract_cost": "kimbleone__contractcost__c",
    "baseline_revenue": "kimbleone__baselinecontractrevenue__c",  # the sold baseline; X01 and variance compare against it
    "baseline_cost": "kimbleone__baselinecontractcost__c",
    "offering": "offering__c",                                  # UBDS custom: offering / proposition (drives the family)
    "pre_mob": "pre_mobilisation__c",                           # UBDS custom: Yes / No / TBC
    "hubspot_deal": "hubspot_deal_id__c",                       # UBDS custom: linked HubSpot deal id
}

# ---- Kantata milestones (X04, X18, X26 and variance's milestone evidence)
MILESTONE_OBJECT = "KimbleOne__Milestone__c"
MILESTONE = {
    "delivery_group": "kimbleone__deliverygroup__c",
    "description": "kimbleone__description__c",
    "forecast": "kimbleone__forecastdate__c",
    "contracted": "kimbleone__contracteddate__c",
    "original_contracted": "kimbleone__originalcontracteddate__c",
    "rag": "kimbleone__ragstatus__c",
    "rag_updated": "kimbleone__ragstatusupdateddate__c",
    "complete": "kimbleone__iscomplete__c",
}

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
    "start": "expected_start_date",
    "end": "expected_end_date",
    "bu": "business_unit",
    "offering": "offering",
    "fstatus": "forecast_status",          # Commit / Most Likely / Best Case / Pipeline
    "margin": "final_margin_percentage",
    "owner": "hubspot_owner_name",
    "sf_account": "salesforce_account_id",
    "company": "company_name",
    "closed": "hs_is_closed",              # deals with this true are left out
}
HUBSPOT_OPEN_BANDS = ("Commit", "Most Likely", "Best Case")   # the snapshot's 62 deals were all in these three bands
