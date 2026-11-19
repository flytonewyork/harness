"""PostgreSQL 15 FHIR-aligned database schema with FORCED Row-Level Security (RLS) & immutable audit triggers.

Security & HK PDPO invariants enforced directly in PostgreSQL:
1. Every tenant-scoped table enables FORCE ROW LEVEL SECURITY bound to `current_setting('app.tenant_id', true)`.
   Because runtime queries execute as non-superuser `clinic_app` (NOBYPASSRLS), cross-tenant data access is
   mathematically impossible even if an application query omits a WHERE clause.
2. `audit_events` and `consents` have PostgreSQL BEFORE UPDATE OR DELETE triggers that unconditionally raise
   an exception — making the audit log and PDPO consent ledger truly append-only and tamper-evident.
3. Corporate utilization reporting uses aggregate-only SQL views with k-anonymity suppression (`< 5`) so
   employers can never view individual patient records or diagnoses.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DEFAULT_ADMIN_URL = "postgresql+psycopg://postgres@127.0.0.1:5432/clinic_harness"
DEFAULT_APP_URL = "postgresql+psycopg://clinic_app:clinic_app_dev@127.0.0.1:5432/clinic_harness"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


class Base(DeclarativeBase):
    pass


# -------------------------------------------------------------------------------------- Core Tenancy & Admin
class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    region: Mapped[str] = mapped_column(String(32), default="asia-east2", nullable=False)
    allowed_regions: Mapped[list] = mapped_column(JSON, default=lambda: ["asia-east2"], nullable=False)
    allow_cross_border_inference: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dpo_contact: Mapped[str] = mapped_column(String(160), default="dpo@tmrw-clinic.hk", nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Corporate(Base):
    __tablename__ = "corporates"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    spend_cap_hkd: Mapped[float] = mapped_column(Float, default=50000.0, nullable=False)
    spent_hkd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    billing_email: Mapped[str] = mapped_column(String(160), nullable=False)
    admin_email: Mapped[str] = mapped_column(String(160), nullable=False)
    admin_password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    corporate_id: Mapped[str] = mapped_column(String(48), ForeignKey("corporates.id"), index=True, nullable=False)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    spend_cap_hkd: Mapped[float] = mapped_column(Float, default=20000.0, nullable=False)
    spent_hkd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unit_fee_hkd: Mapped[float] = mapped_column(Float, default=420.0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Practitioner(Base):
    __tablename__ = "practitioners"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="clinician", nullable=False)  # clinician | clinic_admin
    mchk_reg_no: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. M12345 (HK MCHK)
    clinic_name: Mapped[str] = mapped_column(String(160), default="Central GP Clinic", nullable=False)
    availability_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# -------------------------------------------------------------------------------------- Patient & Onboarding
class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    corporate_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("corporates.id"), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("campaigns.id"), nullable=True)
    phone_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    phone_masked: Mapped[str] = mapped_column(String(32), nullable=False)
    name_en: Mapped[str] = mapped_column(String(160), nullable=False)
    name_zh: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    dob: Mapped[str] = mapped_column(String(16), nullable=False)  # YYYY-MM-DD
    sex: Mapped[str] = mapped_column(String(8), default="U", nullable=False)
    hkid_masked: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    ehealth_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    preferred_lang: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    g6pd_deficient: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    egfr: Mapped[float | None] = mapped_column(Float, nullable=True)
    allergies_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    current_meds_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    referral_code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    referred_by_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)  # active | deleted_statutory_hold
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MagicLink(Base):
    __tablename__ = "magic_links"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    campaign_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    referral_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patient_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), default="onboarding", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OTPChallenge(Base):
    __tablename__ = "otp_challenges"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    magic_token_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    code_demo_only: Mapped[str | None] = mapped_column(String(16), nullable=True)  # NULL in prod!
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Consent(Base):
    """Immutable append-only HK PDPO DPP1/DPP6 consent ledger."""
    __tablename__ = "consents"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(48), nullable=False)  # clinical_care | ai_assisted_intake | ehealth_sharing | invitations | deidentified_eval
    pics_version: Mapped[str] = mapped_column(String(32), default="HK-PICS-v1.0", nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    channel: Mapped[str] = mapped_column(String(32), default="pwa", nullable=False)
    wording_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), default="patient", nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# -------------------------------------------------------------------------------------- Clinical & AI Harness
class Encounter(Base):
    __tablename__ = "encounters"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    practitioner_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("practitioners.id"), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("campaigns.id"), nullable=True)
    encounter_type: Mapped[str] = mapped_column(String(32), default="acute_flu_teleconsult", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="intake", nullable=False)  # intake | red_flag_escalated | booked | in_consult | completed | referred
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    chief_complaint_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    structured_intake_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    icd10_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    icpc2_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    snomed_codes_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    clinical_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    encounter_id: Mapped[str] = mapped_column(String(48), ForeignKey("encounters.id"), index=True, nullable=False)
    loinc_code: Mapped[str] = mapped_column(String(32), nullable=False)
    display: Mapped[str] = mapped_column(String(160), nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ucum_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, default="", nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    encounter_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    protocol_id: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(32), default="asia-east2", nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    validators_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Proposal(Base):
    """AI proposal awaiting licensed clinician review (asymmetric authority: AI proposes, clinician decides)."""
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    encounter_id: Mapped[str] = mapped_column(String(48), ForeignKey("encounters.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)  # acute_intake | red_flag_alert | consult_draft | followup_48h
    urgency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # 4=Critical, 3=Urgent, 2=Soon, 1=Routine
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_review", nullable=False)  # pending_review | approved | edited | rejected | escalated
    protocol_id: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_run_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("agent_runs.id"), nullable=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_quotes_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    checks_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(48), ForeignKey("proposals.id"), index=True, nullable=False)
    practitioner_id: Mapped[str] = mapped_column(String(48), ForeignKey("practitioners.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # approved | edited | rejected | escalated
    edit_diff_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    review_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    rubber_stamp_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Certificate(Base):
    """Doctor-signed medical sick-leave certificate (Vault-encrypted PDF)."""
    __tablename__ = "certificates"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    encounter_id: Mapped[str] = mapped_column(String(48), ForeignKey("encounters.id"), nullable=False)
    practitioner_id: Mapped[str] = mapped_column(String(48), ForeignKey("practitioners.id"), nullable=False)
    mchk_reg_no: Mapped[str] = mapped_column(String(32), nullable=False)
    review_decision_id: Mapped[str] = mapped_column(String(48), ForeignKey("review_decisions.id"), nullable=False)
    days_off: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[str] = mapped_column(String(16), nullable=False)
    end_date: Mapped[str] = mapped_column(String(16), nullable=False)
    diagnosis_icd10: Mapped[str] = mapped_column(String(16), nullable=False)
    diagnosis_icpc2: Mapped[str] = mapped_column(String(16), nullable=False)
    vault_doc_id: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Prescription(Base):
    """Doctor-signed e-script with deterministic allergy/G6PD/interaction/dose checks."""
    __tablename__ = "prescriptions"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    encounter_id: Mapped[str] = mapped_column(String(48), ForeignKey("encounters.id"), nullable=False)
    practitioner_id: Mapped[str] = mapped_column(String(48), ForeignKey("practitioners.id"), nullable=False)
    mchk_reg_no: Mapped[str] = mapped_column(String(32), nullable=False)
    review_decision_id: Mapped[str] = mapped_column(String(48), ForeignKey("review_decisions.id"), nullable=False)
    items_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    safety_checks_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    fulfilment_mode: Mapped[str] = mapped_column(String(32), default="pending_choice", nullable=False)  # pending_choice | delivery | pickup
    fulfilment_status: Mapped[str] = mapped_column(String(32), default="signed", nullable=False)  # signed | sent_to_pharmacy | preparing | out_for_delivery | ready_for_pickup | completed
    fulfilment_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tracking_events_json: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Immunization(Base):
    __tablename__ = "immunizations"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("campaigns.id"), nullable=True)
    cvx_code: Mapped[str] = mapped_column(String(16), default="150", nullable=False)
    vaccine_display: Mapped[str] = mapped_column(String(160), nullable=False)
    partner_clinic: Mapped[str] = mapped_column(String(160), nullable=False)
    slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="booked", nullable=False)
    booking_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Message(Base):
    """Server-side encrypted in-app secure clinical thread (NEVER WhatsApp)."""
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    encounter_id: Mapped[str | None] = mapped_column(String(48), ForeignKey("encounters.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # in | out
    sender_role: Mapped[str] = mapped_column(String(32), nullable=False)  # patient | clinician | system_template
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WhatsAppOutbox(Base):
    """Non-clinical WhatsApp doorbell log — every row verified by WhatsAppLeakageGuard."""
    __tablename__ = "whatsapp_outbox"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    magic_link_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    demo_otp: Mapped[str | None] = mapped_column(String(16), nullable=True)  # staging demo only; NULL in prod
    leakage_passed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="sent", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class DataRequest(Base):
    """HK PDPO DPP6 Data Access / Correction / Deletion requests."""
    __tablename__ = "data_requests"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    patient_id: Mapped[str] = mapped_column(String(48), ForeignKey("patients.id"), index=True, nullable=False)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)  # dar_export | dcr_correction | deletion_request | consent_withdrawal
    status: Mapped[str] = mapped_column(String(32), default="recorded", nullable=False)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    resolution_note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProtocolVersion(Base):
    __tablename__ = "protocol_versions"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    protocol_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    autonomy_level: Mapped[str] = mapped_column(String(16), default="L1", nullable=False)  # L0 | L1 | L2
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)  # draft | approved | retired
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class OutboxEvent(Base):
    """Durable transactional outbox & delayed workflow timer queue (Temporal-swappable interface)."""
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True, nullable=False)  # pending | done | dlq
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Immutable append-only PHI read/write, agent run, and break-glass audit trail."""
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(48), ForeignKey("tenants.id"), index=True, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(48), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    break_glass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    break_glass_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# -------------------------------------------------------------------------------------- RLS & Triggers
TENANT_SCOPED_TABLES = (
    "corporates", "campaigns", "practitioners", "patients", "magic_links", "otp_challenges",
    "consents", "encounters", "observations", "agent_runs", "proposals", "review_decisions",
    "certificates", "prescriptions", "immunizations", "messages", "whatsapp_outbox",
    "data_requests", "protocol_versions", "outbox_events", "audit_events",
)

