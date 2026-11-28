"""Unified external integration adapters & fault-injecting simulators (Section 6 & 17).

All five adapters implement `AdapterContract` and accept a deterministic `FaultInjector`:
1. `WhatsAppDoorbellAdapter` — bilingual EN/廣東話 non-clinical doorbell templates with mandatory
   `WhatsAppLeakageGuard.assert_clean(body)` on 100% of sends, Meta Cloud API webhook HMAC-SHA256
   verification, and live Meta Graph API dispatch when `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_ID` are set.
2. `BookingAdapter` — deterministic teleconsult slots with idempotency keys & fault injection.
3. `PharmacyFulfilmentAdapter` — single HK fulfilment partner supporting `delivery` and `pickup`,
   idempotent order submission, strict fulfilment state transitions, and webhook verification.
4. `FluVaxAdapter` — partner clinic seasonal quadrivalent influenza vaccination (`CVX 150/171/185`)
   slot listing and idempotent booking.
5. `CorporateBillingAdapter` — aggregate corporate invoice & fulfilment reconciliation with
   k-anonymity suppression and zero individual patient PII.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

import httpx

from .contracts import AdapterError, AdapterResult, FaultInjector, verify_hmac_sha256
from .security import HKT, WhatsAppLeakageGuard
from .terminology import CVX_VACCINES

# -------------------------------------------------------------------------------------- 1. WhatsApp Doorbell
WA_TEMPLATE_IDS = (
    "welcome_magic_link",
    "otp_code",
    "booking_confirmed",
    "booking_reminder",
    "document_ready",
    "rx_status",
    "followup_48h",
    "new_message",
)

_RX_STATUS_LABELS: dict[str, tuple[str, str]] = {
    "signed": ("Order received", "已收到訂單"),
    "sent_to_pharmacy": ("Sent to partner dispensary", "已傳送至合作配藥中心"),
    "preparing": ("Being prepared", "正在處理中"),
    "out_for_delivery": ("Out for courier delivery", "快遞正在派送"),
    "ready_for_pickup": ("Ready for branch pickup", "已可到門市領取"),
    "completed": ("Completed", "已完成"),
}

_WA_TEMPLATES: dict[str, dict[str, str]] = {
    "welcome_magic_link": {
        "en": "Welcome to {clinic_name}. Open your secure portal link to continue: {url}",
        "zh": "歡迎使用 {clinic_name}。請點擊以下安全連結繼續：{url}",
    },
    "otp_code": {
        "en": "Your one-time verification code is {otp}. It expires in {ttl_minutes} minutes. Do not share this code.",
        "zh": "您的一次性驗證碼為 {otp}，有效期 {ttl_minutes} 分鐘，請勿與他人分享。",
    },
    "booking_confirmed": {
        "en": "Your teleconsultation appointment is confirmed for {slot_label} with {doctor_name}. Open your secure portal: {url}",
        "zh": "您的視像診症預約已確認（{slot_label}，{doctor_name}）。請於預約時間前登入安全網站：{url}",
    },
    "booking_reminder": {
        "en": "Reminder: Your teleconsultation appointment starts at {slot_label} with {doctor_name}. Open your secure portal: {url}",
        "zh": "提示：您的視像診症將於 {slot_label} 開始（{doctor_name}）。請登入安全網站：{url}",
    },
    "document_ready": {
        "en": "A new clinic document is ready in your secure portal: {url}",
        "zh": "您有一份新的診所文件已準備就緒，請登入安全網站查閱：{url}",
    },
    "rx_status": {
        "en": "Your clinic order status has been updated: {status_en}. Open your secure portal for tracking: {url}",
        "zh": "您的診所訂單狀態已更新：{status_zh}。請登入安全網站查閱進度：{url}",
    },
    "followup_48h": {
        "en": "Hi {patient_first_name}, it has been 48 hours since your visit. Please open your secure portal for a quick check-in: {url}",
        "zh": "您好 {patient_first_name}，距離您的診症已過48小時，請登入安全網站完成簡短跟進：{url}",
    },
    "new_message": {
        "en": "You have a new message from your clinic care team in your secure portal: {url}",
        "zh": "您有一則來自診所醫護團隊的新訊息，請登入安全網站查閱：{url}",
    },
}


class WhatsAppDoorbellAdapter:
    """Non-clinical WhatsApp doorbell adapter (Meta Cloud API live or deterministic simulator)."""

    name = "whatsapp_doorbell"
    capabilities = ("send_template", "send_raw", "verify_webhook", "bilingual_utility")

    def __init__(
        self,
        *,
        fault_injector: FaultInjector | None = None,
        token: str | None = None,
        phone_id: str | None = None,
        graph_version: str = "v20.0",
    ) -> None:
        self.faults = fault_injector or FaultInjector()
        self.token = token if token is not None else os.environ.get("WHATSAPP_TOKEN", "")
        self.phone_id = phone_id if phone_id is not None else os.environ.get("WHATSAPP_PHONE_ID", "")
        self.graph_version = graph_version
        self.outbox: list[dict[str, Any]] = []

    @property
    def live_enabled(self) -> bool:
        return bool(self.token and self.phone_id)

    def render_template(
        self,
        template_id: str,
        params: dict[str, Any] | None = None,
        *,
        lang: str = "bilingual",
    ) -> str:
        if template_id not in _WA_TEMPLATES:
            raise AdapterError(f"Unknown WhatsApp template: {template_id}", retryable=False, code="unknown_template")
        raw_params = dict(params or {})
        defaults: dict[str, Any] = {
            "clinic_name": "TMRW HK Clinic",
            "url": "https://portal.tmrw-clinic.hk",
            "otp": "000000",
            "ttl_minutes": 10,
            "slot_label": "upcoming slot",
            "doctor_name": "your clinic doctor",
            "patient_first_name": "there",
        }
        merged = {**defaults, **raw_params}
        if template_id == "rx_status":
            raw_status = str(merged.get("status", "preparing"))
            status_en, status_zh = _RX_STATUS_LABELS.get(raw_status, (raw_status, raw_status))
            merged.setdefault("status_en", status_en)
            merged.setdefault("status_zh", status_zh)

        tpl = _WA_TEMPLATES[template_id]
        norm_lang = lang.strip().lower()
        if norm_lang == "en":
            body = tpl["en"].format(**merged)
        elif norm_lang in ("zh", "zh-hk", "zh_hk", "yue"):
            body = tpl["zh"].format(**merged)
        else:
            body = f"{tpl['en'].format(**merged)}\n{tpl['zh'].format(**merged)}"
        return body

    def send_template(
        self,
        *,
        phone: str,
        template_id: str,
        params: dict[str, Any] | None = None,
        lang: str = "bilingual",
        conversation_id: str = "",
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        body = self.render_template(template_id, params=params, lang=lang)
        return self.send_raw(
            phone=phone,
            body=body,
            template_id=template_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
        )

    def send_raw(
        self,
        *,
        phone: str,
        body: str,
        template_id: str = "custom",
        conversation_id: str = "",
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        # HARD INVARIANT: 100% of outbound WhatsApp messages pass WhatsAppLeakageGuard BEFORE dispatch.
        WhatsAppLeakageGuard.assert_clean(body)

        cached = self.faults.check("whatsapp.send", idempotency_key)
        if cached is not None:
            return cached

        if self.live_enabled:
            ext_ref = self._dispatch_meta_cloud(phone=phone, body=body)
            mode = "meta_cloud_live"
        else:
            ext_ref = f"wamid.sim_{uuid.uuid4().hex[:14]}"
            mode = "simulated"

        record = {
            "external_ref": ext_ref,
            "phone": phone,
            "conversation_id": conversation_id,
            "template_id": template_id,
            "body": body,
            "mode": mode,
            "leakage_passed": True,
            "idempotency_key": idempotency_key or "",
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        self.outbox.append(record)

        result = AdapterResult(
            ok=True,
            external_ref=ext_ref,
            status="sent",
            payload=record,
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def _dispatch_meta_cloud(self, *, phone: str, body: str) -> str:
        url = f"https://graph.facebook.com/{self.graph_version}/{self.phone_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone.lstrip("+"),
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=15.0)
        except httpx.HTTPError as exc:
            raise AdapterError(f"Meta Graph API network error: {exc}", retryable=True, code="meta_network_error") from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            raise AdapterError(f"Meta Graph API transient {resp.status_code}", retryable=True, code="meta_transient")
        if resp.status_code not in (200, 201):
            raise AdapterError(
                f"Meta Graph API error ({resp.status_code}): {resp.text[:200]}",
                retryable=False,
                code="meta_rejected",
            )
        data = resp.json()
        messages = data.get("messages") or []
        return str(messages[0].get("id")) if messages else f"wamid.live_{uuid.uuid4().hex[:12]}"

    def verify_webhook(self, raw_body: bytes, signature: str, secret: str) -> bool:
        """Verify Meta Cloud API `X-Hub-Signature-256` HMAC-SHA256 webhook header."""
        return verify_hmac_sha256(raw_body, signature, secret)

    def health(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": "meta_cloud_live" if self.live_enabled else "simulated",
            "capabilities": list(self.capabilities),
            "outbox_count": len(self.outbox),
            "ok": "whatsapp.send" not in self.faults.permanent_down,
        }


# -------------------------------------------------------------------------------------- 2. Teleconsult Booking
class BookingAdapter:
    """Deterministic teleconsult clinician slot listing & idempotent booking adapter."""

    name = "teleconsult_booking"
    capabilities = ("list_slots", "book_slot", "cancel_booking", "verify_webhook")

    def __init__(self, *, fault_injector: FaultInjector | None = None) -> None:
        self.faults = fault_injector or FaultInjector()
        self._booked_slots: dict[str, dict[str, Any]] = {}  # slot_id -> booking record
        self._bookings_by_ref: dict[str, dict[str, Any]] = {}  # booking_ref -> booking record

    def list_slots(
        self,
        *,
        practitioner_id: str,
        practitioner_name: str = "Dr. Clinic GP",
        date_str: str | None = None,
        count: int = 6,
        duration_minutes: int = 15,
    ) -> list[dict[str, Any]]:
        self.faults.check("booking.list_slots")
        target_day = date.fromisoformat(date_str) if date_str else datetime.now(HKT).date()
        # Deterministic daily schedule starting 09:00 HKT in 30-min cadence
        base_dt = datetime(target_day.year, target_day.month, target_day.day, 9, 0, tzinfo=HKT)
        slots: list[dict[str, Any]] = []
        for idx in range(count):
            start_dt = base_dt + timedelta(minutes=30 * idx)
            end_dt = start_dt + timedelta(minutes=duration_minutes)
            slot_id = f"slot_{practitioner_id}_{target_day.strftime('%Y%m%d')}_{start_dt.strftime('%H%M')}"
            slots.append(
                {
                    "slot_id": slot_id,
                    "practitioner_id": practitioner_id,
                    "practitioner_name": practitioner_name,
                    "start_at": start_dt.isoformat(),
                    "end_at": end_dt.isoformat(),
                    "duration_minutes": duration_minutes,
                    "available": slot_id not in self._booked_slots,
                }
            )
        return slots

    def book_slot(
        self,
        *,
        patient_id: str,
        practitioner_id: str,
        slot_id: str,
        encounter_id: str = "",
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        cached = self.faults.check("booking.book_slot", idempotency_key)
        if cached is not None:
            return cached

        if slot_id in self._booked_slots:
            raise AdapterError(f"Slot {slot_id} is already booked", retryable=False, code="slot_unavailable")

        digest = hashlib.sha256(f"{slot_id}|{patient_id}|{idempotency_key or ''}".encode()).hexdigest()[:12]
        booking_ref = f"bk_{digest}"
        record = {
            "booking_ref": booking_ref,
            "slot_id": slot_id,
            "patient_id": patient_id,
            "practitioner_id": practitioner_id,
            "encounter_id": encounter_id,
            "status": "confirmed",
            "booked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._booked_slots[slot_id] = record
        self._bookings_by_ref[booking_ref] = record

        result = AdapterResult(
            ok=True,
            external_ref=booking_ref,
            status="confirmed",
            payload=record,
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def cancel_booking(self, booking_ref: str, *, idempotency_key: str | None = None) -> AdapterResult:
        cached = self.faults.check("booking.cancel_booking", idempotency_key)
        if cached is not None:
            return cached

        record = self._bookings_by_ref.get(booking_ref)
        if not record:
            raise AdapterError(f"Booking not found: {booking_ref}", retryable=False, code="booking_not_found")
        if record.get("status") != "cancelled":
            record["status"] = "cancelled"
            record["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        self._booked_slots.pop(record["slot_id"], None)

        result = AdapterResult(
            ok=True,
            external_ref=booking_ref,
            status="cancelled",
            payload=dict(record),
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def verify_webhook(self, raw_body: bytes, signature: str, secret: str) -> bool:
        return verify_hmac_sha256(raw_body, signature, secret)

    def health(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "capabilities": list(self.capabilities),
            "active_bookings": len(self._booked_slots),
            "ok": "booking.book_slot" not in self.faults.permanent_down,
        }


# -------------------------------------------------------------------------------------- 3. Pharmacy Fulfilment
class PharmacyFulfilmentAdapter:
    """Single HK pharmacy fulfilment partner supporting `delivery` and `pickup` with strict state machine."""

    name = "pharmacy_fulfilment"
    capabilities = ("submit_order", "advance_status", "get_order", "verify_webhook", "delivery", "pickup")

    VALID_MODES = frozenset({"delivery", "pickup"})
    TRANSITIONS: dict[str, dict[str, tuple[str, ...]]] = {
        "delivery": {
            "signed": ("sent_to_pharmacy",),
            "sent_to_pharmacy": ("preparing",),
            "preparing": ("out_for_delivery",),
            "out_for_delivery": ("completed",),
            "completed": (),
        },
        "pickup": {
            "signed": ("sent_to_pharmacy",),
            "sent_to_pharmacy": ("preparing",),
            "preparing": ("ready_for_pickup",),
            "ready_for_pickup": ("completed",),
            "completed": (),
        },
    }

    def __init__(self, *, fault_injector: FaultInjector | None = None, partner_name: str = "HK Central Partner Pharmacy") -> None:
        self.faults = fault_injector or FaultInjector()
        self.partner_name = partner_name
        self._orders: dict[str, dict[str, Any]] = {}

    def submit_order(
        self,
        *,
        prescription_id: str,
        patient_id: str,
        mode: str,
        items: Sequence[dict[str, Any]],
        delivery_address: str | None = None,
        pickup_branch: str | None = None,
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        if mode not in self.VALID_MODES:
            raise AdapterError(f"Unsupported fulfilment mode: {mode!r}", retryable=False, code="invalid_mode")
        if mode == "delivery" and not (delivery_address and delivery_address.strip()):
            raise AdapterError("delivery_address is required for delivery fulfilment", retryable=False, code="missing_address")
        if not items:
            raise AdapterError("Prescription items cannot be empty", retryable=False, code="empty_items")

        cached = self.faults.check("pharmacy.submit_order", idempotency_key)
        if cached is not None:
            return cached

        digest = hashlib.sha256(f"{prescription_id}|{mode}|{idempotency_key or ''}".encode()).hexdigest()[:12]
        fulfilment_ref = f"phx_{digest}"
        now_iso = datetime.now(timezone.utc).isoformat()
        order = {
            "fulfilment_ref": fulfilment_ref,
            "prescription_id": prescription_id,
            "patient_id": patient_id,
            "partner_name": self.partner_name,
            "mode": mode,
            "delivery_address": delivery_address.strip() if delivery_address else None,
            "pickup_branch": (pickup_branch or "Central Dispensary").strip() if mode == "pickup" else None,
            "items": [dict(i) for i in items],
            "status": "sent_to_pharmacy",
            "tracking_events": [
                {"status": "signed", "at": now_iso, "note": "Doctor signed e-script"},
                {"status": "sent_to_pharmacy", "at": now_iso, "note": f"Dispatched to {self.partner_name}"},
            ],
        }
        self._orders[fulfilment_ref] = order

        result = AdapterResult(
            ok=True,
            external_ref=fulfilment_ref,
            status="sent_to_pharmacy",
            payload=dict(order),
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def advance_status(
        self,
        *,
        fulfilment_ref: str,
        new_status: str,
        note: str = "",
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        cached = self.faults.check("pharmacy.advance_status", idempotency_key)
        if cached is not None:
            return cached

        order = self._orders.get(fulfilment_ref)
        if not order:
            raise AdapterError(f"Pharmacy order not found: {fulfilment_ref}", retryable=False, code="order_not_found")

        mode = order["mode"]
        current = order["status"]
        allowed = self.TRANSITIONS[mode].get(current, ())
        if new_status not in allowed:
            raise AdapterError(
                f"Invalid pharmacy status transition for mode={mode}: '{current}' -> '{new_status}' "
                f"(allowed: {allowed})",
                retryable=False,
                code="invalid_status_transition",
            )

        order["status"] = new_status
        order["tracking_events"].append(
            {
                "status": new_status,
                "at": datetime.now(timezone.utc).isoformat(),
                "note": note or f"Transitioned to {new_status}",
            }
        )
        result = AdapterResult(
            ok=True,
            external_ref=fulfilment_ref,
            status=new_status,
            payload=dict(order),
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def cancel_order(
        self,
        fulfilment_ref: str,
        *,
        reason: str = "Clinician 10s undo",
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        cached = self.faults.check("pharmacy.cancel_order", idempotency_key)
        if cached is not None:
            return cached
        order = self._orders.get(fulfilment_ref)
        if not order:
            raise AdapterError(f"Pharmacy order not found: {fulfilment_ref}", retryable=False, code="order_not_found")
        if order.get("status") != "cancelled":
            order["status"] = "cancelled"
            order["tracking_events"].append(
                {
                    "status": "cancelled",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "note": reason,
                }
            )
        result = AdapterResult(
            ok=True,
            external_ref=fulfilment_ref,
            status="cancelled",
            payload=dict(order),
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def get_order(self, fulfilment_ref: str) -> dict[str, Any] | None:
        order = self._orders.get(fulfilment_ref)
        return dict(order) if order else None

    def verify_webhook(self, raw_body: bytes, signature: str, secret: str) -> bool:
        return verify_hmac_sha256(raw_body, signature, secret)

    def health(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "partner": self.partner_name,
            "capabilities": list(self.capabilities),
            "orders_tracked": len(self._orders),
            "ok": "pharmacy.submit_order" not in self.faults.permanent_down,
        }


# -------------------------------------------------------------------------------------- 4. Flu Vaccination Partner
class FluVaxAdapter:
    """Partner clinic seasonal quadrivalent influenza vaccination (`CVX 150/171/185`) adapter."""

    name = "flu_vax_partner"
    capabilities = ("list_slots", "book_vaccination", "cancel_booking", "verify_webhook")
    SUPPORTED_CVX = frozenset({"150", "171", "185"})

    PARTNER_CLINICS = (
        {"clinic_id": "hk_central", "name": "Central Harbour Medical Centre", "district": "Central"},
        {"clinic_id": "hk_cwb", "name": "Causeway Bay Wellness Clinic", "district": "Causeway Bay"},
        {"clinic_id": "hk_tst", "name": "Tsim Sha Tsui Harbour Clinic", "district": "Tsim Sha Tsui"},
        {"clinic_id": "hk_qb", "name": "Quarry Bay Taikoo Clinic", "district": "Quarry Bay"},
    )

    def __init__(self, *, fault_injector: FaultInjector | None = None) -> None:
        self.faults = fault_injector or FaultInjector()
        self._booked: dict[str, dict[str, Any]] = {}
        self._bookings_by_ref: dict[str, dict[str, Any]] = {}

    def _validate_cvx(self, cvx_code: str) -> tuple[str, str]:
        code = str(cvx_code).strip()
        if code not in self.SUPPORTED_CVX or code not in CVX_VACCINES:
            raise AdapterError(
                f"Unsupported seasonal flu CVX code: {cvx_code!r} (supported: {sorted(self.SUPPORTED_CVX)})",
                retryable=False,
                code="invalid_cvx_code",
            )
        return CVX_VACCINES[code]

    def list_slots(
        self,
        *,
        district: str | None = None,
        cvx_code: str = "150",
        date_str: str | None = None,
    ) -> list[dict[str, Any]]:
        self.faults.check("fluvax.list_slots")
        disp_en, disp_zh = self._validate_cvx(cvx_code)
        target_day = date.fromisoformat(date_str) if date_str else datetime.now(HKT).date()
        clinics = [
            c for c in self.PARTNER_CLINICS
            if not district or c["district"].lower() == district.strip().lower()
        ]
        slots: list[dict[str, Any]] = []
        for clinic in clinics:
            for hour in (10, 14, 16):
                slot_dt = datetime(target_day.year, target_day.month, target_day.day, hour, 0, tzinfo=HKT)
                slot_id = f"vax_{clinic['clinic_id']}_{target_day.strftime('%Y%m%d')}_{hour:02d}00_{cvx_code}"
                slots.append(
                    {
                        "slot_id": slot_id,
                        "clinic_id": clinic["clinic_id"],
                        "partner_clinic": clinic["name"],
                        "district": clinic["district"],
                        "cvx_code": cvx_code,
                        "vaccine_display": disp_en,
                        "vaccine_display_zh": disp_zh,
                        "slot_at": slot_dt.isoformat(),
                        "available": slot_id not in self._booked,
                    }
                )
        return slots

    def book_vaccination(
        self,
        *,
        patient_id: str,
        slot_id: str,
        cvx_code: str = "150",
        partner_clinic: str = "Central Harbour Medical Centre",
        campaign_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        disp_en, disp_zh = self._validate_cvx(cvx_code)
        cached = self.faults.check("fluvax.book_vaccination", idempotency_key)
        if cached is not None:
            return cached

        if slot_id in self._booked:
            raise AdapterError(f"Vaccination slot {slot_id} is already booked", retryable=False, code="slot_unavailable")

        digest = hashlib.sha256(f"{slot_id}|{patient_id}|{cvx_code}".encode()).hexdigest()[:12]
        booking_ref = f"vaxbk_{digest}"
        record = {
            "booking_ref": booking_ref,
            "slot_id": slot_id,
            "patient_id": patient_id,
            "campaign_id": campaign_id,
            "partner_clinic": partner_clinic,
            "cvx_code": cvx_code,
            "vaccine_display": disp_en,
            "vaccine_display_zh": disp_zh,
            "status": "booked",
            "booked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._booked[slot_id] = record
        self._bookings_by_ref[booking_ref] = record

        result = AdapterResult(
            ok=True,
            external_ref=booking_ref,
            status="booked",
            payload=record,
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def cancel_booking(self, booking_ref: str, *, idempotency_key: str | None = None) -> AdapterResult:
        cached = self.faults.check("fluvax.cancel_booking", idempotency_key)
        if cached is not None:
            return cached
        record = self._bookings_by_ref.get(booking_ref)
        if not record:
            raise AdapterError(f"Vaccination booking not found: {booking_ref}", retryable=False, code="booking_not_found")
        if record.get("status") != "cancelled":
            record["status"] = "cancelled"
            record["cancelled_at"] = datetime.now(timezone.utc).isoformat()
        self._booked.pop(record["slot_id"], None)
        result = AdapterResult(
            ok=True,
            external_ref=booking_ref,
            status="cancelled",
            payload=dict(record),
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def verify_webhook(self, raw_body: bytes, signature: str, secret: str) -> bool:
        return verify_hmac_sha256(raw_body, signature, secret)

    def health(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "capabilities": list(self.capabilities),
            "supported_cvx": sorted(self.SUPPORTED_CVX),
            "booked_count": len(self._booked),
            "ok": "fluvax.book_vaccination" not in self.faults.permanent_down,
        }


# -------------------------------------------------------------------------------------- 5. Corporate Billing
_FORBIDDEN_CORPORATE_PII_KEYS = frozenset(
    {
        "patient_id",
        "patient_name",
        "name_en",
        "name_zh",
        "hkid",
        "hkid_masked",
        "phone",
        "phone_masked",
        "dob",
        "email",
        "icd10",
        "icd10_code",
        "icpc2",
        "icpc2_code",
        "diagnosis",
        "clinical_note",
    }
)


def _assert_zero_pii_keys(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in _FORBIDDEN_CORPORATE_PII_KEYS:
                raise AdapterError(
                    f"Corporate billing invariant violation: PII/clinical key '{k}' detected in aggregate invoice",
                    retryable=False,
                    code="pii_leak_blocked",
                )
            _assert_zero_pii_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            _assert_zero_pii_keys(item)


class CorporateBillingAdapter:
    """Aggregate corporate invoice & fulfilment reconciliation adapter (zero individual patient PII)."""

    name = "corporate_billing"
    capabilities = ("reconcile_invoice", "verify_webhook", "aggregate_only_k_anonymity")

    def __init__(self, *, fault_injector: FaultInjector | None = None) -> None:
        self.faults = fault_injector or FaultInjector()
        self.invoices: dict[str, dict[str, Any]] = {}

    def reconcile_invoice(
        self,
        *,
        corporate_id: str,
        corporate_name: str,
        billing_period: str,
        records: Sequence[dict[str, Any]],
        spend_cap_hkd: float | None = None,
        unit_fee_hkd: float = 420.0,
        flu_vax_fee_hkd: float = 280.0,
        k_anonymity_min: int = 5,
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        """Compute an aggregate corporate invoice & fulfilment reconciliation with zero individual PII.

        Input records may contain arbitrary internal fields, which are strictly aggregated and never
        emitted individually. Sub-group breakdowns with counts < `k_anonymity_min` are suppressed.
        """
        cached = self.faults.check("billing.reconcile_invoice", idempotency_key)
        if cached is not None:
            return cached

        completed_teleconsults = 0
        completed_flu_vax = 0
        cancelled_count = 0
        fulfilment_completed = 0
        subtotal_hkd = 0.0
        campaign_counts: dict[str, int] = {}
        campaign_spend: dict[str, float] = {}
        dept_counts: dict[str, int] = {}

        for rec in records:
            status = str(rec.get("status", "completed")).lower()
            service_type = str(rec.get("service_type", "teleconsult")).lower()
            campaign_code = str(rec.get("campaign_code") or rec.get("campaign_id") or "default")
            department = str(rec.get("department") or "").strip()

            if status in ("cancelled", "no_show", "void"):
                cancelled_count += 1
                continue

            if service_type == "flu_vax":
                fee = float(rec.get("fee_hkd", flu_vax_fee_hkd))
                completed_flu_vax += 1
            else:
                fee = float(rec.get("fee_hkd", unit_fee_hkd))
                completed_teleconsults += 1

            if str(rec.get("fulfilment_status", "")).lower() == "completed":
                fulfilment_completed += 1

            subtotal_hkd += fee
            campaign_counts[campaign_code] = campaign_counts.get(campaign_code, 0) + 1
            campaign_spend[campaign_code] = round(campaign_spend.get(campaign_code, 0.0) + fee, 2)
            if department:
                dept_counts[department] = dept_counts.get(department, 0) + 1

        subtotal_hkd = round(subtotal_hkd, 2)
        total_billable_events = completed_teleconsults + completed_flu_vax

        if spend_cap_hkd is not None:
            cap = round(float(spend_cap_hkd), 2)
            billable_hkd = min(subtotal_hkd, cap)
            over_cap_hkd = round(max(0.0, subtotal_hkd - cap), 2)
            remaining_cap_hkd = round(max(0.0, cap - billable_hkd), 2)
            reconciliation_status = "cap_exceeded" if over_cap_hkd > 0 else "reconciled"
        else:
            cap = None
            billable_hkd = subtotal_hkd
            over_cap_hkd = 0.0
            remaining_cap_hkd = None
            reconciliation_status = "reconciled"

        # Apply k-anonymity suppression to campaign and department breakdowns
        campaign_breakdown = []
        for c_code, cnt in sorted(campaign_counts.items()):
            if cnt < k_anonymity_min:
                campaign_breakdown.append(
                    {
                        "campaign_code": c_code,
                        "count": f"<{k_anonymity_min}",
                        "suppressed": True,
                        "spend_hkd": None,
                    }
                )
            else:
                campaign_breakdown.append(
                    {
                        "campaign_code": c_code,
                        "count": cnt,
                        "suppressed": False,
                        "spend_hkd": campaign_spend[c_code],
                    }
                )

        department_breakdown = []
        for dept, cnt in sorted(dept_counts.items()):
            if cnt < k_anonymity_min:
                department_breakdown.append({"department": dept, "count": f"<{k_anonymity_min}", "suppressed": True})
            else:
                department_breakdown.append({"department": dept, "count": cnt, "suppressed": False})

        inv_digest = hashlib.sha256(f"{corporate_id}|{billing_period}|{total_billable_events}".encode()).hexdigest()[:12]
        invoice_ref = f"inv_{inv_digest}"

        payload: dict[str, Any] = {
            "invoice_ref": invoice_ref,
            "corporate_id": corporate_id,
            "corporate_name": corporate_name,
            "billing_period": billing_period,
            "currency": "HKD",
            "total_billable_events": total_billable_events,
            "completed_teleconsults": completed_teleconsults,
            "completed_flu_vax": completed_flu_vax,
            "cancelled_count": cancelled_count,
            "fulfilment_completed_count": fulfilment_completed,
            "subtotal_hkd": subtotal_hkd,
            "spend_cap_hkd": cap,
            "billable_hkd": billable_hkd,
            "over_cap_hkd": over_cap_hkd,
            "remaining_cap_hkd": remaining_cap_hkd,
            "reconciliation_status": reconciliation_status,
            "k_anonymity_min": k_anonymity_min,
            "campaign_breakdown": campaign_breakdown,
            "department_breakdown": department_breakdown,
            "pii_excluded": True,
        }
        # Hard invariant check: guarantee zero individual patient PII fields ever appear in corporate output
        _assert_zero_pii_keys(payload)
        self.invoices[invoice_ref] = payload

        result = AdapterResult(
            ok=True,
            external_ref=invoice_ref,
            status=reconciliation_status,
            payload=payload,
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, result)

    def verify_webhook(self, raw_body: bytes, signature: str, secret: str) -> bool:
        return verify_hmac_sha256(raw_body, signature, secret)

    def health(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "capabilities": list(self.capabilities),
            "invoices_reconciled": len(self.invoices),
            "ok": "billing.reconcile_invoice" not in self.faults.permanent_down,
        }


# -------------------------------------------------------------------------------------- 6. Diagnostic Labs & Pathology Adapter (WO #72 Stage 3)
HK_LAB_PANELS: dict[str, dict[str, Any]] = {
    "cbc": {
        "display": "Complete Blood Count (CBC)",
        "loinc_codes": ["718-7", "6690-2", "777-3"],
        "turnaround_hours": 12,
        "fasting_required": False,
    },
    "iron_studies": {
        "display": "Iron Studies & Serum Ferritin",
        "loinc_codes": ["2276-4", "2498-4"],
        "turnaround_hours": 24,
        "fasting_required": True,
    },
    "lipid_apob": {
        "display": "Fasting Lipid Profile + ApoB + Lp(a)",
        "loinc_codes": ["13457-7", "1884-6", "10835-7"],
        "turnaround_hours": 24,
        "fasting_required": True,
    },
    "hba1c": {
        "display": "Glycated Haemoglobin (HbA1c)",
        "loinc_codes": ["4548-4"],
        "turnaround_hours": 12,
        "fasting_required": False,
    },
    "renal_electrolytes": {
        "display": "Renal Function & Electrolytes (RFT / eGFR / K+)",
        "loinc_codes": ["33914-3", "2823-3", "2160-0"],
        "turnaround_hours": 6,
        "fasting_required": False,
    },
    "coag_inr": {
        "display": "Prothrombin Time / INR",
        "loinc_codes": ["6301-6"],
        "turnaround_hours": 4,
        "fasting_required": False,
    },
    "thyroid": {
        "display": "Thyroid Function Test (TSH, Free T4)",
        "loinc_codes": ["3016-3", "3024-7"],
        "turnaround_hours": 24,
        "fasting_required": False,
    },
    "urine_culture": {
        "display": "Mid-Stream Urine Microscopy & Culture (MSU C&S)",
        "loinc_codes": ["630-4"],
        "turnaround_hours": 48,
        "fasting_required": False,
    },
}

_LAB_CRITICAL_RULES: dict[str, tuple[float | None, float | None, str]] = {
    # loinc_code -> (critical_low, critical_high, label)
    "2823-3": (2.5, 6.0, "Serum Potassium critical"),
    "718-7": (7.0, None, "Severe anaemia Hb < 7.0 g/dL"),
    "6301-6": (None, 5.0, "Supratherapeutic INR >= 5.0 (bleeding risk)"),
    "2345-7": (3.0, 25.0, "Critical blood glucose"),
}


class LabsAdapter:
    """Hong Kong diagnostic laboratory & pathology adapter (KingMed / PathLab / PHS simulator)
    implementing `AdapterContract` with deterministic `FaultInjector`, idempotent ordering,
    FHIR-aligned LOINC/UCUM result fetching, critical-value detection, and HMAC webhook verification.
    """

    name = "hk_labs_pathology"
    capabilities = ("order_panel", "fetch_results", "ingest_webhook", "cancel_order", "verify_webhook")

    def __init__(
        self,
        *,
        fault_injector: FaultInjector | None = None,
        default_partner: str = "KingMed Diagnostics HK",
    ) -> None:
        self.faults = fault_injector or FaultInjector()
        self.default_partner = default_partner
        self.orders: dict[str, dict[str, Any]] = {}
        self.results: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _evaluate_critical_flags(observations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        criticals: list[dict[str, Any]] = []
        for obs in observations:
            loinc = str(obs.get("loinc_code") or "").strip()
            val = obs.get("value_num")
            if loinc not in _LAB_CRITICAL_RULES or val is None:
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                continue
            lo, hi, label = _LAB_CRITICAL_RULES[loinc]
            if (lo is not None and num < lo) or (hi is not None and num >= hi):
                criticals.append(
                    {
                        "loinc_code": loinc,
                        "value_num": num,
                        "ucum_unit": obs.get("ucum_unit") or "",
                        "alert": label,
                    }
                )
        return criticals

    def order_panel(
        self,
        *,
        patient_id: str,
        practitioner_id: str,
        panels: Sequence[str],
        clinical_indication: str = "",
        priority: str = "routine",
        lab_partner: str | None = None,
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        if not panels:
            raise AdapterError("At least one lab panel is required", retryable=False, code="empty_panels")
        norm_priority = priority.strip().lower()
        if norm_priority not in ("routine", "urgent", "stat"):
            raise AdapterError(f"Unsupported lab priority: {priority}", retryable=False, code="invalid_priority")

        cached = self.faults.check("labs.order", idempotency_key)
        if cached is not None:
            return cached

        resolved_panels: list[dict[str, Any]] = []
        loinc_codes: list[str] = []
        fasting = False
        for raw_panel in panels:
            key = str(raw_panel).strip().lower()
            meta = HK_LAB_PANELS.get(key)
            if meta:
                resolved_panels.append({"code": key, "display": meta["display"], "loinc_codes": list(meta["loinc_codes"])})
                loinc_codes.extend(meta["loinc_codes"])
                fasting = fasting or bool(meta["fasting_required"])
            else:
                resolved_panels.append({"code": key, "display": str(raw_panel).strip(), "loinc_codes": []})

        order_ref = f"lab_{uuid.uuid4().hex[:12]}"
        record = {
            "order_ref": order_ref,
            "patient_id": patient_id,
            "practitioner_id": practitioner_id,
            "lab_partner": lab_partner or self.default_partner,
            "panels": resolved_panels,
            "loinc_codes": sorted(set(loinc_codes)),
            "fasting_required": fasting,
            "clinical_indication": clinical_indication,
            "priority": norm_priority,
            "status": "ordered",
            "idempotency_key": idempotency_key or "",
            "ordered_at": datetime.now(timezone.utc).isoformat(),
        }
        self.orders[order_ref] = record

        res = AdapterResult(
            ok=True,
            external_ref=order_ref,
            status="ordered",
            payload=record,
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, res)

    def publish_simulated_results(
        self,
        order_ref: str,
        observations: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        """Seed deterministic lab observations for a placed order (used by simulators/CI)."""
        if order_ref not in self.orders:
            raise AdapterError(f"Unknown lab order_ref: {order_ref}", retryable=False, code="order_not_found")
        obs_list = [dict(o) for o in observations]
        self.results[order_ref] = obs_list
        self.orders[order_ref]["status"] = "completed"
        criticals = self._evaluate_critical_flags(obs_list)
        return {"order_ref": order_ref, "status": "completed", "observations": obs_list, "critical_alerts": criticals}

    def fetch_results(
        self,
        order_ref: str,
        *,
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        cached = self.faults.check("labs.fetch_results", idempotency_key)
        if cached is not None:
            return cached
        order = self.orders.get(order_ref)
        if order is None:
            raise AdapterError(f"Unknown lab order_ref: {order_ref}", retryable=False, code="order_not_found")
        obs = list(self.results.get(order_ref) or [])
        criticals = self._evaluate_critical_flags(obs)
        status = "final" if obs else order["status"]
        payload = {
            "order_ref": order_ref,
            "patient_id": order["patient_id"],
            "lab_partner": order["lab_partner"],
            "status": status,
            "observations": obs,
            "critical_alerts": criticals,
            "has_critical": bool(criticals),
        }
        res = AdapterResult(
            ok=True,
            external_ref=order_ref,
            status=status,
            payload=payload,
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, res)

    def ingest_result_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        if not self.verify_webhook(raw_body, signature, secret):
            raise AdapterError("Invalid lab webhook HMAC signature", retryable=False, code="invalid_signature")
        cached = self.faults.check("labs.webhook", idempotency_key)
        if cached is not None:
            return cached
        import json

        data = json.loads(raw_body.decode("utf-8"))
        order_ref = str(data.get("order_ref") or "").strip()
        observations = list(data.get("observations") or [])
        if order_ref in self.orders:
            self.results[order_ref] = observations
            self.orders[order_ref]["status"] = "completed"
        criticals = self._evaluate_critical_flags(observations)
        payload = {
            "order_ref": order_ref,
            "observation_count": len(observations),
            "critical_alerts": criticals,
            "has_critical": bool(criticals),
            "status": "ingested",
        }
        res = AdapterResult(
            ok=True,
            external_ref=order_ref or f"labwh_{uuid.uuid4().hex[:10]}",
            status="ingested",
            payload=payload,
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, res)

    def cancel_order(
        self,
        order_ref: str,
        *,
        reason: str = "Clinician cancelled",
        idempotency_key: str | None = None,
    ) -> AdapterResult:
        cached = self.faults.check("labs.cancel", idempotency_key)
        if cached is not None:
            return cached
        order = self.orders.get(order_ref)
        if order is None:
            raise AdapterError(f"Unknown lab order_ref: {order_ref}", retryable=False, code="order_not_found")
        if order["status"] == "completed":
            raise AdapterError("Cannot cancel completed lab order", retryable=False, code="invalid_transition")
        order["status"] = "cancelled"
        order["cancel_reason"] = reason
        res = AdapterResult(
            ok=True,
            external_ref=order_ref,
            status="cancelled",
            payload=dict(order),
            idempotency_key=idempotency_key or "",
        )
        return self.faults.record(idempotency_key, res)

    def verify_webhook(self, raw_body: bytes, signature: str, secret: str) -> bool:
        return verify_hmac_sha256(raw_body, signature, secret)

    def health(self) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "mode": "simulated",
            "default_partner": self.default_partner,
            "capabilities": list(self.capabilities),
            "orders_count": len(self.orders),
            "ok": "labs.order" not in self.faults.permanent_down,
        }


# -------------------------------------------------------------------------------------- 7. Workspace Confirm / Undo Dispatcher
class WorkspaceAdapterDispatcher:
    """Orchestrates BookingAdapter, FluVaxAdapter, PharmacyFulfilmentAdapter, LabsAdapter, and
    WhatsAppDoorbellAdapter for `/workspace` confirm & 10-second Undo rollback with full idempotency
    and WhatsAppLeakageGuard enforcement.
    """

    def __init__(
        self,
        *,
        booking_adapter: BookingAdapter,
        vax_adapter: FluVaxAdapter,
        rx_adapter: PharmacyFulfilmentAdapter,
        wa_adapter: WhatsAppDoorbellAdapter,
        labs_adapter: LabsAdapter | None = None,
    ) -> None:
        self.booking_adapter = booking_adapter
        self.vax_adapter = vax_adapter
        self.rx_adapter = rx_adapter
        self.wa_adapter = wa_adapter
        self.labs_adapter = labs_adapter or LabsAdapter()
        self._trace_records: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def dispatch_orders(
        self,
        *,
        trace_id: str,
        patient_id: str,
        patient_phone: str,
        practitioner_id: str,
        practitioner_name: str,
        orders: Sequence[dict[str, Any]],
        prescription_id: str | None = None,
        has_certificate: bool = False,
        portal_url: str = "https://portal.tmrw-clinic.hk",
    ) -> dict[str, list[dict[str, Any]]]:
        bookings: list[dict[str, Any]] = []
        vaccinations: list[dict[str, Any]] = []
        pharmacy_orders: list[dict[str, Any]] = []
        lab_orders: list[dict[str, Any]] = []
        whatsapp_doorbells: list[dict[str, Any]] = []

        # 1. Dispatch /book orders -> FluVaxAdapter (if flu/vaccine) or BookingAdapter
        for idx, ord_item in enumerate(orders):
            if ord_item.get("type") != "book":
                continue
            visit_type = str(ord_item.get("visit_type") or ord_item.get("visitType") or "Clinic Follow-Up")
            cvx_code = str(ord_item.get("cvx_code") or ord_item.get("cvxCode") or "").strip()
            is_vax = bool(cvx_code) or any(tok in visit_type.lower() for tok in ("flu", "vax", "vaccine", "pcv", "immun"))
            if is_vax:
                eff_cvx = cvx_code if cvx_code in FluVaxAdapter.SUPPORTED_CVX else "150"
                slot_id = f"vax_{trace_id}_{idx}_{eff_cvx}"
                res = self.vax_adapter.book_vaccination(
                    patient_id=patient_id,
                    slot_id=slot_id,
                    cvx_code=eff_cvx,
                    idempotency_key=f"ws_vax_{trace_id}_{idx}",
                )
                vaccinations.append(
                    {
                        "adapter": self.vax_adapter.name,
                        "booking_ref": res.external_ref,
                        "slot_id": slot_id,
                        "cvx_code": eff_cvx,
                        "visit_type": visit_type,
                        "status": res.status,
                    }
                )
            else:
                slot_id = f"slot_{trace_id}_{idx}"
                res = self.booking_adapter.book_slot(
                    patient_id=patient_id,
                    practitioner_id=practitioner_id,
                    slot_id=slot_id,
                    encounter_id=trace_id,
                    idempotency_key=f"ws_bk_{trace_id}_{idx}",
                )
                bookings.append(
                    {
                        "adapter": self.booking_adapter.name,
                        "booking_ref": res.external_ref,
                        "slot_id": slot_id,
                        "visit_type": visit_type,
                        "status": res.status,
                    }
                )

        # 2. Dispatch active /prescribe orders -> PharmacyFulfilmentAdapter
        active_rx = [
            o for o in orders
            if o.get("type") == "rx" and str(o.get("action") or "start").lower() != "stop"
        ]
        if active_rx:
            rx_ref_id = prescription_id or f"rx_{trace_id}"
            res = self.rx_adapter.submit_order(
                prescription_id=rx_ref_id,
                patient_id=patient_id,
                mode="pickup",
                items=[
                    {
                        "drug": o.get("drug", ""),
                        "freq": o.get("freq", ""),
                        "days": o.get("days", ""),
                        "action": o.get("action", "start"),
                    }
                    for o in active_rx
                ],
                pickup_branch="Central Dispensary",
                idempotency_key=f"ws_phx_{trace_id}",
            )
            pharmacy_orders.append(
                {
                    "adapter": self.rx_adapter.name,
                    "fulfilment_ref": res.external_ref,
                    "prescription_id": rx_ref_id,
                    "status": res.status,
                    "item_count": len(active_rx),
                }
            )

        # 2B. Dispatch /lab orders -> LabsAdapter
        active_labs = [o for o in orders if o.get("type") == "lab"]
        for idx, lab_ord in enumerate(active_labs):
            panel_name = str(lab_ord.get("panel") or lab_ord.get("test") or lab_ord.get("title") or "cbc")
            l_res = self.labs_adapter.order_panel(
                patient_id=patient_id,
                practitioner_id=practitioner_id,
                panels=[panel_name],
                clinical_indication=str(lab_ord.get("rationale") or lab_ord.get("indication") or ""),
                priority=str(lab_ord.get("priority") or "routine"),
                idempotency_key=f"ws_lab_{trace_id}_{idx}",
            )
            lab_orders.append(
                {
                    "adapter": self.labs_adapter.name,
                    "order_ref": l_res.external_ref,
                    "panel": panel_name,
                    "status": l_res.status,
                }
            )

        # 3. Non-clinical WhatsApp utility doorbell (strictly enforced by WhatsAppLeakageGuard!)
        if has_certificate or active_rx or bookings or vaccinations or lab_orders:
            tpl_id = "document_ready" if (has_certificate or active_rx or lab_orders) else "booking_confirmed"
            params: dict[str, Any] = {
                "url": portal_url,
                "doctor_name": practitioner_name,
                "slot_label": "your upcoming clinic visit",
            }
            wa_res = self.wa_adapter.send_template(
                phone=patient_phone or "+85290000000",
                template_id=tpl_id,
                params=params,
                lang="bilingual",
                conversation_id=f"ws_{trace_id}",
                idempotency_key=f"ws_wa_{trace_id}",
            )
            whatsapp_doorbells.append(
                {
                    "adapter": self.wa_adapter.name,
                    "external_ref": wa_res.external_ref,
                    "template_id": tpl_id,
                    "status": wa_res.status,
                    "leakage_passed": bool(wa_res.payload.get("leakage_passed", True)),
                }
            )

        bundle = {
            "bookings": bookings,
            "vaccinations": vaccinations,
            "pharmacy_orders": pharmacy_orders,
            "lab_orders": lab_orders,
            "whatsapp_doorbells": whatsapp_doorbells,
        }
        self._trace_records[trace_id] = bundle
        return bundle

    def rollback_trace(self, trace_id: str, *, reason: str = "Clinician triggered 10-second Undo") -> dict[str, Any]:
        """Idempotently cancel provisional bookings, vaccination slots, pharmacy stubs, and lab orders for an undone trace."""
        bundle = self._trace_records.get(trace_id) or {}
        cancelled_bookings: list[str] = []
        cancelled_pharmacy_orders: list[str] = []
        cancelled_lab_orders: list[str] = []

        for bk in bundle.get("bookings") or []:
            ref = bk.get("booking_ref")
            if ref:
                try:
                    self.booking_adapter.cancel_booking(ref, idempotency_key=f"undo_bk_{trace_id}_{ref}")
                    bk["status"] = "cancelled"
                    cancelled_bookings.append(ref)
                except AdapterError:
                    pass

        for vax in bundle.get("vaccinations") or []:
            ref = vax.get("booking_ref")
            if ref:
                try:
                    self.vax_adapter.cancel_booking(ref, idempotency_key=f"undo_vax_{trace_id}_{ref}")
                    vax["status"] = "cancelled"
                    cancelled_bookings.append(ref)
                except AdapterError:
                    pass

        for phx in bundle.get("pharmacy_orders") or []:
            ref = phx.get("fulfilment_ref")
            if ref:
                try:
                    self.rx_adapter.cancel_order(ref, reason=reason, idempotency_key=f"undo_phx_{trace_id}_{ref}")
                    phx["status"] = "cancelled"
                    cancelled_pharmacy_orders.append(ref)
                except AdapterError:
                    pass

        for lab in bundle.get("lab_orders") or []:
            ref = lab.get("order_ref")
            if ref:
                try:
                    self.labs_adapter.cancel_order(ref, reason=reason, idempotency_key=f"undo_lab_{trace_id}_{ref}")
                    lab["status"] = "cancelled"
                    cancelled_lab_orders.append(ref)
                except AdapterError:
                    pass

        return {
            "cancelled_bookings": cancelled_bookings,
            "cancelled_pharmacy_orders": cancelled_pharmacy_orders,
            "cancelled_lab_orders": cancelled_lab_orders,
        }

