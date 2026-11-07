# Stargate Workspace v2 on Databricks Apps

## What is deployed: the artifact's own pages, served by FastAPI (`server.py`)

`app.yaml` runs `uvicorn server:app`. The server serves the **Stargate Workspace v2** artifact's published files from `web/`
byte for byte, so the UI is the artifact's UI: all 12 roles, every view, the same HTML, CSS and JavaScript. The only change
to each page is one `<script src="/_stargate/claude.js">` tag injected into its `<head>` as it is served.

**Data.** The pages fetch their JSON by relative path. The server answers these from the lake (built by
`stargate_rm/pipeline.py`, cached one hour) and the rest from the snapshot published in the artifact:

| Served from the lake | Still the artifact snapshot (no lake build yet) |
|---|---|
| the "gold" data embedded in `web/index.html` (swapped in as the page is served; `stargate_rm/gold`) | `dash/revenue/*`, `dash/resrev/*` |
| `dash/rm/data.json` (Resource manager: utilisation, margin), `dash/rm/demand.json` | |
| `dash/resourcing/data.json`, `match.json`, `history.json` | |

The live files have exactly the artifact's shapes, so the pages read them unchanged.

### The main page's gold data (`stargate_rm/gold`)

The original builder (`build_gold.py` and `engines/`) was never in this repo; the snapshot it made on 4 Nov 2026 was a one-off pull
of the Kantata sandbox and HubSpot. `stargate_rm/gold` rebuilds it from the lake on every app build:

- `queries.py` — engagements, month-by-month forecasts (Kantata **month** periods only), assignments, milestones, HubSpot deals;
- `build.py` — people, capacity, team utilisation, engagement × month, demand, generics, p123, assignments, deals;
- `engines.py` — role cards and matcher, proposals and cross-checks, exceptions X01–X29, variance D0–D7, levers;
- `rules.py` — every rule and threshold, with where it came from;
- `fields.py` — custom / unconfirmed column names (see TODO below).

**How the rules were recovered.** The matcher's scoring and outcomes and the bench rule are ported exactly from the page's own
JavaScript, which states it uses the same rules as `build_gold.py`. The rest were inferred by re-deriving each part of the
snapshot from its inputs; `tests/test_gold.py` pins them. Against the snapshot: every role card and matcher choice; X01, X02,
X03, X27 and X29 exactly; variance rows and cause codes 119 of 119; bench, over-graded and ending levers exactly; displacement
rows exactly, with candidates the most available same-family employee within one SFIA level (ties broken at a precision the
snapshot does not keep). X04, X18 and X26 and variance's milestone evidence need the milestones pull (TODO 11 below).

**The snapshot's figures were doubled.** It summed Kantata's week and month periods, so revenue, cost and usage were twice the
month values (November P1 £4.13m instead of £2.07m), and it compared those doubled days against available *hours*, so nearly
everyone showed about 25% utilised. The lake build reads month periods only and converts days to hours the way the page expects
(booked hours = days × 8; available hours = working days × 7.5). Revenue figures roughly halve and utilisation rises; rules that
depend on them (X03 team utilisation, X27 generic hours, the bench) change accordingly. The snapshot also listed 5 HubSpot deals
twice; the build de-duplicates deals by id.

If one query fails or a column is missing, that part is empty and the reason is listed in `/api/status` under `gaps`. Only if the
build itself errors does the page keep the published snapshot, and `/api/status` → `build.gold` then says `snapshot` and why.

**Artifact runtime.** On claude.ai the pages call `claude.use('user' | 'db' | 'sample' | 'downloads')`. `web_runtime/claude.js`
provides the same calls, backed by the server:

| Call | On Databricks |
|---|---|
| `user` | the signed-in user, from the `X-Forwarded-Email` header the Apps proxy adds (`/api/me`) |
| `db` (approvals, proposals, exception ledger, settings, pricing deals …) | Firestore-style collections in the Delta table `<APP_STATE_SCHEMA>.workspace_db` (`/api/db`); browsers poll every 15 s for other users' changes |
| `sample` (Explain, Ask) | the model serving endpoint `LLM_ENDPOINT` (`/api/sample`) |
| `downloads` | a normal browser download |