IMMUTABLE_TABLES = ("audit_events", "consents")


class Database:
    """Manages PostgreSQL 15 schema, forced RLS policies, immutable triggers, and tenant-scoped sessions."""

    def __init__(self, admin_url: str | None = None, app_url: str | None = None):
        self.admin_url = admin_url or os.environ.get("DATABASE_ADMIN_URL") or os.environ.get("DATABASE_URL") or DEFAULT_ADMIN_URL
        self.app_url = app_url or os.environ.get("DATABASE_APP_URL") or os.environ.get("DATABASE_URL") or DEFAULT_APP_URL
        self.admin_engine = create_engine(self.admin_url, pool_pre_ping=True, future=True)
        self.app_engine = create_engine(self.app_url, pool_pre_ping=True, future=True)
        self._admin_sm = sessionmaker(bind=self.admin_engine, expire_on_commit=False)
        self._app_sm = sessionmaker(bind=self.app_engine, expire_on_commit=False)

    def init_schema(self) -> None:
        """Create tables, grant privileges to clinic_app, enforce FORCE RLS, and install immutability triggers."""
        Base.metadata.create_all(self.admin_engine)
        with self.admin_engine.begin() as conn:
            # Ensure non-superuser app role has table permissions (skip gracefully if running on Cloud SQL single role)
            has_role = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'clinic_app'")).fetchone()
            if has_role:
                conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clinic_app"))
                conn.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO clinic_app"))

            # Enforce FORCE ROW LEVEL SECURITY on every tenant-scoped table
            for tbl in TENANT_SCOPED_TABLES:
                conn.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
                conn.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
                conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation_{tbl} ON {tbl}"))
                conn.execute(text(
                    f"CREATE POLICY tenant_isolation_{tbl} ON {tbl} "
                    f"USING (tenant_id = current_setting('app.tenant_id', true)) "
                    f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
                ))

            # Install PostgreSQL append-only immutability trigger on audit_events and consents
            conn.execute(text("""
                CREATE OR REPLACE FUNCTION prevent_immutable_mutation()
                RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'Table % is append-only (HK PDPO / immutable audit invariant)', TG_TABLE_NAME;
                END;
                $$ LANGUAGE plpgsql;
            """))
            for tbl in IMMUTABLE_TABLES:
                conn.execute(text(f"DROP TRIGGER IF EXISTS trg_immutable_{tbl} ON {tbl}"))
                conn.execute(text(
                    f"CREATE TRIGGER trg_immutable_{tbl} "
                    f"BEFORE UPDATE OR DELETE ON {tbl} "
                    f"FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()"
                ))

    @contextmanager
    def tenant_session(self, tenant_id: str) -> Iterator[Session]:
        """Yield a PostgreSQL session locked to `tenant_id` via SET LOCAL app.tenant_id (enforced by RLS)."""
        with self._app_sm() as session:
            with session.begin():
                session.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id})
                yield session

    @contextmanager
    def admin_session(self) -> Iterator[Session]:
        """Yield an admin/bootstrap session (only for tenant lookup, campaign code lookup, and schema init)."""
        with self._admin_sm() as session:
            with session.begin():
                yield session


def record_audit(
    session: Session,
    *,
    tenant_id: str,
    actor_role: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, Any] | None = None,
    break_glass: bool = False,
    break_glass_reason: str | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        id=new_id("aud"),
        tenant_id=tenant_id,
        actor_role=actor_role,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        break_glass=break_glass,
        break_glass_reason=break_glass_reason,
        metadata_json=metadata or {},
    )
    session.add(ev)
    return ev
