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

    def cancel_booking(self, *, booking_ref: str, idempotency_key: str | None = None) -> AdapterResult:
        cached = self.faults.check("booking.cancel_booking", idempotency_key)
        if cached is not None:
            return cached

        record = self._bookings_by_ref.get(booking_ref)
        if not record:
            raise AdapterError(f"Booking not found: {booking_ref}", retryable=False, code="booking_not_found")
        record["status"] = "cancelled"
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
    capabilities = ("list_slots", "book_vaccination", "verify_webhook")
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

        result = AdapterResult(
            ok=True,
            external_ref=booking_ref,
            status="booked",
            payload=record,
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
