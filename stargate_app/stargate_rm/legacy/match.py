#!/usr/bin/env python3
"""Match live generic placeholders in Kantata to named resources (capability profile + availability).

Inputs (--match-dir): resources.json, res_roles.json, booked.json, cap_*.json (raw soqlQuery dumps)
        --data: the dashboard data.json (for the live placeholder list)
Output: match.json  {asof, placeholders:[{..., options:[...]}], people:[...], summary:{...}}
"""
import argparse, json, glob, os, re, datetime as dt
from collections import defaultdict

UK_HOLS = {'2026-12-25', '2026-12-28', '2027-01-01', '2027-04-02', '2027-04-05', '2027-05-03', '2027-05-31', '2027-08-30', '2027-12-27', '2027-12-28', '2028-01-03'}
LEVEL_RE = re.compile(r'SFIA\s*(\d)', re.I)

# role families: keyword -> family, checked in order
FAMILY_RULES = [
    ('pentest', ['penetration', 'dfir', 'pen test']),
    ('security', ['security', 'soc analyst', 'sioc', 'shift lead', 'ioc', 'information security', 'cyber']),
    ('architect', ['architect']),
    ('test', ['test engineer', 'tester']),
    ('data', ['data engineer', 'data analyst', 'data scientist', 'bi dev', 'business intelligence', 'power bi']),
    ('developer', ['developer', 'full stack', 'power platform', 'software engineer']),
    ('cloud', ['cloud engineer', 'cloud infrastructure', 'infrastructure engineer', 'platform engineer', 'devops', 'engineer']),
    ('delivery', ['delivery manager', 'delivery principal', 'delivery director', 'project manager', 'programme manager', 'scrum master']),
    ('pmo', ['pmo']),
    ('service', ['service manager', 'service transition', 'service delivery', 'transition manager', 'service architect']),
    ('content', ['content', 'design specialist', 'ux', 'user research']),
    ('consultant', ['consultant', 'consulting', 'business analyst', 'advisory']),
]
FAMILY_LABEL = {'pentest': 'Penetration testing', 'security': 'Security', 'architect': 'Architecture', 'test': 'Test engineering', 'data': 'Data engineering', 'developer': 'Software development', 'cloud': 'Cloud engineering', 'delivery': 'Delivery management', 'pmo': 'PMO', 'service': 'Service management', 'content': 'Content & design', 'consultant': 'Consulting'}
# which capability groups evidence each family
FAMILY_CAPS = {
    'pentest': ['Security Advisory & Engineering', 'Information Security & GRC'],
    'security': ['Security Advisory & Engineering', 'Information Security & GRC', 'Secure Connectivity'],
    'architect': ['Solution Architecture', 'Azure', 'AWS'],
    'test': ['App Development & Modernisation'],
    'data': ['Data, Analytics, and AI'],
    'developer': ['App Development & Modernisation'],
    'cloud': ['Azure', 'AWS', 'Secure Connectivity'],
    'delivery': ['PMO & Project Management', 'Portfolio & Programme Management'],
    'pmo': ['PMO & Project Management'],
    'service': ['Service Design & Management', 'Service Delivery'],
    'content': ['Digital Transformation'],
    'consultant': ['Digital Transformation', 'General Consulting'],
}
# practice tag on the placeholder -> business units that naturally supply it
PRACTICE_BU = {
    'CLOUD': ['Azure', 'AWS (Rayo)', 'AWS (UBDS Digital)', 'App Development & Modernisation', 'App Development & Modernisation (Rayo)', 'Solution Architecture'],
    'CON': ['Digital Transformation', 'Strategy and Innovation', 'Portfolio & Programme Management', 'Service Design & Management'],
    'P3M': ['PMO & Project Management', 'Portfolio & Programme Management'],
    'MAN': ['Service Design & Management', 'Azure', 'Secure Connectivity'],
    'NET': ['Secure Connectivity', 'Azure', 'Solution Architecture', 'AWS (Rayo)'],
    'CYBER': ['Security Advisory & Engineering', 'Security Advisory & Engineering (Rayo)', 'Penetration Testing', 'Information Security (UBDS)'],
    '3BDATA': ['Penetration Testing', 'Security Advisory & Engineering', 'Security Advisory & Engineering (Rayo)'],
    'DATA': ['Data, Analytics and AI', 'Data, Analytics and AI (Rayo)'],
    'RAYO': ['AWS (Rayo)'],
}
LEADERSHIP = ['chief', 'director', 'partner', 'founder', 'managing', 'officer', 'people manager', 'talent', 'head of bid', 'head of business', 'head of partner', 'communications', 'marketing', 'accountant', 'business support', 'business operations', 'group ']
NON_DELIVERY_BU = {'Service Delivery', 'Client Success', 'Group Management Board', 'People', 'Finance and Legal', 'Business Operations', 'Operations', 'UBDS India - Shared Services', 'UBDS India - Win Centre', 'UBDS India', 'UBDS UK', 'Technology & Innovation'}
PUBLIC_CAP = {'Central Government', 'Law Enforcement', 'Local Council', 'Healthcare'}
LAW = {'National Crime Agency'}
# Resourcing policy (Dip, 7 Oct 2026): fill with employees first, always; use suppliers/partners only where no employee can fill;
# recruit FTE only for long-term, strategic engagements in strategic roles; displace suppliers/partners wherever an employee can do the role.
STRATEGIC_FAMILIES = {'cloud', 'architect', 'security', 'data', 'developer', 'delivery', 'pentest'}
LONG_TERM_DAYS = 120          # placeholder of 120+ remaining days, or engagement running 6+ months, counts as long term
STRATEGIC_STATUS = {'Won Closed', 'Contracted', 'Commit'}
# Stacking rule (Dip, 9 Oct 2026): someone on a T&M engagement, even at 100%, can still be allocated to an outcome-based engagement.
# Cap (Dip, 10 Oct 2026): UBDS forecasts everyone at 90% of working days, so a 'full' T&M booking is ~90% and the remaining ~10% is the leave/non-billable
# allowance, not spare capacity. Stacking is therefore capped: total planned load (T&M + outcome + absence/internal) may not exceed MAX_LOAD x capacity
# (default 1.1 = 110%, i.e. the 10% fixed-price efficiency assumed in the FY27 plan). Pass --max-load to change it; 1.0 switches stacking off.
# So for outcome-based placeholders, availability ignores the person's T&M bookings (absence, internal time and other
# outcome-based bookings still count). T&M placeholders keep the full availability test. Kantata product groups:
TM_PG = {'Time Only', 'Time and Expense'}
OUTCOME_PG = {'Fixed Price Project', 'Annuities', 'Managed Services (BU Delivered)', 'IoC Managed Services', 'SoC Managed Services', 'Product Sale', 'Cost only', '3rd Party Pass through'}


