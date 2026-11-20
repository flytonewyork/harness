/**
 * Strongly typed REST client for the Clinic AI Harness — Flu-Season Acute Care MVP (/api/v1/...).
 * Matches backend/acute/db.py, backend/acute/security.py, and backend/acute/terminology.py.
 */

export type Lang = "en" | "zh";

export function tr(lang: Lang, en: string, zh: string): string {
  return lang === "zh" ? zh : en;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  body?: unknown;

  constructor(status: number, detail: string, body?: unknown) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

// -------------------------------------------------------------------------------------- Reference Clinical Data
export interface HKDiagnosisOption {
  icd10: string;
  icd10_display: string;
  icpc2: string;
  icpc2_display: string;
  display_zh: string;
}

export const HK_DIAGNOSES: HKDiagnosisOption[] = [
  { icd10: "J11.1", icd10_display: "Influenza with other respiratory manifestations, virus not identified", icpc2: "R80", icpc2_display: "Influenza", display_zh: "流行性感冒" },
  { icd10: "J10.1", icd10_display: "Influenza with other respiratory manifestations, seasonal virus identified", icpc2: "R80", icpc2_display: "Influenza", display_zh: "季節性流行性感冒（已確診）" },
  { icd10: "J06.9", icd10_display: "Acute upper respiratory infection, unspecified", icpc2: "R74", icpc2_display: "Upper respiratory infection acute", display_zh: "急性上呼吸道感染" },
  { icd10: "J02.9", icd10_display: "Acute pharyngitis, unspecified", icpc2: "R74", icpc2_display: "Acute pharyngitis / URTI", display_zh: "急性咽喉炎" },
  { icd10: "J20.9", icd10_display: "Acute bronchitis, unspecified", icpc2: "R78", icpc2_display: "Acute bronchitis/bronchiolitis", display_zh: "急性支氣管炎" },
  { icd10: "R50.9", icd10_display: "Fever, unspecified", icpc2: "A03", icpc2_display: "Fever", display_zh: "發燒（待查）" },
  { icd10: "A09", icd10_display: "Infectious gastroenteritis and colitis, unspecified", icpc2: "D73", icpc2_display: "Gastroenteritis presumed infection", display_zh: "急性腸胃炎" },
];

export interface HKFormularyDrug {
  key: string;
  rxnorm: string;
  atc: string;
  display: string;
  display_zh: string;
  hk_schedule: "OTC" | "PART_I_POISON" | "DANGEROUS_DRUG";
  default_dose_mg: number;
  default_frequency: string;
  default_days: number;
  g6pd_risk: boolean;
}

export const HK_FORMULARY: HKFormularyDrug[] = [
  { key: "paracetamol", rxnorm: "161", atc: "N02BE01", display: "Paracetamol 500mg Tablet", display_zh: "撲熱息痛 500毫克", hk_schedule: "OTC", default_dose_mg: 500, default_frequency: "QDS", default_days: 3, g6pd_risk: false },
  { key: "oseltamivir", rxnorm: "261315", atc: "J05AH02", display: "Oseltamivir (Tamiflu) 75mg Capsule", display_zh: "奧司他韋（特敏福）75毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 75, default_frequency: "BD", default_days: 5, g6pd_risk: false },
  { key: "ibuprofen", rxnorm: "5640", atc: "M01AE01", display: "Ibuprofen 400mg Tablet", display_zh: "布洛芬 400毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 400, default_frequency: "TDS", default_days: 3, g6pd_risk: false },
  { key: "naproxen", rxnorm: "7258", atc: "M01AE02", display: "Naproxen 250mg Tablet", display_zh: "萘普生 250毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 250, default_frequency: "BD", default_days: 3, g6pd_risk: false },
  { key: "aspirin", rxnorm: "1191", atc: "N02BA01", display: "Aspirin 300mg Tablet", display_zh: "阿士匹靈 300毫克", hk_schedule: "OTC", default_dose_mg: 300, default_frequency: "TDS", default_days: 3, g6pd_risk: true },
  { key: "amoxicillin", rxnorm: "723", atc: "J01CA04", display: "Amoxicillin 500mg Capsule", display_zh: "阿莫西林 500毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 500, default_frequency: "TDS", default_days: 5, g6pd_risk: false },
  { key: "amoxicillin-clavulanate", rxnorm: "617995", atc: "J01CR02", display: "Amoxicillin-Clavulanate 625mg Tablet", display_zh: "安滅菌 625毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 625, default_frequency: "TDS", default_days: 5, g6pd_risk: false },
  { key: "clarithromycin", rxnorm: "21212", atc: "J01FA09", display: "Clarithromycin 500mg Tablet", display_zh: "克拉黴素 500毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 500, default_frequency: "BD", default_days: 5, g6pd_risk: false },
  { key: "co-trimoxazole", rxnorm: "10831", atc: "J01EE01", display: "Co-trimoxazole 480mg Tablet (G6PD Risk)", display_zh: "復方新諾明 480毫克（蠶豆症忌用）", hk_schedule: "PART_I_POISON", default_dose_mg: 960, default_frequency: "BD", default_days: 5, g6pd_risk: true },
  { key: "nitrofurantoin", rxnorm: "7454", atc: "J01XE01", display: "Nitrofurantoin 100mg Capsule (G6PD Risk)", display_zh: "呋喃妥因 100毫克（蠶豆症忌用）", hk_schedule: "PART_I_POISON", default_dose_mg: 100, default_frequency: "BD", default_days: 5, g6pd_risk: true },
  { key: "cetirizine", rxnorm: "20610", atc: "R06AE07", display: "Cetirizine 10mg Tablet", display_zh: "西替利嗪 10毫克", hk_schedule: "OTC", default_dose_mg: 10, default_frequency: "QD", default_days: 5, g6pd_risk: false },
  { key: "dextromethorphan", rxnorm: "3289", atc: "R05DA09", display: "Dextromethorphan 15mg Tablet", display_zh: "右美沙芬 15毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 15, default_frequency: "TDS", default_days: 5, g6pd_risk: false },
  { key: "pseudoephedrine", rxnorm: "8896", atc: "R01BA02", display: "Pseudoephedrine 60mg Tablet", display_zh: "偽麻黃鹼 60毫克", hk_schedule: "PART_I_POISON", default_dose_mg: 60, default_frequency: "TDS", default_days: 3, g6pd_risk: false },
  { key: "codeine", rxnorm: "2670", atc: "R05DA04", display: "Codeine Phosphate 15mg (Cap. 134 Dangerous Drug)", display_zh: "可待因 15毫克（危險藥物）", hk_schedule: "DANGEROUS_DRUG", default_dose_mg: 15, default_frequency: "TDS", default_days: 3, g6pd_risk: false },
  { key: "zolpidem", rxnorm: "39993", atc: "N05CF02", display: "Zolpidem 10mg (Cap. 134 Dangerous Drug)", display_zh: "唑吡坦 10毫克（危險藥物）", hk_schedule: "DANGEROUS_DRUG", default_dose_mg: 10, default_frequency: "ON", default_days: 5, g6pd_risk: false },
  { key: "diazepam", rxnorm: "3322", atc: "N05BA01", display: "Diazepam 5mg (Cap. 134 Dangerous Drug)", display_zh: "地西泮 5毫克（危險藥物）", hk_schedule: "DANGEROUS_DRUG", default_dose_mg: 5, default_frequency: "BD", default_days: 3, g6pd_risk: false },
  { key: "tramadol", rxnorm: "10689", atc: "N02AX02", display: "Tramadol 50mg (Cap. 134 Dangerous Drug)", display_zh: "曲馬多 50毫克（危險藥物）", hk_schedule: "DANGEROUS_DRUG", default_dose_mg: 50, default_frequency: "TDS", default_days: 3, g6pd_risk: false },
];

export const CVX_VACCINE_OPTIONS = [
  { code: "150", display: "Quadrivalent Inactivated Flu Vaccine (IIV4, Preservative-Free)", display_zh: "四價滅活季節性流感疫苗（無防腐劑）" },
  { code: "171", display: "Cell-Based Quadrivalent Flu Vaccine (ccIIV4)", display_zh: "四價細胞培養季節性流感疫苗" },
  { code: "185", display: "Recombinant Quadrivalent Flu Vaccine (RIV4)", display_zh: "四價重組季節性流感疫苗" },
];

// -------------------------------------------------------------------------------------- API Types
export interface HealthResponse {
  status: string;
  demo_mode: boolean;
  region?: string;
  tenant_id?: string;
  ai_enabled?: boolean;
  version?: string;
}

export interface DemoPersona {
  id: string;
  key?: string;
  role: "patient" | "clinician" | "clinic_admin" | "corporate_admin";
  label: string;
  label_zh?: string;
  description?: string;
  email?: string;
  phone?: string;
  surface?: string;
  mchk_reg_no?: string;
}

export interface AuthSession {
  access_token: string;
  token?: string;
  role: "patient" | "clinician" | "clinic_admin" | "corporate_admin";
  subject_id?: string;
  user_id?: string;
  tenant_id?: string;
  step_up_verified?: boolean;
  onboarded?: boolean;
  name_en?: string;
  name_zh?: string;
  mchk_reg_no?: string | null;
  corporate_id?: string | null;
  campaign_code?: string | null;
}

export interface WhatsAppMessage {
  id: string;
  phone: string;
  conversation_id: string;
  template_id: string;
  body_text: string;
  magic_link_url?: string | null;
  magic_token?: string | null;
  demo_otp?: string | null;
  leakage_passed: boolean;
  status: string;
  created_at: string;
}

export interface ScanQrResponse {
  ok?: boolean;
  conversation_id: string;
  campaign_code?: string;
  campaign_name?: string;
  magic_token: string;
  magic_link_url: string;
  message?: WhatsAppMessage;
  leakage_passed?: boolean;
}

export interface MagicPreviewResponse {
  valid: boolean;
  redeemed: boolean;
  burned?: boolean;
  expired?: boolean;
  purpose: string;
  campaign_code?: string | null;
  campaign_name?: string | null;
  clinic_name?: string;
  phone_masked?: string;
  expires_at?: string;
  note?: string;
}

export interface MagicRedeemResponse {
  ok?: boolean;
  challenge_id: string;
  otp_challenge_id?: string;
  phone_masked?: string;
  expires_at?: string;
  demo_otp?: string | null;
  message?: string;
}

export interface VerifyOtpResponse extends AuthSession {
  verified?: boolean;
  patient_id?: string | null;
  needs_onboarding?: boolean;
}

export interface PicsDocument {
  version: string;
  title_en: string;
  title_zh: string;
  summary_en: string;
  summary_zh: string;
  ai_disclosure_en: string;
  ai_disclosure_zh: string;
  dpo_contact: string;
  wording_hash?: string;
  scopes: Array<{
    scope: string;
    label_en: string;
    label_zh: string;
    description_en: string;
    description_zh: string;
    required: boolean;
    default_checked?: boolean;
  }>;
}

export interface PatientProfile {
  id: string;
  name_en: string;
  name_zh?: string;
  dob: string;
  sex: string;
  hkid_masked: string;
  phone_masked: string;
  preferred_lang: string;
  ehealth_consent: boolean;
  g6pd_deficient: boolean;
  weight_kg?: number | null;
  egfr?: number | null;
  allergies: string[];
  current_meds: string[];
  referral_code: string;
  referred_by_code?: string | null;
  corporate_name?: string | null;
  campaign_code?: string | null;
  status: string;
  deletion_requested_at?: string | null;
  retention_until?: string | null;
}

export interface ConsentRecord {
  id: string;
  scope: string;
  pics_version: string;
  granted: boolean;
  recorded_at: string;
}

export interface EncounterSummary {
  id: string;
  encounter_type: string;
  status: string;
  scheduled_at?: string | null;
  chief_complaint_text: string;
  icd10_code?: string | null;
  icpc2_code?: string | null;
  snomed_codes?: string[];
  clinical_note?: string;
  escalation_reason?: string | null;
  practitioner_name?: string | null;
  created_at: string;
}

export interface CertificateRecord {
  id: string;
  encounter_id: string;
  practitioner_name?: string;
  mchk_reg_no: string;
  days_off: number;
  start_date: string;
  end_date: string;
  diagnosis_icd10: string;
  diagnosis_icpc2: string;
  verification_hash: string;
  signed_at: string;
}

export interface PrescriptionItem {
  drug: string;
  display?: string;
  display_zh?: string;
  rxnorm?: string;
  atc?: string;
  hk_schedule?: string;
  dose_mg: number;
  frequency: string;
  days: number;
  instructions?: string;
}

export interface PrescriptionRecord {
  id: string;
  encounter_id: string;
  practitioner_name?: string;
  mchk_reg_no: string;
  items: PrescriptionItem[];
  safety_checks?: RxSafetyFinding[];
  fulfilment_mode: "pending_choice" | "delivery" | "pickup" | string;
  fulfilment_status: string;
  fulfilment_ref?: string | null;
  tracking_events?: Array<{ status: string; at: string; detail?: string; note?: string }>;
  signed_at: string;
}

export interface ImmunizationRecord {
  id: string;
  cvx_code: string;
  vaccine_display: string;
  partner_clinic: string;
  slot_at: string;
  status: string;
  booking_ref: string;
  created_at: string;
}

export interface SecureMessage {
  id: string;
  direction: "in" | "out";
  sender_role: string;
  body_text: string;
  created_at: string;
}

export interface PatientMeResponse {
  onboarded?: boolean;
  patient: PatientProfile | null;
  consents: ConsentRecord[];
  encounters: EncounterSummary[];
  certificates: CertificateRecord[];
  prescriptions: PrescriptionRecord[];
  immunizations: ImmunizationRecord[];
  messages: SecureMessage[];
  data_requests?: Array<{
    id: string;
    request_type: string;
    status: string;
    resolution_note?: string;
    created_at: string;
  }>;
}

export interface IntakeResponse {
  encounter_id: string;
  proposal_id?: string;
  status: string;
  red_flag: boolean;
  red_flags?: string[];
  urgency?: number;
  crisis_banner?: {
    title_en?: string;
    title_zh?: string;
    body_en?: string;
    body_zh?: string;
    emergency_number?: string;
    samaritans_hotline?: string;
    is_mental_health_crisis?: boolean;
  } | null;
  structured_intake?: Record<string, unknown>;
  ai_used?: boolean;
  ai_disabled_notice?: string | null;
}

export interface TeleconsultSlot {
  slot_id?: string;
  practitioner_id: string;
  practitioner_name: string;
  practitioner_name_zh?: string;
  mchk_reg_no: string;
  clinic_name?: string;
  slot_at: string;
  available?: boolean;
}

export interface CheckResult {
  id: string;
  name?: string;
  label?: string;
  status: "pass" | "warn" | "fail" | "na";
  detail?: string;
}

export interface SourceQuote {
  field?: string;
  quote: string;
  source?: string;
  timestamp?: string;
}

export interface QueueProposalSummary {
  id: string;
  patient_id: string;
  patient_name_en: string;
  patient_name_zh?: string;
  encounter_id: string;
  kind: string;
  urgency: number;
  urgency_label?: string;
  sla_due_at: string;
  overdue?: boolean;
  status: string;
  protocol_id: string;
  protocol_version: string;
  red_flag?: boolean;
  g6pd_deficient?: boolean;
  allergies?: string[];
  checks_summary?: { passed: number; total: number; warnings?: number; failures?: number };
  checks?: CheckResult[];
  chief_complaint?: string;
  created_at: string;
}

export interface ClinicalDecisionSupport {
  oseltamivir_window?: string;
  onset_hours?: number | null;
  antibiotic_stewardship?: string;
  high_risk_host_factors?: string[];
  pregnancy_detected?: boolean;
  double_sickening_suspected?: boolean;
  safety_netting_en?: string;
  safety_netting_zh?: string;
}

export interface ProposalDetail extends QueueProposalSummary {
  patient: PatientProfile;
  encounter: EncounterSummary;
  observations: Array<{
    id: string;
    loinc_code: string;
    display: string;
    value_num?: number | null;
    value_text?: string | null;
    ucum_unit: string;
    source_quote?: string;
    recorded_at: string;
  }>;
  messages?: SecureMessage[];
  payload: {
    summary?: string;
    draft_clinical_note?: string;
    clinical_note?: string;
    clinical_decision_support?: ClinicalDecisionSupport | null;
    contraindications_noted?: string[];
    suggested_icd10?: string;
    suggested_icpc2?: string;
    recommended_sick_leave_days?: number;
    suggested_rx?: PrescriptionItem[];
    red_flags?: string[];
    snomed_symptoms?: Array<{ code: string; display: string; display_zh?: string }>;
    structured_intake?: Record<string, unknown>;
    [key: string]: unknown;
  };
  source_quotes: SourceQuote[];
  checks: CheckResult[];
  agent_run?: {
    id: string;
    model_id: string;
    provider: string;
    region: string;
    redacted: boolean;
    latency_ms: number;
    protocol_id: string;
    protocol_version: string;
  } | null;
  practitioner?: {
    id: string;
    name_en: string;
    name_zh?: string;
    mchk_reg_no?: string;
    clinic_name?: string;
  };
}

export interface RxSafetyFinding {
  drug: string;
  severity: "hard_block" | "override_required" | "warn" | "pass" | "HARD_BLOCK" | "OVERRIDE_REQUIRED" | "WARN" | "PASS" | string;
  rule?: string;
  rule_ref?: string;
  message: string;
  message_zh?: string;
  code?: string;
}

export interface RxSafetyCheckResponse {
  status?: "pass" | "warn" | "override_required" | "hard_block" | string;
  can_sign_without_override?: boolean;
  safe?: boolean;
  allowed?: boolean;
  has_hard_block?: boolean;
  requires_override?: boolean;
  hard_blocks?: RxSafetyFinding[];
  overrides_required?: RxSafetyFinding[];
  warnings?: RxSafetyFinding[];
  findings: RxSafetyFinding[];
  enriched_items?: PrescriptionItem[];
  normalized_items?: PrescriptionItem[];
}

export interface AuditEventItem {
  id: string;
  actor_role: string;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string;
  break_glass: boolean;
  break_glass_reason?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
}

export interface ProtocolVersionItem {
  id: string;
  protocol_id: string;
  version: string;
  content_hash: string;
  yaml_content: string;
  autonomy_level: string;
  status: string;
  approved_by?: string | null;
  approved_at?: string | null;
  created_at: string;
}

export interface AdminOverviewResponse {
  tenant: {
    id: string;
    name: string;
    region: string;
    allowed_regions: string[];
    allow_cross_border_inference: boolean;
    ai_enabled: boolean;
    dpo_contact: string;
  };
  metrics?: {
    total_patients?: number;
    pending_proposals?: number;
    completed_encounters?: number;
    signed_certificates?: number;
    signed_prescriptions?: number;
    flu_vaccinations?: number;
    red_flag_escalations?: number;
    outbox_pending?: number;
    outbox_dlq?: number;
  };
  corporates: Array<{
    id: string;
    name: string;
    spend_cap_hkd: number;
    spent_hkd: number;
    billing_email: string;
    admin_email: string;
    active: boolean;
  }>;
  campaigns: Array<{
    id: string;
    corporate_id: string;
    corporate_name?: string;
    code: string;
    name: string;
    spend_cap_hkd: number;
    spent_hkd: number;
    unit_fee_hkd: number;
    active: boolean;
  }>;
  protocols: ProtocolVersionItem[];
}

export interface InvoicingResponse {
  currency?: string;
  total_billable_hkd?: number;
  rows: Array<{
    corporate_id: string;
    corporate_name: string;
    campaign_id?: string;
    campaign_code: string;
    campaign_name?: string;
    billable_visits: number;
    unit_fee_hkd: number;
    total_hkd: number;
    spend_cap_hkd: number;
    utilization_pct?: number;
    cap_reached?: boolean;
  }>;
}

export interface CorporateDashboardResponse {
  corporate: {
    id: string;
    name: string;
    spend_cap_hkd: number;
    spent_hkd: number;
    billing_email?: string;
  };
  privacy_guarantee?: string;
  k_anonymity_threshold?: number;
  aggregates: {
    total_visits: number | string;
    visits_suppressed?: boolean;
    sick_leave_certificates: number | string;
    certificates_suppressed?: boolean;
    flu_vaccinations: number | string;
    vaccinations_suppressed?: boolean;
    total_spent_hkd: number;
    spend_cap_hkd: number;
  };
  campaigns: Array<{
    id: string;
    code: string;
    name: string;
    spend_cap_hkd: number;
    spent_hkd: number;
    unit_fee_hkd: number;
    active: boolean;
    visits_display: number | string;
    visits_suppressed?: boolean;
    certs_display?: number | string;
    certs_suppressed?: boolean;
    vax_display?: number | string;
    vax_suppressed?: boolean;
  }>;
}

export interface AdminObservabilityResponse {
  telemetry: {
    uptime_seconds: number;
    http: {
      requests_total: number;
      status_classes: Record<string, number>;
      error_rate_5xx_pct: number;
      latency_ms: { p50: number; p95: number; p99: number };
      routes: Record<string, { count: number; p50_ms: number; p95_ms: number; p99_ms: number }>;
    };
    clinical_safety: Record<string, number>;
  };
  gateway: {
    fallback_count: number;
    circuit_breaker_trips: number;
    open_circuits: Array<{ provider: string; region: string; model_id: string; remaining_seconds: number }>;
    audit_entries: number;
    tenant_usage: { requests: number; total_tokens: number; cost_usd: number };
  };
  vault: {
    ok: boolean;
    backend?: string;
    bucket?: string;
    kms_key_id?: string;
  };
  outbox: {
    counts: { pending: number; done: number; dlq: number };
    worker: {
      enabled: boolean;
      poll_interval_seconds: number;
      ticks: number;
      last_tick_at: string | null;
      last_processed: number;
      last_error: string | null;
    };
    recent_events: Array<{
      id: string;
      event_type: string;
      aggregate_id: string;
      status: string;
      attempts: number;
      max_attempts: number;
      last_error?: string | null;
      run_after: string;
      created_at: string;
      processed_at?: string | null;
    }>;
  };
}

export interface AdminDataRequestItem {
  id: string;
  patient_id: string;
  patient_name_en: string;
  hkid_masked: string;
  request_type: string;
  status: string;
  details: Record<string, unknown>;
  resolution_note: string;
  created_at: string;
  resolved_at?: string | null;
}

// -------------------------------------------------------------------------------------- Token Storage & HTTP Core
let activeToken: string | null = null;

export function setAuthToken(token: string | null): void {
  activeToken = token;
}

export function getAuthToken(): string | null {
  return activeToken;
}

async function request<T>(
  method: string,
  path: string,
  options: {
    body?: unknown;
    params?: Record<string, string | number | boolean | undefined | null>;
    token?: string | null;
  } = {}
): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (options.params) {
    for (const [k, v] of Object.entries(options.params)) {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    }
  }

  const headers: Record<string, string> = {};
  const tok = options.token !== undefined ? options.token : activeToken;
  if (tok) {
    headers["Authorization"] = `Bearer ${tok}`;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(url.pathname + url.search, {
    method,
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  const contentType = resp.headers.get("content-type") || "";
  if (!resp.ok) {
    let detail = `Request failed (${resp.status})`;
    let parsed: unknown = undefined;
    try {
      if (contentType.includes("application/json")) {
        parsed = await resp.json();
        const obj = parsed as Record<string, unknown>;
        if (typeof obj.detail === "string") {
          detail = obj.detail;
        } else if (Array.isArray(obj.detail)) {
          detail = obj.detail.map((e: Record<string, unknown>) => String(e.msg || JSON.stringify(e))).join("; ");
        } else if (typeof obj.message === "string") {
          detail = obj.message;
        }
      } else {
        const txt = await resp.text();
        if (txt) detail = txt.slice(0, 300);
      }
    } catch {
      // ignore parse error
    }
    throw new ApiError(resp.status, detail, parsed);
  }

  if (resp.status === 204) {
    return {} as T;
  }
  if (contentType.includes("application/json")) {
    return (await resp.json()) as T;
  }
  return (await resp.text()) as unknown as T;
}

// -------------------------------------------------------------------------------------- Locked API Client
export const acuteApi = {
  // System & Health
  health: () => request<HealthResponse>("GET", "/api/v1/health", { token: null }),

  // Staging Demo Controls
  demoPersonas: () =>
    request<{ personas?: DemoPersona[] } | DemoPersona[]>("GET", "/api/v1/demo/personas", { token: null }).then((r) =>
      Array.isArray(r) ? r : (r.personas ?? [])
    ),

  demoLoginAs: (personaKey: string) =>
    request<AuthSession>("POST", "/api/v1/demo/login-as", {
      body: { persona: personaKey, persona_id: personaKey },
      token: null,
    }),

  demoReset: () => request<{ ok: boolean; message?: string }>("POST", "/api/v1/demo/reset", { body: {} }),

  // Standard Staff / Admin Login
  authLogin: (email: string, password: string) =>
    request<AuthSession>("POST", "/api/v1/auth/login", {
      body: { email, password },
      token: null,
    }),

  // Surface 1: WhatsApp Doorbell (Zero Clinical / Zero ID Leakage)
  waScanQr: (payload: { phone: string; campaign_code: string; referral_code?: string; lang?: Lang }) =>
    request<ScanQrResponse>("POST", "/api/v1/wa/scan-qr", { body: payload, token: null }),

  waInbox: (phone?: string) =>
    request<{ messages?: WhatsAppMessage[] } | WhatsAppMessage[]>("GET", "/api/v1/wa/inbox", {
      params: { phone },
      token: null,
    }).then((r) => (Array.isArray(r) ? r : (r.messages ?? []))),

  // Magic-Link GET Preview (safe, never burns) + POST Redeem + 6-digit OTP Step-Up
  magicPreview: (token: string) =>
    request<MagicPreviewResponse>("GET", `/api/v1/magic/${encodeURIComponent(token)}`, { token: null }),

  magicRedeem: (token: string) =>
    request<MagicRedeemResponse>("POST", `/api/v1/magic/${encodeURIComponent(token)}/redeem`, {
      body: {},
      token: null,
    }),

  magicVerifyOtp: (payload: { challenge_id: string; code: string; magic_token?: string }) =>
    request<VerifyOtpResponse>("POST", "/api/v1/magic/verify-otp", {
      body: { challenge_id: payload.challenge_id, otp: payload.code, code: payload.code, magic_token: payload.magic_token },
      token: null,
    }),

  // Surface 2: Patient PWA (Stories 1–10)
  patientPics: (lang?: Lang) =>
    request<PicsDocument>("GET", "/api/v1/patient/pics", { params: { lang } }),

  patientOnboard: (payload: {
    name_en: string;
    name_zh?: string;
    dob: string;
    sex: string;
    hkid: string;
    preferred_lang: Lang;
    g6pd_deficient?: boolean;
    weight_kg?: number | null;
    allergies?: string[];
    current_meds?: string[];
    consents: Record<string, boolean>;
    pics_version?: string;
  }) => request<{ ok?: boolean; patient: PatientProfile; access_token?: string }>("POST", "/api/v1/patient/onboard", { body: payload }),

  patientMe: () => request<PatientMeResponse>("GET", "/api/v1/patient/me"),

  patientIntake: (payload: {
    symptoms_text: string;
    temperature_c?: number | null;
    spo2_pct?: number | null;
    flu_rat_a?: string | null;
    flu_rat_b?: string | null;
    covid_rat?: string | null;
    duration_days?: number | null;
    lang?: Lang;
  }) => request<IntakeResponse>("POST", "/api/v1/patient/intake", { body: payload }),

  patientSlots: () =>
    request<{ slots?: TeleconsultSlot[] } | TeleconsultSlot[]>("GET", "/api/v1/patient/slots").then((r) =>
      Array.isArray(r) ? r : (r.slots ?? [])
    ),

  patientBookTeleconsult: (payload: { encounter_id?: string; practitioner_id: string; slot_at: string }) =>
    request<{ ok?: boolean; encounter_id: string; scheduled_at: string; status: string }>(
      "POST",
      "/api/v1/patient/book-teleconsult",
      { body: payload }
    ),

  patientPrescriptionFulfilment: (
    rxId: string,
    payload: { mode: "delivery" | "pickup"; address?: string; pharmacy?: string }
  ) =>
    request<{ ok?: boolean; prescription: PrescriptionRecord }>(
      "POST",
      `/api/v1/patient/prescriptions/${encodeURIComponent(rxId)}/fulfilment`,
      { body: { fulfilment_mode: payload.mode, mode: payload.mode, address: payload.address, pharmacy: payload.pharmacy } }
    ),

  patientFluVaxBook: (payload: { cvx_code: string; partner_clinic: string; slot_at: string }) =>
    request<{ ok?: boolean; immunization: ImmunizationRecord }>("POST", "/api/v1/patient/flu-vax/book", {
      body: payload,
    }),

  patientFollowup48h: (payload: {
    encounter_id?: string;
    status: "improving" | "same" | "worse";
    fever_resolved: boolean;
    new_red_flags?: string;
    notes?: string;
  }) =>
    request<{ ok?: boolean; escalated?: boolean; proposal_id?: string; message?: string }>(
      "POST",
      "/api/v1/patient/followup-48h",
      { body: payload }
    ),

  patientSendMessage: (payload: { body_text: string; encounter_id?: string; request_human?: boolean }) =>
    request<{ ok?: boolean; message: SecureMessage }>("POST", "/api/v1/patient/messages", { body: payload }),

  patientCertDownloadToken: (certId: string) =>
    request<{ download_url?: string; url?: string; token: string; expires_in?: number }>(
      "POST",
      `/api/v1/patient/certificates/${encodeURIComponent(certId)}/download-token`,
      { body: {} }
    ),

  patientExportFhir: () => request<Record<string, unknown>>("GET", "/api/v1/patient/export"),

  patientDataCorrection: (payload: { field: string; requested_value: string; reason: string }) =>
    request<{ ok?: boolean; request_id: string; status: string }>("POST", "/api/v1/patient/data-correction", {
      body: payload,
    }),

  patientWithdrawConsent: (payload: { scope: string; reason?: string }) =>
    request<{ ok?: boolean; consent?: ConsentRecord; message?: string }>("POST", "/api/v1/patient/withdraw-consent", {
      body: payload,
    }),

  patientDeleteData: (payload: { reason?: string }) =>
    request<{ ok?: boolean; status: string; retention_until?: string; notice: string }>(
      "POST",
      "/api/v1/patient/delete-data",
      { body: payload }
    ),

  // Surface 3: Clinician Console (Stories 11–15)
  clinicianQueue: (status?: string) =>
    request<{ queue?: QueueProposalSummary[]; items?: QueueProposalSummary[]; proposals?: QueueProposalSummary[] } | QueueProposalSummary[]>(
      "GET",
      "/api/v1/clinician/queue",
      { params: { status } }
    ).then((r) => (Array.isArray(r) ? r : (r.queue ?? r.proposals ?? r.items ?? []))),

  clinicianProposal: (id: string) =>
    request<ProposalDetail>("GET", `/api/v1/clinician/proposals/${encodeURIComponent(id)}`),

  clinicianRxSafetyCheck: (payload: { patient_id: string; items: PrescriptionItem[]; diagnosis_icd10?: string }) =>
    request<RxSafetyCheckResponse>("POST", "/api/v1/clinician/rx-safety-check", { body: payload }),

  clinicianSendMessage: (proposalId: string, body_text: string) =>
    request<{ ok?: boolean; message: SecureMessage; doorbell_sent?: boolean }>(
      "POST",
      `/api/v1/clinician/proposals/${encodeURIComponent(proposalId)}/message`,
      { body: { body_text } }
    ),

  clinicianDecide: (
    id: string,
    payload: {
      decision: "approved" | "edited" | "rejected" | "escalated";
      clinical_note?: string;
      icd10_code?: string;
      icpc2_code?: string;
      issue_certificate?: boolean;
      cert_days_off?: number;
      cert_start_date?: string;
      cert_end_date?: string;
      rx_items?: PrescriptionItem[];
      override_reason?: string | null;
      escalation_reason?: string | null;
      review_seconds: number;
      rubber_stamp_confirmed: boolean;
      edit_diff?: Record<string, unknown>;
    }
  ) =>
    request<{
      ok?: boolean;
      decision_id?: string;
      status: string;
      certificate?: CertificateRecord | null;
      prescription?: PrescriptionRecord | null;
    }>("POST", `/api/v1/clinician/proposals/${encodeURIComponent(id)}/decide`, { body: payload }),

  clinicianAvailability: (slots: Array<{ day?: string; start?: string; end?: string; slot_at?: string; active?: boolean }>) =>
    request<{ ok?: boolean; availability: unknown[] }>("POST", "/api/v1/clinician/availability", {
      body: { slots, availability: slots },
    }),

  clinicianAudit: () =>
    request<{ events?: AuditEventItem[] } | AuditEventItem[]>("GET", "/api/v1/clinician/audit").then((r) =>
      Array.isArray(r) ? r : (r.events ?? [])
    ),

  // Surface 4: Clinic Ops Admin & Corporate Admin (Stories 16–19)
  adminOverview: () => request<AdminOverviewResponse>("GET", "/api/v1/admin/overview"),

  adminObservability: () => request<AdminObservabilityResponse>("GET", "/api/v1/admin/observability"),

  adminProcessOutbox: () =>
    request<{ ok?: boolean; batch: { processed: number; succeeded: number; retried: number; dlq: number }; counts: { pending: number; done: number; dlq: number } }>(
      "POST",
      "/api/v1/admin/outbox/process",
      { body: {} }
    ),

  adminDataRequests: () =>
    request<{ data_requests?: AdminDataRequestItem[] }>("GET", "/api/v1/admin/data-requests").then(
      (r) => r.data_requests ?? []
    ),

  adminResolveDataRequest: (reqId: string, payload: { action: "approved" | "rejected"; resolution_note: string }) =>
    request<{ ok?: boolean; data_request: AdminDataRequestItem }>(
      "POST",
      `/api/v1/admin/data-requests/${encodeURIComponent(reqId)}/resolve`,
      { body: payload }
    ),

  adminCreateCorporate: (payload: {
    name: string;
    spend_cap_hkd: number;
    billing_email: string;
    admin_email: string;
    admin_password?: string;
  }) => request<{ ok?: boolean; corporate: AdminOverviewResponse["corporates"][number] }>("POST", "/api/v1/admin/corporates", { body: payload }),

  adminCreateCampaign: (payload: {
    corporate_id: string;
    code: string;
    name: string;
    spend_cap_hkd: number;
    unit_fee_hkd: number;
  }) => request<{ ok?: boolean; campaign: AdminOverviewResponse["campaigns"][number] }>("POST", "/api/v1/admin/campaigns", { body: payload }),

  adminApproveProtocol: (id: string) =>
    request<{ ok?: boolean; protocol: ProtocolVersionItem }>(
      "POST",
      `/api/v1/admin/protocols/${encodeURIComponent(id)}/approve`,
      { body: {} }
    ),

  adminKillSwitch: (payload: { ai_enabled: boolean; reason?: string }) =>
    request<{ ok?: boolean; ai_enabled: boolean; message?: string }>("POST", "/api/v1/admin/kill-switch", {
      body: payload,
    }),

  adminInvoicing: () => request<InvoicingResponse>("GET", "/api/v1/admin/invoicing"),

  adminAudit: (limit?: number) =>
    request<{ events?: AuditEventItem[] } | AuditEventItem[]>("GET", "/api/v1/admin/audit", {
      params: { limit },
    }).then((r) => (Array.isArray(r) ? r : (r.events ?? []))),

  corporateDashboard: () => request<CorporateDashboardResponse>("GET", "/api/v1/corporate/dashboard"),

  corporateCreateCampaign: (payload: {
    code: string;
    name: string;
    spend_cap_hkd: number;
    unit_fee_hkd?: number;
  }) => request<{ ok?: boolean; campaign: CorporateDashboardResponse["campaigns"][number] }>("POST", "/api/v1/corporate/campaigns", { body: payload }),
};

/**
 * Helper to fetch a vault-backed PDF with Bearer auth and trigger browser download.
 */
export async function downloadVaultPdf(downloadUrl: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {};
  if (activeToken) {
    headers["Authorization"] = `Bearer ${activeToken}`;
  }
  const resp = await fetch(downloadUrl, { headers });
  if (!resp.ok) {
    throw new ApiError(resp.status, `Certificate download failed (${resp.status})`);
  }
  const blob = await resp.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 10000);
}
