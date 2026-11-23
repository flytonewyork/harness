/**
 * Clean typed API client for the 2-surface Minimalist Clinical OS (Patient /p & Staff /c).
 */

export interface UnifiedCheck {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "na" | "unknown";
  affects: string;
  detail: string;
  detail_zh?: string;
  requires_reason?: boolean;
  quick_choice?: { keep: string; reduce: string };
}

export interface ChecksSummary {
  passed: number;
  applicable?: number;
  total?: number;
  warn?: number;
  warnings?: number;
  fail?: number;
  failures?: number;
  unknown?: number;
  can_sign: boolean;
  sign_cta: string;
}

export interface QuoteAnchor {
  field?: string;
  quote: string;
  source?: string;
  record_id?: string;
  start_char?: number;
  end_char?: number;
  verified?: boolean;
}

export interface ConflictSide {
  key: "1" | "2";
  site: string;
  date: string;
  regimen: string;
  quote: string;
}

export interface DoseConflictItem {
  id: string;
  drug: string;
  resolved: boolean;
  chosen_side?: string | null;
  resolution_summary?: string;
  sides: ConflictSide[];
}

export interface StaffQueueItem {
  id: string;
  patient_id: string;
  patient_name_en: string;
  patient_name_zh: string;
  kind: string;
  title: string;
  story_tag?: string | null;
  nurse_ready?: boolean;
  nurse_prep_note?: string;
  urgency: number;
  status: string;
  sla_due_at: string;
  sla_overdue: boolean;
  red_flag: boolean;
  unresolved_conflicts: number;
  checks: UnifiedCheck[];
  checks_summary: ChecksSummary;
  patient?: {
    id: string; name_en: string; name_zh: string; dob: string; sex: string;
    hkid_masked: string; g6pd_deficient: boolean; allergies: string[];
    current_meds: string[]; egfr?: number | null;
  };
}

export interface ProposalDetail extends StaffQueueItem {
  encounter_id: string;
  protocol_id: string;
  protocol_version: string;
  kill_switch_active: boolean;
  viewer_role: "clinician" | "nurse";
  source_quotes: QuoteAnchor[];
  payload: Record<string, any>;
  encounter?: { id: string; status: string; chief_complaint_text: string; icd10_code?: string; icpc2_code?: string; clinical_note?: string };
  practitioner?: { id: string; name_en: string; name_zh: string; role: string; mchk_reg_no?: string | null; clinic_name: string };
  observations: Array<{ id: string; display: string; value_text: string; recorded_at: string }>;
  messages: Array<{ id: string; direction: string; sender_role: string; body_text: string; created_at: string }>;
  suggested_cert?: { days_off: number; start_date: string } | null;
  issued_certificates?: Array<{
    id: string;
    days_off: number;
    start_date: string;
    end_date: string;
    mchk_reg_no: string;
    verification_hash?: string;
    signed_at: string;
  }>;
  suggested_rx: Array<Record<string, any>>;
  runtime_toggles?: Record<string, any>;
}

export interface CrisisPayload {
  kind: "medical_999" | "mental_health_crisis";
  pager_acked: boolean;
  pager_acked_at?: string;
  paged_clinician?: string;
  hotlines?: Array<{ name_en: string; name_zh: string; tel: string; display: string }>;
}

export interface CompanionHome {
  clinic_name: string;
  doctor_name: string;
  doctor_mchk: string;
  patient: {
    id: string; name_en: string; name_zh: string; dob: string; sex: string;
    hkid_masked: string; phone_masked: string; preferred_lang: string;
    g6pd_deficient: boolean; allergies: string[]; current_meds: string[];
  };
  now: Record<string, any> | null;
  goal_line: { label_en: string; label_zh: string; current: string; source_quote: string } | null;
  protocol_nudges: Array<{ id: string; because_en: string; because_zh: string; action_en: string; action_zh: string; source_quote: string; source_doc: string }>;
  review_sla: { doctor_name: string; due_at: string; label_en: string; label_zh: string; status: string };
  timeline: Array<{ id: string; direction: string; voice: "doctor" | "patient" | "assistant"; sender_label: string; body: string; created_at: string }>;
  episodes: Array<{ id: string; title: string; encounter_type: string; status: string; date: string; summary: string; source_text: string }>;
  record: {
    allergies: string[];
    g6pd_deficient: boolean;
    current_meds: string[];
    observations: Array<{ id: string; display: string; value_text: string; recorded_at: string }>;
    certificates: Array<{ id: string; days_off: number; start_date: string; end_date: string; mchk_reg_no: string; diagnosis_icd10?: string; signed_at: string }>;
    prescriptions: Array<{ id: string; items: Array<Record<string, any>>; fulfilment_mode: string; fulfilment_status: string; fulfilment_ref?: string; mchk_reg_no: string; signed_at: string }>;
    consents: Array<{ id: string; purpose: string; granted: boolean; created_at: string }>;
  };
  crisis_hotlines: Array<{ name_en: string; name_zh: string; tel: string; display: string }>;
  runtime_toggles?: Record<string, any>;
}

