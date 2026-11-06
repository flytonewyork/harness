"""Unit economics of an AI-native clinic in Hong Kong, against the alternatives.

Illustrative model for docs/analysis/ai-native-clinic-investment.md. Every input is an
assumption unless a source is named next to it. No patient data.

    python docs/analysis/clinic_model.py          # print the tables
    python docs/analysis/clinic_model.py --json   # also print chart rows as JSON
"""
import json
import sys

DAYS = 22  # working days a month, one full-time doctor (FTE)
HK = lambda x: round(x / 1000, 1)  # HK$ thousands

# Human Health Holdings FY2025 (year to 30 Jun 2025), HKEX results announcement 25 Sep 2025:
# revenue 644.4m; doctor/dentist fees 233.8m; staff 135.7m; pharmaceutical supplies 92.7m;
# depreciation 66.2m; lease interest 5.0m; profit 26.6m.
HH = dict(rev=644.4, doctor=233.8, staff=135.7, drugs=92.7, dep=66.2, lease_int=5.0, profit=26.6)
DRUG_PER_VISIT = 400 * HH["drugs"] / HH["rev"]  # ~HK$58 of medicine inside a HK$400 visit


def solo_gp(visits=35, fee=400):
    v = visits * DAYS
    rev = {"Visits": v * fee}
    cost = {"Medicines": v * DRUG_PER_VISIT, "Staff": 2 * 18000 * 1.05,
            "Premises": 35000, "Other": 13500}
    owner = sum(rev.values()) - sum(cost.values())
    hours = DAYS * (visits * 10 / 60 + 1.5 + 0.5)
    return dict(name="Solo GP (owner-run)", rev=rev, cost=cost, doctor=owner, operator=0,
                hours=hours, capital=600000 + 3 * 35000 + 100000)


def group_sessional(visits=35, fee=400, split=0.50):
    v = visits * DAYS
    rev = {"Visits": v * fee}
    total = sum(rev.values())
    cost = {"Medicines": v * DRUG_PER_VISIT, "Staff": 40000, "Premises": 35000, "Other": 15000}
    doc = split * total
    return dict(name="Group clinic, sessional 50%", rev=rev, cost=cost, doctor=doc,
                operator=total - doc - sum(cost.values()),
                hours=DAYS * (visits * 10 / 60 + 1.5), capital=500000)


def listed_chain(visits=35, fee=400):
    """Human Health FY2025 ratios applied to the same revenue so the columns compare."""
    total = visits * DAYS * fee
    r = lambda k: HH[k] / HH["rev"]
    pre_tax = HH["profit"] / HH["rev"] / (1 - 0.165)  # back out HK profits tax, approx.
    prem = (HH["dep"] + HH["lease_int"]) / HH["rev"]  # premises and equipment (incl. right-of-use)
    other = 1 - r("doctor") - r("staff") - r("drugs") - prem - pre_tax
    cost = {"Medicines": r("drugs") * total, "Staff": r("staff") * total,
            "Premises": prem * total, "Other": other * total}
    return dict(name="Listed GP chain (reported ratios)", rev={"Visits": total}, cost=cost,
                doctor=r("doctor") * total, operator=pre_tax * total,
                hours=DAYS * (visits * 10 / 60 + 1.5), capital=800000)


def ai_added(visits=37, fee=400, split=0.50):
    v = visits * DAYS
    total = v * fee
    cost = {"Medicines": v * DRUG_PER_VISIT, "Staff": 38000, "Premises": 35000,
            "AI and software": 4000, "Other": 15000}
    doc = split * total
    return dict(name="AI-added clinic (scribe, paid reminders)", rev={"Visits": total}, cost=cost,
                doctor=doc, operator=total - doc - sum(cost.values()),
                hours=DAYS * (visits * 9 / 60 + 1.2), capital=500000)


def ai_native(visits=35, fee=400, tasks=20, task_price=200, members=200, member_price=300,
              split=0.60, refills=150, basket_price=250, basket_cost=170, ai_per_active=4,
              active=1000, session_rent=600, nurse=30000, ramp_visits=1.0, ramp_line=1.0):
    v = visits * DAYS * ramp_visits
    rev = {"Visits": v * fee,
           "WhatsApp tasks": tasks * DAYS * task_price * ramp_line,
           "Memberships": members * member_price * ramp_line,
           "Medicine refills": refills * basket_price * ramp_line}
    service = rev["Visits"] + rev["WhatsApp tasks"] + rev["Memberships"]
    cost = {"Medicines": v * DRUG_PER_VISIT * 0.9 + refills * basket_cost * ramp_line,
            "Staff": nurse * 1.05,
            "Premises": 2 * DAYS * session_rent * ramp_visits,
            "AI and software": active * ai_per_active * ramp_line + 3000,
            "Other": (0.025 * (rev["WhatsApp tasks"] + rev["Memberships"] + rev["Medicine refills"])
                      + 0.01 * rev["Visits"] + 25 * refills * ramp_line + 20000)}
    doc = split * service
    hours = DAYS * (visits * 9 / 60 + tasks * 4 / 60 + 0.5 + 0.5)
    return dict(name="AI-native practice (tmrw-run, 60%)", rev=rev, cost=cost, doctor=doc,
                operator=sum(rev.values()) - doc - sum(cost.values()), hours=hours, capital=100000)


