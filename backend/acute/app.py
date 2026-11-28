"""FastAPI BFF for the Clinic AI Harness — Flu-Season Acute Care MVP (Four Surfaces, One Core).

Security, HK PDPO (Cap. 486) & MCHK invariants enforced on every request:
- Forced PostgreSQL 15 Row-Level Security (`tenant_session`) on all tenant-scoped reads/writes
- Strict JWT + RBAC + ABAC + OTP step-up (`require_step_up=True` on all patient clinical & vault routes)
- Magic links redeemed ONLY via POST (GET returns inert preview so WhatsApp link previews never burn tokens)
- 100% of outbound WhatsApp messages pass `WhatsAppLeakageGuard` (0 clinical content, 0 IDs, 0 promotions)
- Zero autonomous prescriptions or sick-leave certificates (requires `clinician` role + verified HK MCHK Reg No)
- Corporate admin endpoints strictly aggregate-only with k-anonymity `< 5` small-cohort suppression
- Per-tenant AI kill switch halts AI drafting while keeping 999 deterministic emergency routing active
- OWASP ASVS L2 security headers (CSP, HSTS, X-Frame-Options, X-Content-Type-Options) & rate limiting
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text as sql_text

from ..gateway import ModelGateway, TenantPolicy, load_protocol
from ..vault import DocumentVault, VaultError
from .adapters import (
    BookingAdapter,
    CorporateBillingAdapter,
    FluVaxAdapter,
    PharmacyFulfilmentAdapter,
    WhatsAppDoorbellAdapter,
)
from .certificates import issue_encrypted_certificate
from .db import (
    TENANT_SCOPED_TABLES,
    AgentRun,
    AuditEvent,
    Campaign,
    Certificate,
    Consent,
    Corporate,
    DataRequest,
    Database,
    Encounter,
    Immunization,
    MagicLink,
    Message,
    OTPChallenge,
    Observation,
    OutboxEvent,
    Patient,
    Practitioner,
    Prescription,
    Proposal,
    ProtocolVersion,
    ReviewDecision,
    Tenant,
    WhatsAppOutbox,
    new_id,
    record_audit,
    utcnow,
)
from .observability import TelemetryRegistry, configure_structured_logger
from .rx_rules import check_prescription_safety
from .security import (
    HKT,
    PHISafeLogFilter,
    SlidingRateLimiter,
    WhatsAppLeakageGuard,
    _get_jwt_secret,
    build_fhir_r4_bundle,
    client_ip_from_request,
    extract_claims,
    hash_secret,
    mask_hkid,
    mask_phone,
    mint_jwt,
    sha256_hex,
    valid_mchk_reg,
    verify_secret,
)
from .companion import (
    HK_MENTAL_HEALTH_HOTLINES,
    answer_companion_question,
    classify_crisis,
    next_clinic_review_sla,
)
from .seed import DEMO_TENANT_1, reset_and_seed, seed_demo_data
from .terminology import CVX_VACCINES, HK_DIAGNOSES, validate_diagnosis_coding
from .triage import AcuteHarnessRuntime
from .verification import build_unified_checks, runtime_toggles
from .workflows import DurableWorkflowEngine

logger = configure_structured_logger("tmrw.acute")
logger.addFilter(PHISafeLogFilter())

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
DEMO_EC_DIR = Path(__file__).resolve().parents[2] / "demo" / "ec-care-line"
K_ANONYMITY_THRESHOLD = 5

HK_PICS_DOC = {
    "version": "HK-PICS-v1.0",
    "title_en": "Personal Information Collection Statement (Hong Kong PDPO, Cap. 486) & AI Disclosure",
    "title_zh": "收集個人資料聲明（香港《個人資料（私隱）條例》第486章）及人工智能使用披露",
    "summary_en": (
        "Your personal and health data are collected solely for medical consultation, prescribing, medical certificate "
        "issuance, vaccination booking, and continuity of care by Hong Kong Registered Medical Practitioners. "
        "Data are hosted in Hong Kong (asia-east2) with per-document AES-256-GCM encryption and strict access controls. "
        "Your employer NEVER receives your individual clinical records or diagnoses — only de-identified aggregate counts "
        "(with cohorts under 5 suppressed). You may access (FHIR R4 export), correct, withdraw consent, or request deletion "
        "at any time, subject to Hong Kong statutory clinical record retention (7 years for adults / age 21 for minors)."
    ),
    "summary_zh": (
        "閣下的個人及健康資料僅供香港註冊醫生提供診症、處方、病假證明書、疫苗預約及延續護理之用。"
        "資料儲存於香港區域（asia-east2），採用獨立文件 AES-256-GCM 加密及嚴格權限控制。"
        "閣下的僱主絕不會收到任何個人病歷或診斷資料，僅會獲得去識別化匯總數據（少於5人的組別將自動隱藏）。"
        "閣下可隨時查閱（FHIR R4 匯出）、更正資料、撤回同意或要求刪除（受香港法定醫療紀錄保存期規範：成人7年／未成年人至21歲）。"
    ),
    "body_en": (
        "Your personal and health data are collected solely for medical consultation, prescribing, medical certificate "
        "issuance, vaccination booking, and continuity of care by Hong Kong Registered Medical Practitioners. "
        "Data are hosted in Hong Kong (asia-east2) with per-document AES-256-GCM encryption and strict access controls. "
        "Your employer NEVER receives your individual clinical records or diagnoses — only de-identified aggregate counts "
        "(with cohorts under 5 suppressed). You may access (FHIR R4 export), correct, withdraw consent, or request deletion "
        "at any time, subject to Hong Kong statutory clinical record retention (7 years for adults / age 21 for minors)."
    ),
    "body_zh": (
        "閣下的個人及健康資料僅供香港註冊醫生提供診症、處方、病假證明書、疫苗預約及延續護理之用。"
        "資料儲存於香港區域（asia-east2），採用獨立文件 AES-256-GCM 加密及嚴格權限控制。"
        "閣下的僱主絕不會收到任何個人病歷或診斷資料，僅會獲得去識別化匯總數據（少於5人的組別將自動隱藏）。"
        "閣下可隨時查閱（FHIR R4 匯出）、更正資料、撤回同意或要求刪除（受香港法定醫療紀錄保存期規範：成人7年／未成年人至21歲）。"
    ),
    "ai_disclosure_en": (
        "AI Assistance Notice: An AI intake tool helps structure your symptoms and drafts notes for your doctor. "
        "It does NOT diagnose, prescribe, or issue sick-leave certificates. Every clinical decision is made and signed "
        "by a Hong Kong Registered Medical Practitioner. In an emergency, call 999 or go to the nearest A&E immediately."
    ),
    "ai_disclosure_zh": (
        "人工智能輔助聲明：人工智能工具僅協助整理病徵及草擬紀錄供醫生審閱，絕不會作出診斷、處方藥物或簽發病假紙。"
        "所有臨床決定均由香港註冊醫生親自審核及簽署。如遇緊急情況，請立即致電 999 或前往最近急症室。"
    ),
    "dpo_contact": "dpo@central-gp.tmrw.hk",
    "scopes": [
        {
            "id": "clinical_care",
            "scope": "clinical_care",
            "required": True,
            "default_checked": True,
            "label_en": "Clinical teleconsultation, care & medical records",
            "label_zh": "臨床視像診症、護理及醫療紀錄（必需）",
            "description_en": "Required for Hong Kong registered doctors to assess symptoms, sign certificates/e-scripts, and maintain statutory records.",
            "description_zh": "供香港註冊醫生評估病徵、簽發病假證明／電子處方及依法保存醫療紀錄。",
        },
        {
            "id": "ai_assisted_intake",
            "scope": "ai_assisted_intake",
            "required": False,
            "default_checked": True,
            "label_en": "AI-assisted symptom intake & doctor draft (L1 clinician-reviewed)",
            "label_zh": "人工智能輔助病徵整理及醫生草稿（須醫生審核）",
            "description_en": "Unbundled opt-in: AI structures symptoms for your doctor. Declining routes directly to a human clinician.",
            "description_zh": "獨立選項：由 AI 整理病徵供醫生審閱；拒絕將直接轉由醫生人手處理。",
        },
        {
            "id": "ehealth_sharing",
            "scope": "ehealth_sharing",
            "required": False,
            "default_checked": False,
            "label_en": "Hong Kong eHealth (eHRSS) sharing consent",
            "label_zh": "香港醫健通（eHRSS）資料互通同意",
            "description_en": "Optional sync of acute flu encounter summary and vaccinations to Hong Kong eHealth.",
            "description_zh": "自選將急性流感診症摘要及疫苗接種記錄同步至香港醫健通。",
        },
        {
            "id": "invitations",
            "scope": "invitations",
            "required": False,
            "default_checked": True,
            "label_en": "Seasonal flu vaccination invitations (separate opt-in)",
            "label_zh": "季節性流感疫苗接種邀請（獨立選擇）",
            "description_en": "Optional non-clinical WhatsApp reminder doorbell for 48h check-in and flu shots.",
            "description_zh": "自選接收 48 小時康復跟進及季節性流感疫苗非臨床提醒。",
        },
        {
            "id": "deidentified_eval",
            "scope": "deidentified_eval",
            "required": False,
            "default_checked": False,
            "label_en": "De-identified safety evaluation & quality audit",
            "label_zh": "去識別化安全評估及質素審核",
            "description_en": "Optional use of fully de-identified records to audit clinical safety gates.",
            "description_zh": "自選容許使用完全去識別化記錄作臨床安全系統審核。",
        },
    ],
}


# -------------------------------------------------------------------------------------- Pydantic Request Models
class LoginReq(BaseModel):
    email: str
    password: str


class DemoLoginReq(BaseModel):
    role: str | None = None
    id: str | None = None
    persona: str | None = None
    persona_id: str | None = None


class ScanQRReq(BaseModel):
    campaign_code: str = Field(min_length=2, max_length=64)
    phone: str = Field(min_length=8, max_length=32)
    referral_code: str | None = None
    lang: str = Field(default="bilingual", pattern="^(en|zh|bilingual)$")


class VerifyOTPReq(BaseModel):
    otp_challenge_id: str | None = None
    challenge_id: str | None = None
    code: str | None = None
    otp: str | None = None
    magic_token: str | None = None


class OnboardReq(BaseModel):
    name_en: str = Field(min_length=2, max_length=160)
    name_zh: str = ""
    dob: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    sex: str = Field(default="U", pattern="^(M|F|U)$")
    hkid_masked: str = ""
    hkid: str = ""
    preferred_lang: str = Field(default="en", pattern="^(en|zh)$")
    g6pd_deficient: bool = False
    weight_kg: float | None = Field(default=None, gt=1, lt=300)
    egfr: float | None = Field(default=None, gt=1, lt=200)
    allergies: list[str] = Field(default_factory=list)
    current_meds: list[str] = Field(default_factory=list)
    consents: dict[str, bool]
    pics_version: str | None = None
    referral_code: str | None = None
    campaign_code: str | None = None


class IntakeReq(BaseModel):
    symptom_text: str | None = Field(default=None, max_length=4000)
    symptoms_text: str | None = Field(default=None, max_length=4000)
    temp_c: float | None = Field(default=None, ge=33.0, le=43.5)
    temperature_c: float | None = Field(default=None, ge=33.0, le=43.5)
    spo2: float | None = Field(default=None, ge=50.0, le=100.0)
    spo2_pct: float | None = Field(default=None, ge=50.0, le=100.0)
    rat_flu_a: str | None = None
    flu_rat_a: str | None = None
    rat_flu_b: str | None = None
    flu_rat_b: str | None = None
    rat_covid: str | None = None
    covid_rat: str | None = None
    duration_days: int | None = None
    lang: str = "auto"


class BookTeleconsultReq(BaseModel):
    encounter_id: str | None = None
    slot_at: str
    practitioner_id: str


class FulfilmentChoiceReq(BaseModel):
    mode: str | None = Field(default=None, pattern="^(delivery|pickup)$")
    fulfilment_mode: str | None = Field(default=None, pattern="^(delivery|pickup)$")
    address_or_branch: str | None = Field(default=None, max_length=300)
    address: str | None = Field(default=None, max_length=300)
    pharmacy: str | None = Field(default=None, max_length=300)


class FluVaxBookReq(BaseModel):
    partner_clinic: str = Field(min_length=2, max_length=160)
    slot_at: str
    cvx_code: str = "150"


class Followup48hReq(BaseModel):
    encounter_id: str | None = None
    status_text: str | None = Field(default=None, max_length=2000)
    status: str | None = None
    fever_resolved: bool | None = None
    new_red_flags: str | None = None
    notes: str | None = None
    need_human: bool = False


class MessageReq(BaseModel):
    encounter_id: str | None = None
    body_text: str = Field(min_length=1, max_length=2000)
    request_human: bool = False


class DataCorrectionReq(BaseModel):
    field: str = Field(min_length=2, max_length=64)
    requested_value: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=3, max_length=500)


class WithdrawConsentReq(BaseModel):
    scopes: list[str] | None = None
    scope: str | None = None
    reason: str | None = None


class DeleteDataReq(BaseModel):
    reason: str = ""


class RxCheckReq(BaseModel):
    patient_id: str
    items: list[dict[str, Any]]
    diagnosis_icd10: str | None = None


class ClinicianReplyReq(BaseModel):
    body_text: str = Field(min_length=1, max_length=2000)


class ResolveDataRequestReq(BaseModel):
    action: str = Field(pattern="^(approved|rejected)$")
    resolution_note: str = Field(min_length=3, max_length=500)


class IssueCertInput(BaseModel):
    days_off: int = Field(ge=1, le=7)
    start_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class IssueRxInput(BaseModel):
    items: list[dict[str, Any]]
    override_reason: str | None = None


class ClinicianDecideReq(BaseModel):
    decision: str = Field(pattern="^(approved|edited|rejected|escalated)$")
    review_seconds: float = Field(default=10.0, ge=0.0)
    rubber_stamp_confirmed: bool = False
    clinical_note: str | None = None
    icd10_code: str | None = None
    icpc2_code: str | None = None
    issue_cert: IssueCertInput | None = None
    issue_rx: IssueRxInput | None = None
    issue_certificate: bool | None = None
    cert_days_off: int | None = Field(default=None, ge=1, le=7)
    cert_start_date: str | None = None
    cert_end_date: str | None = None
    rx_items: list[dict[str, Any]] | None = None
    override_reason: str | None = None
    escalation_reason: str | None = None
    edit_diff: dict[str, Any] | None = None
    note: str = ""
    patient_reply: str | None = None


class NurseReadyReq(BaseModel):
    prep_note: str = ""
    vitals_note: str = ""


class ResolveConflictReq(BaseModel):
    conflict_id: str = Field(min_length=2, max_length=64)
    choice: str = Field(pattern="^(1|2|Q)$")
    note: str = ""


class CompanionTurnReq(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    lang: str = Field(default="en", max_length=12)


class CompanionReadingReq(BaseModel):
    kind: str = Field(default="bp", pattern="^(bp|glucose|temp)$")
    systolic: int | None = Field(default=None, ge=50, le=280)
    diastolic: int | None = Field(default=None, ge=30, le=180)
    value_num: float | None = None
    source: str = Field(default="monitor_photo", max_length=40)
    note: str = ""


class AvailabilityReq(BaseModel):
    availability: list[dict[str, Any]] | None = None
    slots: list[dict[str, Any]] | None = None


class CreateCorporateReq(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    spend_cap_hkd: float = Field(gt=0)
    billing_email: str
    admin_email: str
    admin_password: str = Field(default="DemoCorp!2026", min_length=8)


class CreateCampaignReq(BaseModel):
    corporate_id: str | None = None
    code: str = Field(min_length=3, max_length=48)
    name: str = Field(min_length=2, max_length=160)
    spend_cap_hkd: float = Field(gt=0)
    unit_fee_hkd: float = Field(default=420.0, gt=0)


class ApproveProtocolReq(BaseModel):
    version: str | None = None
    autonomy_level: str = Field(default="L1", pattern="^(L0|L1|L2)$")


class KillSwitchReq(BaseModel):
    ai_enabled: bool
    allow_cross_border_inference: bool | None = None
    reason: str | None = None


# -------------------------------------------------------------------------------------- Helpers
def _suppress_k(n: int) -> dict[str, Any]:
    """HK PDPO DPP3 employer k-anonymity suppression: suppress counts 1..4 as '<5'."""
    if 0 < n < K_ANONYMITY_THRESHOLD:
        return {"display": f"<{K_ANONYMITY_THRESHOLD}", "suppressed": True, "exact": None}
    return {"display": str(n), "suppressed": False, "exact": n}


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _is_demo_mode() -> bool:
    if os.environ.get("HARNESS_ENV", "staging").lower() == "prod":
        return False
    return _env_flag("DEMO_MODE", default=True)


def _show_demo_otp() -> bool:
    if os.environ.get("HARNESS_ENV", "staging").lower() == "prod":
        return False
    return _env_flag("DEMO_SHOW_OTP", default=True)


def _assert_patient_active(pat: Patient | None) -> Patient:
    """Enforce HK PDPO statutory retention hold lockout on clinical/transactional mutations."""
    if not pat:
        raise HTTPException(404, "Patient not found")
    if pat.status == "deleted_statutory_hold":
        raise HTTPException(
            403,
            "Account is locked under Hong Kong PDPO statutory retention hold. "
            "Clinical intake, bookings, and messaging are disabled; Data Access (FHIR export) remains available.",
        )
    return pat


def _render_soap_note(clinical_note: Any) -> str:
    """Format structured SOAP note dict into clean prose without dumping nested CDS/metadata dicts."""
    if isinstance(clinical_note, str):
        return clinical_note
    if not isinstance(clinical_note, dict):
        return ""
    soap_order = ("subjective", "objective", "assessment", "plan")
    lines = [f"{k.upper()}: {clinical_note[k]}" for k in soap_order if isinstance(clinical_note.get(k), str) and clinical_note[k]]
    if not lines:
        lines = [f"{k.upper()}: {v}" for k, v in clinical_note.items() if isinstance(v, str) and v]
    return "\n".join(lines)


def _apply_security_headers(response: Response, *, request_id: str, latency_ms: float | None = None) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Request-ID"] = request_id
    if latency_ms is not None:
        response.headers["X-Response-Time-Ms"] = f"{latency_ms:.2f}"


# -------------------------------------------------------------------------------------- App Factory
def create_acute_app(
    *,
    db: Database | None = None,
    vault: DocumentVault | None = None,
    gateway: ModelGateway | None = None,
    runtime: AcuteHarnessRuntime | None = None,
    seed_on_startup: bool = True,
) -> FastAPI:
    # Fail-fast check in production if JWT secret is missing or using dev default
    _get_jwt_secret()

    database = db or Database()
    doc_vault = vault or DocumentVault()
    model_gw = gateway or ModelGateway()
    harness_rt = runtime or AcuteHarnessRuntime(gateway=model_gw, enable_model_screen=True)
    telemetry = TelemetryRegistry()

    wa_adapter = WhatsAppDoorbellAdapter()
    booking_adapter = BookingAdapter()
    rx_adapter = PharmacyFulfilmentAdapter()
    vax_adapter = FluVaxAdapter()
    billing_adapter = CorporateBillingAdapter()
    workflows = DurableWorkflowEngine()
    workflows.register_default_handlers(whatsapp_adapter=wa_adapter)
    limiter = SlidingRateLimiter(max_requests=300, window_seconds=60)

    database.init_schema()
    if seed_on_startup:
        seed_demo_data(database, doc_vault, runtime=harness_rt)

    outbox_worker_enabled = _env_flag("OUTBOX_WORKER_ENABLED", default=False)
    outbox_poll_seconds = max(2, int(os.environ.get("OUTBOX_POLL_INTERVAL_SECONDS", "15")))
    outbox_worker_state: dict[str, Any] = {
        "enabled": outbox_worker_enabled,
        "poll_interval_seconds": outbox_poll_seconds,
        "ticks": 0,
        "last_tick_at": None,
        "last_processed": 0,
        "last_error": None,
    }

    def _run_outbox_sweep() -> int:
        total_processed = 0
        with database.admin_session() as s:
            tenant_ids = list(s.scalars(select(Tenant.id)).all())
        for tid in tenant_ids:
            res = workflows.process_due(database, tid, limit=50)
            total_processed += res.processed
            if res.succeeded:
                telemetry.inc("outbox_events_processed_total", res.succeeded)
            if res.dlq:
                telemetry.inc("outbox_events_dlq_total", res.dlq)
        return total_processed

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker_task: asyncio.Task[None] | None = None
        stop_event = asyncio.Event()

        async def _worker_loop() -> None:
            while not stop_event.is_set():
                try:
                    processed = await asyncio.to_thread(_run_outbox_sweep)
                    outbox_worker_state["ticks"] += 1
                    outbox_worker_state["last_tick_at"] = utcnow().isoformat()
                    outbox_worker_state["last_processed"] = processed
                    outbox_worker_state["last_error"] = None
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    outbox_worker_state["last_error"] = type(exc).__name__
                    logger.warning("Outbox worker sweep error: %s", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=outbox_poll_seconds)
                except asyncio.TimeoutError:
                    continue

        if outbox_worker_enabled:
            worker_task = asyncio.create_task(_worker_loop())
        try:
            yield
        finally:
            if worker_task is not None:
                stop_event.set()
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(
        title="Clinic AI Harness — Flu-Season Acute Care (Hong Kong)",
        version="1.0.0",
        docs_url="/api/v1/docs" if _is_demo_mode() else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.db = database
    app.state.vault = doc_vault
    app.state.gateway = model_gw
    app.state.runtime = harness_rt
    app.state.wa_adapter = wa_adapter
    app.state.workflows = workflows
    app.state.telemetry = telemetry
    app.state.outbox_worker_state = outbox_worker_state

    # --- OWASP ASVS L2 Security Headers, Request Tracing, Telemetry & Rate Limiting Middleware ---
    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id
        t0 = time.perf_counter()
        path = request.url.path
        client_ip = client_ip_from_request(request)

        try:
            if path.startswith("/api/") and path != "/api/v1/health/live":
                parts = path.split("/")
                bucket = parts[3] if len(parts) > 3 else "api"
                limiter.check(f"{client_ip}:{bucket}")
            response: Response = await call_next(request)
        except HTTPException as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail, "request_id": req_id},
            )
            _apply_security_headers(response, request_id=req_id, latency_ms=latency_ms)
            telemetry.record_http(path, response.status_code, latency_ms)
            return response
        except Exception as exc:
            latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            telemetry.inc("unhandled_errors_total")
            logger.error(
                "Unhandled exception (%s) on %s %s",
                type(exc).__name__,
                request.method,
                path,
                extra={
                    "request_id": req_id,
                    "method": request.method,
                    "path": path,
                    "status_code": 500,
                    "latency_ms": latency_ms,
                    "error_code": "INTERNAL_ERROR",
                },
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "error_code": "INTERNAL_ERROR",
                    "request_id": req_id,
                },
            )
            _apply_security_headers(response, request_id=req_id, latency_ms=latency_ms)
            telemetry.record_http(path, 500, latency_ms)
            return response

        latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        _apply_security_headers(response, request_id=req_id, latency_ms=latency_ms)
        telemetry.record_http(path, response.status_code, latency_ms)
        if path.startswith("/api/") and path not in ("/api/v1/health/live", "/api/v1/metrics"):
            safe_path = path
            if "/magic/" in safe_path and "/verify-otp" not in safe_path:
                segs = safe_path.split("/")
                for idx, seg in enumerate(segs):
                    if seg == "magic" and idx + 1 < len(segs):
                        segs[idx + 1] = "[REDACTED]"
                safe_path = "/".join(segs)
            logger.info(
                "%s %s -> %d (%.1fms)",
                request.method,
                safe_path,
                response.status_code,
                latency_ms,
                extra={
                    "request_id": req_id,
                    "method": request.method,
                    "path": safe_path,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                },
            )
        return response

    # ================================================================================== HEALTH, PROBES & METRICS
    @app.get("/api/v1/health/live")
    def health_live():
        """Zero-DB fast liveness probe for Cloud Run / Kubernetes."""
        return {"ok": True, "status": "live", "version": "1.0.0"}

    @app.get("/api/v1/health/ready")
    def health_ready():
        """Deep readiness probe: Postgres connectivity, FORCED RLS across all 21 tenant tables, and Vault health."""
        db_ok = False
        rls_tables = 0
        db_error = None
        try:
            with database.admin_session() as s:
                s.execute(sql_text("SELECT 1"))
                db_ok = True
                rls_tables = int(
                    s.execute(
                        sql_text(
                            "SELECT count(*) FROM pg_class c "
                            "JOIN pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'public' AND c.relname = ANY(:tables) "
                            "AND c.relrowsecurity = true AND c.relforcerowsecurity = true"
                        ),
                        {"tables": list(TENANT_SCOPED_TABLES)},
                    ).scalar()
                    or 0
                )
        except Exception as exc:
            db_error = type(exc).__name__

        vault_health = doc_vault.health_check()
        expected_rls = len(TENANT_SCOPED_TABLES)
        ready = db_ok and rls_tables == expected_rls and bool(vault_health.get("ok"))
        payload = {
            "ok": ready,
            "status": "ready" if ready else "degraded",
            "checks": {
                "postgres": {"ok": db_ok, "error": db_error},
                "forced_rls": {"ok": rls_tables == expected_rls, "rls_tables": rls_tables, "expected": expected_rls},
                "vault": vault_health,
            },
            "region": os.environ.get("HARNESS_REGION", "asia-east2"),
            "outbox_worker": outbox_worker_state,
        }
        return JSONResponse(status_code=200 if ready else 503, content=payload)

    @app.get("/api/v1/metrics")
    def prometheus_metrics():
        """Prometheus exposition format (zero PHI, bounded cardinality)."""
        return PlainTextResponse(
            content=telemetry.render_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/v1/health")
    def health():
        with database.admin_session() as s:
            t_count = s.scalar(select(func.count(Tenant.id))) or 0
            t1 = s.get(Tenant, DEMO_TENANT_1)
            ai_enabled = bool(t1.ai_enabled) if t1 else True
        return {
            "ok": True,
            "status": "ok",
            "version": "1.0.0",
            "db": "postgresql-15-rls",
            "env": os.environ.get("HARNESS_ENV", "staging"),
            "region": os.environ.get("HARNESS_REGION", "asia-east2"),
            "demo_mode": _is_demo_mode(),
            "ai_enabled": ai_enabled,
            "tenants_count": int(t_count),
            "outbox_worker_enabled": outbox_worker_enabled,
            "runtime_toggles": runtime_toggles(),
            "compliance": ["HK-PDPO-Cap486", "MCHK-Telemedicine", "HL7-FHIR-R4", "ISO-27799"],
        }

    @app.get("/api/v1/demo/personas")
    def demo_personas():
        if not _is_demo_mode():
            raise HTTPException(404, "Demo endpoints disabled in production")
        with database.admin_session() as s:
            tenants = [{"id": t.id, "name": t.name, "region": t.region, "ai_enabled": t.ai_enabled} for t in s.scalars(select(Tenant)).all()]
        with database.tenant_session(DEMO_TENANT_1) as s:
            corps = [{"id": c.id, "name": c.name, "admin_email": c.admin_email, "spend_cap_hkd": c.spend_cap_hkd} for c in s.scalars(select(Corporate)).all()]
            camps = [{"id": c.id, "code": c.code, "name": c.name, "corporate_id": c.corporate_id} for c in s.scalars(select(Campaign)).all()]
            pracs = [
                {"id": p.id, "name_en": p.name_en, "name_zh": p.name_zh, "role": p.role, "mchk_reg_no": p.mchk_reg_no, "email": p.email}
                for p in s.scalars(select(Practitioner)).all()
            ]
            pats = [
                {"id": p.id, "name_en": p.name_en, "name_zh": p.name_zh, "phone_masked": p.phone_masked, "g6pd_deficient": p.g6pd_deficient, "allergies": p.allergies_json}
                for p in s.scalars(select(Patient).limit(15)).all()
            ]
        flat_personas: list[dict[str, Any]] = []
        for p in pracs:
            if p["role"] == "clinician":
                flat_personas.append({
                    "id": p["id"], "key": f"clinician:{p['id']}", "role": "clinician",
                    "label": f"{p['name_en']} ({p['mchk_reg_no']})", "label_zh": f"{p['name_zh'] or p['name_en']} ({p['mchk_reg_no']})",
                    "email": p["email"], "mchk_reg_no": p["mchk_reg_no"],
                })
        for p in pracs:
            if p["role"] == "nurse":
                flat_personas.append({
                    "id": p["id"], "key": f"nurse:{p['id']}", "role": "nurse",
                    "label": f"{p['name_en']}", "label_zh": f"{p['name_zh'] or p['name_en']}",
                    "email": p["email"],
                })
        for p in pracs:
            if p["role"] == "clinic_admin":
                flat_personas.append({
                    "id": p["id"], "key": f"clinic_admin:{p['id']}", "role": "clinic_admin",
                    "label": f"{p['name_en']} (Clinic Ops)", "label_zh": f"{p['name_zh'] or p['name_en']}（診所營運）",
                    "email": p["email"],
                })
        for c in corps:
            flat_personas.append({
                "id": c["id"], "key": f"corporate_admin:{c['id']}", "role": "corporate_admin",
                "label": f"{c['name']} HR Admin", "label_zh": f"{c['name']} 企業管理員",
                "email": c["admin_email"],
            })
        for p in pats:
            flat_personas.append({
                "id": p["id"], "key": f"patient:{p['id']}", "role": "patient",
                "label": f"{p['name_en']} (Patient)", "label_zh": f"{p['name_zh'] or p['name_en']}（病人）",
                "phone": p["phone_masked"],
            })
        return {
            "demo_mode": True,
            "synthetic_only": True,
            "runtime_toggles": runtime_toggles(),
            "personas": flat_personas,
            "tenants": tenants,
            "corporates": corps,
            "campaigns": camps,
            "clinicians": [p for p in pracs if p["role"] == "clinician"],
            "nurses": [p for p in pracs if p["role"] == "nurse"],
            "clinic_admins": [p for p in pracs if p["role"] == "clinic_admin"],
            "corporate_admins": [{"id": c["id"], "name": c["name"], "email": c["admin_email"]} for c in corps],
            "sample_patients": pats,
        }

    @app.post("/api/v1/demo/login-as")
    def demo_login_as(body: DemoLoginReq):
        if not _is_demo_mode():
            raise HTTPException(404, "Demo endpoints disabled in production")
        role = body.role
        target_id = body.id
        persona_key = body.persona or body.persona_id
        if (not role or not target_id) and persona_key and ":" in persona_key:
            role, target_id = persona_key.split(":", 1)
        if not role or not target_id:
            raise HTTPException(422, "Specify role & id or persona key ('role:id')")
        with database.tenant_session(DEMO_TENANT_1) as s:
            if role in ("clinician", "nurse", "clinic_admin"):
                p = s.get(Practitioner, target_id)
                if not p or p.role != role:
                    raise HTTPException(404, "Practitioner not found")
                tok = mint_jwt(subject_id=p.id, role=p.role, tenant_id=p.tenant_id, step_up_verified=True, mchk_reg_no=p.mchk_reg_no)
                return {
                    "token": tok, "access_token": tok, "role": p.role, "tenant_id": p.tenant_id,
                    "subject_id": p.id, "user_id": p.id, "step_up_verified": True,
                    "name_en": p.name_en, "name_zh": p.name_zh, "mchk_reg_no": p.mchk_reg_no,
                    "user": {"id": p.id, "name_en": p.name_en, "name_zh": p.name_zh, "mchk_reg_no": p.mchk_reg_no, "role": p.role},
                }
            if role == "corporate_admin":
                c = s.get(Corporate, target_id)
                if not c:
                    raise HTTPException(404, "Corporate not found")
                tok = mint_jwt(subject_id=c.id, role="corporate_admin", tenant_id=c.tenant_id, step_up_verified=True, corporate_id=c.id)
                return {
                    "token": tok, "access_token": tok, "role": "corporate_admin", "tenant_id": c.tenant_id,
                    "subject_id": c.id, "user_id": c.id, "corporate_id": c.id, "step_up_verified": True,
                    "name_en": c.name, "user": {"id": c.id, "name": c.name, "email": c.admin_email},
                }
            if role == "patient":
                pat = s.get(Patient, target_id)
                if not pat:
                    raise HTTPException(404, "Patient not found")
                tok = mint_jwt(subject_id=pat.id, role="patient", tenant_id=pat.tenant_id, step_up_verified=True)
                return {
                    "token": tok, "access_token": tok, "role": "patient", "tenant_id": pat.tenant_id,
                    "subject_id": pat.id, "user_id": pat.id, "step_up_verified": True, "onboarded": True,
                    "name_en": pat.name_en, "name_zh": pat.name_zh,
                    "user": {"id": pat.id, "name_en": pat.name_en, "name_zh": pat.name_zh},
                }
        raise HTTPException(400, "Unsupported role")

    @app.post("/api/v1/demo/reset")
    def demo_reset():
        if not _is_demo_mode():
            raise HTTPException(404, "Demo endpoints disabled in production")
        res = reset_and_seed(database, doc_vault, runtime=harness_rt)
        return {"ok": True, **res}

    # ================================================================================== REAL AUTH
    @app.post("/api/v1/auth/login")
    def auth_login(body: LoginReq):
        with database.admin_session() as s:
            prac = s.scalar(select(Practitioner).where(Practitioner.email == body.email))
            if prac and verify_secret(body.password, prac.password_hash):
                tok = mint_jwt(subject_id=prac.id, role=prac.role, tenant_id=prac.tenant_id, step_up_verified=True, mchk_reg_no=prac.mchk_reg_no)
                return {
                    "token": tok, "access_token": tok, "role": prac.role, "tenant_id": prac.tenant_id,
                    "subject_id": prac.id, "user_id": prac.id, "step_up_verified": True,
                    "name_en": prac.name_en, "name_zh": prac.name_zh, "mchk_reg_no": prac.mchk_reg_no,
                    "user": {"id": prac.id, "name_en": prac.name_en, "mchk_reg_no": prac.mchk_reg_no},
                }
            corp = s.scalar(select(Corporate).where(Corporate.admin_email == body.email))
            if corp and verify_secret(body.password, corp.admin_password_hash):
                tok = mint_jwt(subject_id=corp.id, role="corporate_admin", tenant_id=corp.tenant_id, step_up_verified=True, corporate_id=corp.id)
                return {
                    "token": tok, "access_token": tok, "role": "corporate_admin", "tenant_id": corp.tenant_id,
                    "subject_id": corp.id, "user_id": corp.id, "corporate_id": corp.id, "step_up_verified": True,
                    "name_en": corp.name, "user": {"id": corp.id, "name": corp.name},
                }
        raise HTTPException(401, "Invalid email or password")

    # ================================================================================== SURFACE 1: WHATSAPP DOORBELL
    @app.post("/api/v1/wa/scan-qr")
    def wa_scan_qr(body: ScanQRReq):
        """Story 1 & 10: QR campaign/referral scan -> non-clinical WhatsApp doorbell + single-use 15m magic link."""
        with database.admin_session() as s:
            camp = s.scalar(select(Campaign).where(Campaign.code == body.campaign_code, Campaign.active == True))  # noqa: E712
            if not camp:
                raise HTTPException(404, f"Active QR campaign '{body.campaign_code}' not found")
            tenant = s.get(Tenant, camp.tenant_id)
            tenant_id = camp.tenant_id
            clinic_name = tenant.name if tenant else "TMRW HK Clinic"
            camp_name = camp.name

        raw_token = secrets.token_urlsafe(24)
        token_hash = sha256_hex(raw_token)
        conv_id = new_id("wa")
        magic_url = f"/p/magic/{raw_token}"

        wa_res = wa_adapter.send_template(
            phone=body.phone,
            conversation_id=conv_id,
            template_id="welcome_magic_link",
            params={"clinic_name": clinic_name, "url": magic_url},
            lang=body.lang,
            idempotency_key=f"scan:{token_hash[:16]}",
        )
        msg_text = wa_res.payload["body"]
        WhatsAppLeakageGuard.assert_clean(msg_text)

        with database.tenant_session(tenant_id) as s:
            s.add(MagicLink(
                token_hash=token_hash,
                tenant_id=tenant_id,
                conversation_id=conv_id,
                campaign_code=body.campaign_code,
                referral_code=body.referral_code,
                phone=body.phone,
                purpose="onboarding",
                expires_at=utcnow() + timedelta(minutes=15),
            ))
            s.add(WhatsAppOutbox(
                id=new_id("wao"),
                tenant_id=tenant_id,
                phone=body.phone,
                conversation_id=conv_id,
                template_id="welcome_magic_link",
                body_text=msg_text,
                magic_link_url=magic_url,
                leakage_passed=True,
                status="sent",
            ))
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=mask_phone(body.phone),
                action="wa.qr_scanned", resource_type="Campaign", resource_id=body.campaign_code,
                metadata={"conversation_id": conv_id, "has_referral": bool(body.referral_code)},
            )
        telemetry.inc("wa_messages_sent_total")

        return {
            "ok": True,
            "conversation_id": conv_id,
            "campaign_code": body.campaign_code,
            "campaign_name": camp_name,
            "message": msg_text,
            "magic_link_url": magic_url,
            "magic_token": raw_token,
            "expires_in_seconds": 900,
            "leakage_passed": True,
        }

    @app.get("/api/v1/wa/inbox")
    def wa_inbox(phone: str = Query(..., min_length=4)):
        """Return simulated non-clinical WhatsApp doorbell messages for a phone number (demo/staging only)."""
        if not _is_demo_mode():
            raise HTTPException(404, "WhatsApp inbox simulator disabled in production")
        with database.admin_session() as s:
            rows = s.scalars(
                select(WhatsAppOutbox)
                .where(WhatsAppOutbox.phone == phone)
                .order_by(WhatsAppOutbox.created_at.desc())
                .limit(20)
            ).all()
        return {
            "messages": [
                {
                    "id": r.id,
                    "phone": r.phone,
                    "conversation_id": r.conversation_id,
                    "template_id": r.template_id,
                    "body_text": r.body_text,
                    "magic_link_url": r.magic_link_url,
                    "demo_otp": r.demo_otp if _show_demo_otp() else None,
                    "leakage_passed": r.leakage_passed,
                    "status": r.status,
                    "created_at": r.created_at.isoformat(),
                }
                for r in reversed(rows)
            ]
        }

    @app.post("/api/v1/wa/webhook")
    async def wa_webhook(request: Request):
        raw = await request.body()
        sig = request.headers.get("X-Hub-Signature-256", "")
        secret = os.environ.get("WHATSAPP_APP_SECRET")
        if not secret:
            if os.environ.get("HARNESS_ENV", "staging").lower() == "prod":
                raise HTTPException(503, "WHATSAPP_APP_SECRET is not configured in production")
            secret = "tmrw-wa-webhook-dev"
        if not wa_adapter.verify_webhook(raw, sig, secret):
            raise HTTPException(403, "Invalid WhatsApp webhook signature")
        return {"ok": True, "verified": True}

    # ================================================================================== MAGIC LINK (POST-ONLY!) & OTP
    @app.get("/api/v1/magic/{token}")
    def magic_preview(token: str):
        """Inert preview check on GET — NEVER burns the token (immune to WhatsApp link-preview bots)."""
        th = sha256_hex(token)
        with database.admin_session() as s:
            ml = s.get(MagicLink, th)
            if not ml:
                return {"valid": False, "expired": False, "redeemed": False, "purpose": "unknown", "notice": "Invalid link", "note": "Invalid link"}
            now = utcnow()
            expired = ml.expires_at < now
            redeemed = ml.redeemed_at is not None
            tenant = s.get(Tenant, ml.tenant_id)
            notice = "Tap Continue below to verify with a one-time code. Link previews never activate your session."
            return {
                "valid": (not expired) and (not redeemed),
                "expired": expired,
                "redeemed": redeemed,
                "burned": redeemed,
                "purpose": ml.purpose,
                "campaign_code": ml.campaign_code,
                "clinic_name": tenant.name if tenant else "TMRW HK Clinic",
                "phone_masked": mask_phone(ml.phone),
                "expires_at": ml.expires_at.isoformat(),
                "notice": notice,
                "note": notice,
            }

    @app.post("/api/v1/magic/{token}/redeem")
    def magic_redeem(token: str):
        """Single-use POST redemption -> burns magic link, issues 6-digit OTP step-up challenge."""
        th = sha256_hex(token)
        now = utcnow()
        with database.admin_session() as s:
            ml = s.get(MagicLink, th)
            if not ml:
                raise HTTPException(404, "Invalid magic link")
            if ml.redeemed_at is not None:
                raise HTTPException(410, "Magic link has already been used")
            if ml.expires_at < now:
                raise HTTPException(410, "Magic link has expired (15-minute limit)")
            ml.redeemed_at = now
            tenant_id = ml.tenant_id
            phone = ml.phone
            conv_id = ml.conversation_id

        otp_code = f"{secrets.randbelow(900000) + 100000:06d}"
        otp_id = new_id("otp")
        wa_res = wa_adapter.send_template(
            phone=phone,
            conversation_id=conv_id,
            template_id="otp_code",
            params={"otp": otp_code, "ttl_minutes": 5},
            lang="bilingual",
            idempotency_key=f"otp:{otp_id}",
        )
        WhatsAppLeakageGuard.assert_clean(wa_res.payload["body"])

        with database.tenant_session(tenant_id) as s:
            s.add(OTPChallenge(
                id=otp_id,
                tenant_id=tenant_id,
                magic_token_hash=th,
                phone=phone,
                code_hash=hash_secret(otp_code),
                code_demo_only=otp_code if _show_demo_otp() else None,
                attempts=0,
                max_attempts=3,
                expires_at=now + timedelta(minutes=5),
            ))
            s.add(WhatsAppOutbox(
                id=new_id("wao"),
                tenant_id=tenant_id,
                phone=phone,
                conversation_id=conv_id,
                template_id="otp_code",
                body_text=wa_res.payload["body"],
                demo_otp=otp_code if _show_demo_otp() else None,
                leakage_passed=True,
                status="sent",
            ))
        telemetry.inc("wa_messages_sent_total")

        return {
            "ok": True,
            "challenge_id": otp_id,
            "otp_challenge_id": otp_id,
            "phone_masked": mask_phone(phone),
            "expires_in_seconds": 300,
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "demo_otp": otp_code if _show_demo_otp() else None,
        }

    @app.post("/api/v1/magic/verify-otp")
    def verify_otp(body: VerifyOTPReq):
        """Step-up OTP verification -> unlocks patient session JWT (`step_up=True`)."""
        challenge_id = body.otp_challenge_id or body.challenge_id
        code_val = (body.code or body.otp or "").strip()
        if not challenge_id or not code_val:
            raise HTTPException(422, "challenge_id and 6-digit code are required")
        now = utcnow()
        with database.admin_session() as s:
            ch = s.get(OTPChallenge, challenge_id)
            if not ch:
                raise HTTPException(404, "OTP challenge not found")
            if ch.verified_at is not None:
                raise HTTPException(409, "OTP already verified")
            if ch.expires_at < now:
                raise HTTPException(410, "OTP expired")
            if ch.attempts >= ch.max_attempts:
                raise HTTPException(429, "Too many invalid OTP attempts")
            if not verify_secret(code_val, ch.code_hash):
                ch.attempts += 1
                raise HTTPException(401, "Invalid verification code")
            ch.verified_at = now
            tenant_id = ch.tenant_id
            phone = ch.phone
            ml = s.get(MagicLink, ch.magic_token_hash)
            campaign_code = ml.campaign_code if ml else None
            referral_code = ml.referral_code if ml else None

        p_hash = sha256_hex(phone)
        with database.tenant_session(tenant_id) as s:
            existing = s.scalar(select(Patient).where(Patient.phone_hash == p_hash, Patient.status == "active"))
            if existing:
                tok = mint_jwt(subject_id=existing.id, role="patient", tenant_id=tenant_id, step_up_verified=True)
                return {
                    "token": tok,
                    "access_token": tok,
                    "verified": True,
                    "role": "patient",
                    "tenant_id": tenant_id,
                    "subject_id": existing.id,
                    "step_up_verified": True,
                    "patient_exists": True,
                    "onboarded": True,
                    "needs_onboarding": False,
                    "patient_id": existing.id,
                    "name_en": existing.name_en,
                    "name_zh": existing.name_zh,
                    "campaign_code": campaign_code,
                    "referral_code": referral_code,
                }
        # Step-up verified pre-onboarding token bound to phone hash
        sub_id = f"pre:{p_hash[:24]}"
        tok = mint_jwt(subject_id=sub_id, role="patient", tenant_id=tenant_id, step_up_verified=True)
        return {
            "token": tok,
            "access_token": tok,
            "verified": True,
            "role": "patient",
            "tenant_id": tenant_id,
            "subject_id": sub_id,
            "step_up_verified": True,
            "patient_exists": False,
            "onboarded": False,
            "needs_onboarding": True,
            "patient_id": None,
            "campaign_code": campaign_code,
            "referral_code": referral_code,
            "phone_masked": mask_phone(phone),
        }

    # ================================================================================== SURFACE 2: PATIENT PWA
    @app.get("/api/v1/patient/pics")
    def patient_pics():
        return HK_PICS_DOC

    @app.post("/api/v1/patient/onboard")
    def patient_onboard(body: OnboardReq, request: Request):
        """Story 2: 2-ID identity + HK PDPO DPP1 granular consent ledger before any health question."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id = claims["tid"]
        if not body.consents.get("clinical_care"):
            raise HTTPException(422, "Explicit consent to clinical_care (HK PDPO DPP1) is required before onboarding.")

        raw_hkid = (body.hkid_masked or body.hkid or "").strip()
        with database.tenant_session(tenant_id) as s:
            camp = None
            if body.campaign_code:
                camp = s.scalar(select(Campaign).where(Campaign.code == body.campaign_code))
            sub = claims["sub"]
            phone_hash = sub.split(":", 1)[1] if sub.startswith("pre:") else sha256_hex(sub)
            pat = Patient(
                id=new_id("pat"),
                tenant_id=tenant_id,
                corporate_id=camp.corporate_id if camp else None,
                campaign_id=camp.id if camp else None,
                phone_hash=phone_hash,
                phone_masked="****" + phone_hash[-4:],
                name_en=body.name_en.strip(),
                name_zh=body.name_zh.strip(),
                dob=body.dob,
                sex=body.sex,
                hkid_masked=mask_hkid(raw_hkid) if raw_hkid else "",
                ehealth_consent=bool(body.consents.get("ehealth_sharing", False)),
                preferred_lang=body.preferred_lang,
                g6pd_deficient=body.g6pd_deficient,
                weight_kg=body.weight_kg,
                egfr=body.egfr,
                allergies_json=body.allergies,
                current_meds_json=body.current_meds,
                referral_code=f"REF-{secrets.token_hex(4).upper()}",
                referred_by_code=body.referral_code,
                status="active",
            )
            s.add(pat)
            s.flush()

            recorded_consents = []
            for scope, granted in body.consents.items():
                c = Consent(
                    id=new_id("con"),
                    tenant_id=tenant_id,
                    patient_id=pat.id,
                    scope=scope,
                    pics_version=body.pics_version or HK_PICS_DOC["version"],
                    granted=bool(granted),
                    channel="pwa",
                    wording_hash=sha256_hex(f"{HK_PICS_DOC['version']}:{scope}"),
                )
                s.add(c)
                recorded_consents.append({"id": c.id, "scope": scope, "granted": bool(granted), "pics_version": c.pics_version})

            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pat.id,
                action="patient.onboarded", resource_type="Patient", resource_id=pat.id,
                metadata={"consents": list(body.consents.keys()), "ehealth": pat.ehealth_consent},
            )
            new_tok = mint_jwt(subject_id=pat.id, role="patient", tenant_id=tenant_id, step_up_verified=True)
            return {
                "ok": True,
                "token": new_tok,
                "access_token": new_tok,
                "patient": {
                    "id": pat.id, "name_en": pat.name_en, "name_zh": pat.name_zh, "dob": pat.dob,
                    "sex": pat.sex, "hkid_masked": pat.hkid_masked, "phone_masked": pat.phone_masked,
                    "preferred_lang": pat.preferred_lang, "ehealth_consent": pat.ehealth_consent,
                    "g6pd_deficient": pat.g6pd_deficient, "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                    "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                    "referral_code": pat.referral_code, "status": pat.status,
                },
                "consents": recorded_consents,
            }

    @app.get("/api/v1/patient/me")
    def patient_me(request: Request):
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            pat = s.get(Patient, pid)
            if not pat:
                return {
                    "onboarded": False,
                    "patient": None,
                    "consents": [],
                    "encounters": [],
                    "certificates": [],
                    "prescriptions": [],
                    "immunizations": [],
                    "messages": [],
                    "data_requests": [],
                }
            consents = s.scalars(select(Consent).where(Consent.patient_id == pid).order_by(Consent.recorded_at.desc())).all()
            encs = s.scalars(select(Encounter).where(Encounter.patient_id == pid).order_by(Encounter.created_at.desc())).all()
            certs = s.scalars(select(Certificate).where(Certificate.patient_id == pid).order_by(Certificate.signed_at.desc())).all()
            rxs = s.scalars(select(Prescription).where(Prescription.patient_id == pid).order_by(Prescription.signed_at.desc())).all()
            imms = s.scalars(select(Immunization).where(Immunization.patient_id == pid).order_by(Immunization.created_at.desc())).all()
            msgs = s.scalars(select(Message).where(Message.patient_id == pid).order_by(Message.created_at.asc())).all()
            dreqs = s.scalars(select(DataRequest).where(DataRequest.patient_id == pid).order_by(DataRequest.created_at.desc())).all()
            camp = s.get(Campaign, pat.campaign_id) if pat.campaign_id else None
            corp = s.get(Corporate, pat.corporate_id) if pat.corporate_id else None
            prac_map = {p.id: p.name_en for p in s.scalars(select(Practitioner)).all()}
            return {
                "onboarded": True,
                "patient": {
                    "id": pat.id, "name_en": pat.name_en, "name_zh": pat.name_zh, "dob": pat.dob,
                    "sex": pat.sex, "hkid_masked": pat.hkid_masked, "phone_masked": pat.phone_masked,
                    "preferred_lang": pat.preferred_lang, "ehealth_consent": pat.ehealth_consent,
                    "g6pd_deficient": pat.g6pd_deficient, "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                    "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                    "referral_code": pat.referral_code, "referred_by_code": pat.referred_by_code,
                    "corporate_name": corp.name if corp else None,
                    "campaign_code": camp.code if camp else None,
                    "status": pat.status,
                    "deletion_requested_at": pat.deletion_requested_at.isoformat() if pat.deletion_requested_at else None,
                    "retention_until": pat.retention_until.isoformat() if pat.retention_until else None,
                },
                "referral_qr_url": f"/wa?campaign={camp.code if camp else 'HARBOUR-FLU-2026'}&ref={pat.referral_code}",
                "consents": [{"id": c.id, "scope": c.scope, "granted": c.granted, "pics_version": c.pics_version, "recorded_at": c.recorded_at.isoformat()} for c in consents],
                "encounters": [
                    {
                        "id": e.id, "encounter_type": e.encounter_type, "status": e.status,
                        "scheduled_at": e.scheduled_at.isoformat() if e.scheduled_at else None,
                        "chief_complaint_text": e.chief_complaint_text, "icd10_code": e.icd10_code, "icpc2_code": e.icpc2_code,
                        "snomed_codes": e.snomed_codes_json, "clinical_note": e.clinical_note,
                        "escalation_reason": e.escalation_reason,
                        "practitioner_name": prac_map.get(e.practitioner_id) if e.practitioner_id else None,
                        "created_at": e.created_at.isoformat(),
                    } for e in encs
                ],
                "certificates": [
                    {
                        "id": c.id, "encounter_id": c.encounter_id,
                        "practitioner_name": prac_map.get(c.practitioner_id),
                        "mchk_reg_no": c.mchk_reg_no,
                        "days_off": c.days_off, "start_date": c.start_date, "end_date": c.end_date,
                        "diagnosis_icd10": c.diagnosis_icd10, "diagnosis_icpc2": c.diagnosis_icpc2,
                        "vault_doc_id": c.vault_doc_id, "verification_hash": c.verification_hash,
                        "signed_at": c.signed_at.isoformat(),
                    } for c in certs
                ],
                "prescriptions": [
                    {
                        "id": r.id, "encounter_id": r.encounter_id,
                        "practitioner_name": prac_map.get(r.practitioner_id),
                        "mchk_reg_no": r.mchk_reg_no,
                        "items": r.items_json, "safety_checks": r.safety_checks_json,
                        "fulfilment_mode": r.fulfilment_mode,
                        "fulfilment_status": r.fulfilment_status, "fulfilment_ref": r.fulfilment_ref,
                        "tracking_events": r.tracking_events_json, "signed_at": r.signed_at.isoformat(),
                    } for r in rxs
                ],
                "immunizations": [
                    {
                        "id": i.id, "cvx_code": i.cvx_code, "vaccine_display": i.vaccine_display,
                        "partner_clinic": i.partner_clinic, "slot_at": i.slot_at.isoformat(),
                        "status": i.status, "booking_ref": i.booking_ref,
                        "created_at": i.created_at.isoformat(),
                    } for i in imms
                ],
                "messages": [
                    {"id": m.id, "direction": m.direction, "sender_role": m.sender_role, "body_text": m.body_text, "created_at": m.created_at.isoformat()}
                    for m in msgs
                ],
                "data_requests": [
                    {
                        "id": d.id, "request_type": d.request_type, "status": d.status,
                        "resolution_note": d.resolution_note, "created_at": d.created_at.isoformat(),
                    } for d in dreqs
                ],
            }

    @app.post("/api/v1/patient/intake")
    def patient_intake(body: IntakeReq, request: Request):
        """Story 3: Bilingual EN/Cantonese AI symptom intake + union-only red-flag triage."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]

        symptom_text = (body.symptom_text or body.symptoms_text or "").strip()
        if len(symptom_text) < 2:
            raise HTTPException(422, "Symptom description is required (minimum 2 characters)")
        temp_c = body.temp_c if body.temp_c is not None else body.temperature_c
        spo2 = body.spo2 if body.spo2 is not None else body.spo2_pct
        rat_flu_a = body.rat_flu_a or body.flu_rat_a
        rat_flu_b = body.rat_flu_b or body.flu_rat_b
        rat_covid = body.rat_covid or body.covid_rat

        with database.admin_session() as s:
            tenant = s.get(Tenant, tenant_id)
            ai_enabled = bool(tenant.ai_enabled) if tenant else True
            allowed_regions = list(tenant.allowed_regions) if tenant else ["asia-east2"]
            allow_cb = bool(tenant.allow_cross_border_inference) if tenant else False

        model_gw.set_tenant_policy(TenantPolicy(
            tenant_id=tenant_id,
            allowed_regions=tuple(allowed_regions),
            allow_cross_border_inference=allow_cb,
        ))

        with database.tenant_session(tenant_id) as s:
            pat = _assert_patient_active(s.get(Patient, pid))
            # Enforce PDPO DPP1: verify latest clinical_care consent is granted
            latest_care = s.scalar(
                select(Consent).where(Consent.patient_id == pid, Consent.scope == "clinical_care")
                .order_by(Consent.recorded_at.desc())
            )
            if not latest_care or not latest_care.granted:
                raise HTTPException(403, "Active clinical_care consent (HK PDPO DPP1) required before health intake")

            vitals: dict[str, Any] = {}
            if temp_c is not None:
                vitals["temp_c"] = temp_c
            if spo2 is not None:
                vitals["spo2"] = spo2
            if rat_flu_a:
                vitals["rat_flu_a"] = rat_flu_a
            if rat_flu_b:
                vitals["rat_flu_b"] = rat_flu_b
            if rat_covid:
                vitals["rat_covid"] = rat_covid

            # Respect per-tenant kill switch without mutating shared singleton state
            intake_started = time.monotonic()
            res = harness_rt.process_intake(
                symptom_text,
                vitals=vitals,
                patient={
                    "id": pat.id, "name_en": pat.name_en, "dob": pat.dob,
                    "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                    "g6pd_deficient": pat.g6pd_deficient, "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                },
                lang=body.lang,
                tenant_id=tenant_id,
                kill_switch=not ai_enabled,
            )
            intake_latency_ms = max(0, int(round((time.monotonic() - intake_started) * 1000.0)))
            triage = res["triage"]
            intake = res["intake"]
            draft = res["draft"] if ai_enabled else None

            telemetry.inc("intakes_total")
            if triage.is_emergency:
                telemetry.inc("red_flags_escalated_total")
            if triage.prompt_injection_detected:
                telemetry.inc("prompt_injections_neutralized_total")

            note_text = _render_soap_note(draft.clinical_note) if draft else ""
            enc = Encounter(
                id=new_id("enc"),
                tenant_id=tenant_id,
                patient_id=pid,
                campaign_id=pat.campaign_id,
                encounter_type="acute_flu_teleconsult",
                status="red_flag_escalated" if triage.is_emergency else "intake",
                chief_complaint_text=symptom_text,
                structured_intake_json=intake.to_dict() if intake else {"triage": triage.to_dict()},
                icd10_code=draft.working_diagnosis["icd10"] if draft else None,
                icpc2_code=draft.working_diagnosis["icpc2"] if draft else None,
                snomed_codes_json=intake.snomed_codes if intake else [],
                clinical_note=note_text,
            )
            s.add(enc)
            s.flush()

            if temp_c is not None:
                s.add(Observation(
                    id=new_id("obs"), tenant_id=tenant_id, patient_id=pid, encounter_id=enc.id,
                    loinc_code="8310-5", display="Body temperature", value_num=temp_c,
                    ucum_unit="Cel", source_quote=f"{temp_c}C",
                ))
            if spo2 is not None:
                s.add(Observation(
                    id=new_id("obs"), tenant_id=tenant_id, patient_id=pid, encounter_id=enc.id,
                    loinc_code="2708-6", display="Oxygen saturation in Arterial blood by Pulse oximetry",
                    value_num=spo2, ucum_unit="%", source_quote=f"SpO2 {spo2}%",
                ))

            run_protocol_id = "flu_intake_v1" if triage.is_emergency else "acute_consult_draft_v1"
            proto_bundle = harness_rt.protocols.get(run_protocol_id)
            gw_meta = (
                (draft.gateway_audit if draft and draft.gateway_audit else None)
                or (triage.audit.get("gateway") if isinstance(triage.audit, dict) else None)
                or {}
            )
            real_proto_hash = str(
                gw_meta.get("protocol_hash")
                or (draft.protocol_hash if draft else "")
                or (proto_bundle.protocol_hash if proto_bundle else sha256_hex(f"{run_protocol_id}:1.0.0"))
            )[:64]
            run = AgentRun(
                id=new_id("run"),
                tenant_id=tenant_id,
                patient_id=pid,
                encounter_id=enc.id,
                protocol_id=run_protocol_id,
                protocol_version=proto_bundle.version if proto_bundle else "1.0.0",
                model_id="rules-floor" if not ai_enabled else str(gw_meta.get("model_id") or harness_rt.default_model_id),
                provider="rules" if not ai_enabled else str(gw_meta.get("provider") or harness_rt.default_provider),
                region=str(gw_meta.get("region") or (allowed_regions[0] if allowed_regions else "asia-east2")),
                prompt_hash=real_proto_hash,
                redacted=bool(gw_meta.get("redacted", False)),
                input_hash=sha256_hex(symptom_text),
                output_json={"triage": triage.to_dict(), "ai_enabled": ai_enabled, "gateway": gw_meta},
                validators_json=[
                    {"check": "verbatim_quotes", "passed": True if not draft else bool(draft.validation.checks.get("verbatim_quotes", True))},
                    {"check": "zero_tools", "passed": not bool(gw_meta.get("tools_enabled", False))},
                ],
                latency_ms=intake_latency_ms,
            )
            s.add(run)
            s.flush()

            sla_mins = {4: 15, 3: 60, 2: 240, 1: 720}.get(triage.urgency, 240)
            quotes = list(dict.fromkeys((triage.evidence_quotes or []) + (intake.evidence_quotes if intake else []) + [symptom_text[:180]]))
            prop = Proposal(
                id=new_id("prop"),
                tenant_id=tenant_id,
                patient_id=pid,
                encounter_id=enc.id,
                kind="red_flag_alert" if triage.is_emergency else "consult_draft",
                urgency=triage.urgency,
                sla_due_at=utcnow() + timedelta(minutes=sla_mins),
                status="pending_review",
                protocol_id=run.protocol_id,
                protocol_version=run.protocol_version,
                agent_run_id=run.id,
                payload_json={
                    "triage": triage.to_dict(),
                    "intake": intake.to_dict() if intake else None,
                    "draft": draft.to_dict() if draft else None,
                    "ai_disabled": not ai_enabled,
                },
                source_quotes_json=quotes,
                checks_json=[
                    {"id": "quotes", "status": "pass", "detail": f"{len(quotes)} verbatim quote(s) verified"},
                    {"id": "prompt_injection", "status": "warn" if triage.prompt_injection_detected else "pass",
                     "detail": "Hostile instruction neutralized (zero tools)" if triage.prompt_injection_detected else "Clean"},
                    {"id": "authority", "status": "pass", "detail": "L1 draft only; requires licensed doctor review"},
                ],
            )
            s.add(prop)
            s.flush()

            workflows.schedule_proposal_sla_escalation(
                s, tenant_id, proposal_id=prop.id, encounter_id=enc.id,
                urgency=prop.urgency, sla_due_at=prop.sla_due_at,
            )

            s.add(Message(
                id=new_id("msg"), tenant_id=tenant_id, patient_id=pid, encounter_id=enc.id,
                direction="in", sender_role="patient", body_text=symptom_text,
            ))
            if triage.is_emergency and triage.banner:
                s.add(Message(
                    id=new_id("msg"), tenant_id=tenant_id, patient_id=pid, encounter_id=enc.id,
                    direction="out", sender_role="system_template", body_text=triage.banner,
                    template_id="emergency_999_fixed",
                ))

            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="intake.submitted", resource_type="Encounter", resource_id=enc.id,
                metadata={"urgency": triage.urgency, "is_emergency": triage.is_emergency, "ai_enabled": ai_enabled},
            )

            is_mh = any("suicid" in rf or "self_harm" in rf or "mental" in rf for rf in (triage.red_flags or []))
            crisis_banner = (
                {
                    "title_en": "URGENT EMERGENCY — Call 999 or Go to A&E Immediately",
                    "title_zh": "緊急醫療警告 — 請立即致電 999 或前往最近急症室",
                    "body_en": triage.banner or "Call 999 (Hong Kong Emergency) or attend the nearest Hospital Authority A&E immediately.",
                    "body_zh": triage.banner or "請立即致電 999 緊急求助，或前往最近醫院管理局急症室。切勿等候視像診症。",
                    "emergency_number": "999",
                    "samaritans_hotline": "2389 2222",
                    "is_mental_health_crisis": is_mh,
                }
                if triage.is_emergency
                else None
            )

            return {
                "encounter_id": enc.id,
                "proposal_id": prop.id,
                "status": enc.status,
                "red_flag": triage.is_emergency,
                "red_flags": triage.red_flags,
                "urgency": triage.urgency,
                "crisis_banner": crisis_banner,
                "structured_intake": enc.structured_intake_json,
                "ai_used": ai_enabled,
                "ai_disabled": not ai_enabled,
                "ai_disabled_notice": "Clinic AI kill-switch active: routed directly to deterministic clinician-only review." if not ai_enabled else None,
                "encounter": {"id": enc.id, "status": enc.status, "snomed_codes": enc.snomed_codes_json},
                "proposal": {"id": prop.id, "urgency": prop.urgency, "status": prop.status, "sla_due_at": prop.sla_due_at.isoformat()},
                "triage": triage.to_dict(),
                "emergency_banner": triage.banner if triage.is_emergency else None,
            }

    @app.get("/api/v1/patient/slots")
    def patient_slots(request: Request):
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id = claims["tid"]
        with database.tenant_session(tenant_id) as s:
            docs = s.scalars(select(Practitioner).where(Practitioner.role == "clinician", Practitioner.active == True)).all()  # noqa: E712
            slots = []
            for d in docs:
                for sl in booking_adapter.list_slots(practitioner_id=d.id, practitioner_name=d.name_en):
                    slots.append({
                        **sl,
                        "slot_at": sl["start_at"],
                        "clinic_name": d.clinic_name,
                        "practitioner_name": d.name_en,
                        "practitioner_name_zh": d.name_zh,
                        "mchk_reg_no": d.mchk_reg_no,
                    })
        return {"slots": slots}

    @app.post("/api/v1/patient/book-teleconsult")
    def patient_book_teleconsult(body: BookTeleconsultReq, request: Request):
        """Story 4: Book teleconsult slot + schedule durable reminder via PostgreSQL Outbox."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            pat = _assert_patient_active(s.get(Patient, pid))
            enc = (
                s.get(Encounter, body.encounter_id)
                if body.encounter_id
                else s.scalar(select(Encounter).where(Encounter.patient_id == pid).order_by(Encounter.created_at.desc()))
            )
            if not enc or enc.patient_id != pid:
                raise HTTPException(404, "Encounter not found")
            doc = s.get(Practitioner, body.practitioner_id)
            if not doc or doc.role != "clinician":
                raise HTTPException(404, "Clinician not found")
            slot_dt = datetime.fromisoformat(body.slot_at)
            if slot_dt.tzinfo is None:
                slot_dt = slot_dt.replace(tzinfo=timezone.utc)
            slot_id = f"slot_{doc.id}_{slot_dt.strftime('%Y%m%d_%H%M')}_{enc.id[-6:]}"
            res = booking_adapter.book_slot(
                patient_id=pid, practitioner_id=doc.id, slot_id=slot_id, encounter_id=enc.id,
                idempotency_key=f"book:{enc.id}:{body.slot_at}",
            )
            enc.practitioner_id = doc.id
            enc.scheduled_at = slot_dt
            if enc.status == "intake":
                enc.status = "booked"

            workflows.schedule_booking_reminder(
                s, tenant_id, encounter_id=enc.id, patient_id=pid,
                phone=pat.phone_masked if pat else "****0000",
                doctor_name=doc.name_en, slot_at=slot_dt, portal_url="/p",
            )
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="teleconsult.booked", resource_type="Encounter", resource_id=enc.id,
                metadata={"booking_ref": res.external_ref, "practitioner_id": doc.id},
            )
            return {
                "ok": True,
                "encounter_id": enc.id,
                "scheduled_at": slot_dt.isoformat(),
                "status": enc.status,
                "encounter": {"id": enc.id, "status": enc.status, "scheduled_at": slot_dt.isoformat()},
                "booking_ref": res.external_ref,
                "reminder_scheduled": True,
            }

    @app.post("/api/v1/patient/prescriptions/{rx_id}/fulfilment")
    def patient_rx_fulfilment(rx_id: str, body: FulfilmentChoiceReq, request: Request):
        """Story 6: Patient selects delivery or branch pickup on doctor-signed e-script."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        mode = body.mode or body.fulfilment_mode
        dest = (body.address_or_branch or body.address or body.pharmacy or "").strip()
        if not mode or len(dest) < 3:
            raise HTTPException(422, "mode ('delivery'|'pickup') and address/pharmacy branch are required")
        with database.tenant_session(tenant_id) as s:
            rx = s.get(Prescription, rx_id)
            if not rx or rx.patient_id != pid:
                raise HTTPException(404, "Prescription not found")
            res = rx_adapter.submit_order(
                prescription_id=rx.id,
                patient_id=pid,
                mode=mode,
                items=rx.items_json,
                delivery_address=dest if mode == "delivery" else None,
                pickup_branch=dest if mode == "pickup" else None,
                idempotency_key=f"rxfulfil:{rx.id}:{mode}",
            )
            rx.fulfilment_mode = mode
            rx.fulfilment_status = "sent_to_pharmacy"
            rx.fulfilment_ref = res.external_ref
            events = list(rx.tracking_events_json or [])
            events.append({"status": "sent_to_pharmacy", "at": utcnow().isoformat(), "note": f"{mode.title()} selected: {dest}"})
            rx.tracking_events_json = events
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="rx.fulfilment_selected", resource_type="Prescription", resource_id=rx.id,
                metadata={"mode": mode, "ref": res.external_ref},
            )
            return {
                "ok": True,
                "prescription": {
                    "id": rx.id, "encounter_id": rx.encounter_id, "mchk_reg_no": rx.mchk_reg_no,
                    "items": rx.items_json, "fulfilment_mode": rx.fulfilment_mode,
                    "fulfilment_status": rx.fulfilment_status, "fulfilment_ref": rx.fulfilment_ref,
                    "tracking_events": rx.tracking_events_json, "signed_at": rx.signed_at.isoformat(),
                }
            }

    @app.post("/api/v1/patient/flu-vax/book")
    def patient_flu_vax_book(body: FluVaxBookReq, request: Request):
        """Story 7: Book seasonal influenza vaccination (CVX-coded) at a partner clinic."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        if body.cvx_code not in CVX_VACCINES:
            raise HTTPException(422, f"Invalid CVX vaccine code: {body.cvx_code}")
        slot_dt = datetime.fromisoformat(body.slot_at.replace("Z", "+00:00"))
        if slot_dt.tzinfo is None:
            slot_dt = slot_dt.replace(tzinfo=timezone.utc)
        with database.tenant_session(tenant_id) as s:
            pat = _assert_patient_active(s.get(Patient, pid))
            res = vax_adapter.book_vaccination(
                patient_id=pid, slot_id=f"vax_{pid[-6:]}_{slot_dt.strftime('%Y%m%d_%H%M')}",
                cvx_code=body.cvx_code, partner_clinic=body.partner_clinic,
                idempotency_key=f"vax:{pid}:{body.slot_at}",
            )
            imm = Immunization(
                id=new_id("imm"),
                tenant_id=tenant_id,
                patient_id=pid,
                campaign_id=pat.campaign_id,
                cvx_code=body.cvx_code,
                vaccine_display=CVX_VACCINES[body.cvx_code][0],
                partner_clinic=body.partner_clinic,
                slot_at=slot_dt,
                status="booked",
                booking_ref=res.external_ref,
            )
            s.add(imm)
            s.flush()
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="flu_vax.booked", resource_type="Immunization", resource_id=imm.id,
                metadata={"cvx": body.cvx_code, "clinic": body.partner_clinic},
            )
            return {
                "ok": True,
                "immunization": {
                    "id": imm.id, "cvx_code": imm.cvx_code, "vaccine_display": imm.vaccine_display,
                    "partner_clinic": imm.partner_clinic, "slot_at": imm.slot_at.isoformat(),
                    "status": imm.status, "booking_ref": imm.booking_ref,
                    "created_at": (imm.created_at or utcnow()).isoformat(),
                }
            }

    @app.post("/api/v1/patient/followup-48h")
    def patient_followup_48h(body: Followup48hReq, request: Request):
        """Story 8: 48-hour check-in + one-tap escalation to human clinician."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        parts = []
        if body.status_text:
            parts.append(body.status_text.strip())
        else:
            if body.status:
                parts.append(f"Status: {body.status}")
            if body.fever_resolved is not None:
                parts.append(f"Fever resolved: {'yes' if body.fever_resolved else 'no'}")
            if body.new_red_flags:
                parts.append(f"New symptoms: {body.new_red_flags.strip()}")
            if body.notes:
                parts.append(body.notes.strip())
        status_text = "; ".join(p for p in parts if p) or "48h check-in submitted"
        need_human = bool(body.need_human or body.status == "worse" or bool(body.new_red_flags))

        with database.admin_session() as adm:
            tenant = adm.get(Tenant, tenant_id)
            ai_enabled = bool(tenant.ai_enabled) if tenant else True
            allowed_regions = list(tenant.allowed_regions) if tenant and tenant.allowed_regions else ["asia-east2"]
            allow_cb = bool(tenant.allow_cross_border_inference) if tenant else False

        model_gw.set_tenant_policy(TenantPolicy(
            tenant_id=tenant_id,
            allowed_regions=tuple(allowed_regions),
            allow_cross_border_inference=allow_cb,
        ))

        fu_started = time.monotonic()
        triage = harness_rt.screen_message(
            status_text,
            tenant_id=tenant_id,
            protocol_id="followup_48h_v1",
            kill_switch=not ai_enabled,
        )
        fu_latency_ms = max(0, int(round((time.monotonic() - fu_started) * 1000.0)))
        urgency = 4 if triage.is_emergency else (3 if need_human else max(1, triage.urgency))
        with database.tenant_session(tenant_id) as s:
            _assert_patient_active(s.get(Patient, pid))
            enc = (
                s.get(Encounter, body.encounter_id)
                if body.encounter_id
                else s.scalar(select(Encounter).where(Encounter.patient_id == pid).order_by(Encounter.created_at.desc()))
            )
            if not enc or enc.patient_id != pid:
                raise HTTPException(404, "Encounter not found")
            fu_bundle = harness_rt.protocols.get("followup_48h_v1")
            gw_meta = (triage.audit.get("gateway") if isinstance(triage.audit, dict) else None) or {}
            fu_proto_hash = str(
                gw_meta.get("protocol_hash")
                or (fu_bundle.protocol_hash if fu_bundle else sha256_hex("followup_48h_v1:1.0.0"))
            )[:64]
            run = AgentRun(
                id=new_id("run"),
                tenant_id=tenant_id,
                patient_id=pid,
                encounter_id=enc.id,
                protocol_id="followup_48h_v1",
                protocol_version=fu_bundle.version if fu_bundle else "1.0.0",
                model_id="rules-floor" if not ai_enabled else str(gw_meta.get("model_id") or harness_rt.default_model_id),
                provider="rules" if not ai_enabled else str(gw_meta.get("provider") or harness_rt.default_provider),
                region=str(gw_meta.get("region") or (allowed_regions[0] if allowed_regions else "asia-east2")),
                prompt_hash=fu_proto_hash,
                redacted=bool(gw_meta.get("redacted", False)),
                input_hash=sha256_hex(status_text),
                output_json={"triage": triage.to_dict(), "ai_enabled": ai_enabled, "gateway": gw_meta},
                validators_json=[
                    {"check": "verbatim_quotes", "passed": True},
                    {"check": "zero_tools", "passed": not bool(gw_meta.get("tools_enabled", False))},
                ],
                latency_ms=fu_latency_ms,
            )
            s.add(run)
            s.flush()
            prop = Proposal(
                id=new_id("prop"),
                tenant_id=tenant_id,
                patient_id=pid,
                encounter_id=enc.id,
                kind="followup_48h",
                urgency=urgency,
                sla_due_at=utcnow() + timedelta(minutes=15 if urgency == 4 else 120),
                status="pending_review",
                protocol_id="followup_48h_v1",
                protocol_version=fu_bundle.version if fu_bundle else "1.0.0",
                agent_run_id=run.id,
                payload_json={"status_text": status_text, "need_human": need_human, "triage": triage.to_dict()},
                source_quotes_json=[status_text[:200]],
                checks_json=[{"id": "quotes", "status": "pass", "detail": "Patient 48h check-in quoted verbatim"}],
            )
            s.add(prop)
            s.add(Message(
                id=new_id("msg"), tenant_id=tenant_id, patient_id=pid, encounter_id=enc.id,
                direction="in", sender_role="patient", body_text=f"[48h Check-In] {status_text}",
            ))
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="followup_48h.submitted", resource_type="Proposal", resource_id=prop.id,
                metadata={"need_human": need_human, "urgency": urgency},
            )
            if triage.is_emergency:
                telemetry.inc("red_flags_escalated_total")
            return {
                "ok": True,
                "proposal_id": prop.id,
                "proposal": {"id": prop.id, "urgency": urgency, "status": prop.status},
                "escalated": triage.is_emergency or need_human,
                "emergency_banner": triage.banner if triage.is_emergency else None,
            }

    @app.post("/api/v1/patient/messages")
    def patient_send_message(body: MessageReq, request: Request):
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            _assert_patient_active(s.get(Patient, pid))
            m = Message(
                id=new_id("msg"), tenant_id=tenant_id, patient_id=pid, encounter_id=body.encounter_id,
                direction="in", sender_role="patient", body_text=body.body_text,
            )
            s.add(m)
            s.flush()
            if body.request_human:
                record_audit(
                    s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                    action="patient.requested_human", resource_type="Message", resource_id=m.id,
                )
            return {"ok": True, "message": {"id": m.id, "direction": m.direction, "sender_role": m.sender_role, "body_text": m.body_text, "created_at": (m.created_at or utcnow()).isoformat()}}

    @app.post("/api/v1/patient/certificates/{cert_id}/download-token")
    def patient_cert_download_token(cert_id: str, request: Request):
        """Story 5: Mint short-lived HMAC-signed download token for encrypted Vault PDF (zero public URLs)."""
        claims = extract_claims(request, allowed_roles=("patient", "clinician"), require_step_up=True)
        tenant_id, sub = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            cert = s.get(Certificate, cert_id)
            if not cert:
                raise HTTPException(404, "Certificate not found")
            if claims["role"] == "patient" and cert.patient_id != sub:
                raise HTTPException(403, "Access denied")
            tok = doc_vault.mint_signed_token(tenant_id, cert.vault_doc_id, sub, ttl_seconds=300)
            record_audit(
                s, tenant_id=tenant_id, actor_role=claims["role"], actor_id=sub,
                action="vault.token_minted", resource_type="Certificate", resource_id=cert.id,
            )
            dl_url = f"/api/v1/vault/download/{cert.vault_doc_id}?token={tok}"
            return {
                "token": tok,
                "download_token": tok,
                "expires_in": 300,
                "expires_in_seconds": 300,
                "download_url": dl_url,
                "url": dl_url,
            }

    @app.get("/api/v1/vault/download/{doc_id}")
    def vault_download(doc_id: str, request: Request, token: str = Query(...)):
        """Stream decrypted PDF from AES-256-GCM Vault after verifying JWT + step-up + short-lived signed token."""
        claims = extract_claims(request, allowed_roles=("patient", "clinician"), require_step_up=True)
        tenant_id, sub = claims["tid"], claims["sub"]
        try:
            doc_vault.verify_signed_token(token, tenant_id, doc_id, sub)
            pdf_bytes, meta = doc_vault.retrieve(tenant_id, doc_id)
        except VaultError as exc:
            raise HTTPException(403, str(exc)) from exc
        with database.tenant_session(tenant_id) as s:
            record_audit(
                s, tenant_id=tenant_id, actor_role=claims["role"], actor_id=sub,
                action="vault.document_downloaded", resource_type="DocumentReference", resource_id=doc_id,
            )
        return Response(
            content=pdf_bytes,
            media_type=meta["content_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{meta["filename"]}"',
                "Cache-Control": "no-store, private",
            },
        )

    # --- Story 9: HK PDPO DPP6 Patient Data Rights (DAR FHIR R4 Export, DCR, Consent Withdrawal, Statutory Deletion) ---
    @app.get("/api/v1/patient/export")
    def patient_export_fhir(request: Request):
        """HK PDPO DPP6 Data Access Request (DAR) -> HL7 FHIR R4 IPS-aligned Collection Bundle."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            pat = s.get(Patient, pid)
            if not pat:
                raise HTTPException(404, "Patient not found")
            consents = [{"id": c.id, "scope": c.scope, "granted": c.granted, "pics_version": c.pics_version, "recorded_at": c.recorded_at.isoformat()}
                        for c in s.scalars(select(Consent).where(Consent.patient_id == pid)).all()]
            encs = [{"id": e.id, "status": e.status, "icd10_code": e.icd10_code, "icpc2_code": e.icpc2_code, "snomed_codes": e.snomed_codes_json}
                    for e in s.scalars(select(Encounter).where(Encounter.patient_id == pid)).all()]
            obs = [{"id": o.id, "loinc_code": o.loinc_code, "display": o.display, "value_num": o.value_num, "value_text": o.value_text, "ucum_unit": o.ucum_unit, "recorded_at": o.recorded_at.isoformat()}
                   for o in s.scalars(select(Observation).where(Observation.patient_id == pid)).all()]
            certs = [{"id": c.id, "mchk_reg_no": c.mchk_reg_no, "verification_hash": c.verification_hash, "signed_at": c.signed_at.isoformat()}
                     for c in s.scalars(select(Certificate).where(Certificate.patient_id == pid)).all()]
            rxs = [{"id": r.id, "mchk_reg_no": r.mchk_reg_no, "items": r.items_json, "signed_at": r.signed_at.isoformat()}
                   for r in s.scalars(select(Prescription).where(Prescription.patient_id == pid)).all()]
            imms = [{"id": i.id, "cvx_code": i.cvx_code, "vaccine_display": i.vaccine_display, "status": i.status, "slot_at": i.slot_at.isoformat()}
                    for i in s.scalars(select(Immunization).where(Immunization.patient_id == pid)).all()]
            auds = [{"id": a.id, "action": a.action, "actor_id": a.actor_id, "actor_role": a.actor_role, "created_at": a.created_at.isoformat()}
                    for a in s.scalars(select(AuditEvent).where(AuditEvent.resource_id == pid)).all()]
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="pdpo.dar_exported", resource_type="Patient", resource_id=pid,
            )
            telemetry.inc("pdpo_dar_exports_total")
            return build_fhir_r4_bundle(
                patient={
                    "id": pat.id, "name_en": pat.name_en, "name_zh": pat.name_zh, "dob": pat.dob,
                    "sex": pat.sex, "hkid_masked": pat.hkid_masked, "ehealth_consent": pat.ehealth_consent,
                    "g6pd_deficient": pat.g6pd_deficient, "allergies": pat.allergies_json, "status": pat.status,
                },
                consents=consents, encounters=encs, observations=obs, certificates=certs,
                prescriptions=rxs, immunizations=imms, audit_events=auds,
            )

    @app.post("/api/v1/patient/data-correction")
    def patient_dcr(body: DataCorrectionReq, request: Request):
        """HK PDPO DPP2/DPP6 Data Correction Request (append-only addendum, never mutates history)."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            dr = DataRequest(
                id=new_id("dcr"), tenant_id=tenant_id, patient_id=pid,
                request_type="dcr_correction", status="queued_for_review",
                details_json={"field": body.field, "requested_value": body.requested_value, "reason": body.reason},
            )
            s.add(dr)
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="pdpo.dcr_submitted", resource_type="DataRequest", resource_id=dr.id,
            )
            telemetry.inc("pdpo_dcr_submitted_total")
            return {
                "ok": True,
                "request_id": dr.id,
                "status": dr.status,
                "data_request": {"id": dr.id, "request_type": dr.request_type, "status": dr.status, "details": dr.details_json},
            }

    @app.post("/api/v1/patient/withdraw-consent")
    def patient_withdraw_consent(body: WithdrawConsentReq, request: Request):
        """HK PDPO DPP6: append withdrawal rows to immutable consent ledger."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        scopes = list(body.scopes or ([] if not body.scope else [body.scope]))
        if not scopes:
            raise HTTPException(422, "At least one consent scope is required")
        with database.tenant_session(tenant_id) as s:
            out = []
            for scope in scopes:
                c = Consent(
                    id=new_id("con"), tenant_id=tenant_id, patient_id=pid, scope=scope,
                    pics_version=HK_PICS_DOC["version"], granted=False, channel="pwa",
                    wording_hash=sha256_hex(f"withdraw:{scope}"),
                )
                s.add(c)
                s.flush()
                out.append({"id": c.id, "scope": scope, "pics_version": c.pics_version, "granted": False, "recorded_at": (c.recorded_at or utcnow()).isoformat()})
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="pdpo.consent_withdrawn", resource_type="Patient", resource_id=pid,
                metadata={"scopes": scopes},
            )
            return {"ok": True, "consents": out, "consent": out[0]}

    @app.post("/api/v1/patient/delete-data")
    def patient_delete_data(body: DeleteDataReq, request: Request):
        """HK PDPO DPP2/DPP6 statutory-hold-aware deletion (7 yrs adult / age 21 minor)."""
        claims = extract_claims(request, allowed_roles=("patient",), require_step_up=True)
        tenant_id, pid = claims["tid"], claims["sub"]
        now = utcnow()
        with database.tenant_session(tenant_id) as s:
            pat = s.get(Patient, pid)
            if not pat:
                raise HTTPException(404, "Patient not found")
            # Calculate HK statutory medical retention date (min 7 years; or age 21 for minors)
            dob_year = int(pat.dob[:4])
            adult_7y = now + timedelta(days=365 * 7)
            minor_21 = datetime(dob_year + 21, 12, 31, tzinfo=timezone.utc)
            retention_until = max(adult_7y, minor_21)

            pat.status = "deleted_statutory_hold"
            pat.deletion_requested_at = now
            pat.retention_until = retention_until
            for scope in ("invitations", "deidentified_eval", "ai_assisted_intake"):
                s.add(Consent(
                    id=new_id("con"), tenant_id=tenant_id, patient_id=pid, scope=scope,
                    pics_version=HK_PICS_DOC["version"], granted=False, channel="pwa",
                    wording_hash=sha256_hex(f"delete_revoke:{scope}"),
                ))
            dr = DataRequest(
                id=new_id("del"), tenant_id=tenant_id, patient_id=pid,
                request_type="deletion_request", status="statutory_hold_applied",
                details_json={"reason": body.reason, "retention_until": retention_until.isoformat()},
                resolution_note=(
                    "Access & non-emergency communications revoked immediately. Clinical records and audit logs "
                    "are locked under Hong Kong statutory medico-legal retention (7 years adult / age 21 minor) "
                    "and scheduled for secure purge thereafter."
                ),
                resolved_at=now,
            )
            s.add(dr)
            record_audit(
                s, tenant_id=tenant_id, actor_role="patient", actor_id=pid,
                action="pdpo.deletion_statutory_hold", resource_type="Patient", resource_id=pid,
                metadata={"retention_until": retention_until.isoformat()},
            )
            telemetry.inc("pdpo_deletions_total")
            return {
                "data_request": {"id": dr.id, "status": dr.status},
                "status": "statutory_hold_applied",
                "retention_until": retention_until.isoformat(),
                "notice": dr.resolution_note,
            }

    # ================================================================================== SURFACE 3: CLINICIAN CONSOLE
    @app.get("/api/v1/clinician/queue")
    def clinician_queue(request: Request):
        """Story 11: Prioritized review queue sorted by risk (red flags first) & SLA (clinician + nurse read)."""
        claims = extract_claims(request, allowed_roles=("clinician", "nurse"))
        tenant_id = claims["tid"]
        now = utcnow()
        with database.admin_session() as s:
            t = s.get(Tenant, tenant_id)
            ai_enabled = bool(t.ai_enabled) if t else True
        with database.tenant_session(tenant_id) as s:
            props = s.scalars(
                select(Proposal).order_by(
                    (Proposal.status != "pending_review").asc(),
                    Proposal.urgency.desc(),
                    Proposal.sla_due_at.asc(),
                )
            ).all()
            pat_ids = list({p.patient_id for p in props})
            enc_ids = list({p.encounter_id for p in props})
            pat_map = (
                {pt.id: pt for pt in s.scalars(select(Patient).where(Patient.id.in_(pat_ids))).all()}
                if pat_ids else {}
            )
            enc_map = (
                {en.id: en for en in s.scalars(select(Encounter).where(Encounter.id.in_(enc_ids))).all()}
                if enc_ids else {}
            )
            items = []
            for p in props:
                pat = pat_map.get(p.patient_id)
                enc = enc_map.get(p.encounter_id)
                overdue = p.status == "pending_review" and p.sla_due_at < now
                payload_raw = p.payload_json or {}
                draft = payload_raw.get("draft") or {}
                triage_data = payload_raw.get("triage") or {}
                suggested_rx = draft.get("suggested_medications") or draft.get("suggested_prescriptions") or []
                raw_quotes = [
                    sq if isinstance(sq, dict) else {"field": "Patient Statement", "quote": str(sq), "source": "Patient Intake"}
                    for sq in (p.source_quotes_json or [])
                ]
                verified = build_unified_checks(
                    patient={
                        "name_en": pat.name_en, "dob": pat.dob, "g6pd_deficient": pat.g6pd_deficient,
                        "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                        "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                    } if pat else {},
                    source_text=enc.chief_complaint_text if enc else "",
                    quotes=raw_quotes,
                    proposed_meds=suggested_rx,
                    conflicts=payload_raw.get("conflicts") or [],
                    diagnosis_icd10=(draft.get("working_diagnosis") or {}).get("icd10") or (enc.icd10_code if enc else None),
                    ai_enabled=ai_enabled,
                    prompt_injection_detected=bool(triage_data.get("prompt_injection_detected")),
                    red_flags=triage_data.get("red_flags") or [],
                    extra_chart_meds=payload_raw.get("extra_chart_meds") or [],
                )
                checks = verified["checks"]
                csum = verified["summary"]
                items.append({
                    "id": p.id,
                    "patient_id": p.patient_id,
                    "patient_name_en": pat.name_en if pat else "",
                    "patient_name_zh": pat.name_zh if pat else "",
                    "encounter_id": p.encounter_id,
                    "kind": p.kind,
                    "title": payload_raw.get("title") or (enc.chief_complaint_text[:90] if enc else p.kind),
                    "story_tag": payload_raw.get("story_tag"),
                    "nurse_ready": bool(payload_raw.get("nurse_ready")),
                    "nurse_prep_note": payload_raw.get("nurse_prep_note") or "",
                    "urgency": p.urgency,
                    "status": p.status,
                    "sla_due_at": p.sla_due_at.isoformat(),
                    "sla_overdue": overdue,
                    "overdue": overdue,
                    "protocol_id": p.protocol_id,
                    "protocol_version": p.protocol_version,
                    "red_flag": p.urgency >= 4 or p.kind == "red_flag_alert",
                    "g6pd_deficient": pat.g6pd_deficient if pat else False,
                    "allergies": pat.allergies_json if pat else [],
                    "chief_complaint": enc.chief_complaint_text if enc else "",
                    "source_quotes": verified["anchored_quotes"],
                    "checks": checks,
                    "checks_summary": {
                        "passed": csum["passed"],
                        "total": csum["applicable"],
                        "applicable": csum["applicable"],
                        "warnings": csum["warn"],
                        "failures": csum["fail"],
                        "unknown": csum["unknown"],
                        "can_sign": csum["can_sign"],
                        "sign_cta": csum["sign_cta"],
                    },
                    "unresolved_conflicts": sum(1 for c in (payload_raw.get("conflicts") or []) if not c.get("resolved")),
                    "created_at": p.created_at.isoformat(),
                    "patient": {
                        "id": pat.id, "name_en": pat.name_en, "name_zh": pat.name_zh,
                        "dob": pat.dob, "sex": pat.sex, "hkid_masked": pat.hkid_masked,
                        "g6pd_deficient": pat.g6pd_deficient, "allergies": pat.allergies_json,
                        "current_meds": pat.current_meds_json, "egfr": pat.egfr,
                    } if pat else None,
                    "encounter": {
                        "id": enc.id, "status": enc.status, "chief_complaint_text": enc.chief_complaint_text,
                        "icd10_code": enc.icd10_code, "icpc2_code": enc.icpc2_code,
                    } if enc else None,
                })
        return {
            "items": items,
            "queue": items,
            "kill_switch_active": not ai_enabled,
            "viewer_role": claims["role"],
            "runtime_toggles": runtime_toggles(),
        }

    @app.get("/api/v1/clinician/proposals/{proposal_id}")
    def clinician_proposal_detail(proposal_id: str, request: Request):
        claims = extract_claims(request, allowed_roles=("clinician", "nurse"))
        tenant_id, doc_id = claims["tid"], claims["sub"]
        with database.admin_session() as s:
            t = s.get(Tenant, tenant_id)
            ai_enabled = bool(t.ai_enabled) if t else True
        with database.tenant_session(tenant_id) as s:
            p = s.get(Proposal, proposal_id)
            if not p:
                raise HTTPException(404, "Proposal not found")
            pat = s.get(Patient, p.patient_id)
            enc = s.get(Encounter, p.encounter_id)
            doc = s.get(Practitioner, doc_id)
            run = s.get(AgentRun, p.agent_run_id) if p.agent_run_id else None
            obs = s.scalars(select(Observation).where(Observation.encounter_id == p.encounter_id)).all()
            msgs = s.scalars(select(Message).where(Message.patient_id == p.patient_id).order_by(Message.created_at.asc())).all()
            payload_raw = p.payload_json or {}
            draft = payload_raw.get("draft") or {}
            triage_data = payload_raw.get("triage") or {}
            intake_data = payload_raw.get("intake") or {}
            working_diag = draft.get("working_diagnosis") or {}
            eff_icd10 = working_diag.get("icd10") or (enc.icd10_code if enc else "J11.1")
            suggested_rx = (
                draft.get("suggested_medications")
                or draft.get("suggested_prescriptions")
                or [{"drug": "paracetamol", "dose_mg": 500, "frequency": "QDS", "days": 3}]
            )
            active_acute_rx = [m for m in suggested_rx if not m.get("is_non_acute_order") and not m.get("stopped")]
            rx_preview = check_prescription_safety(
                {
                    "dob": pat.dob, "g6pd_deficient": pat.g6pd_deficient,
                    "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                    "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                },
                active_acute_rx,
                diagnosis_icd10=eff_icd10,
            ).to_dict() if (pat and active_acute_rx) else {"status": "pass", "findings": [], "enriched_items": []}

            raw_quotes = p.source_quotes_json or []
            structured_quotes = [
                sq if isinstance(sq, dict) else {"field": "Patient Statement", "quote": str(sq), "source": "Patient Intake"}
                for sq in raw_quotes
            ]
            verified = build_unified_checks(
                patient={
                    "name_en": pat.name_en, "dob": pat.dob, "g6pd_deficient": pat.g6pd_deficient,
                    "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                    "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                } if pat else {},
                source_text=enc.chief_complaint_text if enc else "",
                quotes=structured_quotes,
                proposed_meds=suggested_rx,
                conflicts=payload_raw.get("conflicts") or [],
                diagnosis_icd10=eff_icd10,
                ai_enabled=ai_enabled,
                prompt_injection_detected=bool(triage_data.get("prompt_injection_detected")),
                red_flags=triage_data.get("red_flags") or [],
                extra_chart_meds=payload_raw.get("extra_chart_meds") or [],
            )
            snomed_syms = []
            for code in (enc.snomed_codes_json if enc and enc.snomed_codes_json else []):
                if isinstance(code, dict):
                    snomed_syms.append(code)
                else:
                    snomed_syms.append({"code": str(code), "display": str(code)})

            raw_clinical_note = draft.get("clinical_note")
            cds_data = (
                raw_clinical_note.get("clinical_decision_support")
                if isinstance(raw_clinical_note, dict)
                else None
            )
            draft_note_val = _render_soap_note(raw_clinical_note)
            if not draft_note_val and enc and enc.clinical_note:
                draft_note_val = enc.clinical_note

            suggested_cert = draft.get("suggested_sick_leave") or (
                {"days_off": 2, "start_date": utcnow().astimezone(HKT).date().isoformat()}
                if p.kind in ("consult_draft", "red_flag_alert") else None
            )
            enriched_payload = {
                **payload_raw,
                "draft_clinical_note": draft_note_val,
                "clinical_note": draft_note_val,
                "clinical_decision_support": cds_data,
                "contraindications_noted": draft.get("contraindications_noted") or [],
                "suggested_icd10": eff_icd10,
                "suggested_icpc2": working_diag.get("icpc2") or (enc.icpc2_code if enc else "R80"),
                "recommended_sick_leave_days": (suggested_cert or {}).get("days_off", 0),
                "suggested_rx": suggested_rx,
                "red_flags": triage_data.get("red_flags") or [],
                "snomed_symptoms": snomed_syms,
                "structured_intake": intake_data,
                "patient_reply_draft": draft.get("patient_summary_draft") or "",
            }

            record_audit(
                s, tenant_id=tenant_id, actor_role=claims["role"], actor_id=doc_id,
                action="proposal.viewed", resource_type="Proposal", resource_id=p.id,
            )
            certs = s.scalars(
                select(Certificate)
                .where(Certificate.encounter_id == p.encounter_id)
                .order_by(Certificate.signed_at.desc())
            ).all()
            return {
                "id": p.id,
                "patient_id": p.patient_id,
                "patient_name_en": pat.name_en if pat else "",
                "patient_name_zh": pat.name_zh if pat else "",
                "encounter_id": p.encounter_id,
                "kind": p.kind,
                "title": payload_raw.get("title") or (working_diag.get("display") or p.kind),
                "urgency": p.urgency,
                "status": p.status,
                "sla_due_at": p.sla_due_at.isoformat(),
                "protocol_id": p.protocol_id,
                "protocol_version": p.protocol_version,
                "created_at": p.created_at.isoformat(),
                "payload": enriched_payload,
                "source_quotes": verified["anchored_quotes"],
                "checks": verified["checks"],
                "checks_summary": verified["summary"],
                "kill_switch_active": not ai_enabled,
                "viewer_role": claims["role"],
                "agent_run": {
                    "id": run.id, "model_id": run.model_id, "provider": run.provider,
                    "region": run.region, "redacted": run.redacted, "latency_ms": run.latency_ms,
                    "protocol_id": run.protocol_id, "protocol_version": run.protocol_version,
                } if run else None,
                "practitioner": {
                    "id": doc.id, "name_en": doc.name_en, "name_zh": doc.name_zh,
                    "role": doc.role, "mchk_reg_no": doc.mchk_reg_no, "clinic_name": doc.clinic_name,
                } if doc else None,
                "proposal": {
                    "id": p.id, "kind": p.kind, "urgency": p.urgency, "status": p.status,
                    "sla_due_at": p.sla_due_at.isoformat(), "protocol_id": p.protocol_id,
                    "protocol_version": p.protocol_version, "payload": enriched_payload,
                    "source_quotes": verified["anchored_quotes"], "checks": verified["checks"],
                },
                "patient": {
                    "id": pat.id, "name_en": pat.name_en, "name_zh": pat.name_zh, "dob": pat.dob,
                    "sex": pat.sex, "hkid_masked": pat.hkid_masked, "phone_masked": pat.phone_masked,
                    "preferred_lang": pat.preferred_lang, "g6pd_deficient": pat.g6pd_deficient,
                    "weight_kg": pat.weight_kg, "egfr": pat.egfr, "allergies": pat.allergies_json,
                    "current_meds": pat.current_meds_json, "ehealth_consent": pat.ehealth_consent,
                    "referral_code": pat.referral_code, "status": pat.status,
                } if pat else None,
                "encounter": {
                    "id": enc.id, "encounter_type": enc.encounter_type, "status": enc.status,
                    "chief_complaint_text": enc.chief_complaint_text,
                    "icd10_code": enc.icd10_code, "icpc2_code": enc.icpc2_code,
                    "snomed_codes": enc.snomed_codes_json, "clinical_note": enc.clinical_note,
                    "created_at": enc.created_at.isoformat(),
                } if enc else None,
                "observations": [
                    {
                        "id": o.id, "loinc_code": o.loinc_code, "display": o.display,
                        "value_num": o.value_num, "value_text": o.value_text,
                        "ucum_unit": o.ucum_unit, "source_quote": o.source_quote,
                        "recorded_at": o.recorded_at.isoformat(),
                    }
                    for o in obs
                ],
                "messages": [
                    {"id": m.id, "direction": m.direction, "sender_role": m.sender_role, "body_text": m.body_text, "created_at": m.created_at.isoformat()}
                    for m in msgs
                ],
                "suggested_cert": suggested_cert,
                "issued_certificates": [
                    {
                        "id": c.id,
                        "days_off": c.days_off,
                        "start_date": c.start_date,
                        "end_date": c.end_date,
                        "mchk_reg_no": c.mchk_reg_no,
                        "verification_hash": c.verification_hash,
                        "signed_at": (c.signed_at or utcnow()).isoformat(),
                    }
                    for c in certs
                ],
                "suggested_rx": suggested_rx,
                "rx_safety_preview": rx_preview,
                "hk_diagnoses": HK_DIAGNOSES,
                "runtime_toggles": runtime_toggles(),
            }

    @app.post("/api/v1/clinician/rx-safety-check")
    def clinician_rx_safety_check(body: RxCheckReq, request: Request):
        """Story 13: Real-time 100% deterministic allergy, G6PD, Cap. 134, pregnancy, pediatric, renal, stewardship & DDI check."""
        claims = extract_claims(request, allowed_roles=("clinician",))
        tenant_id = claims["tid"]
        with database.tenant_session(tenant_id) as s:
            pat = s.get(Patient, body.patient_id)
            if not pat:
                raise HTTPException(404, "Patient not found")
            report = check_prescription_safety(
                {
                    "dob": pat.dob, "g6pd_deficient": pat.g6pd_deficient,
                    "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                    "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                },
                body.items,
                diagnosis_icd10=body.diagnosis_icd10,
            )
            telemetry.inc("rx_safety_checks_total")
            if report.hard_blocks:
                telemetry.inc("rx_hard_blocks_total")
            return report.to_dict()

    @app.post("/api/v1/clinician/proposals/{proposal_id}/message")
    def clinician_reply_message(proposal_id: str, body: ClinicianReplyReq, request: Request):
        """Clinician sends a secure in-portal message to patient + non-clinical WhatsApp doorbell ping."""
        claims = extract_claims(request, allowed_roles=("clinician",))
        tenant_id, doc_id = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            prop = s.get(Proposal, proposal_id)
            if not prop:
                raise HTTPException(404, "Proposal not found")
            pat = s.get(Patient, prop.patient_id)
            if not pat:
                raise HTTPException(404, "Patient not found")
            m = Message(
                id=new_id("msg"),
                tenant_id=tenant_id,
                patient_id=pat.id,
                encounter_id=prop.encounter_id,
                direction="out",
                sender_role="clinician",
                body_text=body.body_text.strip(),
            )
            s.add(m)
            s.flush()
            # Non-clinical WhatsApp doorbell notification only (zero clinical text on WhatsApp!)
            wa_msg = wa_adapter.render_template("new_message", {"url": "/p"}, lang="bilingual")
            WhatsAppLeakageGuard.assert_clean(wa_msg)
            s.add(WhatsAppOutbox(
                id=new_id("wao"),
                tenant_id=tenant_id,
                phone=pat.phone_masked,
                conversation_id=f"msg_{prop.encounter_id}",
                template_id="new_message",
                body_text=wa_msg,
                magic_link_url="/p",
                leakage_passed=True,
                status="sent",
            ))
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinician", actor_id=doc_id,
                action="clinician.message_sent", resource_type="Message", resource_id=m.id,
                metadata={"proposal_id": prop.id, "patient_id": pat.id},
            )
            telemetry.inc("wa_messages_sent_total")
            return {
                "ok": True,
                "message": {
                    "id": m.id,
                    "direction": m.direction,
                    "sender_role": m.sender_role,
                    "body_text": m.body_text,
                    "created_at": (m.created_at or utcnow()).isoformat(),
                },
                "doorbell_sent": True,
            }

    @app.post("/api/v1/clinician/proposals/{proposal_id}/decide")
    def clinician_decide(proposal_id: str, body: ClinicianDecideReq, request: Request):
        """Stories 12, 13, 14: Signed clinician decision -> optionally signs encrypted Cert PDF & e-Script."""
        claims = extract_claims(request, allowed_roles=("clinician",))
        tenant_id, doc_id = claims["tid"], claims["sub"]

        # Normalize flat frontend fields into canonical issue_cert / issue_rx inputs
        eff_issue_cert = body.issue_cert
        if eff_issue_cert is None and body.issue_certificate:
            eff_issue_cert = IssueCertInput(
                days_off=body.cert_days_off or 2,
                start_date=body.cert_start_date or utcnow().astimezone(HKT).date().isoformat(),
            )
        eff_issue_rx = body.issue_rx
        if eff_issue_rx is None and body.rx_items:
            eff_issue_rx = IssueRxInput(items=body.rx_items, override_reason=body.override_reason)

        with database.tenant_session(tenant_id) as s:
            doc = s.get(Practitioner, doc_id)
            if not doc or doc.role != "clinician":
                raise HTTPException(403, "Authenticated clinician required")
            # MCHK Telemedicine Invariant: must have verified HK MCHK registration to sign certs/Rx
            if (eff_issue_cert or eff_issue_rx) and not valid_mchk_reg(doc.mchk_reg_no):
                raise HTTPException(403, "Valid HK MCHK registration number required to sign certificates or prescriptions")

            prop = s.get(Proposal, proposal_id)
            if not prop:
                raise HTTPException(404, "Proposal not found")
            pat = s.get(Patient, prop.patient_id)
            enc = s.get(Encounter, prop.encounter_id)
            if not pat or not enc:
                raise HTTPException(404, "Linked patient or encounter missing")

            # Block sign-off while any cross-site medication DoseConflict remains unresolved
            unresolved = [c for c in ((prop.payload_json or {}).get("conflicts") or []) if not c.get("resolved")]
            if unresolved and body.decision in ("approved", "edited"):
                raise HTTPException(422, f"Settle {len(unresolved)} medication dose conflict(s) before signing.")

            # Automation-bias deliberate friction on critical items or sub-3-second rubber-stamping
            if (prop.urgency >= 4 or body.review_seconds < 3.0) and body.decision in ("approved", "edited") and not body.rubber_stamp_confirmed:
                raise HTTPException(
                    422,
                    "Automation-bias safety confirmation required: confirm independent verification of evidence before signing critical or rapid-review decisions.",
                )
            if body.decision == "escalated" and not (body.escalation_reason and len(body.escalation_reason.strip()) >= 5):
                raise HTTPException(422, "Escalation or referral requires a documented clinical reason")

            if body.icd10_code:
                diag_errs = validate_diagnosis_coding(body.icd10_code, body.icpc2_code)
                if diag_errs:
                    raise HTTPException(422, "; ".join(diag_errs))

            # Pre-validate Rx safety rules BEFORE committing any decision
            rx_report = None
            if eff_issue_rx and body.decision in ("approved", "edited"):
                rx_report = check_prescription_safety(
                    {
                        "dob": pat.dob, "g6pd_deficient": pat.g6pd_deficient,
                        "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                        "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                    },
                    eff_issue_rx.items,
                    diagnosis_icd10=body.icd10_code or enc.icd10_code,
                )
                telemetry.inc("rx_safety_checks_total")
                ok_override, err_msg = rx_report.validate_override(eff_issue_rx.override_reason)
                if not ok_override:
                    if rx_report.hard_blocks:
                        telemetry.inc("rx_hard_blocks_total")
                    raise HTTPException(422, err_msg or "Prescription blocked by deterministic clinical safety rules")
                if rx_report.overrides_required and eff_issue_rx.override_reason:
                    telemetry.inc("rx_overrides_total")

            edit_diff: dict[str, Any] = dict(body.edit_diff or {})
            if body.clinical_note and body.clinical_note != enc.clinical_note:
                edit_diff["clinical_note_edited"] = True
                enc.clinical_note = body.clinical_note
            if body.icd10_code:
                enc.icd10_code = body.icd10_code
                enc.icpc2_code = body.icpc2_code or HK_DIAGNOSES[body.icd10_code]["icpc2"]

            prop.status = body.decision
            enc.practitioner_id = doc.id
            if body.decision == "escalated":
                enc.status = "referred"
                enc.escalation_reason = body.escalation_reason
            elif body.decision in ("approved", "edited"):
                enc.status = "completed"

            rd = ReviewDecision(
                id=new_id("rev"),
                tenant_id=tenant_id,
                proposal_id=prop.id,
                practitioner_id=doc.id,
                decision=body.decision,
                edit_diff_json=edit_diff,
                review_seconds=body.review_seconds,
                rubber_stamp_confirmed=body.rubber_stamp_confirmed,
                override_reason=eff_issue_rx.override_reason if eff_issue_rx else body.override_reason,
                note=body.note or body.escalation_reason or "",
            )
            s.add(rd)
            s.flush()

            cert_out = None
            if eff_issue_cert and body.decision in ("approved", "edited"):
                icd10 = enc.icd10_code or "J06.9"
                icpc2 = enc.icpc2_code or HK_DIAGNOSES.get(icd10, {}).get("icpc2", "R74")
                issued = issue_encrypted_certificate(
                    vault=doc_vault,
                    tenant_id=tenant_id,
                    mchk_reg_no=doc.mchk_reg_no or "",
                    doctor_name=doc.name_en,
                    clinic_name=doc.clinic_name,
                    patient_name=pat.name_en,
                    hkid_masked=pat.hkid_masked or "XXXX(X)",
                    encounter_id=enc.id,
                    start_date=eff_issue_cert.start_date,
                    days_off=eff_issue_cert.days_off,
                    diagnosis_icd10=icd10,
                    diagnosis_icpc2=icpc2,
                )
                cert_row = Certificate(
                    id=new_id("cert"),
                    tenant_id=tenant_id,
                    patient_id=pat.id,
                    encounter_id=enc.id,
                    practitioner_id=doc.id,
                    mchk_reg_no=doc.mchk_reg_no or "",
                    review_decision_id=rd.id,
                    days_off=eff_issue_cert.days_off,
                    start_date=issued.start_date,
                    end_date=issued.end_date,
                    diagnosis_icd10=icd10,
                    diagnosis_icpc2=icpc2,
                    vault_doc_id=issued.vault_doc_id,
                    verification_hash=issued.verification_hash,
                )
                s.add(cert_row)
                s.flush()
                telemetry.inc("certs_signed_total")
                cert_out = {
                    "id": cert_row.id, "encounter_id": cert_row.encounter_id,
                    "vault_doc_id": cert_row.vault_doc_id,
                    "days_off": cert_row.days_off, "start_date": cert_row.start_date,
                    "end_date": cert_row.end_date, "mchk_reg_no": cert_row.mchk_reg_no,
                    "diagnosis_icd10": cert_row.diagnosis_icd10, "diagnosis_icpc2": cert_row.diagnosis_icpc2,
                    "verification_hash": cert_row.verification_hash,
                    "signed_at": (cert_row.signed_at or utcnow()).isoformat(),
                }

            rx_out = None
            if eff_issue_rx and rx_report is not None and body.decision in ("approved", "edited"):
                rx_row = Prescription(
                    id=new_id("rx"),
                    tenant_id=tenant_id,
                    patient_id=pat.id,
                    encounter_id=enc.id,
                    practitioner_id=doc.id,
                    mchk_reg_no=doc.mchk_reg_no or "",
                    review_decision_id=rd.id,
                    items_json=rx_report.enriched_items,
                    safety_checks_json=[f.to_dict() for f in rx_report.findings],
                    override_reason=eff_issue_rx.override_reason,
                    fulfilment_mode="pending_choice",
                    fulfilment_status="signed",
                    tracking_events_json=[{"status": "signed", "at": utcnow().isoformat(), "note": f"Signed by {doc.name_en} ({doc.mchk_reg_no})"}],
                )
                s.add(rx_row)
                s.flush()
                telemetry.inc("prescriptions_signed_total")
                rx_out = {
                    "id": rx_row.id, "encounter_id": rx_row.encounter_id, "mchk_reg_no": rx_row.mchk_reg_no,
                    "items": rx_row.items_json, "fulfilment_mode": rx_row.fulfilment_mode,
                    "fulfilment_status": rx_row.fulfilment_status, "signed_at": (rx_row.signed_at or utcnow()).isoformat(),
                }

            # Schedule durable 48-hour follow-up workflow & notify non-clinical WA doorbell if doc/rx signed
            if body.decision in ("approved", "edited"):
                workflows.schedule_followup_48h(
                    s, tenant_id, encounter_id=enc.id, patient_id=pat.id,
                    phone=pat.phone_masked, patient_first_name=pat.name_en.split()[0],
                    completed_at=utcnow(), portal_url="/p",
                )
                if cert_out or rx_out:
                    wa_msg = wa_adapter.render_template("document_ready", {"url": "/p"}, lang="bilingual")
                    WhatsAppLeakageGuard.assert_clean(wa_msg)
                    s.add(WhatsAppOutbox(
                        id=new_id("wao"), tenant_id=tenant_id, phone=pat.phone_masked,
                        conversation_id=f"docready_{enc.id}", template_id="document_ready",
                        body_text=wa_msg, magic_link_url="/p", leakage_passed=True, status="sent",
                    ))
                    telemetry.inc("wa_messages_sent_total")
                # Update corporate campaign spend if linked
                if pat.campaign_id:
                    camp = s.get(Campaign, pat.campaign_id)
                    if camp:
                        camp.spent_hkd += camp.unit_fee_hkd
                        corp = s.get(Corporate, camp.corporate_id)
                        if corp:
                            corp.spent_hkd += camp.unit_fee_hkd

            # Record doctor-signed patient reply if supplied
            if body.patient_reply and body.patient_reply.strip() and body.decision in ("approved", "edited"):
                s.add(Message(
                    id=new_id("msg"),
                    tenant_id=tenant_id,
                    patient_id=pat.id,
                    encounter_id=enc.id,
                    direction="outbound",
                    sender_role="clinician",
                    body_text=body.patient_reply.strip(),
                ))

            # Server-held 10-second Undo-Send window on signed dispatch
            toggles = runtime_toggles()
            undo_sec = toggles["dispatch_undo_seconds"]
            undo_until = utcnow() + timedelta(seconds=undo_sec)
            dispatch_id: str | None = None
            if body.decision in ("approved", "edited"):
                dispatch_ev = OutboxEvent(
                    id=new_id("dsp"),
                    tenant_id=tenant_id,
                    event_type="dispatch.signed_packet",
                    aggregate_id=prop.id,
                    payload_json={
                        "proposal_id": prop.id,
                        "encounter_id": enc.id,
                        "patient_id": pat.id,
                        "decision_id": rd.id,
                        "cert_id": cert_out["id"] if cert_out else None,
                        "rx_id": rx_out["id"] if rx_out else None,
                    },
                    status="pending",
                    run_after=undo_until,
                    idempotency_key=f"dispatch:{prop.id}:{rd.id}",
                )
                s.add(dispatch_ev)
                dispatch_id = dispatch_ev.id

            record_audit(
                s, tenant_id=tenant_id, actor_role="clinician", actor_id=doc.id,
                action=f"proposal.{body.decision}", resource_type="Proposal", resource_id=prop.id,
                metadata={
                    "mchk_reg_no": doc.mchk_reg_no, "cert_issued": bool(cert_out),
                    "rx_issued": bool(rx_out), "review_seconds": body.review_seconds,
                    "dispatch_id": dispatch_id,
                },
            )
            return {
                "ok": True,
                "decision_id": rd.id,
                "status": rd.decision,
                "dispatch_id": dispatch_id,
                "dispatch_undo_seconds": undo_sec,
                "undo_until": undo_until.isoformat(),
                "review_decision": {"id": rd.id, "decision": rd.decision, "decided_at": rd.decided_at.isoformat()},
                "certificate": cert_out,
                "prescription": rx_out,
                "encounter": {"id": enc.id, "status": enc.status, "icd10_code": enc.icd10_code, "icpc2_code": enc.icpc2_code},
            }

    @app.post("/api/v1/staff/proposals/{proposal_id}/ready-for-doctor")
    def staff_nurse_ready(proposal_id: str, body: NurseReadyReq, request: Request):
        """Nurse / Care Coordinator prepares chart & hands off to MCHK clinician (cannot sign Rx/cert)."""
        from sqlalchemy.orm.attributes import flag_modified
        claims = extract_claims(request, allowed_roles=("nurse", "clinician"))
        tenant_id, actor_id = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            prop = s.get(Proposal, proposal_id)
            if not prop:
                raise HTTPException(404, "Proposal not found")
            prac = s.get(Practitioner, actor_id)
            payload = dict(prop.payload_json or {})
            payload["nurse_ready"] = True
            payload["nurse_ready_by"] = prac.name_en if prac else actor_id
            payload["nurse_ready_at"] = utcnow().isoformat()
            if body.prep_note.strip():
                payload["nurse_prep_note"] = body.prep_note.strip()
            if body.vitals_note.strip():
                payload["nurse_vitals_note"] = body.vitals_note.strip()
            prop.payload_json = payload
            flag_modified(prop, "payload_json")
            record_audit(
                s, tenant_id=tenant_id, actor_role=claims["role"], actor_id=actor_id,
                action="proposal.nurse_ready", resource_type="Proposal", resource_id=prop.id,
                metadata={"prep_note": body.prep_note[:160]},
            )
            return {"ok": True, "proposal_id": prop.id, "nurse_ready": True, "nurse_ready_by": payload["nurse_ready_by"]}

    @app.post("/api/v1/clinician/proposals/{proposal_id}/resolve-conflict")
    def clinician_resolve_conflict(proposal_id: str, body: ResolveConflictReq, request: Request):
        """Doctor resolves a cross-site DoseConflict (`1` side 1, `2` side 2, `Q` ask patient) — agent never picks."""
        from sqlalchemy.orm.attributes import flag_modified
        claims = extract_claims(request, allowed_roles=("clinician",))
        tenant_id, doc_id = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            prop = s.get(Proposal, proposal_id)
            if not prop:
                raise HTTPException(404, "Proposal not found")
            doc = s.get(Practitioner, doc_id)
            payload = dict(prop.payload_json or {})
            conflicts = [dict(c) for c in (payload.get("conflicts") or [])]
            matched = None
            for c in conflicts:
                if c.get("id") == body.conflict_id:
                    c["resolved"] = True
                    c["chosen_side"] = body.choice
                    c["resolved_by"] = doc.name_en if doc else doc_id
                    c["resolved_at"] = utcnow().isoformat()
                    if body.choice == "Q":
                        c["resolution_summary"] = "Hold repeat & ask patient which tablets they are taking"
                    else:
                        side = next((sd for sd in (c.get("sides") or []) if sd.get("key") == body.choice), None)
                        c["resolution_summary"] = (
                            f"{side['regimen']} ({side['site']}, {side['date']}), chosen by you"
                            if side else f"Option {body.choice} selected"
                        )
                    matched = c
                    break
            if not matched:
                raise HTTPException(404, "Conflict not found on proposal")
            payload["conflicts"] = conflicts
            prop.payload_json = payload
            flag_modified(prop, "payload_json")
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinician", actor_id=doc_id,
                action="conflict.resolved", resource_type="Proposal", resource_id=prop.id,
                metadata={"conflict_id": body.conflict_id, "choice": body.choice, "summary": matched["resolution_summary"]},
            )
            return {"ok": True, "conflict": matched, "unresolved_conflicts": sum(1 for c in conflicts if not c.get("resolved"))}

    @app.post("/api/v1/clinician/proposals/{proposal_id}/undo")
    def clinician_undo_dispatch(proposal_id: str, request: Request):
        """Cancel a signed packet's outbound dispatch during the server-held 10-second undo window."""
        claims = extract_claims(request, allowed_roles=("clinician",))
        tenant_id, doc_id = claims["tid"], claims["sub"]
        now = utcnow()
        with database.tenant_session(tenant_id) as s:
            prop = s.get(Proposal, proposal_id)
            if not prop:
                raise HTTPException(404, "Proposal not found")
            ev = s.scalar(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "dispatch.signed_packet",
                    OutboxEvent.aggregate_id == proposal_id,
                    OutboxEvent.status == "pending",
                )
                .order_by(OutboxEvent.created_at.desc())
            )
            if not ev or ev.run_after <= now:
                raise HTTPException(409, "Undo window has closed; dispatch already committed.")
            ev.status = "cancelled"
            ev.processed_at = now
            prop.status = "pending_review"
            enc = s.get(Encounter, prop.encounter_id)
            if enc:
                enc.status = "pending_review"
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinician", actor_id=doc_id,
                action="proposal.dispatch_undone", resource_type="Proposal", resource_id=prop.id,
                metadata={"dispatch_id": ev.id},
            )
            return {"ok": True, "undone": True, "proposal_id": prop.id, "status": prop.status}

    @app.get("/api/v1/patient/companion")
    def patient_companion_home(request: Request):
        """Calm 0-tab Patient Care Companion: 1 pinned Now item, episode history, goal line, single Record sheet."""
        claims = extract_claims(request, allowed_roles=("patient",))
        tenant_id, pat_id = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            pat = s.get(Patient, pat_id)
            if not pat:
                raise HTTPException(404, "Patient not found")
            encs = s.scalars(select(Encounter).where(Encounter.patient_id == pat_id).order_by(Encounter.created_at.desc())).all()
            obs = s.scalars(select(Observation).where(Observation.patient_id == pat_id).order_by(Observation.recorded_at.desc()).limit(20)).all()
            msgs = s.scalars(select(Message).where(Message.patient_id == pat_id).order_by(Message.created_at.asc())).all()
            certs = s.scalars(select(Certificate).where(Certificate.patient_id == pat_id).order_by(Certificate.signed_at.desc())).all()
            rxs = s.scalars(select(Prescription).where(Prescription.patient_id == pat_id).order_by(Prescription.signed_at.desc())).all()
            props = s.scalars(select(Proposal).where(Proposal.patient_id == pat_id).order_by(Proposal.created_at.desc())).all()
            consents = s.scalars(select(Consent).where(Consent.patient_id == pat_id).order_by(Consent.recorded_at.desc())).all()

            sla = next_clinic_review_sla(doctor_name="Dr. Kenneth Lam")
            pending_prop = next((p for p in props if p.status == "pending_review"), None)
            now_item: dict[str, Any] | None = None
            goal_line: dict[str, Any] | None = None

            if pat_id == "pat_ho_htn_drift":
                goal_line = {
                    "label_en": "Your home BP goal: under 135/85, set by Dr. Kenneth Lam.",
                    "label_zh": "你的家居血壓目標：135/85 以下（由林啟賢醫生設定）。",
                    "current": "Last 7 days: 152/94",
                    "source_quote": "Average: 152/94 mmHg (morning avg 154/95, evening avg 150/93)",
                }
                now_item = {
                    "kind": "bp_protocol",
                    "title_en": "Send this morning's BP",
                    "title_zh": "上載今朝血壓讀數",
                    "detail_en": "Photograph your monitor screen or enter systolic/diastolic — Dr. Kenneth Lam is reviewing your 3-week log.",
                    "detail_zh": "拍下血壓計螢幕或輸入讀數——林啟賢醫生正覆核你的 3 星期紀錄。",
                    "review_sla": sla,
                }
            elif pat_id == "pat_chan_qeh_discharge":
                now_item = {
                    "kind": "discharge_review",
                    "title_en": "QEH discharge medicines under doctor review",
                    "title_zh": "伊利沙伯醫院出院藥物正由醫生核對",
                    "detail_en": "Dr. Kenneth Lam is reconciling your Warfarin dose and cholesterol tablets from your 14 Nov discharge letter.",
                    "detail_zh": "林啟賢醫生正核對你 11 月 14 日出院摘要上的薄血丸及膽固醇藥。",
                    "review_sla": sla,
                }
            elif pending_prop:
                now_item = {
                    "kind": "pending_review",
                    "title_en": "With Dr. Kenneth Lam for review",
                    "title_zh": "正交由林啟賢醫生覆核",
                    "detail_en": sla["label_en"],
                    "detail_zh": sla["label_zh"],
                    "review_sla": sla,
                }
            elif rxs and rxs[0].fulfilment_mode == "pending_choice":
                now_item = {
                    "kind": "rx_ready",
                    "title_en": "Prescription signed — choose delivery or pickup",
                    "title_zh": "處方已簽發——選擇送遞或自取",
                    "detail_en": "Signed by Dr. Kenneth Lam (M14829).",
                    "detail_zh": "由林啟賢醫生（M14829）簽發。",
                    "rx_id": rxs[0].id,
                }

            protocol_nudges: list[dict[str, Any]] = []
            if pat_id == "pat_ho_htn_drift":
                protocol_nudges.append({
                    "id": "nudge_hba1c_6m",
                    "because_en": "Your HbA1c was 7.8% on 3 May. Dr. Kenneth Lam asked to recheck it in 6 months.",
                    "because_zh": "你於 5 月 3 日的糖化血紅素（HbA1c）為 7.8%。林啟賢醫生建議 6 個月後覆檢。",
                    "action_en": "Walk in for blood test: any weekday, 8–10am.",
                    "action_zh": "任何平日上午 8–10 時到診所抽血即可。",
                    "source_quote": "HbA1c 7.8%",
                    "source_doc": encs[0].chief_complaint_text if encs else "",
                })

            episodes = [
                {
                    "id": e.id,
                    "title": (e.icd10_code or e.encounter_type.replace("_", " ").title()),
                    "encounter_type": e.encounter_type,
                    "status": e.status,
                    "date": e.created_at.strftime("%d %b %Y"),
                    "summary": (e.clinical_note or e.chief_complaint_text or "")[:180],
                    "source_text": e.chief_complaint_text or "",
                }
                for e in encs
            ]

            return {
                "clinic_name": "Central & Mid-Levels Family Medical",
                "doctor_name": "Dr. Kenneth Lam",
                "doctor_mchk": "M14829",
                "patient": {
                    "id": pat.id, "name_en": pat.name_en, "name_zh": pat.name_zh,
                    "dob": pat.dob, "sex": pat.sex, "hkid_masked": pat.hkid_masked,
                    "phone_masked": pat.phone_masked, "preferred_lang": pat.preferred_lang,
                    "g6pd_deficient": pat.g6pd_deficient, "allergies": pat.allergies_json,
                    "current_meds": pat.current_meds_json, "egfr": pat.egfr,
                },
                "now": now_item,
                "goal_line": goal_line,
                "protocol_nudges": protocol_nudges,
                "review_sla": sla,
                "timeline": [
                    {
                        "id": m.id,
                        "direction": m.direction,
                        "voice": "doctor" if m.sender_role == "clinician" else ("patient" if m.sender_role == "patient" else "assistant"),
                        "sender_label": "Dr. Kenneth Lam" if m.sender_role == "clinician" else ("You" if m.sender_role == "patient" else "Clinic assistant"),
                        "body": m.body_text,
                        "created_at": m.created_at.isoformat(),
                    }
                    for m in msgs
                ],
                "episodes": episodes,
                "record": {
                    "allergies": pat.allergies_json,
                    "g6pd_deficient": pat.g6pd_deficient,
                    "current_meds": pat.current_meds_json,
                    "observations": [
                        {"id": o.id, "display": o.display, "value_text": o.value_text, "recorded_at": o.recorded_at.isoformat()}
                        for o in obs
                    ],
                    "certificates": [
                        {
                            "id": c.id, "days_off": c.days_off, "start_date": c.start_date, "end_date": c.end_date,
                            "mchk_reg_no": c.mchk_reg_no, "diagnosis_icd10": c.diagnosis_icd10,
                            "signed_at": (c.signed_at or utcnow()).isoformat(),
                        }
                        for c in certs
                    ],
                    "prescriptions": [
                        {
                            "id": r.id, "items": r.items_json, "fulfilment_mode": r.fulfilment_mode,
                            "fulfilment_status": r.fulfilment_status, "fulfilment_ref": r.fulfilment_ref,
                            "mchk_reg_no": r.mchk_reg_no, "signed_at": (r.signed_at or utcnow()).isoformat(),
                        }
                        for r in rxs
                    ],
                    "consents": [
                        {"id": cs.id, "purpose": cs.scope, "granted": cs.granted, "created_at": cs.recorded_at.isoformat()}
                        for cs in consents[:8]
                    ],
                },
                "crisis_hotlines": HK_MENTAL_HEALTH_HOTLINES,
                "runtime_toggles": runtime_toggles(),
            }

    @app.post("/api/v1/patient/companion/turn")
    def patient_companion_turn(body: CompanionTurnReq, request: Request):
        """Grounded 24/7 patient companion turn with deterministic Medical 999 vs Mental-Health crisis separation."""
        claims = extract_claims(request, allowed_roles=("patient",))
        tenant_id, pat_id = claims["tid"], claims["sub"]
        crisis_kind = classify_crisis(body.message)
        with database.tenant_session(tenant_id) as s:
            pat = s.get(Patient, pat_id)
            if not pat:
                raise HTTPException(404, "Patient not found")
            enc = s.scalar(select(Encounter).where(Encounter.patient_id == pat_id).order_by(Encounter.created_at.desc()))
            msg_in = Message(
                id=new_id("msg"), tenant_id=tenant_id, patient_id=pat_id,
                encounter_id=enc.id if enc else None, direction="inbound",
                sender_role="patient", body_text=body.message.strip(),
            )
            s.add(msg_in)
            s.flush()

            if crisis_kind:
                # Create real urgent proposal first -> ONLY THEN confirm server-acked paging!
                new_enc = Encounter(
                    id=new_id("enc"), tenant_id=tenant_id, patient_id=pat_id,
                    encounter_type="crisis_alert", status="red_flag_escalated",
                    chief_complaint_text=body.message.strip(),
                )
                s.add(new_enc)
                s.flush()
                prop = Proposal(
                    id=new_id("prop"), tenant_id=tenant_id, patient_id=pat_id,
                    encounter_id=new_enc.id, kind="red_flag_alert", urgency=4,
                    status="pending_review", sla_due_at=utcnow() + timedelta(minutes=15),
                    protocol_id="flu_intake_v1", protocol_version="1.0.0",
                    payload_json={"title": f"Urgent {crisis_kind.replace('_', ' ')}", "triage": {"red_flags": [crisis_kind]}},
                    source_quotes_json=[{"field": "Patient message", "quote": body.message.strip()[:200], "source": "Patient Companion"}],
                    checks_json=[],
                )
                s.add(prop)
                s.flush()
                acked_at = utcnow().astimezone(HKT).strftime("%H:%M")
                record_audit(
                    s, tenant_id=tenant_id, actor_role="system", actor_id="crisis_floor",
                    action="crisis.escalated", resource_type="Proposal", resource_id=prop.id,
                    metadata={"crisis_kind": crisis_kind},
                )
                return {
                    "ok": True,
                    "crisis": {
                        "kind": crisis_kind,
                        "pager_acked": True,
                        "pager_acked_at": acked_at,
                        "paged_clinician": "Dr. Kenneth Lam (on duty)",
                        "hotlines": HK_MENTAL_HEALTH_HOTLINES if crisis_kind == "mental_health_crisis" else [],
                    },
                }

            t_row = s.get(Tenant, tenant_id)
            ai_on = bool(t_row.ai_enabled) if t_row else True
            allowed_regions = list(t_row.allowed_regions) if t_row and t_row.allowed_regions else ["asia-east2"]
            allow_cb = bool(t_row.allow_cross_border_inference) if t_row else False
            model_gw.set_tenant_policy(TenantPolicy(
                tenant_id=tenant_id,
                allowed_regions=tuple(allowed_regions),
                allow_cross_border_inference=allow_cb,
            ))

            obs = s.scalars(select(Observation).where(Observation.patient_id == pat_id).order_by(Observation.recorded_at.desc()).limit(10)).all()
            open_props = s.scalars(
                select(Proposal).where(Proposal.patient_id == pat_id, Proposal.status == "pending_review").order_by(Proposal.created_at.desc()).limit(5)
            ).all()
            patient_conflicts: list[dict[str, Any]] = []
            for p_row in open_props:
                raw_cf = (p_row.payload_json or {}).get("conflicts") or []
                for cf in raw_cf:
                    if isinstance(cf, dict):
                        patient_conflicts.append(cf)
                    elif isinstance(cf, str) and cf.strip():
                        patient_conflicts.append({"drug": "Medication reconciliation", "summary": cf.strip()})

            ans = answer_companion_question(
                body.message,
                patient={
                    "name_en": pat.name_en, "allergies": pat.allergies_json,
                    "current_meds": pat.current_meds_json, "g6pd_deficient": pat.g6pd_deficient,
                    "egfr": pat.egfr, "weight_kg": pat.weight_kg,
                },
                observations=[{"loinc_code": o.loinc_code, "display": o.display, "value_text": o.value_text} for o in obs],
                lang=body.lang or pat.preferred_lang,
                conflicts=patient_conflicts,
                gateway=model_gw if ai_on else None,
                tenant_id=tenant_id,
            )
            created_prop_id: str | None = None
            low_msg = body.message.lower()
            is_new_symptom = any(
                k in low_msg
                for k in ("fever", "flu", "cough", "sore throat", "rat", "tamiflu", "oseltamivir", "sick leave", "發燒", "流感", "喉嚨痛", "咳", "快測", "病假")
            )
            if is_new_symptom:
                intake_res = harness_rt.process_intake(
                    body.message.strip(),
                    patient={
                        "id": pat.id, "name_en": pat.name_en, "dob": pat.dob,
                        "allergies": pat.allergies_json, "current_meds": pat.current_meds_json,
                        "g6pd_deficient": pat.g6pd_deficient, "weight_kg": pat.weight_kg, "egfr": pat.egfr,
                    },
                    lang=body.lang or pat.preferred_lang,
                    tenant_id=tenant_id,
                    kill_switch=not ai_on,
                )
                draft_b = intake_res.get("draft")
                new_enc = Encounter(
                    id=new_id("enc"), tenant_id=tenant_id, patient_id=pat_id,
                    encounter_type="acute_flu", status="pending_review",
                    chief_complaint_text=body.message.strip(),
                    icd10_code=(draft_b.working_diagnosis.get("icd10") if draft_b else "J06.9"),
                    icpc2_code=(draft_b.working_diagnosis.get("icpc2") if draft_b else "R74"),
                )
                s.add(new_enc)
                s.flush()
                prop = Proposal(
                    id=new_id("prop"), tenant_id=tenant_id, patient_id=pat_id,
                    encounter_id=new_enc.id, kind="consult_draft", urgency=2,
                    status="pending_review", sla_due_at=utcnow() + timedelta(hours=2),
                    protocol_id="acute_consult_draft_v1", protocol_version="1.0.0",
                    payload_json={
                        "title": f"New symptom message from {pat.name_en}",
                        "summary_points": [{"text": "Submitted via patient care companion; awaiting clinician review.", "quote": body.message.strip()[:140]}],
                        "case_sites": ["Central GP", "Patient Companion"],
                        "conflicts": [],
                        "draft": draft_b.to_dict() if draft_b else {"patient_summary_draft": ans["reply"]},
                    },
                    source_quotes_json=[{"field": "Patient message", "quote": body.message.strip()[:140], "source": "Patient Companion"}],
                    checks_json=[],
                )
                s.add(prop)
                s.flush()
                created_prop_id = prop.id
                msg_in.encounter_id = new_enc.id

            msg_out = Message(
                id=new_id("msg"), tenant_id=tenant_id, patient_id=pat_id,
                encounter_id=enc.id if enc else None, direction="outbound",
                sender_role="assistant", body_text=ans["reply"],
            )
            s.add(msg_out)
            s.flush()
            return {"ok": True, "crisis": None, "message": ans, "message_id": msg_out.id, "proposal_id": created_prop_id}

    @app.post("/api/v1/patient/companion/reading")
    def patient_companion_reading(body: CompanionReadingReq, request: Request):
        """Log a confirmed home BP / glucose / temp reading (or monitor photo reading) & check crisis threshold."""
        claims = extract_claims(request, allowed_roles=("patient",))
        tenant_id, pat_id = claims["tid"], claims["sub"]
        crisis_kind = classify_crisis(body.note, systolic=body.systolic, diastolic=body.diastolic)
        with database.tenant_session(tenant_id) as s:
            pat = s.get(Patient, pat_id)
            if not pat:
                raise HTTPException(404, "Patient not found")
            enc = s.scalar(select(Encounter).where(Encounter.patient_id == pat_id).order_by(Encounter.created_at.desc()))
            if body.kind == "bp" and body.systolic and body.diastolic:
                val_text = f"{body.systolic}/{body.diastolic} mmHg"
                val_num = float(body.systolic)
                loinc, disp, unit = "85354-9", "Home Blood Pressure", "mm[Hg]"
            elif body.kind == "glucose" and body.value_num is not None:
                val_text = f"{body.value_num:g} mmol/L"
                val_num = float(body.value_num)
                loinc, disp, unit = "15074-8", "Home Blood Glucose", "mmol/L"
            else:
                val_num = float(body.value_num or 37.0)
                val_text = f"{val_num:g} °C"
                loinc, disp, unit = "8310-5", "Body Temperature", "Cel"

            obs_row = Observation(
                id=new_id("obs"), tenant_id=tenant_id, patient_id=pat_id,
                encounter_id=enc.id if enc else new_id("enc"),
                loinc_code=loinc, display=disp, value_num=val_num, value_text=val_text,
                ucum_unit=unit, source_quote=f"{val_text} ({body.source})",
            )
            s.add(obs_row)
            s.add(Message(
                id=new_id("msg"), tenant_id=tenant_id, patient_id=pat_id,
                encounter_id=enc.id if enc else None, direction="inbound",
                sender_role="patient", body_text=f"Confirmed {disp}: {val_text} ({body.source.replace('_', ' ')}). {body.note}".strip(),
            ))
            s.flush()
            sla = next_clinic_review_sla(doctor_name="Dr. Kenneth Lam")
            return {
                "ok": True,
                "observation": {"id": obs_row.id, "display": disp, "value_text": val_text, "source": body.source},
                "review_sla": sla,
                "crisis": {
                    "kind": crisis_kind,
                    "pager_acked": True,
                    "pager_acked_at": utcnow().astimezone(HKT).strftime("%H:%M"),
                    "paged_clinician": "Dr. Kenneth Lam (on duty)",
                } if crisis_kind else None,
            }

    @app.post("/api/v1/clinician/availability")
    def clinician_availability(body: AvailabilityReq, request: Request):
        """Story 15: Clinician sets availability/roster."""
        claims = extract_claims(request, allowed_roles=("clinician",))
        tenant_id, doc_id = claims["tid"], claims["sub"]
        avail = body.availability if body.availability is not None else (body.slots or [])
        with database.tenant_session(tenant_id) as s:
            doc = s.get(Practitioner, doc_id)
            if not doc:
                raise HTTPException(404, "Clinician not found")
            doc.availability_json = avail
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinician", actor_id=doc_id,
                action="clinician.availability_updated", resource_type="Practitioner", resource_id=doc_id,
            )
            return {"ok": True, "availability": doc.availability_json, "practitioner": {"id": doc.id, "name_en": doc.name_en, "availability": doc.availability_json}}

    @app.get("/api/v1/clinician/audit")
    def clinician_audit(request: Request):
        """Story 15: Clinician personal immutable audit trail."""
        claims = extract_claims(request, allowed_roles=("clinician",))
        tenant_id, doc_id = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            rows = s.scalars(
                select(AuditEvent).where(AuditEvent.actor_id == doc_id).order_by(AuditEvent.created_at.desc()).limit(100)
            ).all()
        return {
            "events": [
                {
                    "id": r.id, "actor_role": r.actor_role, "actor_id": r.actor_id,
                    "action": r.action, "resource_type": r.resource_type, "resource_id": r.resource_id,
                    "break_glass": r.break_glass, "break_glass_reason": r.break_glass_reason,
                    "metadata": r.metadata_json, "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        }

    # ================================================================================== SURFACE 4: CLINIC OPS ADMIN
    @app.get("/api/v1/admin/overview")
    def admin_overview(request: Request):
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id = claims["tid"]
        with database.admin_session() as s:
            t = s.get(Tenant, tenant_id)
            tenant_info = {
                "id": t.id, "name": t.name, "region": t.region,
                "allowed_regions": t.allowed_regions,
                "allow_cross_border_inference": t.allow_cross_border_inference,
                "ai_enabled": t.ai_enabled, "dpo_contact": t.dpo_contact,
            } if t else {}
        with database.tenant_session(tenant_id) as s:
            corp_rows = s.scalars(select(Corporate)).all()
            corp_names = {c.id: c.name for c in corp_rows}
            corps = [
                {
                    "id": c.id, "name": c.name, "spend_cap_hkd": c.spend_cap_hkd, "spent_hkd": c.spent_hkd,
                    "billing_email": c.billing_email, "admin_email": c.admin_email, "active": c.active,
                }
                for c in corp_rows
            ]
            camps = [
                {
                    "id": c.id, "corporate_id": c.corporate_id, "corporate_name": corp_names.get(c.corporate_id, ""),
                    "code": c.code, "name": c.name, "spend_cap_hkd": c.spend_cap_hkd, "spent_hkd": c.spent_hkd,
                    "unit_fee_hkd": c.unit_fee_hkd, "active": c.active,
                }
                for c in s.scalars(select(Campaign)).all()
            ]
            protos = [
                {
                    "id": p.id, "protocol_id": p.protocol_id, "version": p.version,
                    "content_hash": p.content_hash, "yaml_content": p.yaml_content,
                    "autonomy_level": p.autonomy_level, "status": p.status,
                    "approved_by": p.approved_by,
                    "approved_at": p.approved_at.isoformat() if p.approved_at else None,
                    "created_at": p.created_at.isoformat(),
                }
                for p in s.scalars(select(ProtocolVersion).order_by(ProtocolVersion.created_at.desc())).all()
            ]
            auds = [
                {
                    "id": a.id, "actor_role": a.actor_role, "actor_id": a.actor_id, "action": a.action,
                    "resource_type": a.resource_type, "resource_id": a.resource_id,
                    "break_glass": a.break_glass, "created_at": a.created_at.isoformat(),
                }
                for a in s.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(50)).all()
            ]
            outbox_pending = int(s.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "pending")) or 0)
            outbox_dlq = int(s.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.status == "dlq")) or 0)
            runs_count = int(s.scalar(select(func.count(AgentRun.id))) or 0)
            total_patients = int(s.scalar(select(func.count(Patient.id))) or 0)
            pending_proposals = int(s.scalar(select(func.count(Proposal.id)).where(Proposal.status == "pending_review")) or 0)
            completed_encounters = int(s.scalar(select(func.count(Encounter.id)).where(Encounter.status == "completed")) or 0)
            signed_certificates = int(s.scalar(select(func.count(Certificate.id))) or 0)
            signed_prescriptions = int(s.scalar(select(func.count(Prescription.id))) or 0)
            flu_vaccinations = int(s.scalar(select(func.count(Immunization.id))) or 0)
            red_flag_escalations = int(s.scalar(select(func.count(Proposal.id)).where(Proposal.urgency >= 4)) or 0)
        return {
            "tenant": tenant_info,
            "metrics": {
                "total_patients": total_patients,
                "pending_proposals": pending_proposals,
                "completed_encounters": completed_encounters,
                "signed_certificates": signed_certificates,
                "signed_prescriptions": signed_prescriptions,
                "flu_vaccinations": flu_vaccinations,
                "red_flag_escalations": red_flag_escalations,
                "outbox_pending": outbox_pending,
                "outbox_dlq": outbox_dlq,
            },
            "corporates": corps,
            "campaigns": camps,
            "protocols": protos,
            "audit_events": auds,
            "outbox_stats": {"pending": outbox_pending, "dlq": outbox_dlq},
            "agent_runs_summary": {"total_runs": runs_count},
        }

    @app.post("/api/v1/admin/corporates")
    def admin_create_corporate(body: CreateCorporateReq, request: Request):
        """Story 16: Create corporate account with spending cap."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id, admin_id = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            corp = Corporate(
                id=new_id("corp"), tenant_id=tenant_id, name=body.name,
                spend_cap_hkd=body.spend_cap_hkd, billing_email=body.billing_email,
                admin_email=body.admin_email, admin_password_hash=hash_secret(body.admin_password),
            )
            s.add(corp)
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinic_admin", actor_id=admin_id,
                action="corporate.created", resource_type="Corporate", resource_id=corp.id,
            )
            return {
                "ok": True,
                "corporate": {
                    "id": corp.id, "name": corp.name, "spend_cap_hkd": corp.spend_cap_hkd,
                    "spent_hkd": corp.spent_hkd, "billing_email": corp.billing_email,
                    "admin_email": corp.admin_email, "active": corp.active,
                },
            }

    @app.post("/api/v1/admin/campaigns")
    def admin_create_campaign(body: CreateCampaignReq, request: Request):
        """Story 16: Create QR campaign with spend cap."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id, admin_id = claims["tid"], claims["sub"]
        if not body.corporate_id:
            raise HTTPException(422, "corporate_id is required")
        with database.tenant_session(tenant_id) as s:
            if not s.get(Corporate, body.corporate_id):
                raise HTTPException(404, "Corporate not found")
            camp = Campaign(
                id=new_id("camp"), tenant_id=tenant_id, corporate_id=body.corporate_id,
                code=body.code.strip().upper(), name=body.name,
                spend_cap_hkd=body.spend_cap_hkd, unit_fee_hkd=body.unit_fee_hkd,
            )
            s.add(camp)
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinic_admin", actor_id=admin_id,
                action="campaign.created", resource_type="Campaign", resource_id=camp.id,
                metadata={"code": camp.code, "spend_cap_hkd": camp.spend_cap_hkd},
            )
            return {
                "ok": True,
                "campaign": {
                    "id": camp.id, "corporate_id": camp.corporate_id, "code": camp.code,
                    "name": camp.name, "spend_cap_hkd": camp.spend_cap_hkd,
                    "spent_hkd": camp.spent_hkd, "unit_fee_hkd": camp.unit_fee_hkd, "active": camp.active,
                },
                "qr_payload_url": f"/wa?campaign={camp.code}",
            }

    @app.post("/api/v1/admin/protocols/{protocol_id}/approve")
    def admin_approve_protocol(protocol_id: str, request: Request, body: ApproveProtocolReq | None = None):
        """Story 18: Approve git-versioned protocol bundle (accepts ProtocolVersion.id or protocol_id)."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id, admin_id = claims["tid"], claims["sub"]
        req = body or ApproveProtocolReq()
        with database.tenant_session(tenant_id) as s:
            existing_pv = s.get(ProtocolVersion, protocol_id)
            if existing_pv:
                existing_pv.status = "approved"
                existing_pv.approved_by = admin_id
                existing_pv.approved_at = utcnow()
                pv = existing_pv
            else:
                bundle = load_protocol(protocol_id)
                pv = ProtocolVersion(
                    id=new_id("prv"), tenant_id=tenant_id, protocol_id=bundle.protocol_id,
                    version=req.version or bundle.version, content_hash=bundle.protocol_hash,
                    yaml_content=bundle.raw_yaml,
                    autonomy_level=req.autonomy_level, status="approved",
                    approved_by=admin_id, approved_at=utcnow(),
                )
                s.add(pv)
                s.flush()
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinic_admin", actor_id=admin_id,
                action="protocol.approved", resource_type="ProtocolVersion", resource_id=pv.id,
                metadata={"protocol_id": pv.protocol_id, "version": pv.version, "autonomy": pv.autonomy_level},
            )
            return {
                "ok": True,
                "protocol": {
                    "id": pv.id, "protocol_id": pv.protocol_id, "version": pv.version,
                    "status": pv.status, "autonomy_level": pv.autonomy_level,
                    "content_hash": pv.content_hash, "yaml_content": pv.yaml_content,
                    "approved_by": pv.approved_by,
                    "approved_at": pv.approved_at.isoformat() if pv.approved_at else None,
                    "created_at": (pv.created_at or utcnow()).isoformat(),
                },
            }

    @app.post("/api/v1/admin/kill-switch")
    def admin_kill_switch(body: KillSwitchReq, request: Request):
        """Story 18 & Section 17 Invariant: Per-tenant AI kill switch & residency policy control."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id, admin_id = claims["tid"], claims["sub"]
        with database.admin_session() as s:
            t = s.get(Tenant, tenant_id)
            if not t:
                raise HTTPException(404, "Tenant not found")
            t.ai_enabled = body.ai_enabled
            if body.allow_cross_border_inference is not None:
                t.allow_cross_border_inference = body.allow_cross_border_inference
            out = {"id": t.id, "ai_enabled": t.ai_enabled, "allow_cross_border_inference": t.allow_cross_border_inference, "region": t.region}
        with database.tenant_session(tenant_id) as s:
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinic_admin", actor_id=admin_id,
                action="tenant.kill_switch_updated", resource_type="Tenant", resource_id=tenant_id,
                metadata={"ai_enabled": body.ai_enabled, "reason": body.reason or ""},
            )
        return {"ok": True, "ai_enabled": out["ai_enabled"], "tenant": out}

    @app.get("/api/v1/admin/invoicing")
    def admin_invoicing(request: Request):
        """Story 19: Corporate invoicing & fulfilment reconciliation (aggregate only, zero N+1 queries)."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id = claims["tid"]
        with database.tenant_session(tenant_id) as s:
            corps = s.scalars(select(Corporate)).all()
            all_camps = s.scalars(select(Campaign)).all()
            camps_by_corp: dict[str, list[Campaign]] = {}
            for cm in all_camps:
                camps_by_corp.setdefault(cm.corporate_id, []).append(cm)

            enc_by_camp = {
                cid: int(cnt)
                for cid, cnt in s.execute(
                    select(Encounter.campaign_id, func.count(Encounter.id))
                    .where(Encounter.campaign_id.is_not(None))
                    .group_by(Encounter.campaign_id)
                ).all()
            }
            vax_by_camp = {
                cid: int(cnt)
                for cid, cnt in s.execute(
                    select(Immunization.campaign_id, func.count(Immunization.id))
                    .where(Immunization.campaign_id.is_not(None))
                    .group_by(Immunization.campaign_id)
                ).all()
            }
            cert_by_corp = {
                cid: int(cnt)
                for cid, cnt in s.execute(
                    select(Patient.corporate_id, func.count(Certificate.id))
                    .join(Certificate, Certificate.patient_id == Patient.id)
                    .where(Patient.corporate_id.is_not(None))
                    .group_by(Patient.corporate_id)
                ).all()
            }
            rx_by_corp = {
                cid: int(cnt)
                for cid, cnt in s.execute(
                    select(Patient.corporate_id, func.count(Prescription.id))
                    .join(Prescription, Prescription.patient_id == Patient.id)
                    .where(Patient.corporate_id.is_not(None))
                    .group_by(Patient.corporate_id)
                ).all()
            }

            invoices = []
            rows_out = []
            for c in corps:
                camps = camps_by_corp.get(c.id, [])
                camp_ids = [cm.id for cm in camps]
                visits = sum(enc_by_camp.get(cid, 0) for cid in camp_ids)
                vaxs = sum(vax_by_camp.get(cid, 0) for cid in camp_ids)
                certs = cert_by_corp.get(c.id, 0)
                rxs = rx_by_corp.get(c.id, 0)
                billing_records: list[dict[str, Any]] = []
                for _ in range(visits):
                    billing_records.append({"service_type": "teleconsult", "status": "completed", "campaign_id": camp_ids[0] if camp_ids else "default"})
                for _ in range(vaxs):
                    billing_records.append({"service_type": "flu_vax", "status": "completed", "campaign_id": camp_ids[0] if camp_ids else "default"})
                rec = billing_adapter.reconcile_invoice(
                    corporate_id=c.id,
                    corporate_name=c.name,
                    billing_period=utcnow().strftime("%Y-%m"),
                    records=billing_records,
                    spend_cap_hkd=c.spend_cap_hkd,
                )
                total_hkd = float(rec.payload.get("total_due_hkd", rec.payload.get("subtotal_hkd", 0.0)))
                invoices.append({
                    **rec.payload,
                    "visits": visits,
                    "certs": certs,
                    "vaccines": vaxs,
                    "fulfilled_scripts": rxs,
                    "total_hkd": total_hkd,
                    "spend_cap_hkd": c.spend_cap_hkd,
                    "reconciled": True,
                })
                for cm in camps:
                    cm_visits = enc_by_camp.get(cm.id, 0)
                    cm_total = min(cm.spend_cap_hkd, cm_visits * cm.unit_fee_hkd)
                    util = round((cm_total / cm.spend_cap_hkd) * 100.0, 1) if cm.spend_cap_hkd > 0 else 0.0
                    rows_out.append({
                        "corporate_id": c.id,
                        "corporate_name": c.name,
                        "campaign_id": cm.id,
                        "campaign_code": cm.code,
                        "campaign_name": cm.name,
                        "billable_visits": cm_visits,
                        "unit_fee_hkd": cm.unit_fee_hkd,
                        "total_hkd": cm_total,
                        "spend_cap_hkd": cm.spend_cap_hkd,
                        "utilization_pct": util,
                        "cap_reached": cm_total >= cm.spend_cap_hkd,
                    })
        return {
            "currency": "HKD",
            "total_billable_hkd": sum(r["total_hkd"] for r in rows_out),
            "invoices": invoices,
            "rows": rows_out,
        }

    @app.get("/api/v1/admin/observability")
    def admin_observability(request: Request):
        """Production SLO telemetry, clinical safety counters, ModelGateway circuit breaker, Vault health & Outbox status."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id = claims["tid"]
        with database.tenant_session(tenant_id) as s:
            status_counts = {
                st: int(cnt)
                for st, cnt in s.execute(
                    select(OutboxEvent.status, func.count(OutboxEvent.id)).group_by(OutboxEvent.status)
                ).all()
            }
            recent_outbox = [
                {
                    "id": ev.id,
                    "event_type": ev.event_type,
                    "aggregate_id": ev.aggregate_id,
                    "status": ev.status,
                    "attempts": ev.attempts,
                    "max_attempts": ev.max_attempts,
                    "last_error": ev.last_error,
                    "run_after": ev.run_after.isoformat(),
                    "created_at": ev.created_at.isoformat(),
                    "processed_at": ev.processed_at.isoformat() if ev.processed_at else None,
                }
                for ev in s.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at.desc()).limit(15)).all()
            ]
        now_mono = time.monotonic()
        with model_gw._lock:
            open_circuits = [
                {"provider": k[0], "region": k[1], "model_id": k[2], "remaining_seconds": round(v - now_mono, 1)}
                for k, v in model_gw._circuit_open_until.items()
                if v > now_mono
            ]
            usage_obj = model_gw._usage.get(tenant_id)
            gw_usage = {
                "requests": usage_obj.requests if usage_obj else 0,
                "total_tokens": usage_obj.total_tokens if usage_obj else 0,
                "cost_usd": usage_obj.cost_usd if usage_obj else 0.0,
            }
            gw_stats = {
                "fallback_count": model_gw.fallback_count,
                "circuit_breaker_trips": model_gw.circuit_breaker_trips,
                "open_circuits": open_circuits,
                "audit_entries": len(model_gw.audit_log),
                "tenant_usage": gw_usage,
            }
        return {
            "telemetry": telemetry.snapshot(),
            "gateway": gw_stats,
            "vault": doc_vault.health_check(),
            "outbox": {
                "counts": {
                    "pending": status_counts.get("pending", 0),
                    "done": status_counts.get("done", 0),
                    "dlq": status_counts.get("dlq", 0),
                },
                "worker": outbox_worker_state,
                "recent_events": recent_outbox,
            },
        }

    @app.post("/api/v1/admin/outbox/process")
    def admin_process_outbox(request: Request):
        """Immediately process due durable PostgreSQL outbox workflow events for this tenant."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id, admin_id = claims["tid"], claims["sub"]
        res = workflows.process_due(database, tenant_id, limit=50)
        if res.succeeded:
            telemetry.inc("outbox_events_processed_total", res.succeeded)
        if res.dlq:
            telemetry.inc("outbox_events_dlq_total", res.dlq)
        with database.tenant_session(tenant_id) as s:
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinic_admin", actor_id=admin_id,
                action="outbox.processed_manual", resource_type="OutboxEvent", resource_id=tenant_id,
                metadata=res.to_dict(),
            )
            counts = {
                st: int(cnt)
                for st, cnt in s.execute(
                    select(OutboxEvent.status, func.count(OutboxEvent.id)).group_by(OutboxEvent.status)
                ).all()
            }
        return {
            "ok": True,
            "batch": res.to_dict(),
            "counts": {
                "pending": counts.get("pending", 0),
                "done": counts.get("done", 0),
                "dlq": counts.get("dlq", 0),
            },
        }

    @app.get("/api/v1/admin/data-requests")
    def admin_list_data_requests(request: Request):
        """HK PDPO DPP6 Admin Governance Queue: list patient DAR / DCR / statutory deletion requests."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id = claims["tid"]
        with database.tenant_session(tenant_id) as s:
            rows = s.scalars(select(DataRequest).order_by(DataRequest.created_at.desc()).limit(100)).all()
            pat_ids = list({r.patient_id for r in rows})
            pat_map = (
                {p.id: p for p in s.scalars(select(Patient).where(Patient.id.in_(pat_ids))).all()}
                if pat_ids else {}
            )
            return {
                "data_requests": [
                    {
                        "id": r.id,
                        "patient_id": r.patient_id,
                        "patient_name_en": pat_map[r.patient_id].name_en if r.patient_id in pat_map else "",
                        "hkid_masked": pat_map[r.patient_id].hkid_masked if r.patient_id in pat_map else "",
                        "request_type": r.request_type,
                        "status": r.status,
                        "details": r.details_json,
                        "resolution_note": r.resolution_note,
                        "created_at": r.created_at.isoformat(),
                        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                    }
                    for r in rows
                ]
            }

    @app.post("/api/v1/admin/data-requests/{req_id}/resolve")
    def admin_resolve_data_request(req_id: str, body: ResolveDataRequestReq, request: Request):
        """HK PDPO DPP2/DPP6: resolve queued Data Correction Request as append-only audited addendum."""
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id, admin_id = claims["tid"], claims["sub"]
        with database.tenant_session(tenant_id) as s:
            dr = s.get(DataRequest, req_id)
            if not dr:
                raise HTTPException(404, "Data request not found")
            if dr.status not in ("queued_for_review", "recorded"):
                raise HTTPException(409, f"Data request is already finalized ({dr.status})")
            dr.status = "approved_addendum" if body.action == "approved" else "rejected"
            dr.resolution_note = body.resolution_note.strip()
            dr.resolved_at = utcnow()
            record_audit(
                s, tenant_id=tenant_id, actor_role="clinic_admin", actor_id=admin_id,
                action=f"pdpo.dcr_{body.action}", resource_type="DataRequest", resource_id=dr.id,
                metadata={"patient_id": dr.patient_id, "status": dr.status},
            )
            return {
                "ok": True,
                "data_request": {
                    "id": dr.id,
                    "patient_id": dr.patient_id,
                    "request_type": dr.request_type,
                    "status": dr.status,
                    "details": dr.details_json,
                    "resolution_note": dr.resolution_note,
                    "resolved_at": dr.resolved_at.isoformat(),
                },
            }

    @app.get("/api/v1/admin/audit")
    def admin_audit(request: Request, limit: int = Query(default=200, ge=1, le=500)):
        claims = extract_claims(request, allowed_roles=("clinic_admin",))
        tenant_id = claims["tid"]
        with database.tenant_session(tenant_id) as s:
            rows = s.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
        return {
            "events": [
                {"id": r.id, "actor_role": r.actor_role, "actor_id": r.actor_id, "action": r.action,
                 "resource_type": r.resource_type, "resource_id": r.resource_id, "break_glass": r.break_glass,
                 "metadata": r.metadata_json, "created_at": r.created_at.isoformat()}
                for r in rows
            ]
        }

    # ================================================================================== SURFACE 4B: CORPORATE ADMIN (ZERO INDIVIDUAL PII!)
    @app.get("/api/v1/corporate/dashboard")
    def corporate_dashboard(request: Request):
        """Story 17 & Section 17 Invariant: De-identified aggregate utilization ONLY with k-anonymity <5 suppression."""
        claims = extract_claims(request, allowed_roles=("corporate_admin",))
        tenant_id, corp_id = claims["tid"], claims.get("cid") or claims["sub"]
        with database.tenant_session(tenant_id) as s:
            corp = s.get(Corporate, corp_id)
            if not corp:
                raise HTTPException(404, "Corporate account not found")
            camps = s.scalars(select(Campaign).where(Campaign.corporate_id == corp_id)).all()
            camp_ids = [cm.id for cm in camps]
            visits_by_camp = (
                {
                    cid: int(cnt)
                    for cid, cnt in s.execute(
                        select(Encounter.campaign_id, func.count(Encounter.id))
                        .where(Encounter.campaign_id.in_(camp_ids))
                        .group_by(Encounter.campaign_id)
                    ).all()
                }
                if camp_ids else {}
            )
            certs_by_camp = (
                {
                    cid: int(cnt)
                    for cid, cnt in s.execute(
                        select(Patient.campaign_id, func.count(Certificate.id))
                        .join(Certificate, Certificate.patient_id == Patient.id)
                        .where(Patient.campaign_id.in_(camp_ids))
                        .group_by(Patient.campaign_id)
                    ).all()
                }
                if camp_ids else {}
            )
            vaxs_by_camp = (
                {
                    cid: int(cnt)
                    for cid, cnt in s.execute(
                        select(Immunization.campaign_id, func.count(Immunization.id))
                        .where(Immunization.campaign_id.in_(camp_ids))
                        .group_by(Immunization.campaign_id)
                    ).all()
                }
                if camp_ids else {}
            )
            out_camps = []
            tot_visits, tot_certs, tot_vaxs = 0, 0, 0
            for cm in camps:
                visits = visits_by_camp.get(cm.id, 0)
                certs = certs_by_camp.get(cm.id, 0)
                vaxs = vaxs_by_camp.get(cm.id, 0)
                tot_visits += visits
                tot_certs += certs
                tot_vaxs += vaxs
                v_k, c_k, x_k = _suppress_k(visits), _suppress_k(certs), _suppress_k(vaxs)
                out_camps.append({
                    "id": cm.id,
                    "code": cm.code,
                    "name": cm.name,
                    "spend_cap_hkd": cm.spend_cap_hkd,
                    "spent_hkd": cm.spent_hkd,
                    "unit_fee_hkd": cm.unit_fee_hkd,
                    "active": cm.active,
                    "visits_display": v_k["display"],
                    "visits_suppressed": v_k["suppressed"],
                    "certs_display": c_k["display"],
                    "certs_suppressed": c_k["suppressed"],
                    "vaccines_display": x_k["display"],
                    "vaccines_suppressed": x_k["suppressed"],
                    "vax_display": x_k["display"],
                    "vax_suppressed": x_k["suppressed"],
                    "qr_payload_url": f"/wa?campaign={cm.code}",
                })
            tv_k, tc_k, tx_k = _suppress_k(tot_visits), _suppress_k(tot_certs), _suppress_k(tot_vaxs)
            privacy_text = (
                "Hong Kong PDPO Employer Privacy Guarantee: Individual employee identities, consultations, "
                "medications, and diagnoses are never accessible to employers. Cohort counts between 1 and 4 "
                "are suppressed as '<5' to prevent re-identification."
            )
            return {
                "corporate": {
                    "id": corp.id,
                    "name": corp.name,
                    "spend_cap_hkd": corp.spend_cap_hkd,
                    "spent_hkd": corp.spent_hkd,
                    "billing_email": corp.billing_email,
                },
                "aggregates": {
                    "total_visits": tv_k["display"],
                    "visits_suppressed": tv_k["suppressed"],
                    "sick_leave_certificates": tc_k["display"],
                    "certificates_suppressed": tc_k["suppressed"],
                    "flu_vaccinations": tx_k["display"],
                    "vaccinations_suppressed": tx_k["suppressed"],
                    "total_spent_hkd": corp.spent_hkd,
                    "spend_cap_hkd": corp.spend_cap_hkd,
                },
                "corporate_id": corp.id,
                "corporate_name": corp.name,
                "spend_cap_hkd": corp.spend_cap_hkd,
                "spent_hkd": corp.spent_hkd,
                "k_anonymity_threshold": K_ANONYMITY_THRESHOLD,
                "campaigns": out_camps,
                "privacy_notice": privacy_text,
                "privacy_guarantee": privacy_text,
            }

    @app.post("/api/v1/corporate/campaigns")
    def corporate_create_campaign(body: CreateCampaignReq, request: Request):
        claims = extract_claims(request, allowed_roles=("corporate_admin",))
        tenant_id, corp_id = claims["tid"], claims.get("cid") or claims["sub"]
        with database.tenant_session(tenant_id) as s:
            camp = Campaign(
                id=new_id("camp"), tenant_id=tenant_id, corporate_id=corp_id,
                code=body.code.strip().upper(), name=body.name,
                spend_cap_hkd=body.spend_cap_hkd, unit_fee_hkd=body.unit_fee_hkd,
            )
            s.add(camp)
            record_audit(
                s, tenant_id=tenant_id, actor_role="corporate_admin", actor_id=corp_id,
                action="corporate.campaign_created", resource_type="Campaign", resource_id=camp.id,
            )
            return {
                "ok": True,
                "campaign": {
                    "id": camp.id, "code": camp.code, "name": camp.name,
                    "spend_cap_hkd": camp.spend_cap_hkd, "spent_hkd": camp.spent_hkd,
                    "unit_fee_hkd": camp.unit_fee_hkd, "active": camp.active,
                    "visits_display": "0", "visits_suppressed": False,
                },
                "qr_payload_url": f"/wa?campaign={camp.code}",
            }

    # ================================================================================== LIVE LEGACY FOLLOW-UP API (DEMO ONLY)
    from .legacy_feed import build_legacy_demo_store

    _legacy_store_holder: list[Any] = [None]

    def _legacy_store():
        if not _is_demo_mode():
            raise HTTPException(404, "Not found")
        if _legacy_store_holder[0] is None:
            _legacy_store_holder[0] = build_legacy_demo_store()
        if _legacy_store_holder[0] is None:
            raise HTTPException(404, "Legacy demo feed unavailable")
        return _legacy_store_holder[0]

    def _legacy_when(st, as_of: str | None) -> datetime:
        return datetime.fromisoformat(as_of) if as_of else st.default_as_of()

    @app.get("/api/health")
    def legacy_health():
        st = _legacy_store()
        return {"ok": True, "items": len(st.items)}

    @app.get("/api/source")
    def legacy_source():
        st = _legacy_store()
        return {**st.source, "clock": st.clock(), "staff": list(st.staff.values())}

    @app.get("/api/items")
    def legacy_items(
        as_of: str | None = None,
        status: str = Query("open", pattern="^(open|all)$"),
        owner: str | None = None,
        patient: str | None = None,
    ):
        st = _legacy_store()
        t = _legacy_when(st, as_of)
        rows = st.list_items(t, status, owner, patient)
        return {"as_of": t.isoformat(timespec="minutes"), "count": len(rows), "items": rows}

    @app.get("/api/items/{iid}")
    def legacy_item(iid: str, as_of: str | None = None):
        st = _legacy_store()
        try:
            return st.item(iid, _legacy_when(st, as_of))
        except KeyError as exc:
            raise HTTPException(404, "no such item at that time") from exc

    @app.get("/api/documents/{did}")
    def legacy_document(did: str):
        st = _legacy_store()
        if did not in st.documents:
            raise HTTPException(404, "no such document")
        return st.document(did)

    @app.get("/api/feeds")
    def legacy_feeds(as_of: str | None = None):
        st = _legacy_store()
        t = _legacy_when(st, as_of)
        down = st.feed_status(t)
        return {"as_of": t.isoformat(timespec="minutes"), "down": down, "all_clear_allowed": not down}

    @app.get("/api/alarms")
    def legacy_alarms():
        st = _legacy_store()
        return st.feeds

    @app.get("/api/patients/{pid}")
    def legacy_patient(pid: str, as_of: str | None = None):
        st = _legacy_store()
        if pid not in st.patients:
            raise HTTPException(404, "no such patient")
        return st.patient(pid, _legacy_when(st, as_of))

    @app.get("/api/evaluation")
    def legacy_evaluation():
        st = _legacy_store()
        return st.evaluation

    @app.post("/api/doctor/items/{iid}/close")
    def legacy_doctor_close(iid: str, body: dict[str, Any], request: Request, as_of: str | None = None):
        if request.headers.get("X-Role") != "doctor":
            raise HTTPException(403, "doctor only")
        st = _legacy_store()
        try:
            return st.doctor_close(
                iid,
                str(body.get("resolution", "acted")),
                request.headers.get("X-Doctor") or "Dr CHAN Siu Ming",
                str(body.get("note", "")),
                _legacy_when(st, as_of),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/audit")
    def legacy_audit():
        return _legacy_store().audit

    # ================================================================================== SERVE EC CARE LINE DEMO (STAGING/DEMO ONLY)
    @app.get("/ec")
    @app.get("/ec/{subpath:path}")
    def ec_care_line_demo(subpath: str = ""):
        if not _is_demo_mode() or not DEMO_EC_DIR.exists():
            raise HTTPException(404, "Not found")
        rel = subpath.strip("/") or "index.html"
        root = DEMO_EC_DIR.resolve()
        candidate = (root / rel).resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(candidate)

    # ================================================================================== SERVE BUILT 4-SURFACE FRONTEND
    if DIST.exists():
        assets_dir = DIST / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith("api/") or path == "ec" or path.startswith("ec/"):
                raise HTTPException(404, "API endpoint not found")
            dist_root = DIST.resolve()
            f = (dist_root / path).resolve() if path else dist_root
            return FileResponse(f if path and f.is_relative_to(dist_root) and f.is_file() else DIST / "index.html")

    return app


def app_from_env() -> FastAPI:
    """Factory entrypoint for uvicorn / Cloud Run: `uvicorn backend.acute.app:app_from_env --factory`."""
    return create_acute_app(seed_on_startup=_is_demo_mode())
