"""Follow-up journey: the patient's reply to the day-3 check-in on the doctor's care line.

The care line (not the engine) sends the fixed check-in the doctor's plan asked for. The engine reads the reply
and puts it on the doctor's list, every time: a better reply is routine (only a doctor decides the patient is
fine and closes it), a worse one is urgent, a red-flag symptom is critical and pages the doctor. No reply two
days after the check-in is routine too: the follow-up did not happen. The engine never answers the patient;
the request step suggests the fixed reply the doctor can send.

Reading is deterministic and bilingual (Cantonese and English). Negated symptoms ("冇再發燒", "no more fever") are
removed before matching, and "not better" (唔係好返) reads as worse. Every quote is the reply's own words.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ...engine.rules import Why, cite, line_with, when

NAME = "followup"
SUMMARY = "Day-3 care-line check-in after an acute visit: every reply reaches the doctor, worse is urgent, red flags page."
FAMILIES = {"patient_reported": "A patient's reply (or silence) on the care line that the doctor must review and close."}

RED = [r"透唔到氣", r"唞唔到氣", r"抖唔到氣", r"呼吸困難", r"(心口|胸口).{0,4}(痛|翳|壓)", r"咳.{0,3}血", r"暈(低|倒|咗)",
       r"chest (pain|tight)", r"can'?t breathe", r"(short of breath|breathless) (at rest|lying)", r"cough(ing|ed)? (up )?blood",
       r"faint", r"confus"]
WORSE = [r"唔係好返", r"冇好返", r"未好返", r"差咗", r"嚴重咗", r"發(高)?燒", r"\b(3[89]|4[01])(\.\d)?\s*(度|°|C\b)",
         r"黃(色)?痰|痰.{0,4}黃", r"瞓唔到", r"未退|唔退", r"breathless", r"short of breath", r"worse", r"fever",
         r"not (any )?better", r"仲(係)?(咳|發|痛|好攰)"]
NEGATED = re.compile(r"(冇再|唔再|已經冇|冇|no longer (have )?(a )?|no more |without (a )?)(發(高)?燒|fever|氣促|breathless(ness)?)", re.I)
REPLY = {4: "收到。請即刻打 999 或去急症室；醫生已經收到通知。 / Please call 999 or go to A&E now; the doctor has been alerted.",
         3: "收到，醫生今日會覆你。如果透唔到氣或者心口痛，請即刻去急症室。 / The doctor will reply today. If you are breathless or have chest pain, go to A&E now.",
         1: "收到，多謝你話我哋知。醫生會睇你嘅回覆。 / Thank you. The doctor will read your reply."}


def classify(text: str) -> tuple[str, str | None]:
    """('red_flag' | 'worse' | 'better', the matched words)."""
    t = NEGATED.sub(" ", text or "")
    for label, pats in (("red_flag", RED), ("worse", WORSE)):
        for p in pats:
            m = re.search(p, t, re.I)
            if m:
                return label, m.group(0)
    return "better", None


class FollowupJourney:
    def reset(self, api):
        super().reset(api)
        self.fu_out: dict = {}        # pid -> {"t", "rid", "text"} latest check-in sent by the care line
        self.fu_replied: set = set()  # check-in record ids that got a reply

    def claims(self, api, r: dict) -> bool:
        return (r["kind"] == "message" and r["channel"] == "careline") or super().claims(api, r)

    def on_cms(self, api, r: dict):
        super().on_cms(api, r)
        if r["kind"] != "message" or r["channel"] != "careline" or not r["patient_id"]:
            return
        pid, t = r["patient_id"], datetime.fromisoformat(r["available_at"])
        if r["data"].get("direction") == "out":
            self.fu_out[pid] = {"t": t, "rid": r["id"], "text": r["text"]}
            return
        chk = self.fu_out.get(pid)
        if chk:
            self.fu_replied.add(chk["rid"])
        self.fu_reply(api, r, pid, t, chk)

    def tick(self, api):
        super().tick(api)
        for pid, chk in list(self.fu_out.items()):
            if chk["rid"] in self.fu_replied or ("fu", chk["rid"]) in self.raised:
                continue
            if api.now >= chk["t"] + timedelta(hours=48):
                self.fu_silence(api, pid, chk)

    # ------------------------------------------------------------------------------------- items
    def fu_plan(self, api, pid: str, before: datetime):
        notes = [x for x in api.chart(pid) if x["kind"] == "consult_note" and datetime.fromisoformat(x["available_at"]) <= before]
        return notes[-1] if notes else None

    def fu_context(self, w: Why, note):
        if note is None:
            w.add("context", "No consultation note on file before the check-in.")
            return
        q = line_with(note["text"], r"(?i)check-in|care line|P:") or line_with(note["text"], r"\S")
        w.add("context", f"The visit plan ({datetime.fromisoformat(note['available_at']):%d %b}) asked for this check-in.", cite(note["id"], q))

    def fu_reply(self, api, r, pid, t, chk):
        label, hit = classify(r["text"])
        u = {"red_flag": 4, "worse": 3, "better": 1}[label]
        note = self.fu_plan(api, pid, chk["t"] if chk else t)
        quote = line_with(r["text"], r"\S")
        w = Why()
        w.add("source", f"Patient's reply on the doctor's care line, {when(t)}.", cite(r["id"], quote))
        if chk:
            w.add("identity", f"Sent from the number the care line holds for {self.pname(pid)}, in reply to the check-in of {chk['t']:%d %b %H:%M}.",
                  cite(chk["rid"]))
        else:
            w.add("identity", f"Sent from the number the care line holds for {self.pname(pid)}.")
        if hit:
            w.add("finding", f"The reply reports {'a red-flag symptom' if u == 4 else 'getting worse'}: “{hit}”.", cite(r["id"], hit))
        else:
            w.add("finding", "The reply describes improvement and names no warning symptom; it still needs the doctor to read it.", cite(r["id"], quote))
        self.fu_context(w, note)
        w.add("assessment", {4: "Red-flag symptom after an acute visit: needs a same-day clinical decision (A&E or call now).",
                             3: "Not improving as planned after an acute visit: needs a doctor's call back today or tomorrow.",
                             1: "Reports improvement after an acute visit; the doctor confirms and closes."}[u])
        n, srch = self.chart_search(api, pid, ["consult_note"], t)
        w.add("loop_status", f"{n} consultation{'s' if n != 1 else ''} since the reply.", search=srch)
        reason = {4: "Care-line reply: red-flag symptom", 3: "Care-line reply: worse since the visit",
                  1: "Care-line reply: better; doctor to confirm and close"}[u]
        if hit:
            reason += f" (“{hit}”)"
        w.add("request", f"Suggested fixed reply for the doctor to send: {REPLY[u]}")
        ev = [r["id"]] + ([chk["rid"]] if chk else [])
        self.raise_item(api, ("fu", chk["rid"] if chk else r["id"]), pid, "patient_reported", reason, ev, u,
                        note["author"] if note else None, why=w)

    def fu_silence(self, api, pid, chk):
        note = self.fu_plan(api, pid, chk["t"])
        w = Why()
        w.add("source", f"Day-3 check-in sent on the care line {when(chk['t'])}.", cite(chk["rid"], line_with(chk["text"], r"\S")))
        w.add("identity", f"Sent to the number the care line holds for {self.pname(pid)}.")
        w.add("finding", "No reply in the 48 hours since; the follow-up the plan asked for has not happened.")
        self.fu_context(w, note)
        w.add("assessment", "Follow-up not completed: the patient's progress is unknown.")
        n, srch = self.chart_search(api, pid, ["consult_note"], chk["t"])
        w.add("loop_status", f"{n} consultation{'s' if n != 1 else ''} since the check-in.", search=srch)
        w.add("request", "Suggested: the clinic assistant phones the patient, or the doctor sends the care line's second check-in template.")
        ev = [chk["rid"]] + ([note["id"]] if note else [])
        self.raise_item(api, ("fu", chk["rid"]), pid, "patient_reported", "No reply to the day-3 care-line check-in", ev, 1,
                        note["author"] if note else None, why=w)


JOURNEY = FollowupJourney