If `workspace_db` cannot be created or written, shared state is kept in memory (works, but is lost on restart).
`GET /api/status` shows the build time, any data gaps from the lake build, the number of people in the Utilisation data, and
whether the store is on Delta or in memory. `POST /api/rebuild` clears the cache and rebuilds from the lake.

**Run locally**

```bash
pip install -r requirements.txt
STARGATE_SAMPLE=1 uvicorn server:app --port 8000      # artifact snapshot data, in-memory store, canned Explain text
python -m pytest tests -q                              # server and Streamlit tests (sample mode)
```

**Grants needed** are as in *Deploy* below, plus `CREATE TABLE`/`MODIFY` on `APP_STATE_SCHEMA` for `workspace_db`.

---

## The earlier Streamlit port (`app.py`)

Still in this folder and still working (`STARGATE_SAMPLE=1 streamlit run app.py`); `app.yaml` has the command to deploy it
instead. The rest of this README describes it, and the data pipeline both versions share.

A Python/Streamlit port of the **Resource manager** role of the Stargate Workspace artifact: the four tabs that role can see and
interact with — **Resourcing exceptions · Utilisation · Demand · Margin** — with the same filters, tiles, charts, tables,
colour bands, Explain narratives, Rules-applied panels, UBDS Digital branding and dd/Mmm/yyyy dates (dd-mm-yy in the
Not detailed comparison table, as on the artifact).

All data is read from the Kantata (Salesforce) bronze tables in Unity Catalog (`salesforce_dev.bronze.kimbleone__*`, the
same schema as Salesforce) through a SQL warehouse. The Claude artifact is untouched; this app is independent of it.

## Layout

```
app.py                      Streamlit entry point (four tabs)
app.yaml                    Databricks Apps manifest (command, env vars, warehouse resource)
requirements.txt
.streamlit/config.toml      brand theme
stargate_rm/
  config.py                 env-driven settings, thresholds, brand colours   ← TODO(databricks) flags here
  db.py                     SQL warehouse connection (app service principal OAuth), cached queries
  queries.py                one SQL per Kantata pull, mapped 1:1 from the signed-off SOQL runbooks
  pipeline.py               runs the queries, feeds the legacy builders, returns one data dict per build
  legacy/                   rm_build.py · resourcing_refresh.py · match.py — the signed-off builders, unchanged
  engines.py                page logic ported from the artifact JS (windows, pools, weekly availability, ND compare)
  exceptions.py             Resource manager rules (X27, X29, X30, X03) with severity and SLA
  state.py                  exception ledger, saved actions and weekly history in Delta tables
  ai.py                     Explain narratives via the Foundation Model API (same prompts/word limits as the artifact)
  ui.py                     CSS, logo banner, Purpose/Objective boxes, tiles, charts, rules panels, formatters
  views/                    exceptions.py · utilisation.py · demand.py · margin.py
sample/                     bundled JSON so the app runs without a warehouse (STARGATE_SAMPLE=1)
tests/test_smoke.py         AppTest smoke test in sample mode
```

## Deploy as a Databricks App

1. **Create the app** (name ≤ 30 chars, lowercase/hyphens):
   ```bash
   databricks apps create stargate-rm --profile <PROFILE>
   ```
2. **Attach a SQL warehouse resource** named `sql-warehouse` (Compute → Apps → stargate-rm → Edit → App resources, or in
   the create call with `--json`). `app.yaml` reads its id via `valueFrom: sql-warehouse`; the app's service principal gets
   CAN_USE on it automatically.
3. **Grant the app's service principal** (shown on the app page as `app-<id> stargate-rm`):
   - `USE CATALOG` on `salesforce_dev`, `USE SCHEMA` + `SELECT` on `salesforce_dev.bronze`;
   - `USE SCHEMA`, `CREATE TABLE`, `SELECT`, `MODIFY` on the app-state schema (`APP_STATE_SCHEMA`, default
     `salesforce_dev.stargate_app`) — create the schema first;
   - `SELECT` on the HubSpot deals table (`HUBSPOT_DEALS_TABLE`);
   - `CAN_QUERY` on the Foundation Model endpoint (`LLM_ENDPOINT`) for Explain.
