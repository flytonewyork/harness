"""The Phase 0 report: what a clinic gets back from the look-back (`docs/EC_DATA_REQUEST.md`, "What EC gets back").

    python -m backend.lookback.report --export EXPORT_DIR --lookback LOOKBACK_DIR --out DIR [--validation SCORE.json]

It reads only the export and the look-back's own output (`summary.json`, `flags.csv`), and writes `report.html` and
`owed.csv`: the follow-ups owed and not done, by kind and per 1,000 patients; the keys of the patients still owed one
(keys only: the clinic turns them back into patients, we can't); missed consultations x fee; and the data gaps. An
export whose MANIFEST.json says it is simulated is labelled as a sample at the top of the page. `--validation` adds
how accurate the look-back was where an answer key exists (LoopBench's `score.json`); a clinic's own export has none.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path

# docs/EC_DATA_REQUEST.md: the median private GP fee (Hong Kong Med J 2026;32:354-6), and Co-care's HK$166 subsidy
# plus the HK$150 recommended co-payment
FEES = {"private": (400, 540), "co_care": 316}

LABELS = {
    "abnormal_result": "Abnormal results nobody acted on",
    "test_not_resulted": "Tests ordered but never resulted",
    "referral_reply": "Referrals with no reply",
    "fit_positive": "Positive stool test (FIT) without a colonoscopy",
    "mammogram_recall": "Abnormal mammograms not followed up",
    "drug_monitoring": "Monitoring bloods after a new medicine",
    "planned_review": "Planned reviews that didn't happen",
    "hospital_followup": "Follow-up after a hospital stay",
    "committed_test": "Repeat tests the doctor planned",
    "imaging_followup": "Imaging the doctor planned",
}
URGENCY = {"4": "critical", "3": "urgent", "2": "soon", "1": "routine"}   # backend/engine/rules.py, URGENCY_LABEL
OWED_COLUMNS = ["patient_key", "follow_up", "owed_by", "detail", "source_keys"]
SHOWN = 12   # rows of the owed list on the page; owed.csv has them all


def label(kind: str) -> str:
    return LABELS.get(kind, kind.replace("_", " ").capitalize())


def detail(flag: dict) -> str:
    """The look-back's detail in a clinic's words: urgency by name, and no echo of the commitment's own kind."""
    d = (flag.get("detail") or "").strip()
    m = re.match(r"urgency (\d): (.*)", d)
    if m:
        return f"{URGENCY.get(m.group(1), 'urgency ' + m.group(1))}: {m.group(2)}"
    pre, sep, rest = d.partition(": ")
    if sep and pre in ("review", "repeat_test", "imaging"):
        return "" if rest.strip().lower() in ("", pre.replace("_", " ")) else rest.strip()
    return d


def per_1000(n: int, patients: int) -> float:
    return round(n * 1000 / patients, 1) if patients else 0.0


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load(export_dir: str | Path, lookback_dir: str | Path) -> dict:
    exp, lb = Path(export_dir), Path(lookback_dir)
    manifest = exp / "MANIFEST.json"
    return {
        "summary": json.loads((lb / "summary.json").read_text(encoding="utf-8")),
        "flags": _rows(lb / "flags.csv"),
        "manifest": json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {},
        "patients": len(_rows(exp / "patients.csv")),
        "doctors": len(_rows(exp / "doctors.csv")),
    }


def figures(data: dict) -> dict:
    s, n = data["summary"], data["patients"]
    rows = []
    for kind, c in s["by_kind"].items():
        rows.append({"kind": kind, "label": label(kind), "unclosed": c["unclosed"], "late": c["late"],
                     "unknown": c["unknown"], "per_1000": per_1000(c["unclosed"] + c["late"], n)})
    rows.sort(key=lambda r: (-(r["unclosed"] + r["late"]), -r["unknown"], r["kind"]))
    total = {k: sum(r[k] for r in rows) for k in ("unclosed", "late", "unknown")}
    owed = defaultdict(list)
    for f in data["flags"]:
        if f["status"] == "unclosed":
            owed[f["patient_key"]].append(f)
    owed_rows = sorted(({"patient_key": k, "owed_by": min(f["owed_by"] for f in v), "flags": v} for k, v in owed.items()),
                       key=lambda r: r["owed_by"])
    visits = s["missed_consultations"]
    return {
        "rows": rows, "total": total, "patients": n, "owed": owed_rows,
        "not_done_per_1000": per_1000(total["unclosed"] + total["late"], n),
        "open_per_1000": per_1000(total["unclosed"], n),
        "visits": visits,
        "hkd_private": (visits * FEES["private"][0], visits * FEES["private"][1]),
        "hkd_co_care": visits * FEES["co_care"],
    }


