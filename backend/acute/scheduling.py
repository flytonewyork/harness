"""Clinic Doctor Availability & Agent Booking Offer Engine for Central & Mid-Levels Family Medical.

Reads real clinic practitioner rosters from PostgreSQL (enriched with the covering GP
schedule) and live tenant-scoped bookings across active `CaseLoop` records so Sam can:
1. Inspect live doctor schedules (busy vs. open slots for Today & Tomorrow),
2. Select the best available doctor and open time for the patient's clinical need,
3. Offer an interactive, confirmable booking card directly inside the patient chat, and
4. Confirm or reschedule bookings atomically with real-time sync to `/workspace` and `/emr`.

Product invariant: NEVER imports `loopbench`. Synthetic data only.
"""
from __future__ import annotations

import copy
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .db import CaseLoop, Database, Message, Practitioner, new_id, utcnow
from .live_bus import LiveHarnessBus

# Canonical clinic doctor roster (augmented with Practitioner rows when present in DB)
_CANONICAL_DOCTORS: tuple[dict[str, Any], ...] = (
    {
        "doctor_id": "doc_dr_wong",
        "name_en": "Dr. Sarah Wong",
        "name_zh": "黃莎拉醫生",
        "initials": "SW",
        "mchk_reg_no": "M16412",
        "specialty_en": "Family Medicine GP · Chronic & Post-Discharge",
        "specialty_zh": "家庭醫學普通科・慢病及出院覆診",
        "languages": "EN · 廣東話",
        "room_en": "Room 2",
        "room_zh": "2 號診室",
        "schedule": {
            "today": [
                {"time": "09:30", "status": "booked"},
                {"time": "10:30", "status": "booked"},
                {"time": "11:30", "status": "open"},
                {"time": "12:00", "status": "booked"},
                {"time": "14:15", "status": "open"},
                {"time": "16:00", "status": "open"},
            ],
            "tomorrow": [
                {"time": "09:30", "status": "open"},
                {"time": "10:30", "status": "booked"},
                {"time": "11:00", "status": "open"},
                {"time": "15:00", "status": "open"},
            ],
        },
    },
    {
        "doctor_id": "doc_dr_lam",
        "name_en": "Dr. Kenneth Lam",
        "name_zh": "林啟賢醫生",
        "initials": "KL",
        "mchk_reg_no": "M14829",
        "specialty_en": "Lead Family Physician · Acute & Cardiovascular",
        "specialty_zh": "主診家庭醫生・急症及心血管管理",
        "languages": "EN · 廣東話",
        "room_en": "Room 1",
        "room_zh": "1 號診室",
        "schedule": {
            "today": [
                {"time": "09:00", "status": "booked"},
                {"time": "10:00", "status": "booked"},
                {"time": "11:00", "status": "open"},
                {"time": "14:00", "status": "open"},
                {"time": "15:00", "status": "booked"},
                {"time": "15:30", "status": "open"},
            ],
            "tomorrow": [
                {"time": "09:00", "status": "open"},
                {"time": "10:30", "status": "open"},
                {"time": "11:30", "status": "booked"},
                {"time": "14:00", "status": "open"},
            ],
        },
    },
    {
        "doctor_id": "doc_dr_cheung",
        "name_en": "Dr. Wing-Yan Cheung",
        "name_zh": "張詠欣醫生",
        "initials": "WC",
        "mchk_reg_no": "M18204",
        "specialty_en": "Family Medicine · Acute & Women's Care",
        "specialty_zh": "家庭醫學普通科・急症及婦女健康",
        "languages": "EN · 廣東話",
        "room_en": "Room 3",
        "room_zh": "3 號診室",
        "schedule": {
            "today": [
                {"time": "10:00", "status": "booked"},
                {"time": "11:15", "status": "open"},
                {"time": "12:15", "status": "booked"},
                {"time": "14:30", "status": "open"},
                {"time": "16:30", "status": "open"},
                {"time": "18:00", "status": "open"},
            ],
            "tomorrow": [
                {"time": "10:00", "status": "open"},
                {"time": "11:30", "status": "open"},
                {"time": "16:00", "status": "open"},
            ],
        },
    },
)

DAY_META: dict[str, dict[str, str]] = {
    "today": {
        "day_key": "today",
        "label_en": "Today, Tue 24 Nov",
        "label_zh": "今日（11月24日 星期二）",
        "short_en": "Today",
        "short_zh": "今日",
    },
    "tomorrow": {
        "day_key": "tomorrow",
        "label_en": "Tomorrow, Wed 25 Nov",
        "label_zh": "明日（11月25日 星期三）",
        "short_en": "Tomorrow",
        "short_zh": "明日",
    },
}

