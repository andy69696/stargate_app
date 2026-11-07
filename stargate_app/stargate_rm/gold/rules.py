"""Business rules for the workspace's "gold" data (the JSON embedded in web/index.html).

The original builder (build_gold.py and engines/) is not in this repo.  These rules were recovered from two places:
  * the workspace page's own JavaScript, where it states it uses the same rules as build_gold.py (matcher scoring and
    outcomes, bench, pricing families, band probabilities): ported exactly;
  * the 4 Nov 2026 snapshot itself, by re-deriving each output from its inputs: noted "inferred" below, with how well the
    rule reproduces the snapshot.
Change a rule here, not in the engines.
"""
from __future__ import annotations

HOURS_PER_DAY = 7.5            # Kantata availability hours per working day (the page's bench rule: "7.5h days")
RATE_HOURS_PER_DAY = 8.0       # hourly rates on assignments are daily rates / 8 (UBDS working day)
MONTHS_WINDOW = 4              # capacity / utilisation window: this month + 3
FORWARD_MONTHS = 13            # eng_month / demand / p123: this month + 12
GENERIC_MONTHS = 7             # generics: this month + 6
P2_WEIGHT, P3_WEIGHT = 0.8, 0.5  # p123 weighted view: P1 + 80% of (P2 - P1) + 50% of (P3 - P2)  (meta.units)
EXCLUDE_BU = ["Service Delivery", "Client Success"]

FAMILIES = ["Cloud", "Cybersecurity", "Data and AI", "Digital Advisory", "Managed Services", "P3M", "Secure Connectivity", "Unmapped"]

# People: capability family from business unit (inferred: one family per BU in the snapshot, 266 of 266 people).
BU_FAMILY = {
    "AWS (Rayo)": "Cloud", "Azure": "Cloud", "Solution Architecture": "Cloud",
    "App Development & Modernisation": "Digital Advisory", "Digital Transformation": "Digital Advisory",
    "Service Design & Management": "Digital Advisory",
    "Data, Analytics and AI": "Data and AI",
    "PMO & Project Management": "P3M", "Portfolio & Programme Management": "P3M",
    "Secure Connectivity": "Secure Connectivity",
    "Security Advisory & Engineering": "Cybersecurity",
    "Service Delivery": "Managed Services",   # capacity only: the BU is excluded from people and the bench
}   # any other BU (AWS (UBDS Digital), the Rayo practices, Penetration Testing, Operations, ...) is "Unmapped", as in the snapshot

# Engagements and deals: family from the offering (inferred; 209 of 211 engagements, all deals).
OFFERING_FAMILY = {
    "Cloud": "Cloud", "Cybersecurity": "Cybersecurity", "Data": "Data and AI", "Data and AI (Data)": "Data and AI",
    "Delivery - P3M - PMO": "P3M", "Digital Advisory": "Digital Advisory", "Managed Services": "Managed Services",
    "Secure Connectivity": "Secure Connectivity",
}

# Generic placeholders: family from the generic resource's name prefix (the page's PRICE_FAM).
GENERIC_PREFIX_FAMILY = {"#DATA": "Data and AI", "#CLOUD": "Cloud", "#CON": "Digital Advisory", "#CYBER": "Cybersecurity", "#P3M": "P3M",
                         "#MAN": "Managed Services", "#NET": "Secure Connectivity", "#Rayo": "Cloud", "#3BData": "Cybersecurity"}

MS_PRODUCT_GROUPS = {"IoC Managed Services", "SoC Managed Services", "Annuities"}   # engagement.ms (24 of 24 in the snapshot)