def owed_csv(fig: dict, path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OWED_COLUMNS)
        w.writeheader()
        for r in fig["owed"]:
            for f in sorted(r["flags"], key=lambda f: f["owed_by"]):
                w.writerow({"patient_key": f["patient_key"], "follow_up": label(f["kind"]), "owed_by": f["owed_by"],
                            "detail": detail(f), "source_keys": f["source_keys"]})


def _day(s: str | None) -> str:
    if not s:
        return ""
    d = datetime.fromisoformat(s)
    return f"{d.day} {d:%b %Y}"


def _n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else (f"{x:,.0f}" if x >= 10 else f"{x:,.1f}")


def _policy(p: dict) -> list[str]:
    out = []
    if "result_window_days" in p:
        days = [str(v) for _, v in sorted(p["result_window_days"].items(), key=lambda kv: -int(kv[0]))]
        out.append(f"An abnormal result is acted on within {', '.join(days[:-1])} or {days[-1]} days, from critical to routine.")
    if "order_result_days" in p:
        out.append(f"A test is resulted within {p['order_result_days']} days of the order.")
    if "referral_days" in p:
        out.append(f"A referral gets a reply within {p['referral_days']['urgent']} days if urgent, "
                   f"{p['referral_days']['other']} otherwise.")
    if "fit_days" in p:
        out.append(f"A positive FIT leads to a colonoscopy within {p['fit_days']} days.")
    if "mammogram_days" in p:
        out.append(f"An abnormal screening mammogram leads to more imaging within {p['mammogram_days']} days.")
    if "monitoring_grace_days" in p:
        out.append(f"Monitoring bloods after a new medicine get {p['monitoring_grace_days']} days' grace.")
    if "review_grace_days" in p:
        out.append(f"A planned review is overdue {p['review_grace_days']} days after its date.")
    return out


CSS = """
:root{--ink:#0E1116;--muted:#4F5866;--line:#E3E6EB;--soft:#F5F6F8;--open:#9A3412;--open-soft:#FFF4ED;--ok:#0F6E56}
*{box-sizing:border-box}body{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:var(--ink);background:#fff}
main{max-width:780px;margin:0 auto;padding:28px 20px 48px}h1{font-size:26px;line-height:1.25;margin:0 0 6px;font-weight:600}
h2{font-size:18px;margin:36px 0 10px;font-weight:600}p{margin:8px 0}.muted{color:var(--muted)}.small{font-size:14px}
.banner{background:var(--open-soft);color:var(--open);border:1px solid #F5C9AE;border-radius:10px;padding:10px 14px;font-size:14px;margin-bottom:22px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:22px 0}
.tile{border:1px solid var(--line);border-radius:12px;padding:14px 16px;background:var(--soft)}
.tile b{display:block;font-size:28px;line-height:1.2;font-weight:600}.tile.open b{color:var(--open)}.tile span{font-size:14px;color:var(--muted)}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500}td.n,th.n{text-align:right;white-space:nowrap}td.d{white-space:nowrap}tfoot td{font-weight:600;border-bottom:0}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace}ul{padding-left:20px;margin:8px 0}li{margin:4px 0}
footer{margin-top:40px;border-top:1px solid var(--line);padding-top:14px;font-size:13px;color:var(--muted)}
@media print{.banner{border-color:#999}main{padding:0}}
"""