4. **Deploy** from this folder:
   ```bash
   databricks sync . /Workspace/Users/<you>/stargate-rm --profile <PROFILE>
   databricks apps deploy stargate-rm --source-code-path /Workspace/Users/<you>/stargate-rm --profile <PROFILE>
   ```
5. Open the app URL. The sidebar shows the catalog in use and a **Rebuild from the lake** button (clears the 1-hour cache).

Environment variables (all set in `app.yaml`, override per environment):

| Variable | Default | Purpose |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | from resource `sql-warehouse` | warehouse the queries run on |
| `KANTATA_CATALOG` / `KANTATA_SCHEMA` | `salesforce_dev` / `bronze` | where the `kimbleone__*` tables live |
| `HUBSPOT_DEALS_TABLE` | `hubspot_dev.silver.deals` | HubSpot deals for the Not detailed comparison |
| `APP_STATE_SCHEMA` | `salesforce_dev.stargate_app` | tables the app writes: `exception_ledger`, `rm_actions`, `resourcing_history` |
| `LLM_ENDPOINT` | `databricks-claude-sonnet-4` | chat endpoint for Explain |
| `STARGATE_SAMPLE` | unset | `1` = run from `sample/*.json`, no warehouse needed |

## Run locally

```bash
pip install -r requirements.txt
STARGATE_SAMPLE=1 streamlit run app.py          # bundled sample data
# or against the lake, with a PAT / profile:
DATABRICKS_HOST=https://<workspace> DATABRICKS_TOKEN=<pat> DATABRICKS_WAREHOUSE_ID=<id> streamlit run app.py
STARGATE_SAMPLE=1 python -m pytest tests -q     # smoke test
```

## Data: what is read and from where

Every pull in `queries.py` is the SQL equivalent of a SOQL query in the signed-off runbooks, against the bronze tables with
the Salesforce field names (lower-cased by the ingest). Lookups that SOQL resolved with `__r.Name` are joins on `id`, and the
dotted aliases are nested back into the SOQL record shape by `db.records()`, so the legacy builders run unchanged.

| Area | Bronze tables |
|---|---|
| Resources, capacity, absence | `kimbleone__resource__c`, `kimbleone__businessunit__c`, `kimbleone__capabilitygroup__c`, `kimbleone__capability__c`, `kimbleone__resourcecapability__c`, `kimbleone__resourcedefaultcapability__c`, `kimbleone__resourceabsence__c`, `kimbleone__resourceperiod__c` |
| Demand and assignments | `kimbleone__activityassignment__c`, `kimbleone__activityperiodassignment__c`, `kimbleone__proposal__c`, `kimbleone__deliveryelement__c`, `kimbleone__deliverygroup__c`, `kimbleone__activity__c`, `kimbleone__businessobject__c` (engagements), `account` |
| Margin and revenue | `kimbleone__performanceanalysis__c`, `kimbleone__forecastperiod__c`, `kimbleone__purchaseorder__c`, `kimbleone__invoice__c` |
| New objects now in bronze | `kimbleone__requiredcapability__c`, `kimbleone__deliverygroupactor__c`, `kimbleone__statussummary__c`, `kimbleone__timesheet__c`, `kimbleone__timeentry__c` |
| HubSpot | `HUBSPOT_DEALS_TABLE` |

If a query fails (table or column not yet in bronze, no grant) the build does not stop: the pull is skipped, the gap is listed
in the amber **Data gaps in this build** banner at the top of the app and the dependent figures show as empty.

## TODO(databricks) — data to sort out in Databricks

Search the code for `TODO(databricks)`; every gap is flagged in place. In summary:

1. **HubSpot deals table and columns** (`config.py`, `app.yaml`, `queries.hubspot_deals`) — confirm the table and map its
   columns onto the aliases the Not detailed tab needs (engagement key, deal name, client, stage, amount, close/start date,
   owner, probability).