_JOURNEY_BOOKING_PROFILE: dict[str, dict[str, Any]] = {
    "chan": {
        "preferred_doctors": ["doc_dr_wong", "doc_dr_lam", "doc_dr_cheung"],
        "preferred_times": ["11:30", "14:15", "11:00"],
        "visit_type_en": "Post-Discharge Medication Review",
        "visit_type_zh": "出院藥物核對覆診",
        "duration_min": 20,
        "modality": "in_clinic",
        "allow_teleconsult": False,
        "prep_note_en": "Please bring all QEH discharge papers and home medicine bags",
        "prep_note_zh": "請帶齊伊利沙伯醫院出院紙及所有醫院與屋企藥袋",
        "reason_en": "Earliest open post-discharge slot with {doctor}, who is reviewing your QEH summary today",
        "reason_zh": "{doctor}今日最早空檔，並正跟進你的伊利沙伯醫院出院藥物摘要",
    },
    "wingyan": {
        "preferred_doctors": ["doc_dr_lam", "doc_dr_wong", "doc_dr_cheung"],
        "preferred_times": ["11:00", "11:30", "11:15", "14:00"],
        "visit_type_en": "Same-Day GP Consult & Sick Note",
        "visit_type_zh": "即日醫生診症及病假證明",
        "duration_min": 15,
        "modality": "teleconsult",
        "allow_teleconsult": True,
        "prep_note_en": "Teleconsult link is sent 10 minutes before; switch to In-Clinic anytime if you prefer to come in",
        "prep_note_zh": "視像連結會於診症前 10 分鐘發送；如需親臨診所亦可隨時切換",
        "reason_en": "Earliest open same-day slot with {doctor} after checking all 3 doctors' calendars",
        "reason_zh": "已查閱 3 位醫生時間表，為你挑選{doctor}今日最早空檔",
    },
    "tang_uti": {
        "preferred_doctors": ["doc_dr_cheung", "doc_dr_wong", "doc_dr_lam"],
        "preferred_times": ["11:15", "11:30", "14:30"],
        "visit_type_en": "Same-Day Acute Consult",
        "visit_type_zh": "即日急症診症",
        "duration_min": 15,
        "modality": "teleconsult",
        "allow_teleconsult": True,
        "prep_note_en": "Teleconsult link is sent 10 minutes before; quick urine dipstick is available if you choose In-Clinic",
        "prep_note_zh": "視像連結會於診症前 10 分鐘發送；如選親臨診所可即時安排小便試紙檢查",
        "reason_en": "Earliest open slot with {doctor} (Acute & Women's Care) today",
        "reason_zh": "{doctor}（急症及婦女健康）今日最早空檔",
    },
    "lau_chest": {
        "preferred_doctors": ["doc_dr_wong", "doc_dr_lam", "doc_dr_cheung"],
        "preferred_times": ["14:15", "14:00", "11:30"],
        "visit_type_en": "GP Cardiovascular & BP Review",
        "visit_type_zh": "心血管及血壓覆診",
        "duration_min": 20,
        "modality": "teleconsult",
        "allow_teleconsult": True,
        "prep_note_en": "Have your home blood pressure log and current tablets ready (switch to In-Clinic if you want your flu shot or ECG at the same visit)",
        "prep_note_zh": "請準備好近期家居血壓紀錄及現正服用的藥物（如需同日打流感針或做心電圖，可改選親臨診所）",
        "reason_en": "20-min teleconsult review with {doctor} for BP, lipid & preventive review (or switch to In-Clinic for same-day flu shot)",
        "reason_zh": "為你挑選{doctor} 20 分鐘視像覆診空檔，一併檢視血壓、膽固醇及預防檢查（或可改選親臨診所同日打流感針）",
    },
}

_DEFAULT_BOOKING_PROFILE: dict[str, Any] = {
    "preferred_doctors": ["doc_dr_wong", "doc_dr_lam", "doc_dr_cheung"],
    "preferred_times": ["11:30", "11:00", "11:15", "14:15"],
    "visit_type_en": "GP Consultation",
    "visit_type_zh": "普通科醫生診症",
    "duration_min": 15,
    "modality": "in_clinic",
    "allow_teleconsult": True,
    "prep_note_en": "Please arrive 5 minutes early with your HKID",
    "prep_note_zh": "請提早 5 分鐘到達並帶備香港身份證",
    "reason_en": "Earliest open GP slot found across today's clinic availability",
    "reason_zh": "已查閱今日診所醫生空檔，挑選最早合適時段",
}

_NURSE_PREVENTIVE_PROVIDER: dict[str, Any] = {
    "doctor_id": "nurse_chan",
    "name_en": "Nurse Rachel Chan (RN)",
    "name_zh": "陳嘉敏註冊護士",
    "initials": "RC",
    "mchk_reg_no": "RN-HK-48219",
    "specialty_en": "Preventive Health · Blood Draw & Vaccinations",
    "specialty_zh": "預防保健・抽血及疫苗接種",
    "languages": "EN · 廣東話",
    "room_en": "Nurse Treatment Room",
    "room_zh": "護士治療室",
    "schedule": {
        "today": [
            {"time": "10:00", "status": "booked"},
            {"time": "11:30", "status": "open"},
            {"time": "15:00", "status": "open"},
        ],
        "tomorrow": [
            {"time": "09:00", "status": "open"},
            {"time": "10:00", "status": "open"},
            {"time": "11:00", "status": "booked"},
            {"time": "14:30", "status": "open"},
        ],
    },
}