def own_line(tasks=10, task_price=200, members=100, member_price=300, base_fee=2500, per_task=10):
    """Doctor mode: sessions as in a group clinic, plus the doctor's own line on the platform."""
    s = group_sessional()
    line_gross = tasks * DAYS * task_price + members * member_price
    platform_fee = base_fee + per_task * tasks * DAYS
    line_net = line_gross - platform_fee - 0.025 * line_gross
    line_hours = DAYS * (tasks * 4 / 60 + 0.33)
    platform_cost = 400 * 4 + 500 + 300  # compute for ~400 active patients, WhatsApp, support
    return dict(name="Doctor's own line on the platform", rev={"Visits": s["rev"]["Visits"],
                "Line (tasks and members)": line_gross}, cost=s["cost"],
                doctor=s["doctor"] + line_net, operator=s["operator"],
                platform=platform_fee - platform_cost, platform_rev=platform_fee,
                line_net=line_net, line_hours=line_hours,
                hours=s["hours"] + line_hours, capital=0)


MODELS = [solo_gp, group_sessional, listed_chain, ai_added, ai_native, own_line]


def summary_rows():
    rows = []
    for f in MODELS:
        m = f()
        rev = sum(m["rev"].values())
        rows.append(dict(model=m["name"], revenue=HK(rev), doctor=HK(m["doctor"]),
                         operator=HK(m["operator"]),
                         margin=round(100 * m["operator"] / rev, 1) if m["operator"] else 0.0,
                         hours=round(m["hours"]), per_hour=round(m["doctor"] / m["hours"]),
                         capital=HK(m["capital"]),
                         payback=round(m["capital"] / m["operator"], 1) if m["operator"] > 0 else None,
                         **({"platform_k": HK(m["platform"]), "line_per_hour":
                             round(m["line_net"] / m["line_hours"])} if "platform" in m else {})))
    return rows


def where_money_goes():
    """Stacked shares of a doctor-month, models 1-5 (model 6 is the doctor's view)."""
    out = []
    for f in MODELS[:5]:
        m = f()
        c = m["cost"]
        parts = {"Doctor": m["doctor"], "Staff": c.get("Staff", 0), "Medicines": c.get("Medicines", 0),
                 "Premises": c.get("Premises", 0), "AI and software": c.get("AI and software", 0),
                 "Other": c.get("Other", 0), "Operator margin": m["operator"]}
        for k, v in parts.items():
            out.append(dict(model=m["name"], part=k, hk_k=HK(v)))
    return out


def tornado():
    base = ai_native()["operator"]
    tests = [("Tasks a day (10 / 30)", dict(tasks=10), dict(tasks=30)),
             ("Price per task (HK$150 / 250)", dict(task_price=150), dict(task_price=250)),
             ("Members per doctor (100 / 300)", dict(members=100), dict(members=300)),
             ("Membership price (HK$200 / 400)", dict(member_price=200), dict(member_price=400)),
             ("Doctor's share (65% / 55%)", dict(split=0.65), dict(split=0.55)),
             ("Visits a day (30 / 40)", dict(visits=30), dict(visits=40)),
             ("AI cost per active patient (HK$15 / 1)", dict(ai_per_active=15), dict(ai_per_active=1)),
             ("Room rent per session (HK$900 / 400)", dict(session_rent=900), dict(session_rent=400)),
             ("Nurse cost (HK$40k / 25k)", dict(nurse=40000), dict(nurse=25000))]
    rows = []
    for label, lo, hi in tests:
        rows.append(dict(driver=label, low=HK(ai_native(**lo)["operator"] - base),
                         high=HK(ai_native(**hi)["operator"] - base)))
    rows.sort(key=lambda r: -(abs(r["low"]) + abs(r["high"])))
    return HK(base), rows


