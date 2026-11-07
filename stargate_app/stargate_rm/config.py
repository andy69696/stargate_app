"""Stargate Resource Manager app — configuration.

Everything that points at Databricks lives here so it can be changed without touching the views.
Values come from app.yaml / environment variables first, then the defaults below.

DATA SOURCES
  Kantata (Kimble on Salesforce) bronze tables in Unity Catalog, same object and field names as Salesforce,
  lower-cased by the ingestion:  <KANTATA_CATALOG>.<KANTATA_SCHEMA>.kimbleone__activityassignment__c  etc.
  HubSpot deals (for the Not detailed tab's HubSpot columns) — see HUBSPOT_DEALS_TABLE.
  App state (exception ledger, action choices, resourcing history) — see APP_STATE_SCHEMA.
"""
import os

# ---- Databricks SQL warehouse (Databricks Apps injects DATABRICKS_HOST / client credentials; the warehouse id comes from app.yaml)
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
# ---- Kantata bronze (raw Salesforce objects)
KANTATA_CATALOG = os.environ.get("KANTATA_CATALOG", "salesforce_dev")
KANTATA_SCHEMA = os.environ.get("KANTATA_SCHEMA", "bronze")
# ---- HubSpot deals.  TODO(databricks): confirm the table.  The Not detailed tab needs, per deal: engagement key (CLIENT-PROJ code),
#      amount, deal stage/band, expected start and end.  The lakehouse design used hubspot.silver.deals; set the real name here.
HUBSPOT_DEALS_TABLE = os.environ.get("HUBSPOT_DEALS_TABLE", "hubspot_dev.silver.deals")
# ---- Writable schema for the app's own state (exception ledger, RM action choices, weekly placeholder history).
#      TODO(databricks): create the schema and grant the app's service principal CREATE TABLE / MODIFY on it.
APP_STATE_SCHEMA = os.environ.get("APP_STATE_SCHEMA", "salesforce_dev.stargate_app")
# ---- Model serving endpoint used by the Explain buttons (a Databricks Foundation Model API chat endpoint).
#      TODO(databricks): confirm the endpoint name and grant the app CAN_QUERY on it.
LLM_ENDPOINT = os.environ.get("LLM_ENDPOINT", "ubds_ai_models.default.gpt_apikey_test")   # serving endpoint name, or catalog.schema.function (see llm.py)
# ---- Set STARGATE_SAMPLE=1 to run the UI on the bundled sample JSON instead of the warehouse (offline review / smoke tests).
SAMPLE_MODE = os.environ.get("STARGATE_SAMPLE", "") == "1"

# ---- Business rules (as signed off by the Resource Management team; keep in step with the Rules applied panels)
EXCL_BU = {"Service Delivery", "Client Success"}            # business units outside the resourcing pool
MS_PRODUCT_GROUPS = ("IoC Managed Services", "SoC Managed Services", "Annuities")   # engagements outside resourcing scope

# KimbleOne__ProductGroup__c is not ingested into bronze (Nov 2026), so product group names cannot be joined.  Until it is,
# name the product groups by Id here (JSON {"<Id>": "<name>"}; /api/diagnose lists the Ids in use with example engagements).
# Only the managed-services groups above and match.py's OUTCOME_PG matter.  Once the table is ingested this is ignored.
import json as _json
PRODUCT_GROUP_NAMES: dict = _json.loads(os.environ.get("PRODUCT_GROUP_NAMES", "{}") or "{}")
BENCH_THRESHOLD = 0.25        # weekly availability at or above this = under-utilised
OVER_THRESHOLD = 0.0          # weekly availability below this = over-utilised
MARGIN_TARGET = 40            # target GPM %, Margin watch default threshold and the fixed band on Margin improvement actions
HOURS_PER_DAY = 8.0           # UBDS working day (Kantata holds 7.5h availability; the pages use 8h)
WEEKS = 12                    # utilisation window
MONTHS = 3                    # margin / bench engine window (current month + 3)
ND_VALUE_TOLERANCE = 0.10     # Not detailed: value variance above this is amber
ND_DATE_TOLERANCE_DAYS = 14   # Not detailed: start-date variance above this is amber
X27_MIN_HOURS = 40            # unfilled generics rule: placeholder hours in the next two months

# ---- Brand (UBDS Digital core colours sampled from the logo)
NAVY = "#1f2336"
TEAL = "#548ca3"
PERIWINKLE = "#adbedc"
TEAL_MID = "#7fa9bb"
TEAL_DARK = "#3a5f74"
STATUS_COLOURS = {"Won Closed": NAVY, "Contracted": TEAL_DARK, "Commit": TEAL, "Most Likely": TEAL_MID, "Best Case": PERIWINKLE,
                  "Pipeline": "#eb6834", "Forecast Placeholder": "#eb6834"}
SFIA_COLOURS = {1: "#dde5f0", 2: "#c5d2e6", 3: PERIWINKLE, 4: TEAL_MID, 5: TEAL, 6: TEAL_DARK, 7: NAVY}
GOOD = "#0a7a2f"
BAD = "#b42318"


def kt(obj: str) -> str:
    """Fully qualified bronze table for a Salesforce/Kantata object, e.g. kt('KimbleOne__Resource__c')."""
    return f"{KANTATA_CATALOG}.{KANTATA_SCHEMA}.{obj.lower()}"