_PREVENTIVE_BOOKING_PROFILE: dict[str, Any] = {
    "preferred_doctors": ["nurse_chan", "doc_dr_wong", "doc_dr_lam"],
    "preferred_times": ["09:00", "10:00", "11:30", "14:30"],
    "visit_type_en": "Preventive Blood Panel & Flu Shot",
    "visit_type_zh": "預防健康抽血及流感針",
    "duration_min": 15,
    "modality": "in_clinic",
    "allow_teleconsult": False,
    "prep_note_en": "Fast 8–10 hours (water OK) for baseline blood panel; seasonal flu shot bundled in the same 15-min nurse visit",
    "prep_note_zh": "建議空腹 8 至 10 小時（可飲清水）以便抽血化驗，同場 15 分鐘護士時段一併接種流感針",
    "reason_en": "Morning nurse preventive slot selected with {doctor} so blood tests and flu shot are bundled cleanly without doctor wait",
    "reason_zh": "已為你挑選{doctor}早段預防保健空檔，一次過完成抽血化驗及流感疫苗接種",
}

_BOOKING_INTENT_RE = re.compile(
    r"\b(book|booking|appointment|appointments|schedule|slot|slots|available|availability|"
    r"see a doctor|see the doctor|come in|in-clinic|in clinic|teleconsult|visit|follow[- ]?up|"
    r"reschedule|change time|another time|earlier|later|tomorrow|morning|afternoon)\b|"
    r"(預約|約醫生|約時間|覆診|睇醫生|幾點|時段|空檔|改期|轉時間|今日有冇位|聽日)",
    re.I,
)

_RESCHEDULE_INTENT_RE = re.compile(
    r"\b(reschedule|change (?:my )?(?:time|slot|doctor|appointment)|different (?:time|doctor)|"
    r"another (?:time|slot|doctor)|book another|move (?:my )?appointment|earlier|later)\b|"
    r"(改期|改時間|轉時間|轉醫生|換個時間|約另一個)",
    re.I,
)


def make_slot_id(doctor_id: str, day_key: str, hhmm: str) -> str:
    return f"{doctor_id}__{day_key}__{hhmm.replace(':', '')}"


def _active_booked_slots(session: Session, tenant_id: str, exclude_case_key: str | None = None) -> dict[str, str]:
    """Return mapping of `slot_id -> case_key` for slots actively booked by live cases in this tenant."""
    taken: dict[str, str] = {}
    cases = session.scalars(select(CaseLoop).where(CaseLoop.tenant_id == tenant_id)).all()
    for c in cases:
        if exclude_case_key and c.case_key == exclude_case_key:
            continue
        appt = (c.summary_json or {}).get("booked_appointment")
        if isinstance(appt, dict) and appt.get("status") == "confirmed" and appt.get("slot_id"):
            taken[str(appt["slot_id"])] = c.case_key
    return taken


def build_clinic_availability(
    session: Session,
    tenant_id: str,
    *,
    for_case_key: str | None = None,
    include_preventive_nurse: bool = False,
) -> list[dict[str, Any]]:
    """Build live doctor roster with real busy/open slots across Today & Tomorrow."""
    # Pull any matching Practitioner rows from DB to stay grounded in real MCHK/names
    pracs = {
        p.id: p
        for p in session.scalars(
            select(Practitioner).where(Practitioner.tenant_id == tenant_id, Practitioner.role.in_(("clinician", "nurse")))
        ).all()
    }
    taken_by_other = _active_booked_slots(session, tenant_id, exclude_case_key=for_case_key)

    # Also check if this case itself already holds a slot
    own_slot_id: str | None = None
    if for_case_key:
        own_case = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == for_case_key)
        )
        own_appt = ((own_case.summary_json or {}).get("booked_appointment") if own_case else None) or {}
        if own_appt.get("status") == "confirmed":
            own_slot_id = str(own_appt.get("slot_id") or "")

    providers_src = (_NURSE_PREVENTIVE_PROVIDER, *_CANONICAL_DOCTORS) if include_preventive_nurse else _CANONICAL_DOCTORS
    roster: list[dict[str, Any]] = []
    for base in providers_src:
        doc_id = base["doctor_id"]
        prac = pracs.get(doc_id)
        days_out: dict[str, list[dict[str, Any]]] = {}
        open_today = 0
        open_total = 0

        for day_key in ("today", "tomorrow"):
            slots_out: list[dict[str, Any]] = []
            for raw_slot in base["schedule"].get(day_key, []):
                hhmm = raw_slot["time"]
                sid = make_slot_id(doc_id, day_key, hhmm)
                base_open = raw_slot["status"] == "open"
                if sid in taken_by_other:
                    available = False
                    slot_state = "booked"
                elif sid == own_slot_id:
                    available = True
                    slot_state = "booked_by_you"
                elif base_open:
                    available = True
                    slot_state = "open"
                else:
                    available = False
                    slot_state = "booked"

                if available:
                    open_total += 1
                    if day_key == "today":
                        open_today += 1

                slots_out.append({
                    "slot_id": sid,
                    "day_key": day_key,
                    "day_label_en": DAY_META[day_key]["label_en"],
                    "day_label_zh": DAY_META[day_key]["label_zh"],
                    "short_day_en": DAY_META[day_key]["short_en"],
                    "short_day_zh": DAY_META[day_key]["short_zh"],
                    "time": hhmm,
                    "available": available,
                    "state": slot_state,
                })
            days_out[day_key] = slots_out

        roster.append({
            "doctor_id": doc_id,
            "name_en": prac.name_en if prac and prac.name_en else base["name_en"],
            "name_zh": prac.name_zh if prac and prac.name_zh else base["name_zh"],
            "initials": base["initials"],
            "mchk_reg_no": prac.mchk_reg_no if prac and prac.mchk_reg_no else base["mchk_reg_no"],
            "specialty_en": base["specialty_en"],
            "specialty_zh": base["specialty_zh"],
            "languages": base["languages"],
            "room_en": base["room_en"],
            "room_zh": base["room_zh"],
            "open_today_count": open_today,
            "open_total_count": open_total,
            "days": days_out,
        })
    return roster