SCENARIOS = {
    # end-of-year values, years 1-5
    "bear": dict(fte=[2, 4, 6, 8, 10], tasks=8, task_price=150, members=80, member_price=200,
                 docs=[10, 40, 80, 120, 150], arpu=3000, plat_baskets=10, plat_active=100, consult=[0.1, 0.2, 0.3, 0.3, 0.3],
                 central=[0.25, 0.30, 0.45, 0.50, 0.55], split=0.60, setup=1500000.0),
    "base": dict(fte=[3, 7, 12, 16, 20], tasks=20, task_price=200, members=200, member_price=300,
                 docs=[20, 120, 300, 450, 600], arpu=3600, plat_baskets=20, plat_active=200, consult=[0.2, 0.4, 0.5, 0.6, 0.7],
                 central=[0.25, 0.35, 0.60, 0.75, 0.90], split=0.60, setup=1500000.0),
    "bull": dict(fte=[4, 10, 20, 30, 40], tasks=25, task_price=220, members=300, member_price=300,
                 docs=[30, 200, 500, 800, 1000], arpu=4700, plat_baskets=30, plat_active=300, consult=[0.3, 0.6, 0.8, 1.0, 1.2],
                 central=[0.30, 0.45, 0.80, 1.10, 1.40], split=0.60, setup=2000000.0),
}


