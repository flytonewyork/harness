"""From a model's draft to text a doctor can approve: the checks that make a draft safe to show.

The plan is only ever the doctor's own words, re-laid out. So every item must quote the transcript word for word
(LoopBench's `unsupported_claim` rule, applied to the plan), and every number in the patient-facing text must appear
in that quote (a dose, a day count, a time). Items that fail are dropped and shown to the doctor, never sent.
"""
from __future__ import annotations

import re
import unicodedata

from .models import SECTIONS
from .texts import say

NUM = re.compile(r"\d+(?:[.,]\d+)?")
CN_NUM = {"一": "1", "二": "2", "兩": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[“”\"'‘’`]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def quoted(quote: str, transcript: str) -> bool:
    q = _norm(quote).rstrip(".。!！?？,，")
    return bool(q) and q in _norm(transcript)


def numbers(s: str) -> set[str]:
    s = unicodedata.normalize("NFKC", s or "")
    found = {n.replace(",", ".").rstrip("0").rstrip(".") if "." in n else n for n in NUM.findall(s)}
    return found


def numbers_ok(text: str, quote: str) -> list[str]:
    """Numbers in the patient text that aren't in the doctor's quote (Chinese numerals in the quote count)."""
    q = unicodedata.normalize("NFKC", quote or "")
    qn = numbers(q) | {v for k, v in CN_NUM.items() if k in q}
    return sorted(n for n in numbers(text) if n not in qn)


def check(draft: dict) -> dict:
    """Split a composed draft into kept and dropped items, with reasons."""
    t = draft.get("transcript", "")
    kept, dropped, flagged = [], [], []
    for it in draft.get("items", []):
        if it.get("section") not in SECTIONS:
            it["section"] = "other"
        if not quoted(it.get("quote", ""), t):
            dropped.append({**it, "why": "unsupported_claim"})
            continue
        extra = sorted(set(numbers_ok(it.get("en", ""), it["quote"])) | set(numbers_ok(it.get("zh", ""), it["quote"])))
        if extra:
            dropped.append({**it, "why": "numbers_not_said", "numbers": extra})
            flagged.extend(extra)
            continue
        kept.append(it)
    rems = []
    for r in draft.get("reminders", []) or []:
        if quoted(r.get("quote", ""), t) and not numbers_ok(r.get("en", ""), r["quote"]) and int(r.get("due_in_days") or 0) > 0:
            rems.append(r)
        else:
            dropped.append({**r, "section": "reminder", "why": "unsupported_claim"})
    ci = draft.get("checkin_days")
    return {"transcript": t, "items": kept, "dropped": dropped, "numbers": sorted(set(flagged)), "reminders": rems,
            "checkin_days": ci if isinstance(ci, int) and 1 <= ci <= 30 else None,
            "model": draft.get("model"), "prompt": draft.get("prompt")}


def render_plan(items: list[dict], lang: str, *, doctor: dict, date: dict, hours: dict) -> str:
    """The plan as the patient reads it: the patient's language first, then the other one. `doctor`, `date` and
    `hours` are per language."""
    def block(lg):
        out = [say("plan_header", lg, doctor=doctor[lg], date=date[lg])]
        for sec in SECTIONS:
            rows = [it[lg] for it in items if it["section"] == sec and it.get(lg)]
            if rows:
                out.append("")
                out.append(say(f"sec_{sec}", lg))
                out += [f"• {r}" for r in rows]
        out += ["", say("plan_tail", lg, doctor=doctor[lg], hours=hours[lg])]
        return "\n".join(out)
    other = "zh" if lang == "en" else "en"
    return block(lang) + "\n\n———\n\n" + block(other)


def medicines(items: list[dict]) -> list[str]:
    return [it["med"] for it in items if it["section"] == "take" and it.get("med")]
