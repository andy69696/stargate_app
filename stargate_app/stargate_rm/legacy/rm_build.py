#!/usr/bin/env python3
"""Build data.json for the UBDS Resource Management dashboard: Margin by Resource (exceptions), Over Utilisation, Bench.

Inputs in --kimble-dir (raw soqlQuery dumps, exact names):
  resources.json      Employee / Supplier / Partner resources with grade, BU, role, start/end, contract end, suppress flag
  wk_billable_*.json  ActivityPeriodAssignment Week periods on client engagements (DG not null, not UBDS-*, not Lost Closed):
                      P1 / P2 / P3 usage, P2 revenue and cost, by resource x week period (Kantata splits weeks at month ends; merged to Mondays)
  wk_absence.json     Week periods on absence activities (planned/unplanned absence, non-working, rest days) by resource x week x activity
  mo_margin_*.json    Month periods on client engagements by resource x month x engagement: P1/P2/P3 revenue and cost, P2 usage
  mo_ledger_rev.json  PerformanceAnalysis (fact Revenue) rows with a resource, by resource x calendar y/m x engagement: P1/P2/P3 revenue
                      allocation — this is the revenue Kantata itself attributes to the person, including fixed-price work spread over the team
  mo_ledger_fixed.json PerformanceAnalysis revenue rows on a delivery element with no resource (fixed price, milestones, annuities), by
                      engagement x element x y/m — allocated to the people booked on that engagement that month by P2 cost share
  assignments.json    ActivityAssignment per resource x engagement: forecast end date, start, remaining usage
  wk_eng_*.json       (5 Nov) Week periods on client engagements by resource x week x engagement: P1/P2/P3 usage, for the hover on each week cell
Rules (from the Resource Management team's requirements):
  * billable resources only (Suppress_Utilisation_reporting = false); exclude Service Delivery and Client Success; exclude offboarded
  * demand = P1 + P2 forecast usage on client engagements (P2 is cumulative of P1); non-billable allocations are not demand
  * planned absence reduces capacity; capacity = 5 days x time pattern, less UK bank holidays
  * availability % = (capacity - absence - P2 demand) / capacity; over-utilised when < 0%
  * weeks after a supplier/partner contract end date or an employee leaving date are greyed out and never counted as capacity
  * margin £ = P2 revenue allocation (Kantata ledger, falling back to assignment P2 revenue) - P2 cost; margin % = margin / revenue;
    revenue 0 and cost > 0 -> -100%
  * basis option (5 Nov): every month also carries the P1 and P3 revenue allocation and cost (rev1/cost1, rev3/cost3) so the page can show
    margin on the P1 sold, P2 likely or P3 full-forecast basis; the exception flags and averages are computed per basis (suffix 1/3; no suffix = P2)
"""
import argparse, glob, json, os, re, datetime as dt
from collections import defaultdict

LEVEL_RE = re.compile(r'SFIA\s*(\d)', re.I)
EXCL_BU = {'Service Delivery', 'Client Success'}
UK_BANK_HOLIDAYS = {'2026-08-31', '2026-12-25', '2026-12-28', '2027-01-01', '2027-03-26', '2027-03-29', '2027-05-03', '2027-05-31', '2027-08-30', '2027-12-27', '2027-12-28', '2028-01-03'}


def load(p):
    with open(p) as f:
        return json.load(f).get('records') or []


def monday(d):
    d = dt.date.fromisoformat(d[:10]); return (d - dt.timedelta(days=d.weekday())).isoformat()


def week_capacity(ws, pattern_days=5.0):
    d = dt.date.fromisoformat(ws); n = 0
    for i in range(5):
        if (d + dt.timedelta(days=i)).isoformat() not in UK_BANK_HOLIDAYS: n += 1
    return n * pattern_days / 5.0


