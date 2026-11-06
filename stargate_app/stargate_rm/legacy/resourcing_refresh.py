#!/usr/bin/env python3
"""Build data.json / history.json for the UBDS Resourcing & Margin dashboard from Kantata SOQL dumps.

Inputs (a folder, --kimble-dir) holding the raw soqlQuery results, exact file names:
  generic.json         ActivityAssignment rows for Generic Resource placeholders (end >= 1 Dec 2025)
  named.json           ActivityAssignment rows for Employee/Supplier/Partner with a margin (end >= 1 Dec 2025)
  months_fy26.json     ActivityPeriodAssignment monthly aggregates, Dec 2025 - Nov 2026, billable assignments
  months_fy27.json     same, Dec 2026 - Nov 2027
  generic_months.json  ActivityPeriodAssignment monthly usage for generic placeholders (reference only)
  ms_engagements.json  DeliveryGroups whose product group is IoC Managed Services, SoC Managed Services or Annuities (BU-delivered managed services stay in scope); flagged ms=true everywhere
  not_detailed.json    DeliveryGroups with ForecastAtThisLevel = Y (forecast held at engagement level, not built out in detail)

Usage:
  python3 resourcing_refresh.py --kimble-dir kimble --history history.json --out-dir out [--asof 2026-10-07] [--label "..."]
"""
import argparse, json, os, re, datetime as dt
from collections import defaultdict

KEY_RE = re.compile(r'^([A-Za-z0-9]+-[A-Za-z0-9]+)')
CAP_RE = re.compile(r'^#?\s*([A-Za-z0-9]+)\s+SFIA\s*(\d)\s*(?:-\s*(\w+))?', re.I)
STATUS_ORDER = ['Won Closed', 'Contracted', 'Commit', 'Most Likely', 'Best Case', 'Forecast Placeholder', 'Pipeline']
RETAIN_DAYS = 183
MS = set()


def g(x, *ks):
    for k in ks:
        x = (x or {}).get(k)
    return x


def load(path):
    with open(path) as f:
        d = json.load(f)
    return d.get('records') or d.get('results') or []


def key_of(name):
    m = KEY_RE.match(name or '')
    return m.group(1) if m else (name or '')


def cap_of(res):
    """'#CLOUD  SFIA 5 - Employee' -> ('CLOUD', 5, 'Employee')"""
    m = CAP_RE.match(res or '')
    if not m:
        return ('Other', 0, '')
    cap = m.group(1).upper()
    if cap in ('3BDATA', 'DATA'):
        cap = 'DATA'
    return (cap, int(m.group(2)), (m.group(3) or 'Employee').title())


def workdays(a, b):
    """Working days from date a to date b inclusive."""
    if b < a:
        return 0
    n = 0
    d = a
    while d <= b:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def spread(rem, start, end, today):
    """Spread remaining days evenly over working days between max(start,today) and end -> {YYYY-MM: days}."""
    s = max(start, today)
    if end < s or rem <= 0:
        return {}
    total = workdays(s, end) or 1
    out = defaultdict(float)
    d = s
    while d <= end:
        if d.weekday() < 5:
            out[d.strftime('%Y-%m')] += rem / total
        d += dt.timedelta(days=1)
    return {k: round(v, 2) for k, v in out.items()}


def ms_keys(kdir):
    p = os.path.join(kdir, 'ms_engagements.json')
    if not os.path.exists(p):
        return set()
    # UBDS-* engagements are internal (e.g. UBDS-AJTS, the holiday adjustment placeholder), never client managed services
    return {key_of(r.get('Name')) for r in load(p) if not (r.get('Name') or '').startswith('UBDS-')}