const TOKEN_KEY = "tmrw_v2_session";

export interface StoredSession {
  token: string;
  role: "clinician" | "nurse" | "clinic_admin" | "patient" | "corporate_admin";
  userId: string;
  nameEn: string;
  nameZh?: string;
  mchkRegNo?: string | null;
}

export function loadSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: StoredSession | null): void {
  if (!s) localStorage.removeItem(TOKEN_KEY);
  else localStorage.setItem(TOKEN_KEY, JSON.stringify(s));
}

async function req<T>(path: string, opts: RequestInit = {}, token?: string): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...opts, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || `HTTP ${res.status}`);
  return data as T;
}

export const v2Api = {
  health: () => req<Record<string, any>>("/api/v1/health"),
  demoPersonas: () => req<Record<string, any>>("/api/v1/demo/personas"),
  demoLoginAs: (persona: string) => req<Record<string, any>>("/api/v1/demo/login-as", { method: "POST", body: JSON.stringify({ persona }) }),
  demoReset: () => req<Record<string, any>>("/api/v1/demo/reset", { method: "POST" }),
  staffQueue: (tok: string) => req<{ items: StaffQueueItem[]; kill_switch_active: boolean; viewer_role: "clinician" | "nurse" }>("/api/v1/clinician/queue", {}, tok),
  proposalDetail: (id: string, tok: string) => req<ProposalDetail>(`/api/v1/clinician/proposals/${id}`, {}, tok),
  nurseReady: (id: string, prepNote: string, tok: string) =>
    req<Record<string, any>>(`/api/v1/staff/proposals/${id}/ready-for-doctor`, { method: "POST", body: JSON.stringify({ prep_note: prepNote }) }, tok),
  resolveConflict: (id: string, conflictId: string, choice: "1" | "2" | "Q", tok: string) =>
    req<Record<string, any>>(`/api/v1/clinician/proposals/${id}/resolve-conflict`, { method: "POST", body: JSON.stringify({ conflict_id: conflictId, choice }) }, tok),
  signProposal: (id: string, body: Record<string, any>, tok: string) =>
    req<Record<string, any>>(`/api/v1/clinician/proposals/${id}/decide`, { method: "POST", body: JSON.stringify(body) }, tok),
  undoDispatch: (id: string, tok: string) =>
    req<Record<string, any>>(`/api/v1/clinician/proposals/${id}/undo`, { method: "POST" }, tok),
  companionHome: (tok: string) => req<CompanionHome>("/api/v1/patient/companion", {}, tok),
  companionTurn: (message: string, lang: string, tok: string) =>
    req<{ ok: boolean; crisis: CrisisPayload | null; message?: Record<string, any> }>("/api/v1/patient/companion/turn", { method: "POST", body: JSON.stringify({ message, lang }) }, tok),
  companionReading: (body: Record<string, any>, tok: string) =>
    req<{ ok: boolean; crisis: CrisisPayload | null; observation: Record<string, any>; review_sla: Record<string, any> }>("/api/v1/patient/companion/reading", { method: "POST", body: JSON.stringify(body) }, tok),
  chooseFulfilment: (rxId: string, mode: "delivery" | "pickup", address: string, tok: string) => {
    const dest = address.trim() || (mode === "pickup" ? "Central & Mid-Levels Clinic Reception" : "Central, Hong Kong");
    return req<Record<string, any>>(
      `/api/v1/patient/prescriptions/${rxId}/fulfilment`,
      { method: "POST", body: JSON.stringify({ mode, address: dest, address_or_branch: dest }) },
      tok,
    );
  },
  certDownloadToken: (certId: string, tok: string) =>
    req<{ download_url: string }>(`/api/v1/patient/certificates/${certId}/download-token`, { method: "POST" }, tok),
  downloadVaultPdf: async (downloadUrl: string, tok: string, filename = "HK_Medical_Certificate.pdf") => {
    const res = await fetch(downloadUrl, { headers: { Authorization: `Bearer ${tok}` } });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Vault download failed (HTTP ${res.status})`);
    }
    const blob = await res.blob();
    const objUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
    const popup = window.open(objUrl, "_blank", "noopener");
    if (!popup) {
      const a = document.createElement("a");
      a.href = objUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    }
    setTimeout(() => URL.revokeObjectURL(objUrl), 60_000);
  },
  adminOverview: (tok: string) => req<Record<string, any>>("/api/v1/admin/overview", {}, tok),
  adminKillSwitch: (aiEnabled: boolean, reason: string, tok: string) =>
    req<Record<string, any>>("/api/v1/admin/kill-switch", { method: "POST", body: JSON.stringify({ ai_enabled: aiEnabled, reason }) }, tok),
  adminDataRequests: (tok: string) => req<{ requests: Array<Record<string, any>> }>("/api/v1/admin/data-requests", {}, tok),
  adminResolveDataReq: (id: string, tok: string) =>
    req<Record<string, any>>(`/api/v1/admin/data-requests/${id}/resolve`, { method: "POST", body: JSON.stringify({ status: "completed", resolution_note: "Resolved via clinic settings" }) }, tok),
};