def format_availability_for_prompt(roster: list[dict[str, Any]]) -> str:
    """Compact 1-line live availability string for the LLM Registrar prompt (<70 tokens)."""
    parts: list[str] = []
    for doc in roster:
        today_open = [s["time"] for s in doc["days"].get("today", []) if s["available"]]
        if today_open:
            parts.append(f"{doc['name_en']} ({', '.join(today_open[:3])})")
    return "Live open slots today: " + "; ".join(parts) if parts else "Live open slots today: check tomorrow schedule."


def should_attach_booking_offer(
    *,
    j_key: str,
    patient_text: str,
    existing_summary: dict[str, Any],
    new_state: str,
    has_signed: bool,
    llm_requested_booking: bool = False,
) -> bool:
    """Decide whether Sam should attach an interactive BookingOfferCard to this turn."""
    # Day+3 preventive checkup handoff explicitly requests a fresh preventive nurse booking card
    if existing_summary.get("_attach_preventive_booking"):
        return True

    text = (patient_text or "").strip()
    explicit_booking = bool(_BOOKING_INTENT_RE.search(text))
    explicit_reschedule = bool(_RESCHEDULE_INTENT_RE.search(text))

    booked = existing_summary.get("booked_appointment")
    already_confirmed = isinstance(booked, dict) and booked.get("status") == "confirmed"
    if already_confirmed:
        # Only re-offer if the patient explicitly asks to reschedule / pick another time
        return explicit_reschedule

    existing_chat = existing_summary.get("original_chat") or []
    already_offered = any(
        isinstance(m, dict) and isinstance(m.get("booking_offer"), dict)
        for m in existing_chat
    )

    if j_key in ("wingyan", "tang_uti") and new_state == "running":
        # Wait until Sam finishes red-flag & home-vitals history taking before offering a booking slot
        return False

    if explicit_booking or llm_requested_booking:
        # Avoid back-to-back duplicate card on the exact previous turn unless they asked to reschedule/pick another time
        if already_offered and not explicit_reschedule:
            last_sam = next(
                (m for m in reversed(existing_chat) if isinstance(m, dict) and m.get("actor") == "Sam"),
                None,
            )
            if last_sam and isinstance(last_sam.get("booking_offer"), dict):
                return False
        return True

    # All 4 flagship cases proceed conversationally without abruptly prompting for a booking during intake.
    # Booking cards attach when the patient explicitly asks to book, or post-sign (Case 4 post-tests / Day+3 preventive).
    return False