# Role cards from open HubSpot deals (inferred; reproduces every card in the snapshot except a duplicated set on 5 deals,
# which came from a join fan-out in the original pull).  Cards are taken from the family's template in order; how many depends
# on the deal's total days (amount / DEAL_DAY_RATE): under 200 -> 1, under 400 -> 2, under 600 -> 3, otherwise 4, capped at
# the template's length.  Each card gets an equal share of the days.
DEAL_DAY_RATE = 750.0
CARD_COUNT_STEPS = [(200, 1), (400, 2), (600, 3)]
CARD_COUNT_MAX = 4
STRATEGIC_AMOUNT = 1_000_000   # deal amount at or above which a role is strategic (inferred; 56 of 56 deals)
SOLD_BANDS = {"Contracted", "Closed Won", "Won Closed", "Commit"}
ROLE_TEMPLATES = {
    "P3M": [("Project Manager", 5, ["Agile Project Management", "Project Change Control"]),
            ("PMO Analyst", 3, ["Documentation", "Creating PowerPoint presentations"])],
    "Managed Services": [("Service Manager", 4, ["ITIL", "Service Management"]),
                         ("Cloud Engineer", 3, ["Azure", "Monitoring"])],
    "Cloud": [("Cloud Architect", 5, ["Azure Architecture", "Landing Zones"]), ("Cloud Engineer", 4, ["Terraform", "CI/CD"]),
              ("Cloud Engineer", 4, ["Azure", "Network Security Group"]),
              ("Project Manager", 5, ["Waterfall/Prince 2 Project Management", "Risk and Issue Management"])],
    "Cybersecurity": [("Security Architect", 5, ["Security Architecture", "Zero Trust"]), ("Security Engineer", 4, ["SIEM", "Vulnerability Management"]),
                      ("Penetration Tester", 4, ["Penetration Testing", "OWASP"])],
    "Data and AI": [("Data Architect", 5, ["Data Architecture", "Azure Data Factory"]), ("Data Engineer", 4, ["Python", "SQL"]),
                    ("Data Engineer", 4, ["Databricks", "SQL"]), ("Delivery Manager", 5, ["Agile Project Management", "Stakeholder Management"])],
    "Secure Connectivity": [("Network Architect", 5, ["Network Security Group", "SD-WAN"]), ("Network Engineer", 4, ["Firewalls", "Routing"])],
    "Digital Advisory": [("Transformation Consultant", 5, ["Stakeholder Management", "Workshop facilitation"]),
                         ("Business Analyst", 4, ["Requirement gathering", "Documentation"]),
                         ("Consultant", 4, ["Strategic & critical thinking", "Presentation of data & updates"])],
}
# The matcher scores a card against the family of its role (G.role_family), falling back to the deal's family.
ROLE_FAMILY = {"Data Architect": "Data and AI", "Data Engineer": "Data and AI", "Delivery Manager": "P3M", "Project Manager": "P3M",
               "PMO Analyst": "P3M", "Cloud Architect": "Cloud", "Cloud Engineer": "Cloud", "Security Architect": "Cybersecurity",
               "Security Engineer": "Cybersecurity", "Penetration Tester": "Cybersecurity", "Transformation Consultant": "Digital Advisory",
               "Business Analyst": "Digital Advisory", "Consultant": "Digital Advisory", "Service Manager": "Managed Services",
               "Network Architect": "Secure Connectivity", "Network Engineer": "Secure Connectivity"}

# Capacity flag per person-month (with correct units; the snapshot flagged everyone "under" because of a units error).
UNDER_UTILISED = 0.75          # below this P1 utilisation = under (25% or more free, the RM bench rule)
OVER_UTILISED = 1.0            # above this = over

# Exceptions (severity, owner, days to due from the build date).
EXCEPTION_RULES = {
    "X01": ("X01 Forecast margin below sold by >3pts", "high", "Finance", 5),
    "X02": ("X02 Forecast margin below 25% floor", "high", "Finance", 5),
    "X03": ("X03 Team P1 utilisation under 60% this month", "medium", "Resource manager", 7),
    "X04": ("X04 Amber/Red milestone RAG not updated for 14 days", "medium", "Delivery manager", 5),
    "X18": ("X18 Milestone forecast later than contracted date", "high", "Delivery manager", 3),
    "X26": ("X26 Contracted date moved: confirm CAN", "medium", "Delivery principal", 5),
    "X27": ("X27 Unfilled generic resources on a live engagement", "high", "Resource manager", 5),
    "X29": ("X29 Employee rolls off within 4 weeks with no next assignment", "medium", "Resource manager", 7),
}
MARGIN_RULE_STATUSES = {"Won Closed"}   # X01 / X02 look at sold work only, any stage, managed services included (inferred)
P1_STATUSES = {"Won Closed", "Contracted"}  # X29: a "next assignment" must be P1 work
X03_MIN_HEADCOUNT = 2          # X03 ignores one-person teams (inferred)
X01_POINTS = 3.0
X02_FLOOR = 25.0
X03_TEAM_UTILISATION = 0.60
X04_STALE_DAYS = 14
X27_MIN_HOURS = 40
X27_MONTHS = 2
X29_DAYS = 28

VARIANCE_DRIVERS = {"D1": "Deal moved right", "D2": "Deal value changed", "D3": "Unavailable resources", "D4": "Resource mix change",
                    "D5": "Project plan change", "D6": "CANs", "D7": "RAG status change", "D0": "Unexplained / other"}
VARIANCE_D2_SHARE = 0.02       # a revenue change of 2% or more of baseline is a value change (inferred; all 119 rows)
VARIANCE_MIN_DELTA = 1000      # listed when revenue or cost moved by at least this much (inferred; 119 of 119)
VARIANCE_UNFILLED_MONTHS = 3

# Levers
DISPLACE_MIN_FREE = 0.5        # an employee at least this free can take over a supplier/partner's remaining days
OVER_GRADED_SFIA = 6
OVER_GRADED_MARGIN = 30.0
BENCH_THRESHOLD = 0.25         # bench list: employees at least 25% free over the next three months
ROLL_OFF_WEEKS = 6             # requisition cross-check C1: same-family employees rolling off within this many weeks
C1_SUPPLY_FAMILY = {"Secure Connectivity": "Cloud"}   # C1 looks at Cloud employees for Secure Connectivity roles (inferred; SC has one person)