2. **App-state schema** (`config.py`, `state.py`, `pipeline.py`) — create `APP_STATE_SCHEMA` and grant the service
   principal `CREATE TABLE`/`MODIFY`. Until then the exception ledger, saved actions and weekly history fall back to the
   session (lost on restart) and the Not detailed / Demand history trend has no prior snapshots.
3. **Explain endpoint** (`config.py`, `app.yaml`, `ai.py`) — confirm the Foundation Model endpoint name and grant `CAN_QUERY`.
4. **Reference objects** (`queries.py` top) — the join targets listed in `REFERENCE_OBJECTS` must all be in bronze; any
   missing one breaks the pull that joins to it (the gap banner names the pull).
5. **Period revenue** (`queries.py`, `mo_ledger_*`) — the artifact's monthly ledger revenue/cost comes from
   `KimbleOne__PerformanceAnalysis__c`; if the lake exposes a separate "period revenue" object, point `mo_ledger_rev` /
   `mo_ledger_fixed` at it.
6. **UBDS custom fields on the assignment** (`queries.generic`) — `Recruitment__c` and `Client_Role__c` drive the recruit
   flag and client role title; confirm both are ingested.
7. **RequiredCapability parent field** (`queries.required_capabilities`) — assumed `KimbleOne__ActivityAssignment__c`.
8. **DeliveryGroupActor role field** (`queries.dg_actors`) — assumed `KimbleOne__ActorRole__c` (picklist); actors are
   joined to `user` for the name.
9. **Purchase orders** (`views/demand.py`) — joined to engagements by name for the PO value column in the Not detailed table;
   switch to the id once the PO → engagement lookup is confirmed.
10. **Time entries** (`views/margin.py`) — used for "days booked so far" in the Margin watch person drill-down; the table
    renders without them if the pull is missing.
11. **Gold data columns** (`stargate_rm/gold/fields.py`) — confirm these names in bronze and the HubSpot table; each missing one
    reads as empty and is named in `/api/status`:
    - delivery group: contract cost, baseline revenue and cost (X01, X02, variance), offering (engagement family),
      pre-mobilisation, linked HubSpot deal id;
    - milestones object and its fields (forecast, contracted, original contracted date, RAG, RAG updated, complete) for
      X04, X18, X26 and variance's milestone evidence;
    - HubSpot deals: id, name, stage, probability, amount, close / start / end dates, offering, forecast status, owner, company.
12. **Bank holidays** (`stargate_rm/gold/build.py`, `legacy/rm_build.py`, `legacy/match.py`) — the gold build's list runs to
    the end of 2028; `legacy/match.py` still has Easter 2027 on 2 and 5 April (it is 26 and 29 March).

## Streamlit equivalents of the artifact controls

Streamlit tables cannot carry a button or dropdown per row, so the per-row controls on the artifact become a selector under
the table that acts on the chosen row — behaviour and data are identical:

- **Explain column** (Over/Under utilisation, Not detailed, Margin watch individuals, Displace suppliers) → expander
  "Explain …" with a row picker and an **Explain** button; narratives are cached per row for the session.
- **Action dropdown** (Generic demand and actions) → expander "Choose the action for a generic role" with the same
  options (suggested person, someone else from all resources, Recruit FTE, FTC, Partner, Contractor, Ignore) and **Save**,
  persisted to `rm_actions`.
- **▾ Hide table** bars and collapsible headings → `st.expander`, open by default where the artifact shows the table open.
- **Rules applied** panels → collapsed `st.expander` at the bottom of each tab with the same text.
- **Week detail** (Utilisation) → expander with the chosen person's week-by-week capacity, absence, demand and bookings.

## Rules and thresholds (config.py)

Billable scope excludes the Service Delivery and Client Success business units and anyone offboarded; IoC Managed Services,
SoC Managed Services and Annuities product groups are out of scope. Under-utilisation = 25% free; margin target 40% (green at
or above, red below); 8-hour day; 12-week utilisation window; 3-month demand/margin window; Not detailed tolerances 10% value
/ 14 days; X27 fires above 40 unfilled generic hours; FY27 = December 2026 to November 2027.