def build_generic(kdir, today):
    rows = []
    for r in load(os.path.join(kdir, 'generic.json')):
        eng = g(r, 'KimbleOne__DeliveryGroup__r', 'Name') or ''
        res = g(r, 'KimbleOne__Resource__r', 'Name') or ''
        cap, lvl, src = cap_of(res)
        start = r.get('KimbleOne__StartDate__c')
        end = r.get('KimbleOne__ForecastP3EndDate__c')
        rem = r.get('KimbleOne__ForecastRemainingUsage__c') or 0
        sd = dt.date.fromisoformat(start) if start else today
        ed = dt.date.fromisoformat(end) if end else today
        rows.append(dict(
            id=r.get('Id'), eng=eng, key=key_of(eng), acc=g(r, 'KimbleOne__DeliveryGroup__r', 'KimbleOne__Account__r', 'Name') or '—',
            st=g(r, 'KimbleOne__DeliveryGroup__r', 'KimbleOne__ForecastStatus__r', 'Name') or '',
            res=res, cap=cap, lvl=lvl, src=src,
            role=g(r, 'KimbleOne__ActivityRole__r', 'Name') or r.get('Client_Role__c') or '',
            start=start, end=end, rem=round(rem, 2), usage=round(r.get('KimbleOne__ForecastUsage__c') or 0, 2),
            rev=round(r.get('KimbleOne__ForecastRevenue__c') or 0), cost=round(r.get('KimbleOne__ForecastCost__c') or 0),
            recr=r.get('Recruitment__c') or '', phase=spread(rem, sd, ed, today), ms=key_of(eng) in MS,
        ))
    rows = [r for r in rows if not r['key'].startswith('UBDS-')]
    rows.sort(key=lambda x: (x['start'] or '', -x['rem']))
    return rows


def build_named(kdir):
    rows = []
    for r in load(os.path.join(kdir, 'named.json')):
        eng = g(r, 'KimbleOne__DeliveryGroup__r', 'Name') or ''
        rows.append(dict(
            id=r.get('Id'), res=g(r, 'KimbleOne__Resource__r', 'Name') or '', typ=g(r, 'KimbleOne__Resource__r', 'KimbleOne__ResourceType__r', 'Name') or '',
            eng=eng, key=key_of(eng), acc=g(r, 'KimbleOne__DeliveryGroup__r', 'KimbleOne__Account__r', 'Name') or '—',
            st=g(r, 'KimbleOne__DeliveryGroup__r', 'KimbleOne__ForecastStatus__r', 'Name') or '',
            role=g(r, 'KimbleOne__ActivityRole__r', 'Name') or '',
            start=r.get('KimbleOne__StartDate__c'), end=r.get('KimbleOne__ForecastP3EndDate__c'),
            m=r.get('KimbleOne__Margin__c'),
            rev=round(r.get('KimbleOne__ForecastRevenue__c') or 0), cost=round(r.get('KimbleOne__ForecastCost__c') or 0),
            usage=round(r.get('KimbleOne__ForecastUsage__c') or 0, 1), remu=round(r.get('KimbleOne__ForecastRemainingUsage__c') or 0, 1),
            arev=round(r.get('KimbleOne__TotalActualRevenue__c') or 0), acost=round(r.get('KimbleOne__TotalActualCost__c') or 0), ms=key_of(eng) in MS,
        ))
    rows = [r for r in rows if not r['key'].startswith('UBDS-')]
    rows.sort(key=lambda x: (x['m'] if x['m'] is not None else 999))
    return rows


def build_months(kdir):
    recs = []
    for f in ('months_fy26.json', 'months_fy27.json'):
        p = os.path.join(kdir, f)
        if os.path.exists(p):
            recs += load(p)
    eng = []
    for r in recs:
        eng.append(dict(eng=r.get('dg') or '', key=key_of(r.get('dg')), acc=r.get('acc') or '—', st=r.get('st') or '', m=(r.get('sd') or '')[:7], rt=r.get('rt') or '',
                        n=r.get('n') or 0, rev=round(r.get('rev') or 0), cost=round(r.get('cost') or 0), usage=round(r.get('usage') or 0, 1),
                        arev=round(r.get('arev') or 0), acost=round(r.get('acost') or 0), ausage=round(r.get('ausage') or 0, 1), ms=key_of(r.get('dg')) in MS))
    eng = [r for r in eng if not r['key'].startswith('UBDS-')]
    eng.sort(key=lambda x: (x['m'], x['eng'], x['rt']))
    return eng