def select_booking_offer(
    session: Session,
    tenant_id: str,
    *,
    j_key: str,
    patient_text: str = "",
    preferred_doctor_id: str | None = None,
    preferred_slot_id: str | None = None,
    purpose: str = "standard",
) -> dict[str, Any]:
    """Inspect live clinic availability, pick the best open doctor/nurse & time, and build a BookingOfferCard payload."""
    is_preventive = purpose == "preventive_checkup"
    roster = build_clinic_availability(
        session,
        tenant_id,
        for_case_key=j_key,
        include_preventive_nurse=is_preventive,
    )
    profile = _PREVENTIVE_BOOKING_PROFILE if is_preventive else _JOURNEY_BOOKING_PROFILE.get(j_key, _DEFAULT_BOOKING_PROFILE)
    low = (patient_text or "").lower()

    # Check if patient explicitly asked for tomorrow, afternoon, or teleconsult
    prefer_day = "tomorrow" if (is_preventive or any(k in low for k in ("tomorrow", "聽日", "明日"))) else "today"
    prefer_afternoon = bool(re.search(r"\b(?:afternoon|pm)\b|下午|晏晝", low))
    prefer_teleconsult = profile["allow_teleconsult"] and any(
        k in low for k in ("teleconsult", "video", "online", "remote", "視像", "網上診症")
    )

    # Build ordered preference list of doctors
    doc_order = list(profile["preferred_doctors"])
    if preferred_doctor_id and preferred_doctor_id in {d["doctor_id"] for d in roster}:
        doc_order = [preferred_doctor_id] + [d for d in doc_order if d != preferred_doctor_id]
    # Also honour explicit doctor name mention in patient text
    if any(k in low for k in ("kenneth", "dr lam", "dr. lam", "林醫生")):
        doc_order = ["doc_dr_lam"] + [d for d in doc_order if d != "doc_dr_lam"]
    elif any(k in low for k in ("cheung", "wing-yan", "張醫生")):
        doc_order = ["doc_dr_cheung"] + [d for d in doc_order if d != "doc_dr_cheung"]
    elif any(k in low for k in ("sarah", "dr wong", "dr. wong", "黃醫生")):
        doc_order = ["doc_dr_wong"] + [d for d in doc_order if d != "doc_dr_wong"]

    by_id = {d["doctor_id"]: d for d in roster}

    chosen_doc: dict[str, Any] | None = None
    chosen_slot: dict[str, Any] | None = None

    # 1. Exact preferred_slot_id if open
    if preferred_slot_id:
        for d in roster:
            for dk in ("today", "tomorrow"):
                for sl in d["days"].get(dk, []):
                    if sl["slot_id"] == preferred_slot_id and sl["available"]:
                        chosen_doc, chosen_slot = d, sl
                        break
                if chosen_slot:
                    break
            if chosen_slot:
                break

    # 2. Match preferred doctors + preferred day/time window
    if not chosen_slot:
        day_priority = [prefer_day, "tomorrow" if prefer_day == "today" else "today"]
        for dk in day_priority:
            for doc_id in doc_order:
                d = by_id.get(doc_id)
                if not d:
                    continue
                open_slots = [s for s in d["days"].get(dk, []) if s["available"]]
                if not open_slots:
                    continue
                if prefer_afternoon:
                    pm_slots = [s for s in open_slots if int(s["time"].split(":")[0]) >= 14]
                    if pm_slots:
                        chosen_doc, chosen_slot = d, pm_slots[0]
                        break
                # Check profile's preferred times first
                for pt in profile.get("preferred_times", []):
                    hit = next((s for s in open_slots if s["time"] == pt), None)
                    if hit:
                        chosen_doc, chosen_slot = d, hit
                        break
                if chosen_slot:
                    break
                chosen_doc, chosen_slot = d, open_slots[0]
                break
            if chosen_slot:
                break

    # Ultimate safety fallback (should always have open slots)
    if not chosen_doc or not chosen_slot:
        chosen_doc = roster[0]
        chosen_slot = chosen_doc["days"]["today"][0]

    total_open_today = sum(d["open_today_count"] for d in roster)
    total_busy_today = sum(
        sum(1 for s in d["days"].get("today", []) if not s["available"])
        for d in roster
    )
    modality = "teleconsult" if prefer_teleconsult else profile["modality"]

    reason_en = profile["reason_en"].format(doctor=chosen_doc["name_en"])
    reason_zh = profile["reason_zh"].format(doctor=chosen_doc["name_zh"])
    if prefer_day == "tomorrow" and chosen_slot["day_key"] == "tomorrow":
        reason_en = f"Earliest open slot tomorrow with {chosen_doc['name_en']} after checking all {len(roster)} doctors"
        reason_zh = f"已查閱 {len(roster)} 位醫生明日時間表，為你挑選{chosen_doc['name_zh']}最早空檔"
    elif prefer_afternoon:
        reason_en = f"Earliest open afternoon slot with {chosen_doc['name_en']} ({chosen_slot['time']})"
        reason_zh = f"為你挑選{chosen_doc['name_zh']}下午最早空檔（{chosen_slot['time']}）"

    return {
        "offer_id": new_id("bof"),
        "status": "offered",
        "booking_ref": None,
        "confirmed_at": None,
        "checked_doctors_count": len(roster),
        "open_today_count": total_open_today,
        "busy_today_count": total_busy_today,
        "checked_summary_en": f"Checked clinic schedule · {len(roster)} doctors · {total_open_today} open slots today",
        "checked_summary_zh": f"已查閱診所時間表 · {len(roster)} 位醫生 · 今日 {total_open_today} 個空檔",
        "visit_type_en": profile["visit_type_en"],
        "visit_type_zh": profile["visit_type_zh"],
        "duration_min": profile["duration_min"],
        "modality": modality,
        "allow_async_message": bool(profile.get("allow_async_message", False)),
        "allow_teleconsult": bool(profile["allow_teleconsult"]),
        "location_en": (
            "In-Chat Async Doctor Review · Instant Opinion"
            if modality == "async_message"
            else f"Central & Mid-Levels Family Medical ({chosen_doc['room_en']})"
        ),
        "location_zh": (
            "對話內非同步醫生審閱・即時意見"
            if modality == "async_message"
            else f"中環及半山家庭醫務中心（{chosen_doc['room_zh']}）"
        ),
        "prep_note_en": profile["prep_note_en"],
        "prep_note_zh": profile["prep_note_zh"],
        "selection_reason_en": reason_en,
        "selection_reason_zh": reason_zh,
        "recommended": {
            "doctor_id": chosen_doc["doctor_id"],
            "doctor_name": chosen_doc["name_en"],
            "doctor_name_zh": chosen_doc["name_zh"],
            "initials": chosen_doc["initials"],
            "mchk_reg_no": chosen_doc["mchk_reg_no"],
            "specialty_en": chosen_doc["specialty_en"],
            "specialty_zh": chosen_doc["specialty_zh"],
            "languages": chosen_doc["languages"],
            "room_en": chosen_doc["room_en"],
            "room_zh": chosen_doc["room_zh"],
            "slot_id": chosen_slot["slot_id"],
            "day_key": chosen_slot["day_key"],
            "day_label_en": chosen_slot["day_label_en"],
            "day_label_zh": chosen_slot["day_label_zh"],
            "short_day_en": chosen_slot["short_day_en"],
            "short_day_zh": chosen_slot["short_day_zh"],
            "time": chosen_slot["time"],
        },
        "doctors": roster,
    }


