"""Rules engine: the deterministic follow-up engine (no model calls). Moved here from loopbench/harnesses.

It encodes ordinary clinical knowledge (what a critical potassium is, which drugs need bloods), never the
benchmark's clinical_policy.yaml. It is the floor a reasoning harness has to beat.

Every item carries a reasoning trace (format in verify.py), in the order a clinician would think it through:
where the evidence came from, how the patient was identified, what it shows against the range printed on the
report, the last comparable result and relevant medicines, the working problem and plan, whether the chart
shows it already handled (a search the platform re-runs), who it went to and why, and what is being asked.
The baseline fills these steps from templates; its quotes are copied word for word from the records, so they
verify. A reasoning harness has to produce the same kind of trace and pass the same checks.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from .escalation import Escalation

CRIT = {"Potassium": (2.8, 6.5), "Sodium": (120, 160), "Glucose (fasting)": (2.5, 25), "Haemoglobin": (7.0, None),
        "Platelets": (20, None), "Calcium": (1.75, 3.4), "Creatinine": (None, 500)}
URG = {"Potassium": (3.0, 6.0), "Sodium": (125, 155), "Haemoglobin": (9.0, None), "Platelets": (50, None),
       "Creatinine": (None, 300), "Glucose (fasting)": (3.0, 15), "White cell count": (2.0, 25), "ALT": (None, 400),
       "AST": (None, 400), "Calcium": (1.9, 3.0), "eGFR": (30, None)}
MONITOR = [("ace/arb", r"pril|sartan", "RFT", 14), ("diuretic", r"thiazide|chlorthalidone|spironolactone|furosemide|indapamide", "RFT", 28),
           ("digoxin", r"digoxin", "RFT", 14), ("warfarin", r"warfarin", "INR", 7), ("statin", r"statin", "LFT", 90),
           ("methotrexate", r"methotrexate", "CBC", 14), ("amiodarone", r"amiodarone", "LFT", 42), ("lithium", r"lithium", "LITHIUM", 7)]
PANEL_TESTS = {"RFT": {"Creatinine", "Potassium", "Sodium", "Urea", "eGFR"}, "LFT": {"ALT", "AST", "ALP", "Bilirubin", "Albumin"},
               "CBC": {"Haemoglobin", "White cell count", "Platelets"}, "INR": {"INR"}, "LITHIUM": {"Lithium"}}
RV = re.compile(r"\bRV\s+(\d+)\s*/\s*(7|52|12)\b", re.I)
WINDOW = {4: 1, 3: 2, 2: 7, 1: 14}

# ----------------------------------------------------------------------------- reasoning templates
URGENCY_LABEL = {4: "critical: same day; the doctor's mobile is paged if it is unseen after an hour",
                 3: "urgent: within 2 days", 2: "soon: within a week", 1: "routine: within 2 weeks"}
PROBLEMS = {   # (test, H|L) -> (working problem, plan)
    ("Potassium", "H"): ("hyperkalaemia", "Repeat electrolytes promptly (a haemolysed sample is not excluded); review ACE inhibitor, ARB, spironolactone and potassium supplements; ECG if markedly raised."),
    ("Potassium", "L"): ("hypokalaemia", "Repeat electrolytes; review diuretics; replace potassium if confirmed."),
    ("Sodium", "L"): ("hyponatraemia", "Repeat; review thiazides and other causative medicines; ask about confusion or falls."),
    ("Sodium", "H"): ("hypernatraemia", "Repeat; assess fluid intake and hydration."),
    ("Creatinine", "H"): ("reduced kidney function", "Repeat renal function and compare with baseline; review nephrotoxic medicines and doses."),
    ("eGFR", "L"): ("reduced kidney function", "Repeat renal function; stage chronic kidney disease if it persists; adjust medicine doses."),
    ("Urea", "H"): ("raised urea", "Check hydration and renal function."),
    ("Bicarbonate", "L"): ("low bicarbonate", "Repeat with renal function; consider metabolic acidosis."),
    ("Bicarbonate", "H"): ("raised bicarbonate", "Repeat; consider diuretics or vomiting."),
    ("Chloride", "L"): ("low chloride", "Interpret with sodium and bicarbonate."),
    ("Chloride", "H"): ("raised chloride", "Interpret with sodium and bicarbonate."),
    ("Glucose (fasting)", "H"): ("hyperglycaemia", "Confirm with HbA1c or a repeat fasting glucose; start diabetes care if confirmed."),
    ("Glucose (fasting)", "L"): ("hypoglycaemia", "Review glucose-lowering medicines; ask about symptoms."),
    ("HbA1c", "H"): ("raised HbA1c", "Diagnose or review diabetes control; agree a treatment change."),
    ("Haemoglobin", "L"): ("anaemia", "Check MCV and ferritin; if microcytic consider thalassaemia trait (common in Hong Kong) as well as iron deficiency; look for blood loss."),
    ("Haemoglobin", "H"): ("raised haemoglobin", "Repeat; consider dehydration, smoking and polycythaemia."),
    ("MCV", "L"): ("microcytosis", "Iron studies and haemoglobin pattern for thalassaemia trait (common in Hong Kong)."),
    ("MCV", "H"): ("macrocytosis", "Check B12, folate, thyroid, alcohol and medicines."),
    ("Platelets", "L"): ("thrombocytopenia", "Repeat with a blood film; review medicines; same-day review if bleeding."),
    ("Platelets", "H"): ("thrombocytosis", "Repeat; look for inflammation or iron deficiency."),
    ("White cell count", "L"): ("leucopenia", "Repeat with a differential; review medicines such as methotrexate."),
    ("White cell count", "H"): ("leucocytosis", "Repeat with a differential; assess for infection."),
    ("ALT", "H"): ("raised liver enzymes", "Repeat liver tests; review statins and alcohol; check hepatitis B status (common in Hong Kong)."),
    ("AST", "H"): ("raised liver enzymes", "Repeat liver tests; review medicines and alcohol."),
    ("ALP", "H"): ("raised ALP", "Repeat with GGT to separate a liver from a bone source."),
    ("Bilirubin", "H"): ("raised bilirubin", "Repeat with split bilirubin; consider Gilbert syndrome or liver disease."),
    ("Albumin", "L"): ("low albumin", "Assess nutrition, liver disease and protein loss in the urine."),
    ("Calcium", "H"): ("hypercalcaemia", "Repeat with PTH; review calcium and vitamin D supplements and thiazides."),
    ("Calcium", "L"): ("hypocalcaemia", "Repeat corrected calcium; check vitamin D and magnesium."),
    ("LDL cholesterol", "H"): ("raised LDL cholesterol", "Cardiovascular risk assessment; lifestyle advice and a statin decision."),
    ("Total cholesterol", "H"): ("raised cholesterol", "Cardiovascular risk assessment; lifestyle advice and a statin decision."),
    ("HDL cholesterol", "L"): ("low HDL cholesterol", "Include in the cardiovascular risk assessment."),
    ("Triglycerides", "H"): ("raised triglycerides", "Repeat fasting; review alcohol and glucose control."),
    ("Urine albumin/creatinine ratio", "H"): ("albuminuria", "Repeat to confirm; consider an ACE inhibitor or ARB and tighter blood pressure control."),
    ("INR", "H"): ("INR above the target range", "Adjust the warfarin dose; ask about bleeding and new interacting medicines."),
    ("INR", "L"): ("INR below the target range", "Adjust the warfarin dose; check adherence."),
}
RELEVANT_MEDS = {
    "Potassium": r"pril|sartan|spironolactone|thiazide|furosemide|indapamide|chlorthalidone|potassium",
    "Sodium": r"thiazide|indapamide|chlorthalidone|spironolactone|furosemide",
    "Creatinine": r"pril|sartan|metformin|spironolactone|furosemide|diclofenac|ibuprofen|naproxen",
    "eGFR": r"pril|sartan|metformin|spironolactone|furosemide|diclofenac|ibuprofen|naproxen",
    "ALT": r"statin|methotrexate|amiodarone", "AST": r"statin|methotrexate|amiodarone",
    "INR": r"warfarin", "Haemoglobin": r"aspirin|clopidogrel|warfarin|methotrexate", "Platelets": r"methotrexate|clopidogrel",
    "White cell count": r"methotrexate|carbimazole", "Glucose (fasting)": r"metformin|gliclazide|insulin|gliptin|gliflozin",
    "HbA1c": r"metformin|gliclazide|insulin|gliptin|gliflozin", "LDL cholesterol": r"statin|ezetimibe",
    "Total cholesterol": r"statin|ezetimibe", "Calcium": r"calcium|vitamin d|calcitriol|thiazide",
}
MONITOR_WHY = {"ace/arb": "ACE inhibitor or ARB started: creatinine and potassium are due 1-2 weeks after starting",
               "diuretic": "Diuretic started: sodium, potassium and creatinine are due within about 4 weeks",
               "digoxin": "Digoxin started: renal function and potassium are due (toxicity risk)",
               "warfarin": "Warfarin started: an INR is due within a week to set the dose",
               "statin": "Statin started: liver enzymes are due at about 3 months",
               "methotrexate": "Methotrexate started: blood count and liver tests are due every 2 weeks at first",
               "amiodarone": "Amiodarone started: liver tests are due",
               "lithium": "Lithium started: a level is due within a week (narrow therapeutic range)"}


def _band(v, lohi):
    lo, hi = lohi
    return (lo is not None and v < lo) or (hi is not None and v > hi)


def _lim(lohi) -> str:
    lo, hi = lohi
    return " or ".join(([f"below {lo:g}"] if lo is not None else []) + ([f"above {hi:g}"] if hi is not None else []))


def lab_urgency(results: list[dict]) -> int:
    u = 0
    for r in results:
        t, v = r["test"], r["value"]
        if t in CRIT and _band(v, CRIT[t]):
            u = max(u, 4)
        elif t in URG and _band(v, URG[t]):
            u = max(u, 3)
        elif r.get("flag") in ("H", "L"):
            lo, hi = r.get("ref_low"), r.get("ref_high")
            far = (hi is not None and v > hi * 1.3) or (lo is not None and v < lo * 0.8)
            u = max(u, 2 if far else 1)
    return u


PRIORITY = ["Potassium", "Sodium", "Glucose (fasting)", "Haemoglobin", "Platelets", "Creatinine", "eGFR", "Calcium",
            "White cell count", "INR", "HbA1c", "ALT", "AST", "Urea", "Bilirubin", "ALP", "Albumin", "MCV",
            "Urine albumin/creatinine ratio", "LDL cholesterol", "Total cholesterol", "Triglycerides", "HDL cholesterol"]


def priority(test: str) -> int:
    return PRIORITY.index(test) if test in PRIORITY else len(PRIORITY)


def band_of(r: dict) -> tuple[int, str]:
    """This harness's own urgency band for one result, with the reason in words."""
    t, v = r["test"], r["value"]
    if t in CRIT and _band(v, CRIT[t]):
        return 4, f"critical by this harness's rules ({_lim(CRIT[t])})"
    if t in URG and _band(v, URG[t]):
        return 3, f"urgent by this harness's rules ({_lim(URG[t])})"
    if r.get("flag") in ("H", "L"):
        lo, hi = r.get("ref_low"), r.get("ref_high")
        far = (hi is not None and v > hi * 1.3) or (lo is not None and v < lo * 0.8)
        return (2, "well outside it") if far else (1, "just outside it")
    return 0, "within it"


