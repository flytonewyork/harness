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

NUM = re.compile(r"\d+(?:\.\d+)?")
CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000}
CN_RUN = re.compile("[零〇一二兩两三四五六七八九十百千廿卅]+")
EN_WORDS = {"zero": 0, "one": 1, "once": 1, "a": 1, "an": 1, "each": 1, "every": 1, "daily": 1, "single": 1, "two": 2, "twice": 2,
            "both": 2, "couple": 2, "three": 3, "thrice": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "fourteen": 14, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
            "sixty": 60, "ninety": 90, "hundred": 100, "thousand": 1000, "fortnight": 2}


def _norm(s: str) -> str:
    """Words only: case, spacing and punctuation don't count, so a model tidying a comma can't sink a true quote."""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return "".join(ch for ch in s if not ch.isspace() and unicodedata.category(ch)[0] not in ("P", "S"))


def quoted(quote: str, transcript: str) -> bool:
    q = _norm(quote)
    return len(q) >= 2 and q in _norm(transcript)


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.3f}".rstrip("0")


def _cn(run: str) -> int:
    run = run.replace("廿", "二十").replace("卅", "三十")
    total, num = 0, 0
    for ch in run:
        if ch in CN_DIGIT:
            num = CN_DIGIT[ch]
        else:
            total += (num or 1) * CN_UNIT[ch]
            num = 0
    return total + num


def numbers(s: str) -> set[str]:
    """Numbers written as digits ('5', '2.5', '1,000')."""
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", unicodedata.normalize("NFKC", s or ""))
    s = re.sub(r"\b(\d{1,2}):00\b", r"\1", s)               # "9:00" is "nine o'clock": the 00 was never said
    return {_fmt(float(n)) for n in NUM.findall(s)}


def spoken_numbers(s: str) -> set[str]:
    """Numbers as a doctor says them: digits, Chinese numerals (兩個禮拜, 二十, 廿五, 半粒) and English words (twice, a week)."""
    s = unicodedata.normalize("NFKC", s or "")
    out = numbers(s) | {_fmt(_cn(r)) for r in CN_RUN.findall(s)}
    for ch in s:                                                # 二五 read digit by digit also allows 2 and 5
        if ch in CN_DIGIT:
            out.add(str(CN_DIGIT[ch]))
    words = re.findall(r"[a-z]+", s.lower())
    out |= {str(EN_WORDS[w]) for w in words if w in EN_WORDS}
    if "半" in s or "half" in words:
        out |= {"0.5", "1.5", "2.5"} if re.search(r"半|and a half", s.lower()) else {"0.5"}
    return out


def numbers_ok(text: str, quote: str) -> list[str]:
    """Numbers in the patient text that the doctor didn't say in the quote. An empty list means all were said."""
    said = spoken_numbers(quote)
    return sorted(n for n in numbers(text) if n not in said)


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