def build_not_detailed(kdir, hubspot_path):
    p = os.path.join(kdir, 'not_detailed.json')
    if not os.path.exists(p):
        return []
    hs = {}
    if hubspot_path and os.path.exists(hubspot_path):
        with open(hubspot_path) as f:
            for d in json.load(f).get('deals', []):
                k = d.get('key')
                if not k: continue
                h = hs.setdefault(k, dict(amount=0, bands=set(), start=None, end=None, names=[]))
                h['amount'] += d.get('amount') or 0; h['bands'].add(d.get('band') or ''); h['names'].append(d.get('name'))
                h['start'] = min(filter(None, [h['start'], d.get('start')]), default=None); h['end'] = max(filter(None, [h['end'], d.get('end')]), default=None)
    out = []
    for r in load(p):
        name = r.get('Name') or ''; k = key_of(name); h = hs.get(k)
        out.append(dict(eng=name, key=k, acc=g(r, 'KimbleOne__Account__r', 'Name') or '—', st=g(r, 'KimbleOne__ForecastStatus__r', 'Name') or '',
                        pg=g(r, 'KimbleOne__ProductGroup__r', 'Name') or '', stage=g(r, 'KimbleOne__DeliveryStage__r', 'Name') or '',
                        rev=round(r.get('KimbleOne__ContractRevenue__c') or 0), start=r.get('KimbleOne__ExpectedStartDate__c'), end=r.get('KimbleOne__ExpectedEndDate__c'),
                        ms=k in MS, hs_amount=round(h['amount']) if h else None, hs_band=', '.join(sorted(b for b in h['bands'] if b)) if h else '', hs_start=h['start'] if h else None, hs_end=h['end'] if h else None, hs_deals=len(h['names']) if h else 0))
    out = [r for r in out if not r['key'].startswith('UBDS-')]
    out.sort(key=lambda x: -(x['hs_amount'] or x['rev'] or 0))
    return out


def summarise(generic, named, today):
    t = today.isoformat()
    live_g = [r for r in generic if (r['end'] or '') >= t and r['rem'] > 0 and not r.get('ms')]
    ms_g = [r for r in generic if (r['end'] or '') >= t and r['rem'] > 0 and r.get('ms')]
    by_st = defaultdict(lambda: dict(n=0, days=0.0, rev=0))
    by_cap = defaultdict(lambda: dict(n=0, days=0.0))
    for r in live_g:
        by_st[r['st']]['n'] += 1; by_st[r['st']]['days'] += r['rem']; by_st[r['st']]['rev'] += r['rev']
        k = f"{r['cap']} {r['lvl']}"
        by_cap[k]['n'] += 1; by_cap[k]['days'] += r['rem']
    started = [r for r in live_g if (r['start'] or t) <= t]
    soon = [r for r in live_g if t < (r['start'] or '') <= (today + dt.timedelta(days=30)).isoformat()]
    live_n = [r for r in named if (r['end'] or '') >= t and r['m'] is not None and not r.get('ms')]
    under = [r for r in live_n if r['m'] < 30]
    rev = sum(r['rev'] for r in live_n); cost = sum(r['cost'] for r in live_n)
    urev = sum(r['rev'] for r in under); ucost = sum(r['cost'] for r in under)
    return dict(
        generic=dict(n=len(live_g), days=round(sum(r['rem'] for r in live_g)), rev=round(sum(r['rev'] for r in live_g)),
                     started=len(started), started_days=round(sum(r['rem'] for r in started)),
                     soon=len(soon), soon_days=round(sum(r['rem'] for r in soon)),
                     recruit=sum(1 for r in live_g if r['recr'] == 'Yes'), ms_n=len(ms_g), ms_days=round(sum(r['rem'] for r in ms_g)),
                     by_status={k: dict(n=v['n'], days=round(v['days']), rev=round(v['rev'])) for k, v in by_st.items()},
                     by_cap={k: dict(n=v['n'], days=round(v['days'])) for k, v in by_cap.items()}),
        margin=dict(n=len(live_n), under=len(under), people=len({r['res'] for r in under}),
                    rev=rev, cost=cost, blended=round((rev - cost) / rev * 100, 1) if rev else None,
                    urev=urev, ucost=ucost, ublended=round((urev - ucost) / urev * 100, 1) if urev else None),
    )