def get_live_availability_payload(
    db: Database,
    *,
    tenant_id: str,
    journey_key: str | None = None,
) -> dict[str, Any]:
    """Return full clinic doctor availability matrix + optional recommended pick for a journey."""
    with db.tenant_session(tenant_id) as session:
        roster = build_clinic_availability(session, tenant_id, for_case_key=journey_key)
        offer = (
            select_booking_offer(session, tenant_id, j_key=journey_key)
            if journey_key
            else None
        )
        return {
            "ok": True,
            "clinic_name": "Central & Mid-Levels Family Medical",
            "clinic_name_zh": "中環及半山家庭醫務中心",
            "days": [DAY_META["today"], DAY_META["tomorrow"]],
            "doctors": roster,
            "recommended_offer": offer,
        }


def confirm_live_chat_booking(
    db: Database,
    *,
    tenant_id: str,
    journey_key: str,
    patient_id: str,
    patient_name: str,
    doctor_id: str,
    slot_id: str,
    modality: str = "in_clinic",
    offer_id: str | None = None,
    lang: str = "en",
    live_bus: LiveHarnessBus,
    hhmm: str,
    session_epoch: str,
) -> dict[str, Any]:
    """Confirm or reschedule a patient's chosen doctor & slot, updating phone chat + laptop /workspace live."""
    is_zh = str(lang or "en").lower().startswith("zh")
    clean_modality = (
        "async_message"
        if modality == "async_message"
        else ("teleconsult" if modality == "teleconsult" else "in_clinic")
    )

    with db.tenant_session(tenant_id) as session:
        case = session.scalar(
            select(CaseLoop).where(CaseLoop.tenant_id == tenant_id, CaseLoop.case_key == journey_key)
        )
        if case is None:
            raise ValueError(f"Case for journey '{journey_key}' not found")

        is_nurse_preventive = doctor_id == "nurse_chan"
        roster = build_clinic_availability(
            session,
            tenant_id,
            for_case_key=journey_key,
            include_preventive_nurse=is_nurse_preventive,
        )
        target_doc = next((d for d in roster if d["doctor_id"] == doctor_id), None)
        if target_doc is None:
            raise ValueError(f"Doctor '{doctor_id}' not found in clinic roster")

        target_slot: dict[str, Any] | None = None
        for dk in ("today", "tomorrow"):
            for sl in target_doc["days"].get(dk, []):
                if sl["slot_id"] == slot_id:
                    target_slot = sl
                    break
            if target_slot:
                break

        if target_slot is None and clean_modality == "async_message":
            # Async review does not require a physical clinic slot; fall back to doctor's first open slot metadata
            for dk in ("today", "tomorrow"):
                open_sls = [s for s in target_doc["days"].get(dk, []) if s["available"]]
                if open_sls:
                    target_slot = open_sls[0]
                    break
        if target_slot is None:
            raise ValueError(f"Slot '{slot_id}' not found for {target_doc['name_en']}")
        if clean_modality != "async_message" and not target_slot["available"]:
            raise PermissionError(
                f"{target_doc['name_en']} at {target_slot['time']} is already booked. Please select another open slot."
            )

        summary = copy.deepcopy(case.summary_json or {})
        profile = _PREVENTIVE_BOOKING_PROFILE if is_nurse_preventive else _JOURNEY_BOOKING_PROFILE.get(journey_key, _DEFAULT_BOOKING_PROFILE)
        prev_booking = summary.get("booked_appointment")
        is_reschedule = isinstance(prev_booking, dict) and prev_booking.get("status") == "confirmed"
        booking_ref = (
            str(prev_booking.get("booking_ref"))
            if is_reschedule and prev_booking.get("booking_ref")
            else f"APT-2411-{new_id('bk')[-4:].upper()}"
        )
        confirmed_at = utcnow().isoformat()

        if clean_modality == "async_message":
            modality_en = "Message (Fastest) · Async Review"
            modality_zh = "訊息審閱（最快）"
            location_en = "In-Chat Async Doctor Review · Instant Opinion"
            location_zh = "對話內非同步醫生審閱・即時意見"
        elif clean_modality == "teleconsult":
            modality_en = "Video Teleconsult"
            modality_zh = "視像遙距診症"
            location_en = "Secure Clinic Video Link (sent 10 min before)"
            location_zh = "加密診所視像連結（診症前 10 分鐘發送）"
        else:
            modality_en = "In-Clinic"
            modality_zh = "親臨診所"
            location_en = f"Central & Mid-Levels Family Medical ({target_doc['room_en']})"
            location_zh = f"中環及半山家庭醫務中心（{target_doc['room_zh']}）"

        booked_record = {
            "status": "confirmed",
            "booking_ref": booking_ref,
            "confirmed_at": confirmed_at,
            "doctor_id": target_doc["doctor_id"],
            "doctor_name": target_doc["name_en"],
            "doctor_name_zh": target_doc["name_zh"],
            "initials": target_doc["initials"],
            "mchk_reg_no": target_doc["mchk_reg_no"],
            "specialty_en": target_doc["specialty_en"],
            "specialty_zh": target_doc["specialty_zh"],
            "languages": target_doc["languages"],
            "room_en": target_doc["room_en"],
            "room_zh": target_doc["room_zh"],
            "slot_id": target_slot["slot_id"],
            "day_key": target_slot["day_key"],
            "day_label_en": target_slot["day_label_en"],
            "day_label_zh": target_slot["day_label_zh"],
            "short_day_en": target_slot["short_day_en"],
            "short_day_zh": target_slot["short_day_zh"],
            "time": target_slot["time"],
            "modality": clean_modality,
            "modality_label_en": modality_en,
            "modality_label_zh": modality_zh,
            "visit_type_en": profile["visit_type_en"],
            "visit_type_zh": profile["visit_type_zh"],
            "duration_min": profile["duration_min"],
            "location_en": location_en,
            "location_zh": location_zh,
            "prep_note_en": profile["prep_note_en"],
            "prep_note_zh": profile["prep_note_zh"],
        }
        summary["booked_appointment"] = booked_record

        # Update live roster snapshot so this slot shows booked_by_you
        updated_roster = copy.deepcopy(roster)
        for d in updated_roster:
            for dk in ("today", "tomorrow"):
                for sl in d["days"].get(dk, []):
                    if sl["slot_id"] == target_slot["slot_id"]:
                        sl["state"] = "booked_by_you"
                        sl["available"] = True
                    elif sl.get("state") == "booked_by_you":
                        sl["state"] = "open"
                        sl["available"] = True

        # Update the matching booking_offer inside original_chat in-place
        chat = list(summary.get("original_chat") or [])
        updated_any_offer = False
        for m in reversed(chat):
            if not isinstance(m, dict) or not isinstance(m.get("booking_offer"), dict):
                continue
            bo = m["booking_offer"]
            if offer_id and bo.get("offer_id") != offer_id and updated_any_offer:
                continue
            if offer_id and bo.get("offer_id") != offer_id:
                continue
            bo["status"] = "confirmed"
            bo["booking_ref"] = booking_ref
            bo["confirmed_at"] = confirmed_at
            bo["modality"] = clean_modality
            bo["location_en"] = location_en
            bo["location_zh"] = location_zh
            bo["recommended"] = {
                "doctor_id": target_doc["doctor_id"],
                "doctor_name": target_doc["name_en"],
                "doctor_name_zh": target_doc["name_zh"],
                "initials": target_doc["initials"],
                "mchk_reg_no": target_doc["mchk_reg_no"],
                "specialty_en": target_doc["specialty_en"],
                "specialty_zh": target_doc["specialty_zh"],
                "languages": target_doc["languages"],
                "room_en": target_doc["room_en"],
                "room_zh": target_doc["room_zh"],
                "slot_id": target_slot["slot_id"],
                "day_key": target_slot["day_key"],
                "day_label_en": target_slot["day_label_en"],
                "day_label_zh": target_slot["day_label_zh"],
                "short_day_en": target_slot["short_day_en"],
                "short_day_zh": target_slot["short_day_zh"],
                "time": target_slot["time"],
            }
            bo["doctors"] = updated_roster
            updated_any_offer = True
            break

        # If no specific offer_id matched, update the latest offer card
        if not updated_any_offer:
            for m in reversed(chat):
                if isinstance(m, dict) and isinstance(m.get("booking_offer"), dict):
                    bo = m["booking_offer"]
                    bo["status"] = "confirmed"
                    bo["booking_ref"] = booking_ref
                    bo["confirmed_at"] = confirmed_at
                    bo["modality"] = clean_modality
                    bo["location_en"] = location_en
                    bo["location_zh"] = location_zh
                    bo["recommended"] = {
                        "doctor_id": target_doc["doctor_id"],
                        "doctor_name": target_doc["name_en"],
                        "doctor_name_zh": target_doc["name_zh"],
                        "initials": target_doc["initials"],
                        "mchk_reg_no": target_doc["mchk_reg_no"],
                        "specialty_en": target_doc["specialty_en"],
                        "specialty_zh": target_doc["specialty_zh"],
                        "languages": target_doc["languages"],
                        "room_en": target_doc["room_en"],
                        "room_zh": target_doc["room_zh"],
                        "slot_id": target_slot["slot_id"],
                        "day_key": target_slot["day_key"],
                        "day_label_en": target_slot["day_label_en"],
                        "day_label_zh": target_slot["day_label_zh"],
                        "short_day_en": target_slot["short_day_en"],
                        "short_day_zh": target_slot["short_day_zh"],
                        "time": target_slot["time"],
                    }
                    bo["doctors"] = updated_roster
                    updated_any_offer = True
                    break

        # Add a crisp confirmation message from Sam in the chat thread (bilingual for live EN/ZH toggle)
        if clean_modality == "async_message":
            confirm_en = f"Confirmed doctor review with {target_doc['name_en']} (Ref {booking_ref}). Your symptoms and home vitals are already on the doctor's desk — your signed plan will arrive right here shortly."
            confirm_zh = f"已確認由 {target_doc['name_zh']} 審閱（參考編號 {booking_ref}）。你的症狀同家居讀數已經放上醫生工作台，簽署後會直接喺呢度通知你。"
            sched_label = f"Doctor Review · {target_doc['name_en']} ({booking_ref})"
            tl_label = f"SchedulingAgent · Confirmed doctor review with {target_doc['name_en']} ({booking_ref})."
        else:
            confirm_en = f"{'Updated and confirmed' if is_reschedule else 'Booked'} your {modality_en.lower()} appointment with {target_doc['name_en']} for {target_slot['short_day_en']} at {target_slot['time']} (Ref {booking_ref}). I've synced this straight to the clinic schedule and clinician workspace."
            confirm_zh = f"已為你{'更改並' if is_reschedule else ''}確認預約：{target_slot['short_day_zh']} {target_slot['time']} · {target_doc['name_zh']}（{modality_zh}，參考編號 {booking_ref}）。我已同步更新診所時間表及醫生摘要。"
            sched_label = f"{target_slot['short_day_en']} {target_slot['time']} · {target_doc['name_en']} ({modality_en} · {profile['visit_type_en']} · {booking_ref})"
            tl_label = f"SchedulingAgent · {'Rescheduled' if is_reschedule else 'Booked'} {target_doc['name_en']} ({target_slot['short_day_en']} {target_slot['time']}, {modality_en}, {booking_ref})."
        confirm_text = confirm_zh if is_zh else confirm_en
        chat.append({
            "actor": "Sam",
            "role": "assistant",
            "time": hhmm,
            "text": confirm_text,
            "text_en": confirm_en,
            "text_zh": confirm_zh,
            "lang": "zh-HK" if is_zh else "en",
        })
        summary["original_chat"] = chat

        # Sync into `/workspace` right-rail ScheduledCard (`scheduled_tasks`)
        scheduled_tasks = [
            t for t in (summary.get("scheduled_tasks") or [])
            if not (isinstance(t, dict) and t.get("booking_managed"))
        ]
        scheduled_tasks.insert(
            0,
            {
                "status": "booked",
                "booking_managed": True,
                "booking_ref": booking_ref,
                "doctor_name": target_doc["name_en"],
                "modality": modality_en,
                "label": sched_label,
            },
        )
        summary["scheduled_tasks"] = scheduled_tasks

        # Record in Private Doctor & Sam thread + Case Timeline + Message log
        timeline = list(case.timeline_json or [])
        timeline.append({
            "date": f"Today {hhmm}",
            "label": tl_label,
            "agent": "SchedulingAgent",
        })
        case.summary_json = summary
        case.timeline_json = timeline
        flag_modified(case, "summary_json")
        flag_modified(case, "timeline_json")
        case.updated_at = utcnow()

        session.add(
            Message(
                id=new_id("msg"),
                tenant_id=tenant_id,
                patient_id=patient_id,
                direction="out",
                sender_role="assistant",
                body_text=confirm_text,
            )
        )
        session.flush()
        case_id = case.id

    live_bus.append_tool_call(
        patient_id,
        {
            "agent": "SchedulingAgent",
            "tool": "scheduling.confirm_booking",
            "authority": "BOOK",
            "emr_target": "clinic_schedule",
            "summary": f"Confirmed {target_doc['name_en']} · {target_slot['short_day_en']} {target_slot['time']} ({modality_en}, {booking_ref}) for {patient_name}",
        },
    )
    live_bus.publish(
        "booking.confirmed",
        patient_id=patient_id,
        data={
            "case_id": case_id,
            "journey_key": journey_key,
            "booking": booked_record,
        },
    )
    live_bus.publish(
        "case.updated",
        patient_id=patient_id,
        data={"case_id": case_id, "journey_key": journey_key, "state": case.state},
    )

    return {
        "ok": True,
        "session_epoch": session_epoch,
        "case_id": case_id,
        "booking": booked_record,
        "messages": chat,
        "reply": confirm_text,
    }