def build(s):
    """Monthly build over five years; FTE added evenly, each cohort ramps."""
    months = 60
    fte_path, doc_path = [], []
    prev_f, prev_d = 0.5, 0
    for y in range(5):
        for m in range(12):
            fte_path.append(prev_f + (s["fte"][y] - prev_f) * (m + 1) / 12)
            doc_path.append(prev_d + (s["docs"][y] - prev_d) * (m + 1) / 12)
        prev_f, prev_d = s["fte"][y], s["docs"][y]
    adds = [fte_path[0]] + [fte_path[i] - fte_path[i - 1] for i in range(1, months)]
    years = [dict(practices=0, medicines=0, platform=0, consulting=0, ebitda=0, capex=0,
                  doctor_pay=0) for _ in range(5)]
    cash, low, prev_stock = 0.0, 0.0, 0.0
    for t in range(months):
        y = t // 12
        prac_rev = prac_op = med_rev = med_gp = doc_pay = 0.0
        baskets = 0.0
        for c, n in enumerate(adds[: t + 1]):
            age = t - c
            rv = min(1.0, 0.6 + 0.4 * age / 6)
            rl = min(1.0, 0.2 + 0.8 * age / 12)
            m = ai_native(tasks=s["tasks"], task_price=s["task_price"], members=s["members"],
                          member_price=s["member_price"], split=s["split"],
                          ramp_visits=rv, ramp_line=rl)
            prac_rev += n * (sum(m["rev"].values()) - m["rev"]["Medicine refills"])
            med_rev += n * m["rev"]["Medicine refills"]
            prac_op += n * m["operator"]
            doc_pay += n * m["doctor"]
            baskets += n * 150 * rl
        plat_docs = doc_path[t]
        plat_baskets = plat_docs * s["plat_baskets"]
        med_rev += plat_baskets * 250
        med_gp_extra = plat_baskets * (250 - 170 - 25)
        pharmacist = 45000 * max(1, -(-(baskets + plat_baskets) // 4000)) + 30000 if t >= 9 else 0
        plat_rev = plat_docs * s["arpu"]
        plat_cost = plat_docs * (s["plat_active"] * 4 + 500) + 30000 * -(-plat_docs // 100)
        consult = s["consult"][y] * 1e6
        central = s["central"][y] * 1e6
        ebitda = prac_op + med_gp_extra - pharmacist + plat_rev - plat_cost + 0.6 * consult - central
        capex = max(0.0, adds[t]) * 100000 + (s["setup"] / 12 if t < 12 else 0.0)
        stock = 0.5 * (baskets + plat_baskets) * 170  # half a month of medicine cost, net of payables
        capex += max(0.0, stock - prev_stock)
        prev_stock = stock
        cash += ebitda - capex
        low = min(low, cash)
        Y = years[y]
        Y["practices"] += prac_rev; Y["medicines"] += med_rev; Y["platform"] += plat_rev
        Y["consulting"] += consult; Y["ebitda"] += ebitda; Y["capex"] += capex
        Y["doctor_pay"] += doc_pay
    for i, Y in enumerate(years):
        Y["year"] = i + 1
        Y["revenue"] = Y["practices"] + Y["medicines"] + Y["platform"] + Y["consulting"]
        Y["fte_end"] = s["fte"][i]; Y["docs_end"] = s["docs"][i]
    return years, low


def m_(x):
    return round(x / 1e6, 1)


def payback_with_ramp(**kw):
    cum, t = -100000.0, 0
    while cum < 0 and t < 60:
        rv = min(1.0, 0.6 + 0.4 * t / 6)
        rl = min(1.0, 0.2 + 0.8 * t / 12)
        cum += ai_native(ramp_visits=rv, ramp_line=rl, **kw)["operator"]
        t += 1
    return t


def break_evens():
    out = {"no new lines at 60%": HK(ai_native(tasks=0, members=0, refills=0, active=300)["operator"])}
    target = group_sessional()["operator"]
    for members in (0, 100, 200):
        t = 0
        while ai_native(tasks=t, members=members)["operator"] < target and t < 60:
            t += 1
        out["tasks/day to match a group clinic's margin, %d members" % members] = t
    return out


def bridge():
    g, n = group_sessional(), ai_native()
    new_rev = sum(n["rev"].values()) - n["rev"]["Visits"]
    return dict(group_operator=HK(g["operator"]), new_revenue=HK(new_rev),
                doctor_share=-HK(n["doctor"] - g["doctor"]),
                overhead_saved=HK(g["cost"]["Medicines"] + g["cost"]["Staff"] + g["cost"]["Premises"]
                                  - (n["cost"]["Staff"] + n["cost"]["Premises"] + n["cost"]["Medicines"] - 150 * 170)),
                refill_drug_cost=-HK(150 * 170),
                ai_payments_other=-HK(n["cost"]["AI and software"] + n["cost"]["Other"] - g["cost"]["Other"]),
                ai_native_operator=HK(n["operator"]),
                doctor_from_split=HK((0.60 - 0.50) * 308000), doctor_from_new_lines=HK(0.60 * 148000))


def split_by_line(on_visits=0.50, on_line=0.70, **kw):
    """Doctor pay as a share of visits plus a share of the line (tasks and memberships)."""
    m = ai_native(split=0.0, **kw)
    line = m["rev"]["WhatsApp tasks"] + m["rev"]["Memberships"]
    doctor = on_visits * m["rev"]["Visits"] + on_line * line
    return dict(doctor=HK(doctor), operator=HK(m["operator"] - doctor))


BEAR_LINES = dict(tasks=8, task_price=150, members=80, member_price=200)


def patient_year():
    """A chronic patient (two stable medicines), one year, HK$. Assumptions as in spec 003."""
    return {"Private GP: 6 visits at HK$400, 12 monthly refills at HK$120": 6 * 400 + 12 * 120,
            "AI-native: 2 visits, 4 WhatsApp follow-ups at HK$200, 12 refills at HK$90": 2 * 400 + 4 * 200 + 12 * 90,
            "Video consults (QHMS daytime HK$350) for the 4 follow-ups instead": 2 * 400 + 4 * 350 + 12 * 120,
            "Public GOPC: about 6 visits at HK$50 including drugs": 6 * 50}


if __name__ == "__main__":
    print("Medicine inside a HK$400 visit (Human Health ratio): HK$%.0f" % DRUG_PER_VISIT)
    print("\nPER DOCTOR-MONTH (HK$k)")
    for r in summary_rows():
        print(r)
    print("\nAI-native revenue lines:", {k: HK(v) for k, v in ai_native()["rev"].items()})
    print("AI-native cost lines:", {k: HK(v) for k, v in ai_native()["cost"].items()})
    base, rows = tornado()
    print("\nTORNADO, operator EBITDA per FTE-month, base HK$%sk" % base)
    for r in rows:
        print(r)
    for name, s in SCENARIOS.items():
        years, low = build(s)
        print("\n%s: peak cash need HK$%.1fm" % (name.upper(), -low / 1e6))
        for Y in years:
            print("Y%d rev %5.1f (prac %5.1f med %4.1f plat %4.1f cons %3.1f) ebitda %5.1f (%4.1f%%) "
                  "fte %d docs %d doctor pay %5.1f" % (
                      Y["year"], m_(Y["revenue"]), m_(Y["practices"]), m_(Y["medicines"]),
                      m_(Y["platform"]), m_(Y["consulting"]), m_(Y["ebitda"]),
                      100 * Y["ebitda"] / Y["revenue"], Y["fte_end"], Y["docs_end"], m_(Y["doctor_pay"])))
    print("\nPayback per new AI-native FTE with ramp (months):", payback_with_ramp(),
          "| bear lines:", payback_with_ramp(tasks=8, task_price=150, members=80, member_price=200))
    print("Break-evens:", break_evens())
    print("Bridge, group clinic to AI-native, operator HK$k:", bridge())
    print("Split by line, 50% visits / 70% line: base", split_by_line(), "| bear lines", split_by_line(**BEAR_LINES))
    print("Bear lines at 60%:", HK(ai_native(**BEAR_LINES)["operator"]), "| at 52%:",
          {k: HK(ai_native(split=0.52, **BEAR_LINES)[k]) for k in ("doctor", "operator")})
    bear52 = dict(SCENARIOS["bear"], split=0.52)
    ys, low = build(bear52)
    print("Bear scenario at a 52%% split: year-5 EBITDA HK$%.1fm, peak cash HK$%.1fm" % (ys[4]["ebitda"] / 1e6, -low / 1e6))
    print("Patient's year:", patient_year())
    if "--json" in sys.argv:
        print(json.dumps(dict(where=where_money_goes(), tornado=rows), indent=0))