def range_text(r: dict) -> str:
    lo, hi = r.get("ref_low"), r.get("ref_high")
    if lo is not None and hi is not None:
        return f"{lo:g}-{hi:g}"
    if hi is not None:
        return f"<{hi:g}"
    if lo is not None:
        return f">{lo:g}"
    return "not printed"


def problem_for(test: str, flag: str | None) -> tuple[str, str]:
    direction = "L" if flag == "L" else "H"
    return PROBLEMS.get((test, direction), (f"{test} {'low' if direction == 'L' else 'high'}",
                                            "Review the result and decide whether to repeat it or act."))


def parse_lab_text(text: str) -> tuple[dict, list[dict]]:
    """Header identity and results from either private-lab layout (what OCR of a fax gives you)."""
    hdr = {}
    m = re.search(r"Name\s*:\s*([A-Z][A-Z' -]+),\s*([A-Za-z ]+?)\s{2,}", text)
    if m:
        hdr["name"] = f"{m.group(1).strip()} {m.group(2).strip()}"
    m = re.search(r"Patient:\s*([A-Za-z ]+?)\s+([A-Z]{2,})\s*\(", text)
    if m:
        hdr["name"] = f"{m.group(2)} {m.group(1).strip()}"
    m = re.search(r"DOB\s*:\s*(\d{2})/(\d{2})/(\d{4})", text) or re.search(r"DOB\s+(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        hdr["dob"] = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"D\.O\.B\.\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        hdr["dob"] = m.group(1)
    m = re.search(r"\b([A-Z]\d{3})XXX\(X\)", text)
    if m:
        hdr["hkid"] = m.group(1)
    m = re.search(r"(?:Lab No|Ref No\.)\s*:?\s*([A-Z]{2}\d{2}-\d{6})", text)
    if m:
        hdr["lab_no"] = m.group(1)
    m = re.search(r"(?:Doctor|Requested by)\s*:\s*(Dr [A-Z]+ [A-Za-z ]+?)(?:\s{2,}|\s*\(|$)", text, re.M)
    if m:
        hdr["doctor"] = m.group(1).strip()
    res = []
    for line in text.splitlines():
        a = re.match(r"^(\S.*?)\s{2,}(-?\d+(?:\.\d+)?)\s+(H|L)?\s*\S+\s+(.*)$", line)
        b = re.match(r"^\s+(.+?):\s*(-?\d+(?:\.\d+)?)\s*\S*\s*(\^|v)?\s*\[(.*)\]", line)
        if a and a.group(1) not in ("TEST",):
            res.append(_mk(a.group(1).strip(), float(a.group(2)), a.group(3) or "N", a.group(4)))
        elif b:
            res.append(_mk(b.group(1).strip(), float(b.group(2)), {"^": "H", "v": "L"}.get(b.group(3), "N"), b.group(4)))
    return hdr, res


def _mk(test, val, flag, rr):
    lo = hi = None
    m = re.match(r"\s*([\d.]+)\s*-\s*([\d.]+)", rr or "")
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
    elif (m := re.match(r"\s*<\s*([\d.]+)", rr or "")):
        hi = float(m.group(1))
    elif (m := re.match(r"\s*>\s*([\d.]+)", rr or "")):
        lo = float(m.group(1))
    return {"test": test, "value": val, "flag": flag, "ref_low": lo, "ref_high": hi}


# ------------------------------------------------------------------------------- trace helpers
def _n(s) -> str:
    return " ".join(str(s).split())


def line_with(text: str, pattern: str, flags=0) -> str | None:
    """The first line matching a regex, whitespace collapsed: a verbatim quote once the platform normalises spaces."""
    for ln in (text or "").splitlines():
        if re.search(pattern, ln, flags):
            return _n(ln)[:240] or None
    return None


def result_line(text: str, test: str) -> str | None:
    return line_with(text, r"^\s*" + re.escape(test) + r"(\s{2,}|:)")


def cite(rid: str, quote: str | None = None) -> dict:
    return {"record": rid, "quote": quote} if quote else {"record": rid}


def when(t: datetime) -> str:
    return t.strftime("%a %d %b %H:%M")


def day(t: datetime) -> str:
    return t.strftime("%d %b %Y")


def med_name(quote: str) -> str:
    m = re.match(r"Dispensed:\s*(.+?)\s+x\d+ days", quote or "")
    return m.group(1) if m else (quote or "")[:40]


class Why:
    """Collects reasoning steps for one item."""

    def __init__(self):
        self.steps: list[dict] = []

    def add(self, step: str, text: str, *cites, diff=None, search=None) -> "Why":
        s = {"step": step, "text": text, "cites": [c for c in cites if c]}
        if diff:
            s["diff"] = diff
        if search:
            s["search"] = search
        self.steps.append(s)
        return self


class RulesHarness:
    """version "1.0": the week-1 baseline (the floor every other harness is compared with).
    version "1.1": second attempts are routed through the roster, and unactioned items escalate at day 7 and 14."""

    def __init__(self, ehealth_sweep_days: int = 14, grace_open_days: dict | None = None, attest: bool = False,
                 version: str = "1.0"):
        if version not in ("1.0", "1.1"):
            raise ValueError(f"unknown rules version {version}")
        self.version = version
        self.name = "rules" if version == "1.0" else "rules-v1.1"
        self.sweep_days = ehealth_sweep_days
        self.grace = grace_open_days or {1: 3, 2: 1, 3: 0, 4: 0}
        self.attest = attest
        self.escalation = Escalation() if version != "1.0" else None

    # ------------------------------------------------------------------------------- set-up
    def reset(self, api):
        self._down = getattr(api, "ServiceUnavailable", ConnectionError)   # the port's "source is down" error
        if self.escalation is not None:
            self.escalation.reset()
        self.staff = {s["id"]: s for s in api.staff()}
        self.by_name = {s["name"].upper(): s["id"] for s in api.staff()}
        info = api.clinic()
        self.lab_names = {l["id"]: l["name"] for l in info["labs"]}
        self.portals = [f"portal:{l['id']}" for l in info["labs"] if l["portal"]] + ["portal:crcsp"]
        self.cur = {"cms": 0, **{p: 0 for p in self.portals}}
        self.pts = {p["id"]: p for p in api.patients()}
        self.raised: dict = {}
        self.pending: list[dict] = []
        self.orders: dict = {}
        self.result_lab_nos: set = set()
        self.tests_by_pid = defaultdict(list)          # pid -> [(t, set(tests))]
        self.consults = defaultdict(list)
        self.actions = defaultdict(list)               # pid -> [(t, text, record id)]
        self.closure_ev = defaultdict(list)            # pid -> [(t, kind)]
        self.monitors, self.reviews, self.referrals = [], [], []
        self.arrivals = defaultdict(list)
        self.alarmed: dict = {}
        self.nudged: set = set()
        self.ehealth_next = {pid: datetime(2027, 1, 4) + timedelta(days=i % self.sweep_days)
                             for i, pid in enumerate(sorted(p for p, v in self.pts.items() if v["ehealth_consent"]))}
        self.ehealth_seen: set = set()
        self._roster: dict = {}
        self.lab_hist = defaultdict(lambda: defaultdict(list))   # pid -> test -> [(t, value, record id, quote, lab no)]
        self.meds = defaultdict(list)                            # pid -> [(t, quote, record id)]

    # ------------------------------------------------------------------------------- helpers
    def roster(self, api, d):
        r = self._roster.get(d)
        if r is None:
            r = self._roster[d] = api.roster(d)
        return r

    def nm(self, doc: str | None) -> str:
        return self.staff[doc]["name"] if doc in self.staff else str(doc)

    def pname(self, pid: str) -> str:
        return self.pts[pid]["name_en"] if pid in self.pts else pid

    def chan_label(self, ch: str) -> str:
        if ch.startswith("portal:"):
            lid = ch.split(":", 1)[1]
            return "the colorectal cancer screening programme system" if lid == "crcsp" else f"the {self.lab_names.get(lid, lid)} web portal"
        return {"cms": "the clinic system", "fax": "fax (scanned into the clinic system)",
                "courier": "courier (scanned into the clinic system)", "phone": "a phone call taken by the clinic assistant",
                "patient_carried": "a letter the patient brought in", "whatsapp": "WhatsApp (shown in the clinic system)",
                "ehealth": "eHealth"}.get(ch, ch)

    def lab_of(self, text: str) -> str:
        head = (text or "")[:200].upper()
        return next((n for n in self.lab_names.values() if n.upper() in head), "Laboratory")

    def route(self, api, hint: str | None, pid: str, urgency: int) -> tuple[str, str]:
        """Who should own it: the ordering doctor, else the registered doctor, if rostered soon enough."""
        reg = self.pts.get(pid, {}).get("registered_doctor")
        horizon = {4: 0, 3: 1, 2: 3, 1: 7}[urgency]
        notes, tried = [], set()
        today = self.roster(api, api.now.date())
        if not today["open"]:
            notes.append(f"The clinic is closed today ({today['closed_reason']}).")
        for role, cand in (("ordering doctor", hint), ("registered doctor", reg)):
            if not cand or cand not in self.staff or cand in tried:
                continue
            tried.add(cand)
            for i in range(horizon + 1):
                d = api.now.date() + timedelta(days=i)
                if cand in self.roster(api, d)["doctors"]:
                    return cand, " ".join(notes + [f"{self.nm(cand)} ({role}) is rostered {'today' if i == 0 else 'on ' + d.strftime('%a %d %b')}."])
            notes.append(f"{self.nm(cand)} ({role}) is not rostered " + ("today." if horizon == 0 else f"in the next {horizon + 1} days."))
        for i in range(0, 10):
            d = api.now.date() + timedelta(days=i)
            docs = self.roster(api, d)["doctors"]
            if docs:
                pick = hint if hint in docs else docs[0]
                return pick, " ".join(notes + [f"{self.nm(pick)} is the first doctor rostered ({'today' if i == 0 else d.strftime('%a %d %b')})."])
        pick = reg or next(iter(self.staff))
        return pick, " ".join(notes + [f"No doctor is rostered in the next 10 days; assigned to {self.nm(pick)}."])

    def owner_for(self, api, hint: str | None, pid: str, urgency: int) -> str:
        return self.route(api, hint, pid, urgency)[0]

    def doc_id(self, text_or_id: str | None) -> str | None:
        if not text_or_id:
            return None
        if text_or_id in self.staff:
            return text_or_id
        return self.by_name.get(text_or_id.upper())

    def identify(self, api, rec: dict, hdr: dict) -> tuple[str | None, str]:
        """Use the chart a paper report is filed to only if its header agrees; otherwise search."""
        if rec.get("patient_id") and hdr.get("dob"):
            p = self.pts.get(rec["patient_id"])
            if p and p["dob"] == hdr["dob"] and (not hdr.get("name") or set(hdr["name"].upper().split()) <= set(p["name_en"].upper().split())):
                return rec["patient_id"], f"Filed to {p['name_en']}'s chart; the header's name and date of birth agree with the chart."
        if hdr.get("dob") and hdr.get("name"):
            m = api.find_patients(name=hdr["name"], dob=hdr["dob"], hkid=hdr.get("hkid"))
            if len(m) == 1:
                where = "Filed to a chart whose details do not match the header" if rec.get("patient_id") else "Not filed to any chart"
                return m[0]["id"], (f"{where}. Exactly one patient in the clinic index matches the header's name, date of birth"
                                    f"{' and HKID' if hdr.get('hkid') else ''}: {m[0]['name_en']}.")
        return None, ""

    def id_cites(self, rid: str, text: str) -> list[dict]:
        a = line_with(text, r"^(Name|Patient|Participant|Re)\s*:")
        b = line_with(text, r"DOB|D\.O\.B\.|XXX\(X\)")
        out = [cite(rid, a)] if a else []
        if b and b != a:
            out.append(cite(rid, b))
        return out or [cite(rid)]

    def chart_search(self, api, pid: str, kinds, since: datetime, contains_any=()) -> tuple[int, dict]:
        """Search the chart and state the search so the platform can re-run it."""
        since = since.replace(second=0, microsecond=0)
        n = sum(1 for r in api.chart(pid) if r["kind"] in kinds and datetime.fromisoformat(r["available_at"]) >= since
                and (not contains_any or any(c in r["text"] for c in contains_any)))
        return n, {"kinds": list(kinds), "since": since.isoformat(), "contains_any": list(contains_any), "found": n}

    def remember_results(self, pid, lab_no, results, rid, text, t):
        for x in results:
            hist = self.lab_hist[pid][x["test"]]
            if lab_no and any(h[4] == lab_no for h in hist):
                continue
            hist.append((t, x["value"], rid, result_line(text, x["test"]), lab_no))

    def previous(self, pid, test, before: datetime, lab_no):
        prior = [h for h in self.lab_hist.get(pid, {}).get(test, ()) if h[0] < before and (not lab_no or h[4] != lab_no)]
        return prior[-1] if prior else None

    def relevant_meds(self, pid, tests, before: datetime) -> list[tuple]:
        out, seen = [], set()
        for test in tests:
            pat = RELEVANT_MEDS.get(test)
            if not pat:
                continue
            for t, q, rid in reversed(self.meds.get(pid, ())):
                if t <= before and q and re.search(pat, q, re.I):
                    if rid not in seen:
                        out.append((t, q, rid))
                        seen.add(rid)
                    break
        return out[:3]

    def context_step(self, w: Why, pid: str, tests_vals: list[tuple], t: datetime, lab_no):
        diff, cites_, lines = [], [], []
        for test, val in tests_vals[:4]:
            prev = self.previous(pid, test, t, lab_no)
            if prev:
                pt, pv, prid, pq, _ = prev
                diff.append([test, f"{pv:g} ({pt:%d %b %Y})", f"{val:g} ({t:%d %b %Y})"])
                cites_.append(cite(prid, pq))
            else:
                lines.append(test)
        meds = self.relevant_meds(pid, [tv[0] for tv in tests_vals], t)
        for mt, mq, mrid in meds:
            cites_.append(cite(mrid, mq))
        parts = []
        if diff:
            parts.append("Last comparable result" + ("s: " if len(diff) > 1 else ": ") + "; ".join(f"{d[0]} {d[1]}" for d in diff))
        if lines:
            names = lines[0] if len(lines) == 1 else ", ".join(lines[:-1]) + " or " + lines[-1]
            parts.append(f"No earlier {names} result on file")
        parts.append(("Relevant medicines dispensed here: " + "; ".join(f"{med_name(q)} ({mt:%d %b %Y})" for mt, q, _ in meds))
                     if meds else "No relevant medicines dispensed at this clinic")
        w.add("context", ". ".join(parts) + ".", *cites_, diff=diff or None)
        return meds

    def raise_item(self, api, key, pid, family, reason, evidence, urgency, hint=None, days=None, why: Why | None = None):
        if key in self.raised:
            return
        owner, route_txt = self.route(api, hint, pid, urgency)
        due = api.now + timedelta(days=days if days is not None else WINDOW[urgency])
        steps = None
        if why is not None:
            why.add("routing", route_txt)
            why.add("request", f"Opened for {self.nm(owner)} at urgency {urgency} ({URGENCY_LABEL[urgency]}); due {when(due)}.")
            steps = why.steps
        self.raised[key] = api.open_item(pid, owner, family, reason, list(evidence), urgency, due, reasoning=steps)

    def handled(self, pid: str, since: datetime, needle: str | None = None) -> bool:
        return any(t >= since and (needle is None or needle in txt) for t, txt, _ in self.actions.get(pid, ()))

    # ---------------------------------------------------------------------------------- tick
    def tick(self, api):
        now = api.now
        recs, self.cur["cms"] = api.cms_since(self.cur["cms"])
        for r in recs:
            self.arrivals[r["channel"]].append(now)
            self.on_cms(api, r)
        for src in self.portals:
            try:
                prs, self.cur[src] = api.portal_since(src, self.cur[src])
                self.alarmed.pop(src, None)
            except self._down:
                if not self.alarmed.get(src):
                    api.alarm(src, "portal unavailable")
                    self.alarmed[src] = True
                continue
            except PermissionError:
                continue
            for r in prs:
                self.arrivals[src].append(now)
                self.on_portal(api, src, r)
        self.sweep_ehealth(api)
        self.check_pending(api)
        self.check_watches(api)
        if self.escalation is not None and now.hour == 8:
            self.escalation.tick(self, api)
        self.check_silence(api)

    def on_cms(self, api, r: dict):
        k, pid, t = r["kind"], r["patient_id"], datetime.fromisoformat(r["available_at"])
        text = r["text"]
        if k == "consult_note" and pid:
            self.consults[pid].append(t)
            m = RV.search(text)
            if m and not re.search(r"prn|if not|if fever", text, re.I):
                n, unit = int(m.group(1)), int(m.group(2))
                days = n * {7: 1, 52: 7, 12: 30}[unit]
                self.reviews.append({"pid": pid, "t": t, "days": days, "rid": r["id"], "owner": r["author"], "quote": m.group(0)})
        elif k == "action_note" and pid:
            self.actions[pid].append((t, text, r["id"]))
        elif k == "lab_request":
            ln = r["data"].get("lab_no")
            if ln and pid:
                self.orders[ln] = {"pid": pid, "t": t, "rid": r["id"], "owner": r["author"], "text": text}
        elif k == "lab_report":
            hdr, res = parse_lab_text(text)
            if r["data"].get("results"):
                res = r["data"]["results"]
            if hdr.get("lab_no"):
                self.result_lab_nos.add(hdr["lab_no"])
            pid2, how = self.identify(api, r, hdr)
            if pid2:
                self.tests_by_pid[pid2].append((t, {x["test"] for x in res} | ({"INR"} if "INR" in text else set())))
                self.closure_ev[pid2].append((t, "lab"))
                self.handle_lab(api, pid2, hdr.get("lab_no"), res, r["id"], self.doc_id(hdr.get("doctor")) or r["author"], t,
                                text=text, channel=r["channel"], how=how)
        elif k == "phone_memo" and pid:
            if "CRITICAL" in text:
                m = re.search(r"CRITICAL (.+?) (-?[\d.]+)", text)
                ln = r["data"].get("lab_no")
                self.raise_item(api, ("lab", ln) if ln else ("crit", pid, t.date()), pid, "abnormal_result",
                                f"Lab phoned a critical result: {m.group(1) if m else ''} {m.group(2) if m else ''}".strip(),
                                [r["id"]], 4, why=self.memo_why(api, r, pid, m, ln, t))
            elif re.search(r"haemolysed|recollect|rejected", text, re.I):
                self.raise_item(api, ("lost", pid, t.date()), pid, "test_never_resulted", "Specimen rejected; recollect",
                                [r["id"]], 2, why=self.rejected_why(api, r, pid, t))
        elif k == "dispensing" and pid:
            q = line_with(text, r"^Dispensed")
            self.meds[pid].append((t, q, r["id"]))
            if r["data"].get("new_start"):
                items = str(r["data"].get("items", "")).lower()
                for name, pat, panel, days in MONITOR:
                    if re.search(pat, items):
                        self.monitors.append({"pid": pid, "t": t, "panel": panel, "days": days, "rid": r["id"],
                                              "owner": r["author"], "name": name, "quote": q})
        elif k == "referral_letter" and pid:
            self.referrals.append({"pid": pid, "t": t, "rid": r["id"], "owner": r["author"], "text": text,
                                   "urgent": r["data"].get("urgency") == "urgent" or "URGENT" in text})
        elif k == "imaging_report":
            hdr, _ = parse_lab_text(text)
            pid2, how = self.identify(api, r, hdr)
            if not pid2 and pid:
                pid2, how = pid, f"Filed to {self.pname(pid)}'s chart."
            if not pid2:
                return
            self.closure_ev[pid2].append((t, "imaging"))
            if re.search(r"BI-RADS\s*(0|4|5)", text):
                self.raise_item(api, ("img", r["id"]), pid2, "positive_screen", "Abnormal mammogram needs further imaging", [r["id"]], 3, r["author"], 30,
                                why=self.imaging_why(api, r, pid2, how, t, screen=True))
            elif re.search(r"recommend|repeat imaging|follow-up CT", text, re.I) and "Normal study" not in text:
                self.raise_item(api, ("img", r["id"]), pid2, "followup_recommended", "Imaging report recommends follow-up", [r["id"]], 2, r["author"], 30,
                                why=self.imaging_why(api, r, pid2, how, t, screen=False))
        elif k in ("discharge_summary", "aed_summary") and pid:
            self.on_discharge(api, pid, r, t)
        elif k in ("specialist_letter",) and pid:
            self.closure_ev[pid].append((t, "letter"))
        elif k == "message" and pid and re.search(r"報告|report|高|high", text + str(r["data"]), re.I):
            self.raise_item(api, ("msg", r["id"]), pid, "abnormal_result", "Patient sent an outside report with abnormal results",
                            [r["id"]], 2, why=self.message_why(api, r, pid, t))

    def on_portal(self, api, src, r):
        t = datetime.fromisoformat(r["available_at"])
        if r["kind"] == "screening_result":
            pid = r["patient_id"]
            if r["data"].get("result") == "POSITIVE":
                self.raise_item(api, ("fit", r["id"]), pid, "positive_screen", "FIT positive: explain and refer for colonoscopy",
                                [r["id"]], 2, r.get("author"), 30, why=self.fit_why(api, src, r, pid, t))
            return
        d = r["data"]
        m = api.find_patients(name=d.get("patient_name"), dob=d.get("dob"))
        pid = m[0]["id"] if len(m) == 1 else r.get("patient_id")
        if not pid:
            return
        how = (f"Posted on the lab's portal; its name and date of birth match exactly one patient in the clinic index: {m[0]['name_en']}."
               if len(m) == 1 else "Posted on the lab's portal, linked to this patient by the lab order.")
        if d.get("lab_no"):
            self.result_lab_nos.add(d["lab_no"])
        self.tests_by_pid[pid].append((t, {x["test"] for x in d.get("results", [])}))
        self.handle_lab(api, pid, d.get("lab_no"), d.get("results", []), r["id"], r.get("author"), t,
                        text=r["text"], channel=src, how=how)

    def handle_lab(self, api, pid, lab_no, results, rid, owner_hint, t, text="", channel="", how=""):
        self.remember_results(pid, lab_no, results, rid, text, t)
        u = lab_urgency(results)
        if u == 0:
            return
        key = ("lab", lab_no or rid)
        rec = {"rid": rid, "text": text, "channel": channel, "how": how, "t": t}
        for p in self.pending:                     # a second copy of the same report: add evidence
            if p["key"] == key:
                p["evidence"].append(rid)
                p["urgency"] = max(p["urgency"], u)
                p["recs"].append(rec)
                return
        if key in self.raised:
            return
        if self.handled(pid, t - timedelta(days=3), lab_no) if lab_no else False:
            return
        grace = self.grace[u]
        raise_at = t
        if grace:
            d, left = t, grace
            while left > 0:
                d += timedelta(days=1)
                if self.roster(api, d.date())["open"]:
                    left -= 1
            raise_at = d
        self.pending.append({"key": key, "pid": pid, "lab_no": lab_no, "evidence": [rid], "urgency": u,
                             "owner": owner_hint, "t": t, "raise_at": raise_at, "recs": [rec], "results": results, "grace": grace,
                             "reason": "Abnormal: " + ", ".join(f"{x['test']} {x['value']:g}" for x in results if x.get("flag") in ("H", "L"))[:200]})

    def on_discharge(self, api, pid, r, t):
        if re.search(r"family doctor", r["text"], re.I) and re.search(r"follow up", r["text"], re.I):
            self.raise_item(api, ("dc", pid, r["text"][:120]), pid, "followup_recommended",
                            "Hospital asks family doctor to follow up", [r["id"]], 2, None, 14, why=self.discharge_why(api, r, pid, t))

    # ------------------------------------------------------------------------- reasoning builders
    def lab_why(self, api, p) -> Why:
        w = Why()
        pid, ln, t = p["pid"], p["lab_no"], p["t"]
        first = p["recs"][0]
        lab = self.lab_of(first["text"])
        for i, rec in enumerate(p["recs"]):
            q = line_with(rec["text"], r"Lab No|Ref No\.")
            how_in = ("was posted on its web portal" if rec["channel"].startswith("portal:")
                      else f"arrived by {self.chan_label(rec['channel'])}")
            if i == 0:
                w.add("source", f"{lab} report{' ' + ln if ln else ''} {how_in} on {when(rec['t'])}.", cite(rec["rid"], q))
            else:
                w.add("source", f"Another copy {how_in} on {when(rec['t'])}; added to this item rather than opening a second one.",
                      cite(rec["rid"], q))
        w.add("identity", first["how"] or "Linked to this patient.", *self.id_cites(first["rid"], first["text"]))
        res = p["results"]
        ab = [x for x in res if x.get("flag") in ("H", "L")] or [x for x in res if band_of(x)[0]]
        ab = sorted(ab, key=lambda x: (-band_of(x)[0], priority(x["test"])))
        if ab:
            parts, cites_ = [], []
            for x in ab[:5]:
                unit = (" " + str(x.get("unit", "")).strip()) if x.get("unit") else ""
                side = "above" if x.get("flag") == "H" else "below" if x.get("flag") == "L" else "outside"
                parts.append(f"{x['test']} {x['value']:g}{unit}, {side} the report's range of {range_text(x)} ({band_of(x)[1]})")
                cites_.append(cite(first["rid"], result_line(first["text"], x["test"])))
            more = f" ({len(ab) - 5} more abnormal)" if len(ab) > 5 else ""
            w.add("finding", "; ".join(parts) + more + ".", *cites_)
        else:
            w.add("finding", "The lab flagged results on this report.", cite(first["rid"]))
        meds = self.context_step(w, pid, [(x["test"], x["value"]) for x in ab], t, ln)
        if ab:
            prob, plan = problem_for(ab[0]["test"], ab[0].get("flag"))
            on = [med_name(q) for _, q, _ in meds if re.search(RELEVANT_MEDS.get(ab[0]["test"], r"^$"), q or "", re.I)]
            also = sorted({problem_for(x["test"], x.get("flag"))[0] for x in ab[1:4]} - {prob})
            w.add("assessment", f"Working problem: {prob}{' on ' + ', '.join(on) if on else ''}"
                                f"{'; also ' + ', '.join(also) if also else ''}. Plan: {plan}")
        else:
            w.add("assessment", "Working problem: abnormal result needing review. Plan: review and decide whether to repeat or act.")
        n, q = self.chart_search(api, pid, ["action_note"], t, [ln] if ln else [])
        wait = (f" Waited {p['grace']} open day{'s' if p['grace'] != 1 else ''} for the clinic to act on its own first."
                if p.get("grace") else "")
        if ln:
            w.add("loop_status", f"No action note in the chart mentions {ln} since the result arrived on {day(t)}.{wait}", search=q)
        else:
            w.add("loop_status", f"{n} action note{'s' if n != 1 else ''} in the chart since the result; none can be tied to this report (no lab number).{wait}", search=q)
        return w

    def memo_why(self, api, r, pid, m, ln, t) -> Why:
        w, text = Why(), r["text"]
        cq = re.search(r"CRITICAL .+? (?=for )", text)
        cq = _n(cq.group(0)) if cq else None
        w.add("source", f"The lab phoned a critical result at {t:%H:%M} on {t:%d %b}; the clinic assistant took a memo"
                        f"{' (lab number ' + ln + ' recorded with it)' if ln else ''}.", cite(r["id"], cq))
        iq = re.search(r"for [A-Z][A-Z'-]+ [^()]+\([A-Z]\d{3}XXX\(X\)\)", text)
        w.add("identity", f"The memo gives the patient's name and HKID; it is filed to {self.pname(pid)}'s chart.",
              cite(r["id"], iq.group(0) if iq else None))
        if m:
            test, val = m.group(1), float(m.group(2))
            lo, hi = CRIT.get(test, (None, None))
            flag = "L" if lo is not None and val < lo else "H"
            w.add("finding", f"{test} {val:g}: " + (f"critical by this harness's rules ({_lim(CRIT[test])})." if test in CRIT else "reported as critical by the lab."),
                  cite(r["id"], cq))
            meds = self.context_step(w, pid, [(test, val)], t, ln)
            prob, plan = problem_for(test, flag)
            on = [med_name(q) for _, q, _ in meds]
            w.add("assessment", f"Working problem: {prob}{' on ' + ', '.join(on) if on else ''}, phoned through as critical. Plan: same-day contact. {plan}")
        else:
            w.add("finding", "The lab phoned a critical result.", cite(r["id"]))
            w.add("assessment", "Working problem: critical result. Plan: same-day review and contact.")
        n, q = self.chart_search(api, pid, ["action_note"], t, [ln] if ln else [])
        w.add("loop_status", (f"No action note in the chart mentions {ln} since the call." if ln else
                              f"{n} action note{'s' if n != 1 else ''} in the chart since the call.")
              + " A phoned critical result needs a named doctor today, so it is raised at once.", search=q)
        return w

    def rejected_why(self, api, r, pid, t) -> Why:
        w, text = Why(), r["text"]
        sq = re.search(r"[^.:]*(?:haemolysed|recollect|rejected)[^.]*", text, re.I)
        w.add("source", f"Phone memo on {when(t)}: the lab reports a problem with the specimen.", cite(r["id"], _n(sq.group(0)) if sq else None))
        w.add("identity", f"The memo names the patient; it is filed to {self.pname(pid)}'s chart.", cite(r["id"]))
        w.add("finding", "The specimen cannot be processed, so this test will never produce a result.")
        w.add("assessment", "Working problem: test not resulted. Plan: call the patient back for a new sample; until then the question the test was meant to answer stays open.")
        n, q = self.chart_search(api, pid, ["action_note", "lab_request"], t)
        w.add("loop_status", f"{'No' if n == 0 else n} action note{'s' if n != 1 else ''} or new lab request{'s' if n != 1 else ''} in the chart since the call.", search=q)
        return w

    def imaging_why(self, api, r, pid, how, t, screen: bool) -> Why:
        w, text = Why(), r["text"]
        study = line_with(text, r"REPORT$")
        w.add("source", f"Imaging report arrived via {self.chan_label(r['channel'])} on {when(t)}.", cite(r["id"], study))
        w.add("identity", how, *self.id_cites(r["id"], text))
        imp = line_with(text, r"^IMPRESSION") or line_with(text, r"recommend|repeat imaging|follow-up CT", re.I)
        if screen:
            w.add("finding", "The screening mammogram is not normal (BI-RADS 0 means incomplete; 4 or 5 means suspicious).", cite(r["id"], imp))
            w.add("assessment", "Working problem: abnormal screening mammogram. Plan: diagnostic imaging or a breast clinic referral, and tell the patient.")
        else:
            w.add("finding", "The radiologist recommends further imaging or follow-up.", cite(r["id"], imp))
            w.add("assessment", "Working problem: imaging follow-up recommended by the radiologist. Plan: arrange it and tell the patient.")
        n, q = self.chart_search(api, pid, ["action_note", "referral_letter"], t)
        w.add("loop_status", f"{'No' if n == 0 else n} action note{'s' if n != 1 else ''} or referral letter{'s' if n != 1 else ''} in the chart since the report.", search=q)
        return w

    def fit_why(self, api, src, r, pid, t) -> Why:
        w, text = Why(), r["text"]
        fq = line_with(text, r"FIT result")
        w.add("source", f"Result posted on {self.chan_label(src)} on {when(t)}.", cite(r["id"], fq))
        w.add("identity", f"Linked to {self.pname(pid)} by the programme; the participant's name and date of birth are printed on the result.", *self.id_cites(r["id"], text))
        w.add("finding", "Faecal immunochemical test (FIT) positive.", cite(r["id"], fq))
        w.add("assessment", "Working problem: positive FIT in the colorectal cancer screening programme. Plan: explain the result and refer to an enrolled colonoscopy specialist, as the result itself instructs.",
              cite(r["id"], line_with(text, r"^Action")))
        n, q = self.chart_search(api, pid, ["action_note", "referral_letter"], t)
        w.add("loop_status", f"{'No' if n == 0 else n} action note{'s' if n != 1 else ''} or referral letter{'s' if n != 1 else ''} in the chart since the result.", search=q)
        return w

    def discharge_why(self, api, r, pid, t) -> Why:
        w, text = Why(), r["text"]
        kind = "discharge summary" if r["kind"] == "discharge_summary" else "A&E attendance summary"
        w.add("source", f"Hospital Authority {kind} arrived via {self.chan_label(r['channel'])} on {when(t)}.", cite(r["id"], line_with(text, r"^HOSPITAL AUTHORITY")))
        if r["channel"] == "ehealth":
            idt = f"Read from eHealth for {self.pname(pid)}; the summary prints the patient's name."
        else:
            idt = f"Filed to {self.pname(pid)}'s chart; the summary prints the patient's name."
        w.add("identity", idt, *self.id_cites(r["id"], text))
        fu = line_with(text, r"follow up", re.I)
        w.add("finding", "The hospital asks the family doctor to follow up.", cite(r["id"], fu))
        dx = line_with(text, r"diagnosis", re.I)
        dxt = dx.split(":", 1)[1].strip() if dx and ":" in dx else "the hospital episode"
        w.add("assessment", f"Working problem: follow-up after {dxt}. Plan: book the review in the time the hospital asks and do the tests it lists.", cite(r["id"], dx))
        n, q = self.chart_search(api, pid, ["consult_note"], t)
        event = "discharge" if r["kind"] == "discharge_summary" else "attendance"
        w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} in the chart since the {event}.", search=q)
        return w

    def message_why(self, api, r, pid, t) -> Why:
        w, text = Why(), r["text"]
        mq = re.search(r"報告[^，。？?]*", text) or re.search(r"[^.]*(report|high)[^.]*", text, re.I)
        q = _n(mq.group(0)) if mq else None
        w.add("source", f"WhatsApp message from the patient on {when(t)}, shown in the clinic system.", cite(r["id"], q))
        w.add("identity", f"Received in {self.pname(pid)}'s thread; the patient names themselves in the message.", cite(r["id"]))
        w.add("finding", "The patient reports an outside health check with results flagged high and asks whether to come back.")
        w.add("assessment", "Working problem: abnormal results from outside this clinic, not in its records. Plan: ask for the report, review it, and repeat tests if needed.")
        n, qq = self.chart_search(api, pid, ["consult_note"], t)
        w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} in the chart since the message.", search=qq)
        return w

    # ------------------------------------------------------------------------------- periodic
    def sweep_ehealth(self, api):
        due = [pid for pid, t in self.ehealth_next.items() if t <= api.now][:40]
        for pid in due:
            try:
                recs = api.ehealth(pid)
                self.alarmed.pop("ehealth", None)
            except self._down:
                if not self.alarmed.get("ehealth"):
                    api.alarm("ehealth", "eHealth connection failing")
                    self.alarmed["ehealth"] = True
                return
            except PermissionError as e:
                if "programmatic" in str(e):
                    self.ehealth_next = {}
                    return
                self.ehealth_next.pop(pid, None)
                continue
            self.ehealth_next[pid] = api.now + timedelta(days=self.sweep_days)
            for r in recs:
                if r["id"] in self.ehealth_seen:
                    continue
                self.ehealth_seen.add(r["id"])
                t = datetime.fromisoformat(r["available_at"])
                if r["kind"] == "appointment":
                    self.closure_ev[pid].append((t, "appointment"))
                elif r["kind"] in ("discharge_summary", "aed_summary") and t > api.now - timedelta(days=21):
                    if not any(c > t for c in self.consults.get(pid, ())):
                        self.on_discharge(api, pid, r, t)

    def check_pending(self, api):
        keep = []
        for p in self.pending:
            if p["lab_no"] and self.handled(p["pid"], p["t"], p["lab_no"]):
                continue
            if api.now >= p["raise_at"]:
                if p["key"] not in self.raised:
                    self.raise_item(api, p["key"], p["pid"], "abnormal_result", p["reason"], p["evidence"], p["urgency"], p["owner"],
                                    why=self.lab_why(api, p))
            else:
                keep.append(p)
        self.pending = keep

    def check_watches(self, api):
        now = api.now
        for ln, o in list(self.orders.items()):
            if ln in self.result_lab_nos:
                del self.orders[ln]
            elif now - o["t"] > timedelta(days=6):
                if ("order", ln) not in self.raised:
                    w = Why()
                    w.add("source", f"Lab request recorded on {when(o['t'])} by {self.nm(o['owner'])}; lab number {ln} is in the request's details.",
                          cite(o["rid"], line_with(o["text"], r"Lab request")))
                    w.add("finding", f"No result for {ln} after 6 days in anything this harness can see (lab portals and the clinic system).")
                    n, q = self.chart_search(api, o["pid"], ["lab_report"], o["t"], [ln])
                    w.add("loop_status", f"{'No' if n == 0 else n} lab report{'s' if n != 1 else ''} in the chart mention{'s' if n == 1 else ''} {ln} since the request.", search=q)
                    w.add("assessment", "Working problem: test not resulted. The specimen may be lost or rejected, or the result misfiled; the patient may take no news as good news. Plan: call the lab, then the patient.")
                    self.raise_item(api, ("order", ln), o["pid"], "test_never_resulted", f"No result for lab no {ln} after 6 days",
                                    [o["rid"]], 2, o["owner"], 10, why=w)
                del self.orders[ln]
        for m in list(self.monitors):
            done = any(t >= m["t"] and (tests & PANEL_TESTS[m["panel"]]) for t, tests in self.tests_by_pid.get(m["pid"], ()))
            if done:
                self.monitors.remove(m)
            elif now >= m["t"] + timedelta(days=max(3, m["days"] - 2)):
                if ("mon", m["rid"]) not in self.raised:
                    w = Why()
                    w.add("source", f"New start dispensed on {day(m['t'])}.", cite(m["rid"], m.get("quote")))
                    w.add("finding", f"{MONITOR_WHY[m['name']]}; {(now - m['t']).days} days have passed with no {m['panel']} result for this patient.")
                    tests = sorted(PANEL_TESTS[m["panel"]])
                    n, q = self.chart_search(api, m["pid"], ["lab_report"], m["t"], tests)
                    w.add("loop_status", ("No lab report in the chart since the start includes " + " / ".join(tests) + "." if n == 0 else
                                          f"{n} lab report{'s' if n != 1 else ''} in the chart since the start include these tests, but none could be confirmed as this patient's."),
                          search=q)
                    w.add("assessment", f"Working problem: safety monitoring due after starting {med_name(m.get('quote') or '')}. Plan: order {m['panel']} and review the result.")
                    self.raise_item(api, ("mon", m["rid"]), m["pid"], "drug_monitoring", f"{m['name']} started: {m['panel']} due",
                                    [m["rid"]], 2, m["owner"], 10, why=w)
                self.monitors.remove(m)
        for v in list(self.reviews):
            if self.version == "1.0":
                lo = v["t"] + timedelta(days=max(1, v["days"] - 14))
            else:                                  # v1.1: an unrelated visit soon after does not count as the review
                lo = v["t"] + timedelta(days=max(1, v["days"] - 14, v["days"] * 0.5))
            if any(c >= lo for c in self.consults.get(v["pid"], ()) if c > v["t"]):
                self.reviews.remove(v)
            elif now >= v["t"] + timedelta(days=v["days"] + 2):
                if ("rv", v["rid"]) not in self.raised:
                    w = Why()
                    w.add("source", f"The consultation on {day(v['t'])} planned a review ({v['quote']}).", cite(v["rid"], v["quote"]))
                    due_rv = v["t"] + timedelta(days=v["days"])
                    w.add("finding", f"The review was due around {day(due_rv)}; it is now {(now - due_rv).days} days past that."
                                     + (f" The plan was found by the follow-up extractor ({v['via']})." if v.get("via") else ""))
                    n, q = self.chart_search(api, v["pid"], ["consult_note"], lo)
                    w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} in the chart since {day(lo)}.", search=q)
                    w.add("assessment", "Working problem: planned chronic-care review missed. Plan: recall the patient; the risk is unnoticed drift in blood pressure, glucose or lipids.")
                    self.raise_item(api, ("rv", v["rid"]), v["pid"], "planned_review", "Planned review not attended",
                                    [v["rid"]], 1, v["owner"], 14, why=w)
                self.reviews.remove(v)
        for f in list(self.referrals):
            wait = 4 if f["urgent"] else 42
            if any(t > f["t"] for t, kind in self.closure_ev.get(f["pid"], ()) if kind in ("appointment", "letter")):
                self.referrals.remove(f)
            elif now >= f["t"] + timedelta(days=wait):
                eh_checked = False
                if self.pts[f["pid"]]["ehealth_consent"]:
                    try:
                        if any(r["kind"] == "appointment" and datetime.fromisoformat(r["available_at"]) > f["t"] for r in api.ehealth(f["pid"])):
                            self.referrals.remove(f)
                            continue
                        eh_checked = True
                    except Exception:
                        pass
                if ("ref", f["rid"]) not in self.raised:
                    w = Why()
                    w.add("source", f"{'Urgent referral' if f['urgent'] else 'Referral'} letter written on {day(f['t'])}.",
                          cite(f["rid"], line_with(f["text"], r"^To:")), cite(f["rid"], line_with(f["text"], r"^Reason:")))
                    w.add("finding", f"No booking, appointment or reply {(now - f['t']).days} days after the referral.")
                    n, q = self.chart_search(api, f["pid"], ["specialist_letter", "appointment"], f["t"])
                    w.add("loop_status", f"{'No' if n == 0 else n} specialist letter{'s' if n != 1 else ''} or appointment{'s' if n != 1 else ''} in the chart since the referral."
                                         + (" eHealth shows no new appointment either." if eh_checked else ""), search=q)
                    w.add("assessment", f"Working problem: {'urgent ' if f['urgent'] else ''}referral not acknowledged. The letter may not have arrived or the patient may not have booked. Plan: check with the patient and the receiving clinic.")
                    self.raise_item(api, ("ref", f["rid"]), f["pid"], "referral", "No booking or reply for referral",
                                    [f["rid"]], 3 if f["urgent"] else 2, f["owner"], 7 if f["urgent"] else 30, why=w)
                self.referrals.remove(f)
        if now.hour != 8:
            return
        items = {x["id"]: x for x in api.items(status="actioned")}
        for key, iid in list(self.raised.items()):          # one nudge when the patient could not be reached
            if key in self.nudged or key[0] not in ("fit", "mon", "rv", "ref", "order", "img", "dc"):
                continue
            it = items.get(iid)
            if it and it["status"] == "actioned" and it["resolved_at"]:
                pid = it["patient_id"]
                t_res = datetime.fromisoformat(it["resolved_at"])
                notes = [(t, txt, rid) for t, txt, rid in self.actions.get(pid, ()) if t >= t_res - timedelta(hours=1) and "Unable to reach" in txt]
                if now - t_res > timedelta(days=7) and notes:
                    self.nudged.add(key)
                    nt, _, nrid = notes[0]
                    w = Why()
                    w.add("source", f"Action note on {day(nt)}: the clinic could not reach the patient.", cite(nrid, "Unable to reach patient"))
                    w.add("finding", f"The first item was actioned on {day(t_res)}; the clinic then could not reach the patient.",
                          *[cite(e) for e in it["evidence"]])
                    n, q = self.chart_search(api, pid, ["action_note", "consult_note"], nt + timedelta(minutes=1))
                    w.add("loop_status", f"{'No' if n == 0 else n} further action note{'s' if n != 1 else ''} or consultation{'s' if n != 1 else ''} "
                                         f"in the chart since that note, {(now - nt).days} days ago.", search=q)
                    w.add("assessment", "Working problem: the loop is still open because the patient has not been reached. Plan: try again by another route (a call at a different time, WhatsApp or a letter).")
                    u = max(2, it["urgency"])
                    if self.version == "1.0":          # v1.0 behaviour kept for comparability: original owner, no roster check
                        owner, route_txt = it["owner_id"], f"Same doctor as the first item: {self.nm(it['owner_id'])}."
                    else:
                        owner, route_txt = self.route(api, it["owner_id"], pid, u)
                        route_txt = f"The first item's owner was {self.nm(it['owner_id'])}. " + route_txt
                    w.add("routing", route_txt)
                    w.add("request", f"Second attempt opened for {self.nm(owner)} at urgency {u} ({URGENCY_LABEL[u]}).")
                    api.open_item(pid, owner, it["family"], "Second attempt: patient not reached", it["evidence"], u, reasoning=w.steps)

    def check_silence(self, api):
        now = api.now
        if now.hour < 19:
            return
        for ch in ["courier", "fax"] + [p for p in self.portals if p != "portal:crcsp"]:
            hist = [t for t in self.arrivals.get(ch, ()) if t > now - timedelta(days=30)]
            if len(hist) < 10:
                continue
            open_days = sum(1 for i in range(30) if self.roster(api, (now - timedelta(days=i)).date())["open"])
            lam = len({t.date() for t in hist}) / max(1, open_days)          # share of open days with arrivals
            last = max(self.arrivals[ch])
            silent_open = sum(1 for i in range(0, (now.date() - last.date()).days + 1)
                              if self.roster(api, last.date() + timedelta(days=i))["open"] and last.date() + timedelta(days=i) != last.date())
            need = 2 if lam > 0.85 else 3 if lam > 0.6 else 99
            if silent_open >= need and not self.alarmed.get(ch):
                api.alarm(ch, f"no {ch} arrivals for {silent_open} open days")
                self.alarmed[ch] = True
            elif silent_open == 0:
                self.alarmed.pop(ch, None)