def month_add(ym, k):
    y, m = int(ym[:4]), int(ym[5:7]); m += k
    while m > 12: m -= 12; y += 1
    return f'{y:04d}-{m:02d}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kimble-dir', required=True); ap.add_argument('--out', required=True); ap.add_argument('--today', default=None)
    ap.add_argument('--weeks', type=int, default=12); ap.add_argument('--months', type=int, default=3)
    a = ap.parse_args()
    today = a.today or dt.date.today().isoformat()
    this_monday = monday(today); prev_monday = (dt.date.fromisoformat(this_monday) - dt.timedelta(days=7)).isoformat()
    weeks = [prev_monday] + [(dt.date.fromisoformat(this_monday) + dt.timedelta(days=7 * i)).isoformat() for i in range(a.weeks)]
    this_month = today[:7]; months = [month_add(this_month, i) for i in range(a.months + 1)]
    # ---- resources
    res = {}
    for r in load(os.path.join(a.kimble_dir, 'resources.json')):
        bu = (r.get('KimbleOne__BusinessUnit__r') or {}).get('Name') or '—'
        grade = (r.get('KimbleOne__Grade__r') or {}).get('Name') or ''
        m = LEVEL_RE.search(grade)
        rtype = r['KimbleOne__ResourceType__r']['Name']
        end = r.get('KimbleOne__EndDate__c'); cend = r.get('UBDS_Contract_end_date__c')
        # the cut-off after which the person is no longer available: supplier/partner contract end, or employee leaving date
        cutoff = min([x for x in (end, cend if rtype != 'Employee' else None) if x], default=None)
        res[r['Id']] = dict(rid=r['Id'], name=r['Name'], rt=rtype, grade=grade, lvl=int(m.group(1)) if m else None, bu=bu,
                            grp=((r.get('KimbleOne__BusinessUnit__r') or {}).get('KimbleOne__BusinessUnitGroup__r') or {}).get('Name') or '—',
                            role=(r.get('KimbleOne__ActivityRole__r') or {}).get('Name') or r.get('Budget_role__c') or '—',
                            start=r.get('KimbleOne__StartDate__c'), end=end, cend=cend if rtype != 'Employee' else None, cutoff=cutoff,
                            billable=not r.get('Suppress_Utilisation_reporting__c'), excluded_bu=bu in EXCL_BU,
                            std_cost=r.get('KimbleOne__StandardCost__c'), std_rev=r.get('KimbleOne__StandardRevenue__c'))
    # ---- weekly demand and absence
    wk = defaultdict(lambda: defaultdict(lambda: dict(u1=0.0, u2=0.0, u3=0.0, r2=0.0, c2=0.0, abs=0.0, abs_detail=defaultdict(float))))
    for p in sorted(glob.glob(os.path.join(a.kimble_dir, 'wk_billable_*.json'))):
        for r in load(p):
            w = wk[r['rid']][monday(r['sd'])]
            w['u1'] += r.get('u1') or 0; w['u2'] += r.get('u2') or 0; w['u3'] += r.get('u3') or 0; w['r2'] += r.get('r2') or 0; w['c2'] += r.get('c2') or 0
    wke = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0])))
    for p in sorted(glob.glob(os.path.join(a.kimble_dir, 'wk_eng_*.json'))):
        for r in load(p):
            e = wke[r['rid']][monday(r['sd'])][r.get('dg') or '—']; e[0] += r.get('u1') or 0; e[1] += r.get('u2') or 0; e[2] += r.get('u3') or 0
    for r in load(os.path.join(a.kimble_dir, 'wk_absence.json')):
        w = wk[r['rid']][monday(r['sd'])]; w['abs'] += r.get('u3') or 0; w['abs_detail'][r.get('act') or 'Absence'] += r.get('u3') or 0
    # ---- monthly margin by resource x month x engagement
    mo = defaultdict(lambda: defaultdict(lambda: dict(r2=0.0, c2=0.0, r1=0.0, c1=0.0, r3=0.0, c3=0.0, u2=0.0, eng=defaultdict(lambda: dict(r2=0.0, c2=0.0, u2=0.0, r1=0.0, r3=0.0)))))
    for p in sorted(glob.glob(os.path.join(a.kimble_dir, 'mo_margin_*.json'))):
        for r in load(p):
            m = mo[r['rid']][r['sd'][:7]]
            for k in ('r2', 'c2', 'r1', 'c1', 'r3', 'c3', 'u2'): m[k] += r.get(k) or 0
            e = m['eng'][r.get('dg') or '—']; e['r2'] += r.get('r2') or 0; e['c2'] += r.get('c2') or 0; e['u2'] += r.get('u2') or 0; e['r1'] += r.get('r1') or 0; e['r3'] += r.get('r3') or 0
    # ---- ledger revenue allocation by resource x month x engagement (overrides assignment-level revenue where present)
    led = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: dict(p1=0.0, p2=0.0, p3=0.0))))
    lp = os.path.join(a.kimble_dir, 'mo_ledger_rev.json')
    if os.path.exists(lp):
        for r in load(lp):
            ym = f"{int(r['y']):04d}-{int(r['m']):02d}"
            for k in ('p1', 'p2', 'p3'): led[r['rid']][ym][r.get('dg') or '—'][k] += r.get(k) or 0
    # ---- element-level fixed revenue by engagement x month, allocated by P2 cost share to the people on the engagement that month
    fixed = defaultdict(lambda: defaultdict(lambda: dict(p1=0.0, p2=0.0, p3=0.0)))
    fp = os.path.join(a.kimble_dir, 'mo_ledger_fixed.json')
    if os.path.exists(fp):
        for r in load(fp):
            ym = f"{int(r['y']):04d}-{int(r['m']):02d}"
            for k in ('p1', 'p2', 'p3'): fixed[ym][r.get('dg') or '—'][k] += r.get(k) or 0
        for ym, dgs in fixed.items():
            for dg, amts in dgs.items():
                if max(amts.values()) <= 0: continue
                members = [(rid, mm[ym]['eng'][dg]['c2']) for rid, mm in mo.items() if ym in mm and dg in mm[ym]['eng']]
                if not members: continue
                w = [c for _, c in members]
                if sum(w) <= 0: w = [mo[rid][ym]['eng'][dg]['u2'] for rid, _ in members]
                if sum(w) <= 0: w = [1.0 for _ in members]
                tot = sum(w)
                for (rid, _), wi in zip(members, w):
                    for k in ('p1', 'p2', 'p3'):
                        if amts[k] > 0: led[rid][ym][dg][k] += amts[k] * wi / tot
    # ---- assignments: last assignment date, current engagements
    asg = defaultdict(list)
    for r in load(os.path.join(a.kimble_dir, 'assignments.json')):
        asg[r['rid']].append(dict(dg=r.get('dg'), st=r.get('st'), end=r.get('endp3'), start=r.get('startd'), remaining=r.get('remaining') or 0))
    key_of = lambda dg: (re.match(r'^([A-Za-z0-9]+-[A-Za-z0-9]+)', dg or '') or [None, dg])[1]
    out = []
    for rid, r in res.items():
        if r['excluded_bu'] or not r['billable']: continue
        if r['end'] and r['end'] < prev_monday: continue  # offboarded
        cutoff = r['cutoff']
        # weekly
        wrows = []
        for ws in weeks:
            w = wk[rid].get(ws)
            we = (dt.date.fromisoformat(ws) + dt.timedelta(days=4)).isoformat()
            active = (not cutoff or cutoff >= ws) and (not r['start'] or r['start'] <= we)
            cap = week_capacity(ws)
            if not active:
                wrows.append(dict(ws=ws, na=True)); continue
            u1 = w['u1'] if w else 0.0; u2 = w['u2'] if w else 0.0; u3 = w['u3'] if w else 0.0; ab = min(w['abs'], cap) if w else 0.0
            avail_days = cap - ab - u2
            wrows.append(dict(ws=ws, cap=round(cap, 2), p1=round(u1, 2), p2=round(u2, 2), p3=round(u3, 2), absence=round(ab, 2),
                              avail=round(avail_days / cap, 3) if cap > 0 else None, full_absence=bool(cap > 0 and ab >= 0.8 * cap),
                              abs_detail={k: round(v, 2) for k, v in (w['abs_detail'].items() if w else [])},
                              eng=[[key_of(dg), round(v[0], 2), round(v[1], 2), round(v[2], 2)] for dg, v in sorted(wke[rid].get(ws, {}).items(), key=lambda kv: -kv[1][1])][:8]))
        fwd = [x for x in wrows[1:] if not x.get('na') and x.get('avail') is not None]
        min_avail = min((x['avail'] for x in fwd), default=None); max_avail = max((x['avail'] for x in fwd), default=None)
        # monthly margin
        mrows = []
        for ym in months:
            m = mo[rid].get(ym)
            month_end = (dt.date.fromisoformat(month_add(ym, 1) + '-01') - dt.timedelta(days=1)).isoformat()
            active = (not cutoff or cutoff >= ym + '-01')
            if not active:
                mrows.append(dict(ym=ym, na=True)); continue
            lrev = led[rid].get(ym)
            def basis(k):   # k in 1,2,3: revenue = ledger allocation at that level (falling back to assignment revenue), cost = assignment cost at that level
                cost = (m[f'c{k}'] if m else 0.0)
                rev = sum(v[f'p{k}'] for v in lrev.values()) if lrev else (m[f'r{k}'] if m else 0.0)
                if rev > 0: pct = (rev - cost) / rev
                elif cost > 0: pct = -1.0
                else: pct = None
                return rev, cost, pct
            rev, cost, pct = basis(2); rev1, cost1, pct1 = basis(1); rev3, cost3, pct3 = basis(3)
            eng_rev = {k: round(v['p2']) for k, v in lrev.items()} if lrev else ({k: round(v['r2']) for k, v in m['eng'].items()} if m else {})
            mrows.append(dict(ym=ym, rev=round(rev), cost=round(cost), margin=round(rev - cost), pct=(round(pct, 4) if pct is not None else None), days=round(m['u2'], 1) if m else 0,
                              rev1=round(rev1), cost1=round(cost1), pct1=(round(pct1, 4) if pct1 is not None else None), rev3=round(rev3), cost3=round(cost3), pct3=(round(pct3, 4) if pct3 is not None else None),
                              engagements=sorted(eng_rev.items(), key=lambda kv: -kv[1])[:4]))
        live = [x for x in mrows if not x.get('na')]
        def summ(sfx):
            tr = sum(x['rev' + sfx] for x in live); tc = sum(x['cost' + sfx] for x in live)
            wa = (tr - tc) / tr if tr > 0 else (-1.0 if tc > 0 else None)
            mm = min((x['pct' + sfx] for x in live if x['pct' + sfx] is not None), default=None)
            return tr, tc, wa, mm
        tot_rev, tot_cost, wavg, min_margin = summ(''); tot_rev1, tot_cost1, wavg1, min_margin1 = summ('1'); tot_rev3, tot_cost3, wavg3, min_margin3 = summ('3')
        # current project(s): engagements with P2 usage in the current month, then this week
        cur = mo[rid].get(this_month)
        cur_eng = sorted(((k, v['u2']) for k, v in cur['eng'].items() if v['u2'] > 0), key=lambda kv: -kv[1]) if cur else []
        current = [key_of(k) for k, _ in cur_eng[:3]]
        last_asg = max((x['end'] for x in asg.get(rid, []) if x['end']), default=None)
        out.append(dict(rid=rid, name=r['name'], rt=r['rt'], grade=r['grade'], lvl=r['lvl'], bu=r['bu'], grp=r['grp'], role=r['role'], start=r['start'], end=r['end'], cend=r['cend'], cutoff=cutoff,
                        std_cost=r['std_cost'], std_rev=r['std_rev'], current=current, last_assignment=last_asg,
                        weeks=wrows, min_avail=min_avail, max_avail=max_avail, over=(min_avail is not None and min_avail < 0), bench=(max_avail is not None and max_avail >= 0.25),
                        months=mrows, wavg=(round(wavg, 4) if wavg is not None else None), min_margin=min_margin, margin_exception=(min_margin is not None and min_margin < 0.25),
                        tot_rev=round(tot_rev), tot_cost=round(tot_cost),
                        wavg1=(round(wavg1, 4) if wavg1 is not None else None), min_margin1=min_margin1, margin_exception1=(min_margin1 is not None and min_margin1 < 0.25), tot_rev1=round(tot_rev1), tot_cost1=round(tot_cost1),
                        wavg3=(round(wavg3, 4) if wavg3 is not None else None), min_margin3=min_margin3, margin_exception3=(min_margin3 is not None and min_margin3 < 0.25), tot_rev3=round(tot_rev3), tot_cost3=round(tot_cost3)))
    out.sort(key=lambda x: (x['bu'], x['name']))
    summary = dict(resources=len(out), over=sum(1 for x in out if x['over']), bench=sum(1 for x in out if x['bench']), margin_exceptions=sum(1 for x in out if x['margin_exception']),
                   excluded_bu=sum(1 for r in res.values() if r['excluded_bu']), non_billable=sum(1 for r in res.values() if not r['billable'] and not r['excluded_bu']))
    data = dict(asof=dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ'), today=today, weeks=weeks, months=months, rows=out, summary=summary,
                bus=sorted({x['bu'] for x in out}), types=sorted({x['rt'] for x in out}), rules=dict(margin_threshold=0.25, bench_threshold=0.25, over_threshold=0.0, bases=['p1', 'p2', 'p3']))
    with open(a.out, 'w') as f: json.dump(data, f, separators=(',', ':'))
    print(f"{len(out)} billable resources (excluded: {summary['excluded_bu']} in Service Delivery / Client Success, {summary['non_billable']} non-billable); weeks {weeks[0]} (prev) + {weeks[1]}..{weeks[-1]}; months {months}")
    print(f"over-utilised {summary['over']}, bench {summary['bench']}, margin exceptions {summary['margin_exceptions']}")


if __name__ == '__main__':
    main()