def diff(prev, cur, today):
    """Change log between two generic placeholder sets, keyed by assignment id."""
    t = today.isoformat()
    p = {r['id']: r for r in prev}
    c = {r['id']: r for r in cur if (r['end'] or '') >= t and r['rem'] > 0 and not r.get('ms')}
    out = []
    for i, r in c.items():
        if i not in p:
            out.append(dict(type='added', eng=r['eng'], res=r['res'], role=r['role'], start=r['start'], end=r['end'], days=r['rem'], rev=r['rev']))
        else:
            o = p[i]
            if abs((o.get('rem') or 0) - r['rem']) >= 1 or o.get('start') != r['start'] or o.get('end') != r['end']:
                out.append(dict(type='changed', eng=r['eng'], res=r['res'], role=r['role'], start=r['start'], end=r['end'],
                                days=r['rem'], days_was=o.get('rem'), start_was=o.get('start'), end_was=o.get('end'), rev=r['rev']))
    for i, o in p.items():
        if i not in c:
            out.append(dict(type='removed', eng=o['eng'], res=o['res'], role=o['role'], start=o['start'], end=o['end'], days=o.get('rem'), rev=o.get('rev'),
                            note='filled, removed or expired'))
    out.sort(key=lambda x: (-(x.get('days') or 0)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kimble-dir', required=True)
    ap.add_argument('--history', default=None)
    ap.add_argument('--out-dir', default='out')
    ap.add_argument('--asof', default=None, help='ISO datetime of the pull; default now (UTC)')
    ap.add_argument('--label', default='')
    ap.add_argument('--hubspot-data', default=None, help="the Revenue Profile dashboard's data.json, to cross-reference engagements that are not forecast in detail")
    a = ap.parse_args()

    asof = a.asof or dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%MZ')
    today = dt.date.fromisoformat(asof[:10])

    global MS
    MS = ms_keys(a.kimble_dir)
    generic = build_generic(a.kimble_dir, today)
    named = build_named(a.kimble_dir)
    eng_months = build_months(a.kimble_dir)
    not_detailed = build_not_detailed(a.kimble_dir, a.hubspot_data)
    summary = summarise(generic, named, today)

    hist = dict(runs=[], changes=[], snapshots=[])
    if a.history and os.path.exists(a.history):
        with open(a.history) as f:
            hist = json.load(f)
    prev_snap = hist['snapshots'][-1]['generic'] if hist.get('snapshots') else None
    changes = diff(prev_snap, generic, today) if prev_snap is not None else []

    hist['runs'].append(dict(asof=asof, label=a.label, **summary))
    if changes:
        hist['changes'].append(dict(asof=asof, items=changes))
    # weekly full snapshot of live placeholders (one per ISO week, replace within the week)
    wk = today.isocalendar()[:2]
    snap = dict(asof=asof, week=f"{wk[0]}-W{wk[1]:02d}", generic=[{k: r[k] for k in ('id', 'eng', 'res', 'role', 'st', 'start', 'end', 'rem', 'rev', 'cap', 'lvl')} for r in generic if (r['end'] or '') >= today.isoformat() and r['rem'] > 0 and not r.get('ms')])
    if hist['snapshots'] and hist['snapshots'][-1].get('week') == snap['week']:
        hist['snapshots'][-1] = snap
    else:
        hist['snapshots'].append(snap)
    cutoff = (today - dt.timedelta(days=RETAIN_DAYS)).isoformat()
    hist['runs'] = [r for r in hist['runs'] if r['asof'][:10] >= cutoff]
    hist['changes'] = [c for c in hist['changes'] if c['asof'][:10] >= cutoff]
    hist['snapshots'] = [s for s in hist['snapshots'] if s['asof'][:10] >= cutoff]

    os.makedirs(a.out_dir, exist_ok=True)
    data = dict(asof=asof, today=today.isoformat(), generic=generic, named=named, eng_months=eng_months, summary=summary, ms_keys=sorted(MS), not_detailed=not_detailed)
    with open(os.path.join(a.out_dir, 'data.json'), 'w') as f:
        json.dump(data, f, separators=(',', ':'))
    with open(os.path.join(a.out_dir, 'history.json'), 'w') as f:
        json.dump(hist, f, separators=(',', ':'))

    s = summary
    print(f"as of {asof}: {s['generic']['n']} live placeholders, {s['generic']['days']} days, £{s['generic']['rev']:,}; "
          f"{s['generic']['started']} already started, {s['generic']['soon']} start within 30 days")
    print(f"named: {s['margin']['n']} live billable assignments, {s['margin']['under']} under 30% ({s['margin']['people']} people), blended {s['margin']['blended']}%")
    print(f"changes vs last snapshot: {len(changes)}")
    for c in changes[:15]:
        print(f"  {c['type']:8} {c['eng']} | {c['res']} | {c['role']} | {c.get('days_was', '')}->{c.get('days')} days")


if __name__ == '__main__':
    main()