def g(x, *ks):
    for k in ks:
        x = (x or {}).get(k)
    return x


def load(p):
    with open(p) as f:
        d = json.load(f)
    return d.get('records') or []


def key_of(name):
    m = re.match(r'^([A-Za-z0-9]+-[A-Za-z0-9]+)', name or '')
    return m.group(1) if m else (name or '')


def family_of(text):
    t = (text or '').lower()
    for fam, kws in FAMILY_RULES:
        if any(k in t for k in kws):
            return fam
    return None


def level_of(grade):
    m = LEVEL_RE.search(grade or '')
    return int(m.group(1)) if m else None


def workdays(a, b):
    n = 0
    d = a
    while d <= b:
        if d.weekday() < 5 and d.isoformat() not in UK_HOLS:
            n += 1
        d += dt.timedelta(days=1)
    return n


def month_capacity(m):
    y, mo = int(m[:4]), int(m[5:7])
    first = dt.date(y, mo, 1)
    last = (dt.date(y + (mo // 12), mo % 12 + 1, 1) - dt.timedelta(days=1))
    return workdays(first, last)


def build_people(mdir, today, max_load=None):
    people = {}
    for r in load(os.path.join(mdir, 'resources.json')):
        bu = g(r, 'KimbleOne__BusinessUnit__r', 'Name') or ''
        prole = g(r, 'KimbleOne__ActivityRole__r', 'Name') or ''
        people[r['Id']] = dict(
            id=r['Id'], name=r['Name'], typ=g(r, 'KimbleOne__ResourceType__r', 'Name') or '', grade=g(r, 'KimbleOne__Grade__r', 'Name') or '',
            lvl=level_of(g(r, 'KimbleOne__Grade__r', 'Name')), bu=bu, bu2=g(r, 'KimbleOne__BusinessUnitSecondary__r', 'Name') or '',
            prole=prole, brole=r.get('Budget_role__c') or '', loc=g(r, 'KimbleOne__Location__r', 'Name') or '',
            cost=r.get('KimbleOne__StandardCost__c') or 0, rate=r.get('KimbleOne__StandardRevenue__c') or 0,
            assigned_until=r.get('KimbleOne__LatestP1AssignmentEndDate__c'), contract_end=r.get('UBDS_Contract_end_date__c'), end=r.get('KimbleOne__EndDate__c'),
            roles=set(), fams=set(), caps=defaultdict(list), capn=0, public=False, law=False, certs=[], locs=[],
            booked={}, free={}, booked_tm={}, booked_out={}, booked_other={}, free_out={},
        )
        f = family_of(prole)
        if f: people[r['Id']]['fams'].add(f)
        f = family_of(r.get('Budget_role__c'))
        if f: people[r['Id']]['fams'].add(f)
    for r in load(os.path.join(mdir, 'res_roles.json')):
        p = people.get(r['KimbleOne__Resource__c'])
        if not p: continue
        role = g(r, 'KimbleOne__ActivityRole__r', 'Name') or ''
        if role.startswith('00'): continue
        p['roles'].add(role)
        f = family_of(role)
        if f: p['fams'].add(f)
    for fpath in glob.glob(os.path.join(mdir, 'cap_*.json')):
        for r in load(fpath):
            p = people.get(r['KimbleOne__Resource__c'])
            if not p: continue
            grp = g(r, 'KimbleOne__CapabilityType__r', 'KimbleOne__CapabilityGroup__r', 'Name') or ''
            cap = g(r, 'KimbleOne__CapabilityType__r', 'Name') or ''
            rating = r.get('KimbleOne__NumericRating__c')
            if grp == 'Certifications':
                p['certs'].append(cap); continue
            if grp == 'Location':
                p['locs'].append(cap); continue
            if grp == 'Public Sector':
                p['public'] = True
                if cap == 'Law Enforcement': p['law'] = True
                continue
            if rating is None: continue
            p['caps'][grp].append((cap, rating)); p['capn'] += 1
    # bookings (total, and split by the engagement's product group for the stacking rule)
    for r in load(os.path.join(mdir, 'booked.json')):
        p = people.get(r['res'])
        if not p: continue
        p['booked'][r['sd'][:7]] = round(r.get('u3') or 0, 1)
    for fpath in sorted(glob.glob(os.path.join(mdir, 'booked_type*.json'))):
        for r in load(fpath):
            p = people.get(r['res'])
            if not p: continue
            m = r['sd'][:7]; u = r.get('u3') or 0; pg = r.get('pg')
            k = 'booked_tm' if pg in TM_PG else ('booked_out' if pg in OUTCOME_PG else 'booked_other')
            p[k][m] = round(p[k].get(m, 0) + u, 1)
    months = [f"{y}-{m:02d}" for y in (2026, 2027) for m in range(1, 13) if f"{y}-{m:02d}" >= today[:7] and f"{y}-{m:02d}" <= '2027-03']
    for p in people.values():
        for m in months:
            cap = month_capacity(m)
            if m == today[:7]:
                cap = workdays(dt.date.fromisoformat(today), dt.date(int(m[:4]), int(m[5:7]), 1) + dt.timedelta(days=31)) if False else workdays(dt.date.fromisoformat(today), last_day(m))
            p['free'][m] = round(max(cap - p['booked'].get(m, 0), 0), 1)
            # outcome-based availability: T&M bookings do not count (stacking rule); if the split files are missing, fall back to total
            if p['booked_tm'] or p['booked_out'] or p['booked_other']:
                if max_load is None:
                    p['free_out'][m] = round(max(cap - p['booked_other'].get(m, 0) - p['booked_out'].get(m, 0), 0), 1)
                else:
                    # capped stacking: total planned load (T&M + outcome + absence/internal) may not exceed max_load x capacity
                    p['free_out'][m] = round(max(cap * max_load - p['booked'].get(m, 0), 0), 1)
            else:
                p['free_out'][m] = p['free'][m]
            p['cap_' + m] = cap
    return people, months


def last_day(m):
    y, mo = int(m[:4]), int(m[5:7])
    return dt.date(y + (mo // 12), mo % 12 + 1, 1) - dt.timedelta(days=1)


def deliverable(p):
    if p['lvl'] is None or p['lvl'] >= 7: return False
    if p['bu'] in NON_DELIVERY_BU: return False
    t = (p['prole'] + ' ' + p['brole']).lower()
    if any(k in t for k in LEADERSHIP):
        return False
    return True


def cap_score(p, fam, hint):
    """0..1 evidence that the person has the skills for this family. hint = 'aws'|'azure'|None"""
    groups = FAMILY_CAPS.get(fam, [])
    if hint == 'aws': groups = ['AWS'] + [x for x in groups if x != 'AWS']
    if hint == 'azure': groups = ['Azure'] + [x for x in groups if x != 'Azure']
    best = 0.0
    for i, grp in enumerate(groups):
        ratings = sorted([v for _, v in p['caps'].get(grp, [])], reverse=True)[:5]
        if not ratings: continue
        s = sum(ratings) / len(ratings) / 5.0 * (1.0 if i == 0 or not hint else 0.8)
        best = max(best, s)
    return best


def match(placeholders, people, months, today, known=None):
    known = known or {}
    out = []
    for ph in placeholders:
        fam = family_of(ph['role']) or {'CLOUD': 'cloud', 'CON': 'consultant', 'P3M': 'delivery', 'MAN': 'cloud', 'NET': 'cloud', 'CYBER': 'security', '3BDATA': 'pentest', 'DATA': 'data', 'RAYO': 'service'}.get(ph['cap'], 'consultant')
        hint = 'aws' if 'aws' in (ph['role'] or '').lower() or ph['key'].startswith('NCA') else ('azure' if 'azure' in (ph['role'] or '').lower() else None)
        need_months = {m: v for m, v in ph['phase'].items() if m in months}
        need = sum(need_months.values())
        daily_cost = ph['cost'] / ph['rem'] if ph['rem'] else 0
        daily_rate = ph['rev'] / ph['rem'] if ph['rem'] else 0
        opts = []
        for p in people.values():
            if not deliverable(p): continue
            if p['end'] and p['end'] < ph['start']: continue
            reasons = []; score = 0
            # role family
            if fam in p['fams']:
                score += 40; reasons.append('role: ' + FAMILY_LABEL[fam])
            elif p['bu'] in PRACTICE_BU.get(ph['cap'], []) or p['bu2'] in PRACTICE_BU.get(ph['cap'], []):
                score += 15; reasons.append('practice: ' + p['bu'])
            else:
                continue
            # level
            dl = p['lvl'] - ph['lvl']
            if dl == 0: score += 20; reasons.append(f'SFIA {p["lvl"]}')
            elif dl == 1: score += 12; reasons.append(f'SFIA {p["lvl"]} (one above)')
            elif dl == -1: score += 8; reasons.append(f'SFIA {p["lvl"]} (stretch)')
            elif dl == 2: score += 4; reasons.append(f'SFIA {p["lvl"]} (two above)')
            else: continue
            # skills evidence
            cs = cap_score(p, fam, hint)
            score += round(cs * 20)
            if cs >= 0.6: reasons.append(f'skills {cs*5:.1f}/5')
            elif p['capn'] == 0: reasons.append('no skills profile')
            # availability over the placeholder's months (or next 3 months if it starts later)
            fm = list(need_months.keys()) or months[:3]
            capm = sum(p.get('cap_' + m, 0) for m in fm)
            free_all = sum(p['free'].get(m, 0) for m in fm)
            avail_all = free_all / capm if capm else 0
            tm_load = sum(p['booked_tm'].get(m, 0) for m in fm) / capm if capm else 0
            if ph.get('outcome'):
                free = sum(p['free_out'].get(m, 0) for m in fm)
            else:
                free = free_all
            avail = free / capm if capm else 0
            stacked = bool(ph.get('outcome')) and tm_load >= 0.2 and avail > avail_all + 0.005   # badge + bench-first ordering only when the T&M load is material (20%+)
            label = f'{avail*100:.0f}% free' + (f' for outcome work (on T&M {tm_load*100:.0f}%)' if stacked else '')
            if avail >= 0.6: score += 20; reasons.append(label)
            elif avail >= 0.3: score += 10; reasons.append(label)
            elif avail > 0.1: score += 3; reasons.append(label)
            else: reasons.append('fully booked' + (' (outcome work)' if ph.get('outcome') and tm_load > 0 else ''))
            if stacked: score -= 4   # genuinely free capacity (bench) is used before stacking on top of T&M
            # cost
            cost_flag = ''
            if daily_cost and p['cost'] and p['cost'] > daily_cost * 1.15:
                score -= 8; cost_flag = f'cost £{p["cost"]:.0f}/d vs £{daily_cost:.0f}/d planned'
            if ph['acc'] in known.get(p['name'], set()): score += 6; reasons.append('knows the account')
            elif ph['key'].startswith('NCA') and p['law']: score += 5; reasons.append('law-enforcement experience')
            elif p['public']: score += 3
            opts.append(dict(id=p['id'], name=p['name'], typ=p['typ'], lvl=p['lvl'], bu=p['bu'], role=p['prole'] or p['brole'] or (sorted(p['roles'])[0] if p['roles'] else ''),
                             score=score, avail=round(avail * 100), avail_all=round(avail_all * 100), tm_load=round(tm_load * 100), stacked=stacked, free=round(free), cost=p['cost'], cost_flag=cost_flag, reasons=reasons,
                             assigned_until=p['assigned_until'], skills=cs and round(cs * 5, 1), exact=(fam in p['fams'] and dl == 0), kind='primary' if (fam in p['fams'] and dl in (0, 1) and avail >= 0.3) else 'secondary'))
        opts.sort(key=lambda o: (-o['score'], -o['avail']))
        prim = [o for o in opts if o['kind'] == 'primary'][:3]
        sec = [o for o in opts if o['kind'] != 'primary' and o['score'] >= 45][:4]
        out.append(dict(id=ph['id'], key=ph['key'], eng=ph['eng'], acc=ph['acc'], st=ph['st'], res=ph['res'], cap=ph['cap'], lvl=ph['lvl'], role=ph['role'], fam=fam, famlabel=FAMILY_LABEL[fam], hint=hint, pg=ph.get('pg') or '', outcome=bool(ph.get('outcome')),
                        start=ph['start'], end=ph['end'], rem=ph['rem'], need=round(need), need_months={m: round(v, 1) for m, v in need_months.items()}, rev=ph['rev'], daily_rate=round(daily_rate), daily_cost=round(daily_cost), recr=ph['recr'],
                        primary=prim, secondary=sec, status='covered' if prim else ('partial' if sec else 'none')))
    return out


def allocate(res, people, months, max_load=None):
    """Capacity-constrained pass in policy order: every placeholder tries employees first (primary, then stretch);
    suppliers/partners are only used where no employee could be found. One person per placeholder, earliest/largest first,
    never over-booking anyone. Placeholders starting after the availability horizon get a provisional pick."""
    free = {pid: dict(p['free']) for pid, p in people.items()}            # ledger for T&M placeholders: everything booked counts
    free_out = {pid: dict(p['free_out']) for pid, p in people.items()}    # ledger for outcome-based placeholders: T&M bookings ignored (stacking rule)
    load = defaultdict(float)
    order = sorted(res, key=lambda r: (r['start'], -r['rem']))

    alloc_all = defaultdict(lambda: defaultdict(float))  # all days allocated in this pass, per person per month
    alloc_tm = defaultdict(lambda: defaultdict(float))   # T&M days allocated in this pass, per person per month (so stacking on a proposed T&M fill is labelled too)

    def try_take(r, o, need_m):
        f = free_out[o['id']] if r.get('outcome') else free[o['id']]
        if need_m:
            can = sum(min(f.get(m, 0), v) for m, v in need_m.items())
            if can < 0.5 * sum(need_m.values()):
                return None
            for m, v in need_m.items():
                take = min(f.get(m, 0), v); f[m] = f.get(m, 0) - take
                alloc_all[o['id']][m] += take
                if r.get('outcome'):
                    # outcome work also consumes the person's total ledger (so T&M placeholders and displacement never assume a third commitment)
                    ft = free[o['id']]; ft[m] = max(ft.get(m, 0) - take, 0)
                else:
                    alloc_tm[o['id']][m] += take
                    if max_load is not None:
                        # under a stacking cap, T&M filled in this pass counts towards the person's total load too
                        fo = free_out[o['id']]; fo[m] = max(fo.get(m, 0) - take, 0)
            stacked, tm_load = False, o.get('tm_load', 0)
            if r.get('outcome'):
                p = people[o['id']]; capm = sum(p.get('cap_' + m, 0) for m in need_m) or 1
                tm_eff = (sum(p['booked_tm'].get(m, 0) for m in need_m) + sum(alloc_tm[o['id']].get(m, 0) for m in need_m)) / capm
                total = (sum(p['booked'].get(m, 0) for m in need_m) + sum(alloc_all[o['id']].get(m, 0) for m in need_m)) / capm
                # stacked = this placement takes the person's planned load above 100% of working days, on the back of T&M work
                if total > 1.005 and tm_eff >= 0.2: stacked, tm_load = True, round(tm_eff * 100)
            return dict(id=o['id'], name=o['name'], typ=o['typ'], lvl=o['lvl'], role=o['role'], covered=round(can), kind=o['kind'], score=o['score'], stacked=stacked, tm_load=tm_load)
        if load[o['id']] >= 2:
            return None
        load[o['id']] += 1
        return dict(id=o['id'], name=o['name'], typ=o['typ'], lvl=o['lvl'], role=o['role'], covered=None, kind=o['kind'], score=o['score'], provisional=True, stacked=o.get('stacked', False), tm_load=o.get('tm_load', 0))

    for r in order:
        need_m = {m: v for m, v in r['need_months'].items()} if r.get('need_months') else {}
        r['alloc'] = None; r['alloc_kind'] = None
        cands = [o for o in r['primary'] + r['secondary'] if o['score'] >= 45]
        # policy order: employee primary, employee stretch, then supplier/partner primary, supplier/partner stretch
        # policy order: employee primary, employee stretch, then supplier/partner; within each, people with real free capacity before stacked T&M people
        cands.sort(key=lambda o: (0 if o['typ'] == 'Employee' else 1, 0 if o['kind'] == 'primary' else 1, 1 if o.get('stacked') else 0, -o['score']))
        for o in cands:
            a = try_take(r, o, need_m)
            if a:
                r['alloc'] = a; r['alloc_kind'] = o['kind']; break
        if not r['alloc']:
            r['alloc_status'] = 'gap'
        elif r['alloc']['typ'] != 'Employee':
            r['alloc_status'] = 'contingent'
        elif r['alloc_kind'] == 'primary':
            r['alloc_status'] = 'filled'
        else:
            r['alloc_status'] = 'stretch'
        # recruit recommendation: only where an employee could not be found, the work is long term and the role is strategic
        long_term = r['rem'] >= LONG_TERM_DAYS or (r['end'] and r['start'] and (dt.date.fromisoformat(r['end']) - dt.date.fromisoformat(r['start'])).days >= 180)
        strategic = r['fam'] in STRATEGIC_FAMILIES and r['st'] in STRATEGIC_STATUS
        if r['alloc_status'] in ('gap', 'contingent'):
            r['recommend'] = 'recruit FTE' if (long_term and strategic) else ('contingent ok' if r['alloc_status'] == 'contingent' else 'contingent / defer')
            r['recommend_why'] = ('long-term, strategic role on sold work' if (long_term and strategic) else ('short or non-strategic: do not recruit' if not strategic else 'not long-term enough to recruit'))
        else:
            r['recommend'] = ''; r['recommend_why'] = ''
    return free


def displacement(named, people, months, today, free):
    """Live/forward supplier and partner assignments where an employee with the right profile has capacity left after the placeholder pass.
    Saving = (assignment cost rate - employee standard cost) x remaining days. Capacity-aware, largest saving first."""
    out = []
    fwd = [r for r in named if r['typ'] in ('Supplier', 'Partner') and r['end'] >= today and (r['remu'] or 0) > 0 and (r['usage'] or 0) > 0]
    fwd.sort(key=lambda r: -((r['cost'] / r['usage']) * r['remu']))
    by_name = {p['name']: p for p in people.values()}
    for r in fwd:
        cost_rate = r['cost'] / r['usage']; rev_rate = r['rev'] / r['usage']
        fam = family_of(r['role']) or 'consultant'
        sp = by_name.get(r['res'])
        lvl = sp['lvl'] if sp else None
        # phase remaining days across the assignment's months within the horizon
        s0 = max(r['start'], today); ms_ = [m for m in months if m + '-31' >= s0 and m + '-01' <= r['end']]
        if not ms_:
            out.append(dict(res=r['res'], typ=r['typ'], key=r['key'], acc=r['acc'], st=r['st'], role=r['role'], lvl=lvl, start=r['start'], end=r['end'], remu=round(r['remu']), rev_rate=round(rev_rate), cost_rate=round(cost_rate), margin=r['m'], candidate=None, saving=0, note='starts after the availability horizon'))
            continue
        per_m = r['remu'] / len(ms_)
        best = None
        for p in people.values():
            if p['typ'] != 'Employee' or not deliverable(p): continue
            if fam not in p['fams']: continue
            if lvl is not None and abs(p['lvl'] - lvl) > 1: continue
            f = free[p['id']]
            can = sum(min(f.get(m, 0), per_m) for m in ms_)
            if can < 0.5 * r['remu'] and can < 0.5 * sum(per_m for _ in ms_): continue
            saving = (cost_rate - (p['cost'] or 0)) * min(can, r['remu'])
            if saving <= 0: continue
            cand = dict(id=p['id'], name=p['name'], lvl=p['lvl'], bu=p['bu'], cost=p['cost'], can=round(can), saving=round(saving), new_margin=round((rev_rate - (p['cost'] or 0)) / rev_rate * 100, 1) if rev_rate else None)
            if not best or cand['saving'] > best['saving']: best = cand
        if best:
            f = free[best['id']]
            for m in ms_:
                take = min(f.get(m, 0), per_m); f[m] = f.get(m, 0) - take
        out.append(dict(res=r['res'], typ=r['typ'], key=r['key'], acc=r['acc'], st=r['st'], role=r['role'], lvl=lvl, start=r['start'], end=r['end'], remu=round(r['remu']), rev_rate=round(rev_rate), cost_rate=round(cost_rate), margin=r['m'],
                        candidate=best, saving=best['saving'] if best else 0, note='' if best else 'no employee with the role, level and capacity'))
    return out


def initiatives(named, people, months, today, disp):
    """Margin levers, quantified from Kantata forward data."""
    by_name = {p['name']: p for p in people.values()}
    # 1. displacement
    d_total = sum(d['saving'] for d in disp)
    # 2. rate leakage: employee assignments billed well below the person's standard rate
    leak = []
    for r in named:
        if r['typ'] != 'Employee' or r['end'] < today or not r['usage'] or not r['remu']: continue
        p = by_name.get(r['res'])
        if not p or not p['rate']: continue
        rate = r['rev'] / r['usage']
        if rate < 0.85 * p['rate']:
            leak.append(dict(res=r['res'], key=r['key'], acc=r['acc'], role=r['role'], lvl=p['lvl'], rate=round(rate), std=round(p['rate']), remu=round(r['remu']), uplift=round((p['rate'] - rate) * r['remu']), margin=r['m']))
    leak.sort(key=lambda x: -x['uplift'])
    # 3. over-graded: SFIA 6+ employees on assignments earning under 30%
    over = []
    for r in named:
        if r['typ'] != 'Employee' or r['end'] < today or r['m'] is None or r['m'] >= 30 or not r['remu']: continue
        p = by_name.get(r['res'])
        if p and p['lvl'] and p['lvl'] >= 6:
            over.append(dict(res=r['res'], key=r['key'], acc=r['acc'], role=r['role'], lvl=p['lvl'], margin=r['m'], remu=round(r['remu']), rev=r['rev'], cost=r['cost']))
    over.sort(key=lambda x: x['margin'])
    # 4. bench cost next 3 months
    bench = []
    for p in people.values():
        if p['typ'] != 'Employee' or not deliverable(p): continue
        fm = months[:3]; free = sum(p['free'].get(m, 0) for m in fm); capm = sum(p.get('cap_' + m, 0) for m in fm)
        if capm and free / capm >= 0.3:
            bench.append(dict(name=p['name'], lvl=p['lvl'], bu=p['bu'], role=p['prole'] or p['brole'], free=round(free), cost=round(free * (p['cost'] or 0)), rate=p['rate']))
    bench.sort(key=lambda x: -x['cost'])
    # 5. supplier/partner contracts ending within 120 days while still assigned
    ending = []
    for p in people.values():
        if p['typ'] == 'Employee' or not p['contract_end']: continue
        if today <= p['contract_end'] <= (dt.date.fromisoformat(today) + dt.timedelta(days=120)).isoformat():
            fwd = [r for r in named if r['res'] == p['name'] and r['end'] >= today and (r['remu'] or 0) > 0]
            if fwd:
                ending.append(dict(name=p['name'], typ=p['typ'], lvl=p['lvl'], bu=p['bu'], contract_end=p['contract_end'], engagements=sorted({r['key'] for r in fwd}), remu=round(sum(r['remu'] for r in fwd)), cost_rate=p['cost']))
    ending.sort(key=lambda x: x['contract_end'])
    return dict(displacement=dict(total=round(d_total), n=sum(1 for d in disp if d['candidate']), of=len(disp), items=disp),
                rate_leakage=dict(total=round(sum(x['uplift'] for x in leak)), n=len(leak), items=leak[:40]),
                over_graded=dict(n=len(over), rev=round(sum(x['rev'] for x in over)), items=over[:40]),
                bench_cost=dict(total=round(sum(x['cost'] for x in bench)), n=len(bench), items=bench[:40]),
                contracts_ending=dict(n=len(ending), items=ending))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--match-dir', required=True); ap.add_argument('--data', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--hubspot-data', default=None, help='accepted for compatibility; not used')
    ap.add_argument('--max-load', type=float, default=1.1, help='stacking cap as a share of capacity, e.g. 1.2 = a person may be planned to 120%% of working days across T&M + outcome work; omit = T&M ignored entirely for outcome placeholders')
    a = ap.parse_args()
    with open(a.data) as f: D = json.load(f)
    today = D['today']
    people, months = build_people(a.match_dir, today, a.max_load)
    live = [r for r in D['generic'] if r['rem'] > 0 and r['end'] >= today]
    # managed-services engagements are out of scope (per Dip, 7 Oct 2026): Kantata product group IoC or SoC Managed Services, or Annuities (BU-delivered managed services stay in)
    ms_keys = set()
    msp = os.path.join(a.match_dir, 'ms_engagements.json')
    if os.path.exists(msp):
        ms_keys |= {key_of(r.get('Name')) for r in load(msp)}
    # HubSpot's 'Managed Services' offering is deliberately NOT used: NCA DEV/DRB/NCC carry that tag but are delivery work and stay in scope (Dip, 7 Oct 2026)
    excluded = [r for r in live if r['key'] in ms_keys]
    live = [r for r in live if r['key'] not in ms_keys]
    # 3BData placeholders are 3B Data Security (pen test), not the data practice
    for r in live:
        if '3bdata' in r['res'].lower(): r['cap'] = '3BDATA'
    # engagement product group -> outcome-based flag (stacking rule)
    pgmap = {}
    pgp = os.path.join(a.match_dir, 'eng_pg.json')
    if os.path.exists(pgp):
        for r in load(pgp):
            pgmap[key_of(r.get('Name'))] = g(r, 'KimbleOne__ProductGroup__r', 'Name') or ''
    for r in live:
        r['pg'] = pgmap.get(r['key'], '')
        r['outcome'] = r['pg'] in OUTCOME_PG
    known = defaultdict(set)
    for r in D['named']:
        known[r['res']].add(r['acc'])
    res = match(live, people, months, today, known)
    free_after = allocate(res, people, months, a.max_load)
    named_live = [r for r in D['named'] if not r.get('ms')]
    disp = displacement(named_live, people, months, today, free_after)
    init = initiatives(named_live, people, months, today, disp)
    # people view: who is free and what they match
    plist = []
    for p in people.values():
        if not deliverable(p): continue
        fm = months[:3]
        free = sum(p['free'].get(m, 0) for m in fm); capm = sum(p.get('cap_' + m, 0) for m in fm)
        hits = [r for r in res if any(o['id'] == p['id'] for o in r['primary'] + r['secondary'])]
        plist.append(dict(id=p['id'], name=p['name'], typ=p['typ'], lvl=p['lvl'], bu=p['bu'], role=p['prole'] or p['brole'], fams=sorted(FAMILY_LABEL[f] for f in p['fams']), free=round(free), cap=capm, avail=round(free / capm * 100) if capm else 0,
                          assigned_until=p['assigned_until'], contract_end=p['contract_end'], cost=p['cost'], capn=p['capn'], matches=[dict(key=r['key'], role=r['role'] or r['famlabel'], lvl=r['lvl'], start=r['start']) for r in hits][:8], free_by_month={m: p['free'].get(m, 0) for m in months}))
    plist.sort(key=lambda p: -p['free'])
    summary = dict(placeholders=len(res), covered=sum(1 for r in res if r['status'] == 'covered'), partial=sum(1 for r in res if r['status'] == 'partial'), none=sum(1 for r in res if r['status'] == 'none'),
                   days=round(sum(r['rem'] for r in res)), days_covered=round(sum(r['rem'] for r in res if r['status'] == 'covered')), days_none=round(sum(r['rem'] for r in res if r['status'] == 'none')),
                   bench=sum(1 for p in plist if p['avail'] >= 50), bench_days=round(sum(p['free'] for p in plist if p['avail'] >= 50)),
                   months=months, people=len(plist),
                   filled=sum(1 for r in res if r.get('alloc_status') == 'filled'), stretch=sum(1 for r in res if r.get('alloc_status') == 'stretch'), gap=sum(1 for r in res if r.get('alloc_status') == 'gap'), contingent=sum(1 for r in res if r.get('alloc_status') == 'contingent'), days_contingent=round(sum(r['rem'] for r in res if r.get('alloc_status') == 'contingent')), recruit=sum(1 for r in res if r.get('recommend') == 'recruit FTE'), recruit_days=round(sum(r['rem'] for r in res if r.get('recommend') == 'recruit FTE')),
                   days_filled=round(sum(r['rem'] for r in res if r.get('alloc_status') == 'filled')), days_stretch=round(sum(r['rem'] for r in res if r.get('alloc_status') == 'stretch')), days_gap=round(sum(r['rem'] for r in res if r.get('alloc_status') == 'gap')),
                   outcome_placeholders=sum(1 for r in res if r.get('outcome')), stacked=sum(1 for r in res if r.get('alloc') and r['alloc'].get('stacked')), stacked_days=round(sum(r['rem'] for r in res if r.get('alloc') and r['alloc'].get('stacked'))),
                   excluded_ms=sorted({r['key'] for r in excluded}), excluded_ms_n=len(excluded), excluded_ms_days=round(sum(r['rem'] for r in excluded)),
                   horizon_days=round(sum(r['need'] for r in res)), horizon_days_filled=round(sum(min(r['need'], r['alloc']['covered'] or 0) for r in res if r.get('alloc') and r['alloc'].get('covered') is not None)))
    with open(a.out, 'w') as f:
        json.dump(dict(asof=D['asof'], today=today, summary=summary, placeholders=res, people=plist, initiatives=init), f, separators=(',', ':'))
    print(json.dumps(summary))
    by = defaultdict(lambda: [0, 0, 0])
    for r in res:
        k = f"{r['famlabel']} SFIA {r['lvl']}"
        by[k][0] += 1; by[k][{'covered': 1, 'partial': 2, 'none': 2}[r['status']] - 1 + 1 if False else (1 if r['status'] == 'covered' else 2)] += 1
    for k, v in sorted(by.items(), key=lambda kv: -kv[1][0]):
        print(f'{k:32} {v[0]:3} placeholders  covered {v[1]:3}  not {v[2]:3}')


if __name__ == '__main__':
    main()
