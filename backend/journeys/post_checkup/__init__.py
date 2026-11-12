"""Post-checkup journey: after a health check, every result reaches the patient through their doctor.

What the engine does on top of the rules floor (rules v1.0 and v1.1 are untouched):

1. **Tracks each health check.** A consultation note that says it is a health check starts a watch; the lab
   orders made that day and the imaging the note names are tied to it by lab number and patient.
2. **Waits for every report.** Lab reports (portal, courier, fax, filed or not) and imaging reports are matched
   to the watch. When they are all in (or 10 days have passed), the clinic gets 2 open days to go through them
   with the patient on its own.
3. **Opens one item if nobody has.** If no consultation or action note appears in the chart after the reports,
   it opens a `checkup_result` item for the doctor: the reports, a chart search the platform re-runs, and a
   fixed message the doctor may send in their own name. It never tells the patient anything itself, and never
   says a result is normal to the patient: only the doctor does that.
4. **Flags what the floor misses in health-check imaging.** "Suggest contrast CT", "further evaluation",
   or a Chinese report's 建議 / 覆查 open a `followup_recommended` item with the impression quoted word for word.
   A conditional ("advise surgical consultation if symptomatic") is left for the results conversation.
5. **Ties abnormal results to the health check.** The floor already raises abnormal labs; when they belong to
   a health check, the item says so and asks the doctor to go through the whole report, so one item closes
   both. Abnormal or imaging items already open for the same reports stop a second item.

Authority stays asymmetric: open_item and raise_urgency only. Every quote is copied from the record it cites.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ...engine.rules import Why, cite, day, line_with, parse_lab_text, when

NAME = "post_checkup"
SUMMARY = ("After a health check, every report reaches the patient through their doctor: one item if nobody has gone "
           "through the results, abnormal results tied to the check, imaging follow-up the floor misses.")
FAMILIES = {"checkup_result": "Health-check results back but not yet gone through with the patient"}

HEALTH_CHECK = re.compile(r"health[- ]?check|health screening|check[- ]?up|annual medical|體檢|身體檢查", re.I)
IMAGING_NAMED = re.compile(r"\b(CXR|chest x-?ray|US abdomen|ultrasound|mammogram|USG|X-?ray)\b", re.I)
SUGGESTS = re.compile(r"suggest|further evaluation|advise|recommend|follow[- ]?up|建議|覆查|跟進", re.I)
CONDITIONAL = re.compile(r"if (symptomatic|clinically indicated|needed|required|concern)|as needed|prn|如有(症狀|需要)", re.I)
NORMAL_IMAGING = re.compile(r"normal study|no significant abnormality|未見異常", re.I)
WAIT_OPEN_DAYS = 2          # the clinic's own chance to go through the results first
ALL_IN_BY_DAYS = 10         # stop waiting for a missing report after this long
SEEN = ("consult_note", "action_note")

# the fixed message the doctor may send in their own name (it carries no result and no reassurance)
TEMPLATE = ("你嘅身體檢查報告已經出咗，醫生想同你講解結果。請預約覆診或者等我哋打電話俾你。 / "
            "Your health-check reports are back and your doctor would like to go through them with you. "
            "Please book a results visit, or we will call you.")


class PostCheckupJourney:
    """Cooperative mixin: compose before a RulesHarness subclass, e.g. class X(PostCheckupJourney, AgenticHarness)."""

    def reset(self, api):
        super().reset(api)
        self.checkups: list[dict] = []
        self.pc_stats = {"health_checks": 0, "results_items": 0, "imaging_items": 0, "covered_by_other_item": 0,
                         "explained_by_clinic": 0}

    # ------------------------------------------------------------------------------ reading
    def on_cms(self, api, r: dict):
        super().on_cms(api, r)
        k, pid = r["kind"], r["patient_id"]
        t = datetime.fromisoformat(r["available_at"])
        if k == "consult_note" and pid and HEALTH_CHECK.search(r["text"]):
            self.checkups.append({"pid": pid, "t": t, "rid": r["id"], "owner": r["author"], "text": r["text"],
                                  "quote": line_with(r["text"], HEALTH_CHECK.pattern, re.I),
                                  "lab_nos": set(), "imaging": bool(IMAGING_NAMED.search(r["text"])),
                                  "reports": [], "done": False})
            self.pc_stats["health_checks"] += 1
        elif k == "lab_request" and pid:
            ln = r["data"].get("lab_no")
            for c in self._open_checkups(pid):
                if ln and abs((t - c["t"]).total_seconds()) < 86400:
                    c["lab_nos"].add(ln)
        elif k == "imaging_report":
            hdr, _ = parse_lab_text(r["text"])
            pid2, how = self.identify(api, r, hdr)
            if not pid2 and pid:
                pid2, how = pid, f"Filed to {self.pname(pid)}'s chart."
            if not pid2:
                return
            for c in self._recent_checkups(pid2, t):
                if c["imaging"] and t >= c["t"] and not any(x["kind"] == "imaging" for x in c["reports"]):
                    c["reports"].append({"rid": r["id"], "t": t, "kind": "imaging", "text": r["text"], "how": how})
            self._imaging_followup(api, r, pid2, how, t)

    def handle_lab(self, api, pid, lab_no, results, rid, owner_hint, t, text="", channel="", how=""):
        new = [x["test"] for x in results if x.get("flag") in ("H", "L") and not self.previous(pid, x["test"], t, lab_no)]
        super().handle_lab(api, pid, lab_no, results, rid, owner_hint, t, text=text, channel=channel, how=how)
        for c in self._recent_checkups(pid, t):
            if lab_no and lab_no in c["lab_nos"]:
                if new and not c["done"]:
                    self._new_finding(api, ("lab", lab_no), new)
                c["reports"].append({"rid": rid, "t": t, "kind": "lab", "lab_no": lab_no, "text": text, "how": how,
                                     "channel": channel, "abnormal": any(x.get("flag") in ("H", "L") for x in results)})

    def _recent_checkups(self, pid, t):
        """Health checks a report can still belong to (reports keep attaching after the first item, so a second
        attempt cites everything that has arrived)."""
        return [c for c in self.checkups if c["pid"] == pid and timedelta(0) <= t - c["t"] <= timedelta(days=60)]

    def _open_checkups(self, pid):
        return [c for c in self.checkups if c["pid"] == pid and not c["done"]]

    # ------------------------------------------------------------------------- imaging the floor misses
    def _imaging_followup(self, api, r, pid, how, t):
        text = r["text"]
        if ("img", r["id"]) in self.raised or NORMAL_IMAGING.search(text) or not SUGGESTS.search(text):
            return
        imp = line_with(text, r"IMPRESSION|結論")
        if not imp or not SUGGESTS.search(imp) or CONDITIONAL.search(imp):
            return                            # nothing asked for, or only if symptoms develop: the doctor reads it at the results visit
        w = Why()
        w.add("source", f"Imaging report arrived via {self.chan_label(r['channel'])} on {when(t)}.",
              cite(r["id"], line_with(text, r"REPORT$")))
        w.add("identity", how, *self.id_cites(r["id"], text))
        w.add("finding", "The radiologist's impression asks for further imaging or follow-up.", cite(r["id"], imp),
              cite(r["id"], line_with(text, r"FINDINGS|所見")))
        hc = next((c for c in self.checkups if c["pid"] == pid and t >= c["t"] and c["imaging"]), None)
        if hc:
            w.add("context", f"Part of the health check on {day(hc['t'])}.", cite(hc["rid"], hc["quote"]))
        w.add("assessment", "Working problem: imaging follow-up recommended by the radiologist. "
                            "Plan: the doctor arranges it and tells the patient.")
        n, q = self.chart_search(api, pid, ["action_note", "referral_letter"], t)
        w.add("loop_status", f"{'No' if n == 0 else n} action note{'s' if n != 1 else ''} or referral letter"
                             f"{'s' if n != 1 else ''} in the chart since the report.", search=q)
        self.raise_item(api, ("img", r["id"]), pid, "followup_recommended", "Imaging report recommends follow-up",
                        [r["id"]], 2, r["author"], 30, why=w)
        self.pc_stats["imaging_items"] += 1

    # --------------------------------------------------------------------- abnormal labs in a health check
    def _new_finding(self, api, key, tests):
        """A health check finds something new in a well person who is not under follow-up for it: at least
        'soon' (within a week), with no waiting beyond the one open day that urgency allows."""
        for p in self.pending:
            if p["key"] == key and p["urgency"] < 2:
                p["urgency"], p["grace"], p["new_finding"] = 2, self.grace[2], tests
                d, left = p["t"], self.grace[2]
                while left > 0:
                    d += timedelta(days=1)
                    if self.roster(api, d.date())["open"]:
                        left -= 1
                p["raise_at"] = d

    def lab_why(self, api, p) -> Why:
        w = super().lab_why(api, p)
        hc = next((c for c in self.checkups if c["pid"] == p["pid"] and p["lab_no"] in c["lab_nos"]), None)
        if hc:
            raised = (f" Urgency raised to soon (within a week): {', '.join(p['new_finding'][:4])} "
                      "outside range with no earlier result at this clinic, found in a health check of a patient "
                      "not under follow-up for it.") if p.get("new_finding") else ""
            w.add("context", f"These are health-check bloods from {day(hc['t'])}. Go through the whole report with the "
                             f"patient, not only the flagged results.{raised}", cite(hc["rid"], hc["quote"]))
        return w

    # ------------------------------------------------------------------------------------- tick
    def tick(self, api):
        super().tick(api)
        for c in self.checkups:
            if not c["done"]:
                self._check_results(api, c)
        self._follow_through(api)

    def _follow_through(self, api):
        """The item that carries a health check was actioned, but the clinic then could not reach the patient
        and nobody has seen them since: one second attempt, once, as soon as a week has passed (the floor's nudges
        wait for the 08:00 run; a results conversation has less slack, so this checks at every run)."""
        now = api.now
        items = None
        for c in self.checkups:
            if not c["done"] or c.get("nudged") or not c.get("reports"):
                continue
            keys = [k for k in [("checkup", c["rid"])] + sorted(self._keys(c), key=str) if k in self.raised]
            if not keys:
                continue
            items = items if items is not None else {x["id"]: x for x in api.items(status="actioned")}
            it = next((items[self.raised[k]] for k in keys if self.raised[k] in items), None)
            if not it or not it.get("resolved_at"):
                continue
            t_res = datetime.fromisoformat(it["resolved_at"])
            pid = c["pid"]
            notes = [(t, rid) for t, txt, rid in self.actions.get(pid, ()) if t >= t_res - timedelta(hours=1) and "Unable to reach" in txt]
            if not notes or now - t_res < timedelta(days=7):
                continue
            nt, nrid = notes[0]
            if any(t > nt for t in self.consults.get(pid, ())):
                c["nudged"] = True
                continue
            c["nudged"] = True
            evidence = [c["rid"]] + [x["rid"] for x in c["reports"]]
            flagged = any(x.get("abnormal") for x in c["reports"])
            w = Why()
            w.add("source", f"Action note on {day(nt)}: the clinic could not reach the patient.", cite(nrid, "Unable to reach patient"))
            w.add("context", f"Health check on {day(c['t'])}; the item that carried its results was actioned on {day(t_res)}.",
                  cite(c["rid"], c["quote"]), *[cite(x["rid"]) for x in c["reports"]])
            n, q = self.chart_search(api, pid, ["consult_note"], nt + timedelta(minutes=1))
            w.add("loop_status", f"{'No' if n == 0 else n} consultation{'s' if n != 1 else ''} in the chart since that note, "
                                 f"{(now - nt).days} days ago: the patient still has not had their results.", search=q)
            w.add("assessment", "Working problem: health-check results" + (", including flagged results," if flagged else "")
                                + " still not given to the patient, who could not be reached. Plan: try another route "
                                "(a call at a different time, WhatsApp or a letter).")
            w.add("context", f"Suggested message the doctor may send in their own name (the engine sends nothing): {TEMPLATE}")
            u = max(2, it["urgency"])         # at least soon; never above what the first item carried
            owner, route_txt = self.route(api, it["owner_id"], pid, u)
            w.add("routing", f"The first item's owner was {self.nm(it['owner_id'])}. " + route_txt)
            w.add("request", f"Second attempt opened for {self.nm(owner)} at urgency {u}.")
            self.raised[("checkup2", c["rid"])] = api.open_item(pid, owner, "checkup_result", "Second attempt: health-check results, patient not reached",
                                                                evidence, u, reasoning=w.steps)
            self.pc_stats["second_attempts"] = self.pc_stats.get("second_attempts", 0) + 1

    def _keys(self, c) -> set:
        return ({("lab", x.get("lab_no")) for x in c["reports"] if x["kind"] == "lab"}
                | {("lab", x["rid"]) for x in c["reports"]} | {("img", x["rid"]) for x in c["reports"]})

    def _covered(self, c) -> bool:
        """An item is already open on these reports (abnormal labs or imaging): it carries the conversation."""
        return any(k in self.raised for k in self._keys(c))

    def _check_results(self, api, c):
        now, pid = api.now, c["pid"]
        if not c["reports"]:
            if now - c["t"] > timedelta(days=60):
                c["done"] = True              # no report ever came: the floor's lost-test watch owns that
            return
        labs_in = {x["lab_no"] for x in c["reports"] if x["kind"] == "lab"}
        img_in = any(x["kind"] == "imaging" for x in c["reports"])
        all_in = labs_in >= c["lab_nos"] and (img_in or not c["imaging"])
        first = min(x["t"] for x in c["reports"])
        last = max(x["t"] for x in c["reports"])
        if not all_in and now - c["t"] < timedelta(days=ALL_IN_BY_DAYS):
            return
        if any(t >= first for t in self.consults.get(pid, ())) or self.handled(pid, first):
            c["done"] = True
            self.pc_stats["explained_by_clinic"] += 1
            return
        if any(p["key"] in self._keys(c) for p in self.pending):
            return                            # the floor is about to raise these reports: let that item carry it
        if self._covered(c):
            c["done"] = True
            self.pc_stats["covered_by_other_item"] += 1
            return
        d, left = last, WAIT_OPEN_DAYS
        while left > 0:
            d += timedelta(days=1)
            if self.roster(api, d.date())["open"]:
                left -= 1
        if now < d.replace(hour=9, minute=0):
            return
        c["done"] = True
        self._raise_results(api, c, first, last, all_in)

    def _raise_results(self, api, c, first, last, all_in):
        pid = c["pid"]
        w = Why()
        w.add("source", f"Health-check consultation on {when(c['t'])}.", cite(c["rid"], c["quote"]),
              cite(c["rid"], line_with(c["text"], r"go through|results|Plan", re.I)))
        reps = sorted(c["reports"], key=lambda x: x["t"])
        lab = next((x for x in reps if x["kind"] == "lab"), reps[0])
        w.add("identity", lab["how"] or f"Reports matched to {self.pname(pid)} by lab number and chart.",
              *self.id_cites(lab["rid"], lab["text"]))
        parts, cites_, seen_ln = [], [], set()
        for x in reps:
            if x["kind"] == "lab":
                copy = x["lab_no"] in seen_ln
                seen_ln.add(x["lab_no"])
                parts.append(f"{'another copy of ' if copy else ''}lab report {x['lab_no']} "
                             f"({'portal' if str(x.get('channel', '')).startswith('portal:') else self.chan_label(x.get('channel') or 'cms')}) on {when(x['t'])}")
                cites_.append(cite(x["rid"], line_with(x["text"], r"Lab No|Ref No\.")))
            else:
                parts.append(f"imaging report on {when(x['t'])}")
                cites_.append(cite(x["rid"], line_with(x["text"], r"IMPRESSION|結論")))
        flagged = any(x.get("abnormal") for x in reps)
        missing = "" if all_in else " Not every ordered report has arrived; the ones that have are listed."
        w.add("finding", "Health-check reports in: " + "; ".join(parts) + "."
                         + (" The lab flagged results on it." if flagged else
                            " The lab flagged no result outside its printed range; the doctor still reads every line.")
                         + missing, *cites_)
        n, q = self.chart_search(api, pid, list(SEEN), first)
        w.add("loop_status", f"{'No' if n == 0 else n} consultation or action note{'s' if n != 1 else ''} in the chart since "
                             f"the first report on {day(first)}: nobody has gone through the results with the patient yet. "
                             f"Waited {WAIT_OPEN_DAYS} open days for the clinic to do it first.", search=q)
        w.add("assessment", "Working problem: health-check results not yet given to the patient. Plan: the doctor goes "
                            "through every report with them at a results visit or by phone, and documents it.")
        w.add("context", f"Suggested message the doctor may send in their own name (the engine sends nothing): {TEMPLATE}")
        evidence = [c["rid"]] + [x["rid"] for x in reps]
        self.raise_item(api, ("checkup", c["rid"]), pid, "checkup_result", "Health-check results not yet gone through with the patient",
                        evidence, 1, c["owner"], 14, why=w)
        self.pc_stats["results_items"] += 1


JOURNEY = PostCheckupJourney