def render(data: dict, fig: dict, validation: dict | None = None, title: str | None = None) -> str:
    s, m = data["summary"], data["manifest"]
    sample = bool(m.get("simulated"))
    window = m.get("index_window") or []
    e = escape
    h = []
    h.append(f"<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
             f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
             f"<title>{e(title or 'Phase 0 follow-up look-back')}{' (sample)' if sample else ''}</title>"
             f"<style>{CSS}</style></head><body><main>")
    if sample:
        h.append("<div class=\"banner\"><b>Sample on a simulated clinic: synthetic patients only.</b> On a clinic's "
                 "own export, the same look-back writes this same report.</div>")
    h.append(f"<h1>{e(title or 'Phase 0 follow-up look-back')}</h1>")
    scope = [f"{_n(data['doctors'])} doctors" if data.get("doctors") else "", f"{_n(fig['patients'])} patients"]
    if len(window) == 2:
        scope.append(f"follow-ups started {_day(window[0])} to {_day(window[1])}")
    scope.append(f"checked against records up to {_day(s['as_of'])}")
    h.append(f"<p class=\"muted\">{e(' · '.join(x for x in scope if x))}</p>")
    h.append("<p>The follow-ups this clinic owed its patients, and which of them never happened. Nothing was sent "
             "to doctors or patients.</p>")

    t = fig["total"]
    lo, hi = fig["hkd_private"]
    h.append("<div class=\"tiles\">"
             f"<div class=\"tile open\"><b>{_n(t['unclosed'])}</b><span>follow-ups still open, for "
             f"{_n(len(fig['owed']))} patients</span></div>"
             f"<div class=\"tile\"><b>{_n(t['late'])}</b><span>done, but late</span></div>"
             f"<div class=\"tile\"><b>{_n(fig['not_done_per_1000'])}</b><span>not done on time, per 1,000 patients</span></div>"
             f"<div class=\"tile\"><b>HK${_n(round(lo / 1000))}k–{_n(round(hi / 1000))}k</b><span>"
             f"{_n(fig['visits'])} missed consultations at private fees</span></div>"
             "</div>")

    h.append("<h2>By kind</h2><div class=\"scroll\"><table><thead><tr><th>Follow-up</th><th class=\"n\">Open</th>"
             "<th class=\"n\">Late</th><th class=\"n\">Per 1,000</th><th class=\"n\">Can't tell</th>"
             "</tr></thead><tbody>")
    for r in fig["rows"]:
        h.append(f"<tr><td>{e(r['label'])}</td><td class=\"n\">{_n(r['unclosed'])}</td><td class=\"n\">{_n(r['late'])}</td>"
                 f"<td class=\"n\">{_n(r['per_1000'])}</td><td class=\"n\">{_n(r['unknown']) if r['unknown'] else ''}</td></tr>")
    h.append(f"</tbody><tfoot><tr><td>All</td><td class=\"n\">{_n(t['unclosed'])}</td><td class=\"n\">{_n(t['late'])}</td>"
             f"<td class=\"n\">{_n(fig['not_done_per_1000'])}</td><td class=\"n\">{_n(t['unknown'])}</td></tr></tfoot></table>"
             "</div>")
    h.append("<p class=\"small muted\">Open: still not done when the records end. Late: done after it was due. Per "
             "1,000 patients counts the open and the late together. Can't tell: this export can't show whether it "
             "happened.</p>")

    h.append("<h2>What it's worth</h2>"
             f"<p>{_n(fig['visits'])} of these follow-ups should have brought the patient back for a consultation. At the "
             f"median private GP fee of HK${FEES['private'][0]}–{FEES['private'][1]}, that's "
             f"<b>HK${_n(lo)}–{_n(hi)}</b>. At the Co-care fee of HK${FEES['co_care']}, it's "
             f"<b>HK${_n(fig['hkd_co_care'])}</b>. A range for one year of this clinic, not a forecast.</p>")

    h.append(f"<h2>Patients still owed a follow-up</h2><p>{_n(len(fig['owed']))} patients, oldest first. Keys only: the "
             "clinic turns them back into patients to call. The full list is in <code>owed.csv</code>.</p>"
             "<div class=\"scroll\"><table><thead><tr><th>Patient key</th><th>Owed by</th><th>What</th></tr></thead><tbody>")
    for r in fig["owed"][:SHOWN]:
        what = "; ".join(f"{label(f['kind'])}: {detail(f)}" if detail(f) else label(f["kind"]) for f in r["flags"])
        h.append(f"<tr><td><code>{e(r['patient_key'])}</code></td><td class=\"d\">{e(_day(r['owed_by']))}</td>"
                 f"<td>{e(what[:160])}</td></tr>")
    h.append("</tbody></table></div>")
    if len(fig["owed"]) > SHOWN:
        h.append(f"<p class=\"small muted\">And {_n(len(fig['owed']) - SHOWN)} more in <code>owed.csv</code>.</p>")

    gaps = []
    if s.get("unfiled_results_linked_by_request_number"):
        gaps.append(f"{_n(s['unfiled_results_linked_by_request_number'])} lab results reached the clinic without being "
                    "filed to a patient. The look-back linked them back through the clinic's own request numbers.")
    if s.get("unfiled_lab_reports"):
        gaps.append(f"{_n(s['unfiled_lab_reports'])} lab results couldn't be linked to any patient.")
    if s.get("unfiled_documents"):
        gaps.append(f"{_n(s['unfiled_documents'])} letters or reports were never filed to a patient, so nothing they "
                    "asked for can be checked.")
    if t["unknown"]:
        gaps.append(f"{_n(t['unknown'])} follow-ups can't be judged from this export, such as hospital referrals whose "
                    "reply only reaches eHealth.")
    if m.get("not_in_export"):
        gaps.append(f"Not in the export: {m['not_in_export']}.")
    if gaps:
        h.append("<h2>Data gaps</h2><ul>" + "".join(f"<li>{e(g)}</li>" for g in gaps) + "</ul>")

    if validation:
        v = validation
        h.append("<h2>How accurate this is</h2><p>On a simulated clinic every answer is known, so the look-back can be "
                 f"scored. Of the {_n(v['missed_determinable'])} missed follow-ups the records can show, it found "
                 f"<b>{v['recall_determinable']:.0%}</b>. Of its {_n(v['flags_definite'])} definite flags, "
                 f"{_n(v['flags_correct'])} were right (<b>{v['precision']:.0%}</b>). Counting the "
                 f"{_n(v['missed'] - v['missed_determinable'])} the records can't show at all, it found "
                 f"{v['recall_all']:.0%}.")
        if v.get("flags_unknown"):
            h.append(f" Of the {_n(v['flags_unknown'])} it couldn't judge, {_n(v.get('unknown_truly_missed', 0))} had "
                     "in fact been missed.")
        h.append("</p>")

    rules = _policy(s.get("policy", {}))
    if rules:
        h.append("<h2>When a follow-up counts as owed</h2><ul>" + "".join(f"<li>{e(r)}</li>" for r in rules) + "</ul>")
    h.append(f"<footer>Made by tmrw's follow-up look-back, with the {e(s.get('extractor', 'rules'))} reader for free "
             "text. It reads the export once and sends nothing to doctors or patients.</footer></main></body></html>")
    return "".join(h)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m backend.lookback.report")
    ap.add_argument("--export", required=True)
    ap.add_argument("--lookback", required=True, help="the look-back's output: summary.json and flags.csv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--validation", default=None, help="score.json from a simulated export, if there is one")
    ap.add_argument("--title", default=None)
    a = ap.parse_args(argv)
    data = load(a.export, a.lookback)
    fig = figures(data)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    validation = json.loads(Path(a.validation).read_text(encoding="utf-8")) if a.validation else None
    (out / "report.html").write_text(render(data, fig, validation, a.title), encoding="utf-8")
    owed_csv(fig, out / "owed.csv")
    print(f"wrote {out / 'report.html'} and {out / 'owed.csv'}: {fig['total']['unclosed']} open for "
          f"{len(fig['owed'])} patients")


if __name__ == "__main__":
    main()
