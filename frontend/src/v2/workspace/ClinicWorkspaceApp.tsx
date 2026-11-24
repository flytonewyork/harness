import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import QRCode from "qrcode";
import { v2Api } from "../api";
import "./workspace.css";

type NavView = "my_work" | "clinic_team" | "playbook" | string;

export type OrderType = "rx" | "cert" | "lab" | "imaging" | "refer" | "goal" | "leaflet" | "nurse" | "book";
export type RxVerb = "start" | "update" | "stop" | "continue";
export type OrderUrgency = "routine" | "soon" | "urgent";
export type NurseProtocol =
  | "medication_adherence_check"
  | "vaccine_session"
  | "bp_inr_followup_call"
  | "screening_kit_dispatch"
  | "booking_followup_call"
  | "pre_visit_questionnaire";
export type NurseLanguage = "en" | "zh-HK" | "bilingual";

export interface PlanOrder {
  id: string;
  type: OrderType;
  issue: string;
  checked: boolean;
  // rx
  action?: RxVerb;
  drug?: string;
  dose_mg?: number;
  doseMg?: number;
  dose_mcg?: number;
  doseMcg?: number;
  freq?: string;
  days?: string;
  note?: string;
  // cert
  days_off?: number;
  daysOff?: number;
  start_date?: string;
  startDate?: string;
  end_date?: string;
  dates?: string;
  omit_employer_diagnosis?: boolean;
  omitDx?: boolean;
  diagnosis_icd10?: string;
  diagnosis_icpc2?: string;
  // lab
  test_name?: string;
  testName?: string;
  loinc_code?: string;
  loincCode?: string;
  lab?: string;
  fasting?: boolean;
  when?: string;
  // imaging
  modality?: string;
  region?: string;
  indication?: string;
  urgency?: OrderUrgency;
  priority?: string;
  // refer
  specialty?: string;
  clinical_question?: string;
  reason?: string;
  attach_labs?: boolean;
  attachLabs?: boolean;
  // goal
  metric?: string;
  target_sys?: number;
  targetSys?: number;
  target_dia?: number;
  targetDia?: number;
  target_value?: number;
  targetValue?: number;
  target_text?: string;
  unit?: string;
  leaflet_id?: string;
  leafletId?: string;
  title?: string;
  template?: string;
  // nurse
  protocol?: NurseProtocol;
  assignee?: string;
  task?: string;
  language?: NurseLanguage;
  due?: string;
  // book
  visit_type?: string;
  visitType?: string;
  duration_min?: number;
  durationMin?: number;
  cvx_code?: string;
  cvxCode?: string;
}

interface CdsFeedbackState {
  hardBlock: boolean;
  summary: string;
  items: Array<{ title: string; detail: string; severity: "hard" | "override" }>;
  canOverride: boolean;
}

interface CommitReceipt {
  traceId?: string;
  confirmedAt: string;
  certificates: Array<{
    id?: string;
    days_off?: number;
    start_date?: string;
    end_date?: string;
    omit_employer_diagnosis?: boolean;
    mchk_reg_no?: string;
    verification_hash?: string;
  }>;
  emrAddendumSummary?: string;
  emrAddendumCount?: number;
}

const RX_VERB_LABEL: Record<RxVerb, string> = {
  start: "Start",
  update: "Update",
  stop: "Stop",
  continue: "Continue",
};

const NURSE_PROTOCOLS: Array<{ value: NurseProtocol; label: string; defaultTask: string }> = [
  { value: "medication_adherence_check", label: "Medication adherence check", defaultTask: "Medication adherence & pillbox check" },
  { value: "vaccine_session", label: "Vaccine session", defaultTask: "Protocolised nurse vaccination session" },
  { value: "bp_inr_followup_call", label: "BP / INR follow-up call", defaultTask: "Home BP / INR follow-up check-in call" },
  { value: "screening_kit_dispatch", label: "Screening kit dispatch", defaultTask: "Prepare & dispatch HK screening kit" },
  { value: "booking_followup_call", label: "Booking follow-up call", defaultTask: "Booking follow-up call (confirm clinic slot)" },
  { value: "pre_visit_questionnaire", label: "Pre-visit questionnaire", defaultTask: "Send structured pre-visit symptom & red-flag questionnaire via Sam" },
];

const PATIENT_LEAFLETS: Array<{ id: string; label: string }> = [
  { id: "", label: "No leaflet attached" },
  { id: "dietary_iron_hk", label: "HK Dietary Iron & Oral Supplement Guide (EN/繁)" },
  { id: "low_sodium_bp_hk", label: "Home BP Monitoring & Low-Sodium Guide (EN/繁)" },
  { id: "heart_healthy_ldl_hk", label: "HK Heart-Healthy Lipid Lifestyle Sheet (EN/繁)" },
  { id: "t2dm_lifestyle_hk", label: "Type 2 Diabetes Glycaemic & Diet Sheet (EN/繁)" },
  { id: "uti_hydration_hk", label: "Acute UTI Hydration & Red-Flag Advice (EN/繁)" },
];

const FALLBACK_SLASH_CATALOG: Array<{ cmd: string; type: OrderType; label: string; desc: string }> = [
  { cmd: "/prescribe", type: "rx", label: "Prescription (HK Formulary)", desc: "Action verb, single drug, dose, freq, duration" },
  { cmd: "/cert", type: "cert", label: "Medical Certificate", desc: "Bilingual MCHK PDF · 1–7d, employer Dx privacy toggle" },
  { cmd: "/order", type: "lab", label: "Lab Investigation", desc: "Standardised lab test, partner lab, fasting flag, timing" },
  { cmd: "/imaging", type: "imaging", label: "Imaging Request", desc: "Modality, anatomical region, indication, urgency" },
  { cmd: "/refer", type: "refer", label: "Specialist Referral", desc: "Specialty, urgency, clinical question, attach labs" },
  { cmd: "/goal", type: "goal", label: "Chronic Treatment Goal", desc: "BP slider or HbA1c / LDL / TSH target / patient leaflet" },
  { cmd: "/nurse", type: "nurse", label: "Nurse Protocol", desc: "Closed 6-protocol clinic menu, assignee, language, due" },
  { cmd: "/book", type: "book", label: "Follow-Up / Preventive Slot", desc: "Visit type, timeframe, duration, optional note" },
];

const CJK_REGEX = /[\u4e00-\u9fff\u3400-\u4dbf]/;

function isCantoneseText(text: string | undefined | null): boolean {
  return Boolean(text && CJK_REGEX.test(text));
}

function clampDaysOff(v: unknown): number {
  const n = Number(v);
  if (!Number.isFinite(n)) return 2;
  return Math.max(1, Math.min(7, Math.round(n)));
}

function formatDateRange(startIso: string, daysOff: number): { endIso: string; label: string } {
  const safeDays = clampDaysOff(daysOff);
  const parsed = new Date(`${startIso}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) {
    return { endIso: startIso, label: `${safeDays}d from ${startIso}` };
  }
  const end = new Date(parsed.getTime() + (safeDays - 1) * 86_400_000);
  const endIso = end.toISOString().slice(0, 10);
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const sDay = parsed.getUTCDate();
  const sMon = months[parsed.getUTCMonth()];
  const sYr = parsed.getUTCFullYear();
  const eDay = end.getUTCDate();
  const eMon = months[end.getUTCMonth()];
  const eYr = end.getUTCFullYear();
  if (safeDays === 1) return { endIso, label: `${sDay} ${sMon} ${sYr}` };
  if (sMon === eMon && sYr === eYr) return { endIso, label: `${sDay}–${eDay} ${sMon} ${sYr}` };
  return { endIso, label: `${sDay} ${sMon}–${eDay} ${eMon} ${eYr}` };
}

function normalizeUrgency(raw: unknown): OrderUrgency {
  const v = String(raw || "routine").trim().toLowerCase();
  if (v === "urgent" || v === "stat") return "urgent";
  if (v === "soon" || v === "semi-urgent") return "soon";
  return "routine";
}

function titleCase(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

function inferNurseProtocol(rawProtocol: unknown, taskText?: string): NurseProtocol {
  const valid = new Set<NurseProtocol>(NURSE_PROTOCOLS.map((p) => p.value));
  const candidate = String(rawProtocol || "").trim() as NurseProtocol;
  if (valid.has(candidate)) return candidate;
  const low = (taskText || "").toLowerCase();
  if (low.includes("questionnaire") || low.includes("intake") || low.includes("pre-visit")) return "pre_visit_questionnaire";
  if (low.includes("vaccine") || low.includes("flu") || low.includes("pcv")) return "vaccine_session";
  if (low.includes("inr") || low.includes("bp") || low.includes("blood pressure")) return "bp_inr_followup_call";
  if (low.includes("fit") || low.includes("kit") || low.includes("screen")) return "screening_kit_dispatch";
  if (low.includes("book") || low.includes("slot") || low.includes("lipid")) return "booking_followup_call";
  return "medication_adherence_check";
}

/**
 * Bidirectional order normalizer (SEAM_CONTRACT.md §2):
 * Accepts either canonical snake_case OR legacy camelCase and populates both read aliases.
 */
export function normalizeOrder(raw: Record<string, any>, fallbackIssue = "Additional Orders"): PlanOrder {
  const type = (raw.type || "book") as OrderType;
  const base: PlanOrder = {
    id: String(raw.id || `ord_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`),
    type,
    issue: String(raw.issue || fallbackIssue),
    checked: raw.checked !== undefined ? Boolean(raw.checked) : true,
  };

  if (type === "rx") {
    const rawAction = String(raw.action || "start").toLowerCase();
    const action: RxVerb = (["start", "update", "stop", "continue"].includes(rawAction) ? rawAction : "start") as RxVerb;
    const doseMg = raw.dose_mg ?? raw.doseMg;
    const doseMcg = raw.dose_mcg ?? raw.doseMcg;
    return {
      ...base,
      action,
      drug: String(raw.drug || ""),
      ...(doseMg !== undefined ? { dose_mg: Number(doseMg), doseMg: Number(doseMg) } : {}),
      ...(doseMcg !== undefined ? { dose_mcg: Number(doseMcg), doseMcg: Number(doseMcg) } : {}),
      freq: String(raw.freq ?? raw.frequency ?? (action === "stop" ? "Discontinue" : "")),
      days: String(raw.days ?? (action === "stop" ? "—" : "5d")),
      note: raw.note ? String(raw.note) : undefined,
    };
  }

  if (type === "cert") {
    const daysOff = clampDaysOff(raw.days_off ?? raw.daysOff ?? 2);
    const startDate = String(raw.start_date ?? raw.startDate ?? "2026-11-23");
    const range = formatDateRange(startDate, daysOff);
    const omit = raw.omit_employer_diagnosis ?? raw.omitDx ?? raw.omit_dx;
    const omitBool = omit !== undefined ? Boolean(omit) : true;
    return {
      ...base,
      days_off: daysOff,
      daysOff,
      start_date: startDate,
      startDate,
      end_date: String(raw.end_date || range.endIso),
      dates: String(raw.dates || range.label),
      omit_employer_diagnosis: omitBool,
      omitDx: omitBool,
      ...(raw.diagnosis_icd10 ? { diagnosis_icd10: String(raw.diagnosis_icd10) } : {}),
      ...(raw.diagnosis_icpc2 ? { diagnosis_icpc2: String(raw.diagnosis_icpc2) } : {}),
    };
  }

  if (type === "lab") {
    const testName = String(raw.test_name ?? raw.testName ?? "");
    const loinc = raw.loinc_code ?? raw.loincCode;
    return {
      ...base,
      test_name: testName,
      testName,
      ...(loinc ? { loinc_code: String(loinc), loincCode: String(loinc) } : {}),
      lab: String(raw.lab || "KingMed"),
      fasting: Boolean(raw.fasting),
      when: String(raw.when || "Next available"),
    };
  }

  if (type === "imaging") {
    const urgency = normalizeUrgency(raw.urgency ?? raw.priority);
    return {
      ...base,
      modality: String(raw.modality || ""),
      region: String(raw.region || ""),
      indication: String(raw.indication || ""),
      urgency,
      priority: titleCase(urgency),
    };
  }

  if (type === "refer") {
    const urgency = normalizeUrgency(raw.urgency ?? raw.priority);
    const question = String(raw.clinical_question ?? raw.reason ?? "");
    const attachRaw = raw.attach_labs ?? raw.attachLabs;
    const attach = attachRaw !== undefined ? Boolean(attachRaw) : true;
    return {
      ...base,
      specialty: String(raw.specialty || ""),
      urgency,
      priority: titleCase(urgency),
      clinical_question: question,
      reason: question,
      attach_labs: attach,
      attachLabs: attach,
    };
  }

  if (type === "leaflet" || (type === "goal" && (raw.leaflet_id || raw.leafletId) && !raw.target_sys && !raw.targetSys)) {
    const leaflet = raw.leaflet_id ?? raw.leafletId ?? "clinic_care_sheet";
    return {
      ...base,
      type: "leaflet",
      leaflet_id: String(leaflet),
      leafletId: String(leaflet),
      title: String(raw.title || raw.metric || "Patient Information Leaflet"),
      template: String(raw.template || "Central Medical · Clinic Template"),
      target_text: raw.target_text ? String(raw.target_text) : "AI-personalised from clinic guidelines and sent to patient careline.",
    };
  }

  if (type === "goal") {
    const metric = String(raw.metric || "Blood Pressure");
    const isBp = metric.toLowerCase().includes("blood pressure") || metric.toLowerCase() === "bp";
    const tSys = raw.target_sys ?? raw.targetSys ?? (isBp ? 135 : undefined);
    const tDia = raw.target_dia ?? raw.targetDia ?? (isBp ? 85 : undefined);
    const tVal = raw.target_value ?? raw.targetValue;
    const leaflet = raw.leaflet_id ?? raw.leafletId;
    return {
      ...base,
      metric,
      ...(tSys !== undefined ? { target_sys: Number(tSys), targetSys: Number(tSys) } : {}),
      ...(tDia !== undefined ? { target_dia: Number(tDia), targetDia: Number(tDia) } : {}),
      ...(tVal !== undefined && tVal !== "" ? { target_value: Number(tVal), targetValue: Number(tVal) } : {}),
      target_text: raw.target_text ? String(raw.target_text) : undefined,
      unit: raw.unit ? String(raw.unit) : undefined,
      ...(leaflet ? { leaflet_id: String(leaflet), leafletId: String(leaflet) } : {}),
    };
  }

  if (type === "nurse") {
    const task = String(raw.task || "");
    const protocol = inferNurseProtocol(raw.protocol, task);
    const langRaw = String(raw.language || (task.toLowerCase().includes("cantonese") ? "zh-HK" : "bilingual"));
    const language: NurseLanguage = (["en", "zh-HK", "bilingual"].includes(langRaw) ? langRaw : "bilingual") as NurseLanguage;
    return {
      ...base,
      protocol,
      assignee: String(raw.assignee || "Nurse Rachel"),
      task: task || (NURSE_PROTOCOLS.find((p) => p.value === protocol)?.defaultTask ?? "Clinic nurse follow-up"),
      language,
      due: String(raw.due || "Today"),
    };
  }

  // book
  const visitType = String(raw.visit_type ?? raw.visitType ?? "Follow-up visit");
  const dur = raw.duration_min ?? raw.durationMin;
  const cvx = raw.cvx_code ?? raw.cvxCode;
  return {
    ...base,
    visit_type: visitType,
    visitType,
    when: String(raw.when || "Next available"),
    ...(dur !== undefined && dur !== "" ? { duration_min: Number(dur), durationMin: Number(dur) } : { duration_min: 15, durationMin: 15 }),
    ...(cvx ? { cvx_code: String(cvx), cvxCode: String(cvx) } : {}),
    note: raw.note ? String(raw.note) : undefined,
  };
}

function summarizeOrder(rawItem: PlanOrder): string {
  const item = normalizeOrder(rawItem);
  if (item.type === "leaflet") {
    return `Leaflet · ${item.title || item.leaflet_id}`;
  }
  if (item.type === "cert") {
    return `Sick Leave Cert · ${item.days_off || 2}d (${item.dates || "23–24 Nov"})`;
  }
  if (item.type === "rx") {
    const verb = RX_VERB_LABEL[item.action || "start"] || "Start";
    if (item.action === "stop") return `Stop ${item.drug}`;
    return `${verb} ${item.drug} (${item.freq}${item.days && item.days !== "—" ? `, ${item.days}` : ""})`;
  }
  if (item.type === "lab") {
    return `Order ${item.test_name} (${item.when}${item.fasting ? ", fasting" : ""})`;
  }
  if (item.type === "goal") {
    const isBp = (item.metric || "").toLowerCase().includes("blood pressure") || (item.metric || "").toLowerCase() === "bp";
    if (isBp && item.target_sys && item.target_dia) {
      return `BP Target < ${item.target_sys}/${item.target_dia} mmHg`;
    }
    if (item.target_value !== undefined) {
      return `${item.metric} Target < ${item.target_value}${item.unit ? ` ${item.unit}` : ""}`;
    }
    if (item.leaflet_id) {
      const lf = PATIENT_LEAFLETS.find((l) => l.id === item.leaflet_id);
      return `Leaflet · ${lf ? lf.label : item.leaflet_id}`;
    }
    return `Goal · ${item.metric}${item.target_text ? ` (${item.target_text})` : ""}`;
  }
  if (item.type === "imaging") {
    const region = item.region ? ` (${item.region})` : "";
    const urg = item.urgency && item.urgency !== "routine" ? ` · ${titleCase(item.urgency)}` : "";
    return `Imaging · ${item.modality}${region}${urg}`;
  }
  if (item.type === "refer") {
    const urg = item.urgency && item.urgency !== "routine" ? ` (${titleCase(item.urgency)})` : "";
    return `Refer · ${item.specialty}${urg}`;
  }
  if (item.type === "nurse") {
    const proto = NURSE_PROTOCOLS.find((p) => p.value === item.protocol)?.label;
    return `${item.assignee || "Nurse Rachel"} · ${item.task || proto}`;
  }
  return `Book · ${item.visit_type} (${item.when})`;
}

function defaultOrderForCmd(cmd: string, issue: string): PlanOrder {
  const id = `ord_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
  switch (cmd) {
    case "/prescribe":
      return normalizeOrder({
        id, type: "rx", action: "start", issue, checked: true,
        drug: "Paracetamol 500mg", freq: "QDS PRN", days: "5d", note: "G6PD-safe · Formulary verified",
      });
    case "/cert":
      return normalizeOrder({
        id, type: "cert", issue, checked: true,
        days_off: 2, start_date: "2026-11-23", omit_employer_diagnosis: true,
      });
    case "/order":
      return normalizeOrder({
        id, type: "lab", issue, checked: true,
        test_name: "Serum Ferritin + FBC", lab: "KingMed", fasting: false, when: "Feb 2027",
      });
    case "/imaging":
      return normalizeOrder({
        id, type: "imaging", issue, checked: true,
        modality: "CXR", region: "Chest PA", indication: "Clinical evaluation", urgency: "routine",
      });
    case "/refer":
      return normalizeOrder({
        id, type: "refer", issue, checked: true,
        specialty: "Cardiology", urgency: "routine", clinical_question: "Post-discharge AFib follow-up", attach_labs: true,
      });
    case "/goal":
      return normalizeOrder({
        id, type: "goal", issue, checked: true,
        metric: "Blood Pressure", target_sys: 135, target_dia: 85,
      });
    case "/nurse":
      return normalizeOrder({
        id, type: "nurse", issue, checked: true,
        protocol: "medication_adherence_check", assignee: "Nurse Rachel",
        language: "zh-HK", task: "Medication adherence & pillbox check", due: "Today",
      });
    default:
      return normalizeOrder({
        id, type: "book", issue, checked: true,
        visit_type: "10-min Nurse Preventive Visit", when: "Next available", duration_min: 10, note: "",
      });
  }
}

function modalityBadgeCode(modality: string): { isEcg: boolean; code: string } {
  const low = (modality || "").toLowerCase();
  if (low.includes("ecg") || low.includes("ekg")) return { isEcg: true, code: "ECG" };
  if (low.includes("cxr") || low.includes("x-ray") || low.includes("xray") || low.includes("radiograph")) return { isEcg: false, code: "CXR" };
  if (low.includes("echo")) return { isEcg: false, code: "ECHO" };
  if (low.includes("mammo")) return { isEcg: false, code: "MAMMO" };
  if (low.includes("ultra") || low.includes("usg") || low.includes("us ")) return { isEcg: false, code: "US" };
  if (low.includes("mri")) return { isEcg: false, code: "MRI" };
  if (low.includes("ct")) return { isEcg: false, code: "CT" };
  return { isEcg: false, code: (modality || "IMG").slice(0, 5).toUpperCase() };
}

/**
 * Dynamic N-point TrendSvg supporting all 8 HK GP pathways:
 * bp | inr | ferritin | hba1c | ldl | tsh (plus generic numeric fallback).
 */
function TrendSvg({ chart, goalSys, goalDia }: { chart: Record<string, any>; goalSys?: number; goalDia?: number }) {
  const rawPoints = Array.isArray(chart?.points) ? chart.points : [];
  if (!rawPoints.length) {
    return (
      <div className="ws-attachment">
        <strong style={{ fontSize: 12.5, color: "var(--ws-ink)" }}>{chart?.title || "Longitudinal Trend"}</strong>
        <div className="ws-empty-note">Insufficient longitudinal readings to plot trend.</div>
      </div>
    );
  }

  const kind = String(chart.kind || "generic").toLowerCase();
  const n = rawPoints.length;
  const xs = rawPoints.map((_: any, i: number) => (n === 1 ? 210 : 58 + (i * 304) / (n - 1)));
  const labelEvery = n > 6 ? Math.ceil(n / 5) : 1;

  if (kind === "bp") {
    const pts = rawPoints as Array<{ label: string; sys: number; dia: number }>;
    const tSys = goalSys || chart.target_sys || 135;
    const tDia = goalDia || chart.target_dia || 85;
    const allVals = pts.flatMap((p) => [p.sys, p.dia]).concat([tSys, tDia]);
    const minV = Math.min(60, Math.min(...allVals) - 10);
    const maxV = Math.max(165, Math.max(...allVals) + 12);
    const span = Math.max(20, maxV - minV);
    const mapY = (v: number) => Math.max(16, Math.min(96, 98 - ((v - minV) / span) * 80));
    const sysLine = pts.map((p, i) => `${xs[i]},${mapY(p.sys)}`).join(" ");
    const diaLine = pts.map((p, i) => `${xs[i]},${mapY(p.dia)}`).join(" ");
    const targetY = mapY(tSys);
    return (
      <div className="ws-attachment">
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, gap: 8 }}>
          <strong style={{ color: "var(--ws-ink)" }}>{chart.title || "Blood Pressure Trend"}</strong>
          <span className="ws-num" style={{ color: "var(--ws-jade)", fontWeight: 600 }}>Target &lt; {tSys}/{tDia} mmHg</span>
        </div>
        <svg viewBox="0 0 410 120" style={{ width: "100%", height: 112, display: "block" }}>
          <line x1="36" y1={targetY} x2="390" y2={targetY} stroke="#16695D" strokeDasharray="4 4" strokeWidth="1.4" />
          <text x="6" y={targetY + 4} fontSize="10" fontWeight="600" fill="#16695D">{tSys}</text>
          {n > 1 && <polyline fill="none" stroke="#1E252B" strokeWidth="2.2" points={sysLine} />}
          {n > 1 && <polyline fill="none" stroke="#767E87" strokeWidth="1.5" strokeDasharray="3 2" points={diaLine} />}
          {pts.map((p, i) => (
            <g key={i}>
              <circle cx={xs[i]} cy={mapY(p.sys)} r="4" fill={p.sys > 140 || p.dia > 90 ? "#B91C1C" : "#16695D"} />
              <circle cx={xs[i]} cy={mapY(p.dia)} r="3" fill="#767E87" />
              <text x={xs[i]} y={mapY(p.sys) - 8} textAnchor="middle" fontSize="10" fontWeight="600" fill={p.sys > 140 || p.dia > 90 ? "#B91C1C" : "#1E252B"}>
                {p.sys}/{p.dia}
              </text>
              {(i % labelEvery === 0 || i === n - 1) && (
                <text x={xs[i]} y="114" textAnchor="middle" fontSize="10" fill="#767E87">{p.label}</text>
              )}
            </g>
          ))}
        </svg>
      </div>
    );
  }

  // Single-series numeric pathways: inr | ferritin | hba1c | ldl | tsh | generic
  const pts = rawPoints.map((p: any) => ({
    label: String(p.label ?? ""),
    v: Number(p.v ?? p.value ?? 0),
  }));

  const presets: Record<string, {
    bandMin?: number;
    bandMax?: number;
    threshold?: number;
    direction?: "below_bad" | "above_bad" | "band";
    defaultLabel: string;
    decimals: number;
  }> = {
    inr: { bandMin: 2.0, bandMax: 3.0, direction: "band", defaultLabel: "Therapeutic 2.0–3.0", decimals: 1 },
    tsh: { bandMin: 0.4, bandMax: 4.0, direction: "band", defaultLabel: "Ref 0.4–4.0 mIU/L", decimals: 2 },
    ferritin: { threshold: 15, direction: "below_bad", defaultLabel: "BSG threshold ≥ 15 ng/mL", decimals: 0 },
    hba1c: { threshold: 7.0, direction: "above_bad", defaultLabel: "Target < 7.0%", decimals: 1 },
    ldl: { threshold: 3.4, direction: "above_bad", defaultLabel: "Target < 3.4 mmol/L", decimals: 1 },
  };
  const cfg = presets[kind] || {
    threshold: chart.target_value,
    bandMin: chart.target_min,
    bandMax: chart.target_max,
    direction: chart.target_min !== undefined && chart.target_max !== undefined ? "band" : "above_bad",
    defaultLabel: chart.target_label || "",
    decimals: 1,
  };

  const bandMin = chart.target_min !== undefined ? Number(chart.target_min) : cfg.bandMin;
  const bandMax = chart.target_max !== undefined ? Number(chart.target_max) : cfg.bandMax;
  const threshold = chart.target_value !== undefined ? Number(chart.target_value) : cfg.threshold;

  const domainRefs = pts.map((p) => p.v);
  if (bandMin !== undefined) domainRefs.push(bandMin);
  if (bandMax !== undefined) domainRefs.push(bandMax);
  if (threshold !== undefined) domainRefs.push(threshold);
  const rawMin = Math.min(...domainRefs);
  const rawMax = Math.max(...domainRefs);
  const pad = Math.max((rawMax - rawMin) * 0.25, kind === "inr" || kind === "tsh" ? 0.5 : 1.0);
  const minV = Math.max(0, rawMin - pad);
  const maxV = rawMax + pad;
  const span = Math.max(0.5, maxV - minV);
  const mapY = (v: number) => Math.max(16, Math.min(82, 86 - ((v - minV) / span) * 68));

  const isAbnormalPoint = (v: number): boolean => {
    if (bandMin !== undefined && bandMax !== undefined) return v < bandMin || v > bandMax;
    if (threshold !== undefined) {
      return cfg.direction === "below_bad" ? v < threshold : v > threshold;
    }
    return false;
  };

  const formatNum = (v: number) => {
    if (Number.isInteger(v) && cfg.decimals === 0) return String(v);
    return Number(v.toFixed(cfg.decimals)).toString();
  };

  return (
    <div className="ws-attachment">
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5, gap: 8 }}>
        <strong style={{ color: "var(--ws-ink)" }}>{chart.title || `${kind.toUpperCase()} Trend`}</strong>
        <span className="ws-num" style={{ color: "var(--ws-ink-3)" }}>{chart.target_label || cfg.defaultLabel}</span>
      </div>
      <svg viewBox="0 0 410 102" style={{ width: "100%", height: 96, display: "block" }}>
        {bandMin !== undefined && bandMax !== undefined && (
          <>
            <rect
              x="38"
              y={mapY(bandMax)}
              width="354"
              height={Math.max(4, mapY(bandMin) - mapY(bandMax))}
              fill="#EBF5F2"
              opacity="0.88"
            />
            <line x1="38" y1={mapY(bandMax)} x2="392" y2={mapY(bandMax)} stroke="#16695D" strokeDasharray="3 3" strokeWidth="1" />
            <line x1="38" y1={mapY(bandMin)} x2="392" y2={mapY(bandMin)} stroke="#16695D" strokeDasharray="3 3" strokeWidth="1" />
            <text x="5" y={mapY(bandMax) + 4} fontSize="10" fill="#16695D">{formatNum(bandMax)}</text>
            <text x="5" y={mapY(bandMin) + 4} fontSize="10" fill="#16695D">{formatNum(bandMin)}</text>
          </>
        )}
        {threshold !== undefined && bandMin === undefined && (
          <>
            <line
              x1="38"
              y1={mapY(threshold)}
              x2="392"
              y2={mapY(threshold)}
              stroke={cfg.direction === "below_bad" ? "#B45309" : "#16695D"}
              strokeDasharray="4 4"
              strokeWidth="1.25"
            />
            <text x="5" y={mapY(threshold) + 4} fontSize="10" fill={cfg.direction === "below_bad" ? "#B45309" : "#16695D"}>
              {formatNum(threshold)}
            </text>
          </>
        )}
        {n > 1 && (
          <polyline
            fill="none"
            stroke="#1E252B"
            strokeWidth="2.2"
            points={pts.map((p, i) => `${xs[i]},${mapY(p.v)}`).join(" ")}
          />
        )}
        {pts.map((p, i) => {
          const abnormal = isAbnormalPoint(p.v);
          const dotColor = abnormal ? (kind === "hba1c" && p.v >= 8.5 ? "#B91C1C" : "#B45309") : "#16695D";
          return (
            <g key={i}>
              <circle cx={xs[i]} cy={mapY(p.v)} r="4" fill={dotColor} />
              <text x={xs[i]} y={mapY(p.v) - 8} textAnchor="middle" fontSize="10" fontWeight="600" fill={abnormal ? dotColor : "#1E252B"}>
                {formatNum(p.v)}
              </text>
              {(i % labelEvery === 0 || i === n - 1) && (
                <text x={xs[i]} y="97" textAnchor="middle" fontSize="10" fill="#767E87">{p.label}</text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function ClinicWorkspaceApp() {
  const workspaceMode: "live" | "seeded" = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("mode") === "seeded" ? "seeded" : "live";
  }, []);

  const [data, setData] = useState<Record<string, any> | null>(null);
  const [view, setView] = useState<NavView>("my_work");
  const [role, setRole] = useState<"locum" | "nurse">("locum");
  const [showXray, setShowXray] = useState(false);
  const [qrModalOpen, setQrModalOpen] = useState(false);
  const [publicBaseUrl, setPublicBaseUrl] = useState<string>(() => window.location.origin);
  const [editingHost, setEditingHost] = useState(false);
  const [qrDataUrl, setQrDataUrl] = useState<string>("");
  const [copiedQr, setCopiedQr] = useState(false);
  // Management Plan starts EMPTY by default per Clinical UI Design System (builds idempotently from clicked + pills)
  const [plans, setPlans] = useState<Record<string, PlanOrder[]>>({});
  const [goalOverrides, setGoalOverrides] = useState<Record<string, { sys: number; dia: number }>>({});
  const [slashInput, setSlashInput] = useState("");
  const [slashIdx, setSlashIdx] = useState(0);
  const [expandedImaging, setExpandedImaging] = useState<Record<string, boolean>>({});
  const [expandedClips, setExpandedClips] = useState<Record<string, boolean>>({});
  const [samReadMap, setSamReadMap] = useState<Record<string, boolean>>({});
  const [samInput, setSamInput] = useState("");
  const [playbookQ, setPlaybookQ] = useState("How do we handle QEH Warfarin discharges?");
  const [playbookAns, setPlaybookAns] = useState<Record<string, any> | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [cdsFeedback, setCdsFeedback] = useState<Record<string, CdsFeedbackState | null>>({});
  const [overrideReasonMap, setOverrideReasonMap] = useState<Record<string, string>>({});
  const [receipts, setReceipts] = useState<Record<string, CommitReceipt | null>>({});
  const [aiFollowupMap, setAiFollowupMap] = useState<Record<string, boolean>>({});
  const [showPatientChatMap, setShowPatientChatMap] = useState<Record<string, boolean>>({});
  const [seenTodayOpen, setSeenTodayOpen] = useState(false);
  const [postSignGuide, setPostSignGuide] = useState<{
    caseId: string;
    patientName: string;
    traceId?: string;
    confirmedAt: string;
  } | null>(null);
  const [sseConnected, setSseConnected] = useState(false);
  const [busy, setBusy] = useState(false);

  const sseRefreshTimer = useRef<number | null>(null);
  const sessionEpochRef = useRef<string | null>(null);

  const phoneQrUrl = useMemo(() => {
    const cleanBase = publicBaseUrl.trim().replace(/\/+$/, "") || window.location.origin;
    return `${cleanBase}/p/chat`;
  }, [publicBaseUrl]);

  useEffect(() => {
    QRCode.toDataURL(phoneQrUrl, {
      width: 240,
      margin: 1,
      color: { dark: "#162026", light: "#FFFFFF" },
    })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(""));
  }, [phoneQrUrl]);

  const clearTransientUiState = useCallback(() => {
    setPlans({});
    setGoalOverrides({});
    setSamReadMap({});
    setCdsFeedback({});
    setOverrideReasonMap({});
    setReceipts({});
    setBanner(null);
    setPostSignGuide(null);
  }, []);

  const refresh = useCallback(async () => {
    const ov = await v2Api.workspaceOverview(workspaceMode);
    const nextEpoch = ov?.live_session?.session_epoch || null;
    if (nextEpoch && sessionEpochRef.current && nextEpoch !== sessionEpochRef.current) {
      clearTransientUiState();
      setView("my_work");
    }
    if (nextEpoch) {
      sessionEpochRef.current = nextEpoch;
    }
    setData(ov);
  }, [workspaceMode, clearTransientUiState]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Live SSE EventSource + 2s poll fallback (reliable over Cloudflare quick tunnels & LAN)
  useEffect(() => {
    let closed = false;
    let es: EventSource | null = null;
    if (typeof EventSource !== "undefined") {
      try {
        es = new EventSource("/api/v1/live/stream");
        const scheduleRefresh = () => {
          if (sseRefreshTimer.current !== null) return;
          sseRefreshTimer.current = window.setTimeout(() => {
            sseRefreshTimer.current = null;
            if (!closed) refresh().catch(() => {});
          }, 220);
        };
        es.onopen = () => setSseConnected(true);
        es.onerror = () => {
          setSseConnected(false);
        };
        es.onmessage = () => scheduleRefresh();
        const events = [
          "phone.connected",
          "agent.step",
          "tool.call",
          "workspace.committed",
          "workspace.undone",
          "emr.mutated",
          "document.ingested",
          "document.resolved",
          "case.updated",
          "booking.confirmed",
          "patient.care_plan_delivered",
          "patient.care_plan_retracted",
        ];
        events.forEach((ev) => es?.addEventListener(ev, scheduleRefresh));
        es.addEventListener("demo.reset", () => {
          clearTransientUiState();
          setView("my_work");
          scheduleRefresh();
        });
      } catch {
        setSseConnected(false);
      }
    }
    const poll = window.setInterval(() => {
      if (!closed) refresh().catch(() => {});
    }, 2000);
    return () => {
      closed = true;
      window.clearInterval(poll);
      if (sseRefreshTimer.current !== null) {
        window.clearTimeout(sseRefreshTimer.current);
        sseRefreshTimer.current = null;
      }
      es?.close();
    };
  }, [refresh, clearTransientUiState]);

  const cases: Array<Record<string, any>> = data?.cases || [];
  const activeCase = cases.find((c) => c.id === view) || null;
  const graph = data?.practice_graph;
  const orderCatalog: Array<Record<string, any>> =
    graph?.order_catalog?.length ? graph.order_catalog : FALLBACK_SLASH_CATALOG;
  const aiName = data?.careline_profile?.name || "Sam";
  const liveSession = data?.live_session || null;
  const recentToolCalls: Array<Record<string, any>> = liveSession?.recent_tool_calls || [];

  // If the currently viewed case disappeared (e.g., global reset triggered from phone), return cleanly to #my-work
  useEffect(() => {
    if (view !== "my_work" && view !== "clinic_team" && view !== "playbook" && data && !activeCase) {
      setView("my_work");
    }
  }, [view, data, activeCase]);

  // Clear postSignGuide if its case no longer exists (e.g., after Reset Demo)
  useEffect(() => {
    if (postSignGuide && data && !cases.some((c) => c.id === postSignGuide.caseId)) {
      setPostSignGuide(null);
    }
  }, [postSignGuide, data, cases]);

  // Auto-dismiss the compact "Plan signed" banner shortly after returning to #my-work
  useEffect(() => {
    if (!postSignGuide || view !== "my_work") return;
    const timer = window.setTimeout(() => {
      setPostSignGuide(null);
    }, 4500);
    return () => window.clearTimeout(timer);
  }, [postSignGuide, view]);

  const handleResetDemo = async () => {
    setBusy(true);
    try {
      if (workspaceMode === "live") {
        await v2Api.liveDemoReset();
      } else {
        await v2Api.workspaceReset();
      }
      clearTransientUiState();
      setView("my_work");
      setQrModalOpen(false);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const triggerAuthorityProbe = async () => {
    setBusy(true);
    try {
      const targetPid = activeCase?.patient_id || "pat_lam_urti_demo";
      await v2Api.emrSimulateAuthorityProbe(targetPid);
      setBanner("Asymmetric Authority guardrail verified: AI attempt to call emr.write_signed_orders was BLOCKED.");
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const copyQrUrl = async () => {
    try {
      await navigator.clipboard.writeText(phoneQrUrl);
      setCopiedQr(true);
      window.setTimeout(() => setCopiedQr(false), 1800);
    } catch {
      // clipboard unavailable
    }
  };

  const openCase = (caseId: string) => {
    setView(caseId);
    setSlashInput("");
    setSlashIdx(0);
    setBanner(null);
  };

  const updateCasePlan = (caseId: string, updater: (cur: PlanOrder[]) => PlanOrder[]) => {
    setPlans((prev) => ({ ...prev, [caseId]: updater(prev[caseId] || []) }));
  };

  const patchOrderAt = (caseId: string, idx: number, patch: Partial<PlanOrder>) => {
    updateCasePlan(caseId, (cur) =>
      cur.map((x, i) => (i === idx ? normalizeOrder({ ...x, ...patch, checked: true }, x.issue) : x)),
    );
    setCdsFeedback((prev) => (prev[caseId] ? { ...prev, [caseId]: null } : prev));
  };

  const toggleOrderInPlan = (caseId: string, rawOrd: Record<string, any>) => {
    const ord = normalizeOrder(rawOrd);
    updateCasePlan(caseId, (cur) => {
      const exists = cur.some((x) => x.id === ord.id);
      if (exists) return cur.filter((x) => x.id !== ord.id);
      return [...cur, { ...ord, checked: true }];
    });
    setCdsFeedback((prev) => (prev[caseId] ? { ...prev, [caseId]: null } : prev));
  };

  const applySlashCmd = (caseId: string, cmd: string) => {
    const issueName = activeCase?.summary?.issues?.[0]?.name || "Additional Orders";
    updateCasePlan(caseId, (cur) => [...cur, defaultOrderForCmd(cmd, issueName)]);
    setSlashInput("");
    setSlashIdx(0);
  };

  const parse422Feedback = (errBody: Record<string, any>): CdsFeedbackState => {
    const detail = errBody?.detail;
    const detailObj = typeof detail === "object" && detail !== null ? detail : errBody;
    const report = detailObj?.rx_safety_report || errBody?.rx_safety_report || {};
    const items: Array<{ title: string; detail: string; severity: "hard" | "override" }> = [];

    const hardBlocks = detailObj?.hard_blocks || report?.hard_blocks || detailObj?.blocks || [];
    const conflicts = detailObj?.overridable_conflicts || report?.overridable_conflicts || detailObj?.warnings || [];

    if (Array.isArray(hardBlocks)) {
      hardBlocks.forEach((b: any) => {
        items.push({
          title: b.rule || b.drug || b.code || "Clinical safety hard block",
          detail: b.message || b.detail || b.reason || String(b),
          severity: "hard",
        });
      });
    }
    if (Array.isArray(conflicts)) {
      conflicts.forEach((w: any) => {
        items.push({
          title: w.rule || w.drug || w.code || "Override rationale required",
          detail: w.message || w.detail || w.reason || String(w),
          severity: "override",
        });
      });
    }

    const hasHard =
      Boolean(detailObj?.hard_block) ||
      items.some((x) => x.severity === "hard") ||
      (Array.isArray(report?.checks) && report.checks.some((c: any) => c.status === "fail" && !c.overridable));
    const overrideRequired =
      !hasHard &&
      (Boolean(detailObj?.override_required) ||
        Boolean(report?.override_required) ||
        items.some((x) => x.severity === "override") ||
        String(typeof detail === "string" ? detail : detailObj?.message || "").toLowerCase().includes("override"));

    const summary =
      (typeof detail === "string" ? detail : detailObj?.message || detailObj?.error || errBody?.message) ||
      (hasHard
        ? "Prescription blocked by deterministic clinical safety rules (G6PD / Allergy / Renal floor)."
        : "Clinical safety check requires a documented override rationale before signing.");

    return {
      hardBlock: hasHard,
      summary,
      items,
      canOverride: overrideRequired && !hasHard,
    };
  };

  const signPlan = async (caseId: string, overrideReasonParam?: string) => {
    const selected = (plans[caseId] || []).filter((x) => x.checked).map((x) => normalizeOrder(x));
    if (!selected.length) return;
    const compiledText = selected.map(summarizeOrder).join("; ");
    const actorName = role === "locum" ? "Locum Dr. Sarah Wong" : "Nurse Rachel Chan (RN)";
    const payload = {
      instruction_text: compiledText,
      orders: selected,
      structured_orders: selected,
      actor_name: actorName,
      mchk_reg_no: role === "locum" ? "M16204" : undefined,
      override_reason: overrideReasonParam?.trim() || undefined,
    };

    setBusy(true);
    setCdsFeedback((prev) => ({ ...prev, [caseId]: null }));
    try {
      let res = await fetch(`/api/v1/workspace/cases/${encodeURIComponent(caseId)}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.status === 404) {
        res = await fetch(`/api/v1/workspace/cases/${encodeURIComponent(caseId)}/commit`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      const body = await res.json().catch(() => ({}));
      if (res.status === 422) {
        setCdsFeedback((prev) => ({ ...prev, [caseId]: parse422Feedback(body) }));
        return;
      }
      if (!res.ok) {
        throw new Error(typeof body?.detail === "string" ? body.detail : body?.message || `HTTP ${res.status}`);
      }

      // Extract MCHK certificates & additive EMR addendum confirmation defensively
      const rawCerts: any[] = [];
      if (Array.isArray(body.issued_certificates)) rawCerts.push(...body.issued_certificates);
      else if (Array.isArray(body.certificates)) rawCerts.push(...body.certificates);
      else if (body.certificate) rawCerts.push(body.certificate);
      else if (Array.isArray(body.trace?.issued_certificates)) rawCerts.push(...body.trace.issued_certificates);

      const addendumObj = body.emr_addendum || body.trace?.emr_addendum || body.trace?.emr_addendum_json;
      const addendumEntries = Array.isArray(addendumObj?.entries) ? addendumObj.entries : [];
      const emrSummary =
        addendumObj?.summary ||
        (addendumEntries.length ? `${addendumEntries.length} additive EMR addendum ${addendumEntries.length === 1 ? "entry" : "entries"} recorded` : undefined) ||
        (body.emr_written ? "Additive EMR addendum recorded (Sole SoT)" : undefined);

      const signedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      const traceId = body.trace?.id || body.trace_id;
      const signedCaseObj = (data?.cases || []).find((c: any) => c.id === caseId);
      const signedPatientName = signedCaseObj?.patient_name || "Patient";

      if (rawCerts.length > 0 || emrSummary) {
        setReceipts((prev) => ({
          ...prev,
          [caseId]: {
            traceId,
            confirmedAt: signedAt,
            certificates: rawCerts,
            emrAddendumSummary: emrSummary,
            emrAddendumCount: addendumEntries.length || (emrSummary ? 1 : 0),
          },
        }));
      }

      updateCasePlan(caseId, (cur) => cur.filter((x) => !x.checked));
      setOverrideReasonMap((prev) => ({ ...prev, [caseId]: "" }));
      setPostSignGuide({
        caseId,
        patientName: signedPatientName,
        traceId,
        confirmedAt: signedAt,
      });
      setView("my_work");
      await refresh();
    } catch (e: any) {
      setBanner(e?.message || "Failed to confirm plan");
    } finally {
      setBusy(false);
    }
  };

  const sendSamMessage = async (caseId: string) => {
    const txt = samInput.trim();
    if (!txt) return;
    setBusy(true);
    setSamReadMap((m) => ({ ...m, [caseId]: true }));
    try {
      await v2Api.workspaceAsk(caseId, txt, role === "locum" ? "Locum Dr. Sarah Wong" : "Nurse Rachel Chan (RN)");
      const low = txt.toLowerCase();
      if (low.includes("3 day") || low.includes("3d")) {
        updateCasePlan(caseId, (cur) => {
          const hasCert = cur.some((x) => x.type === "cert");
          if (hasCert) {
            return cur.map((x) =>
              x.type === "cert"
                ? normalizeOrder({ ...x, days_off: 3, start_date: x.start_date || "2026-11-23", dates: "23–25 Nov 2026", checked: true })
                : x,
            );
          }
          return [
            ...cur,
            normalizeOrder({
              id: `cert_${Date.now()}`, type: "cert", issue: "1. Acute viral URTI", checked: true,
              days_off: 3, start_date: "2026-11-23", dates: "23–25 Nov 2026", omit_employer_diagnosis: true,
            }),
          ];
        });
      } else if (low.includes("tsh") || low.includes("thyroid")) {
        const issueName = activeCase?.summary?.issues?.[0]?.name || "Additional Orders";
        updateCasePlan(caseId, (cur) => [
          ...cur,
          normalizeOrder({
            id: `ord_${Date.now()}`, type: "lab", issue: issueName, checked: true,
            test_name: "Serum TSH + Free T4", lab: "KingMed", fasting: false, when: "With next blood draw",
          }),
        ]);
      } else if (low.includes("130")) {
        setGoalOverrides((g) => ({ ...g, [caseId]: { sys: 130, dia: 80 } }));
        updateCasePlan(caseId, (cur) => {
          const hasGoal = cur.some((x) => x.type === "goal");
          if (hasGoal) {
            return cur.map((x) => (x.type === "goal" ? normalizeOrder({ ...x, target_sys: 130, target_dia: 80, checked: true }) : x));
          }
          return [
            ...cur,
            normalizeOrder({
              id: `goal_${Date.now()}`, type: "goal", issue: "1. Essential hypertension — on target",
              checked: true, metric: "Blood Pressure", target_sys: 130, target_dia: 80,
            }),
          ];
        });
      }
      setSamInput("");
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const runUndo = async (caseId: string, traceId: string) => {
    setBusy(true);
    try {
      await v2Api.workspaceUndo(caseId, traceId);
      setReceipts((prev) => ({ ...prev, [caseId]: null }));
      setBanner("Plan undone inside 10-second window.");
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const runTick = async (caseId: string, eventKind: string) => {
    setBusy(true);
    try {
      await v2Api.workspaceTick(caseId, eventKind);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const resolveQuarantinedDoc = async (docId: string, action: "approve" | "reject") => {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/workspace/documents/${encodeURIComponent(docId)}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          decision: action,
          resolved_by: "Nurse Rachel Chan (RN)",
          note: action === "approve" ? "3-point identity verified manually by Nurse Rachel." : "Identity mismatch confirmed; document held from patient chart.",
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(typeof body?.detail === "string" ? body.detail : body?.message || `HTTP ${res.status}`);
      }
      setBanner(
        action === "approve"
          ? "Quarantined document verified and released by Nurse Rachel."
          : "Quarantined document rejected and held from patient chart.",
      );
      await refresh();
    } catch (e: any) {
      setBanner(e?.message || "Unable to resolve quarantined document.");
    } finally {
      setBusy(false);
    }
  };

  const runPlaybookQuery = async (q: string) => {
    setPlaybookQ(q);
    setPlaybookAns(await v2Api.graphQuery(q));
  };

  const runCuratorDecision = async (proposalId: string, decision: "approve" | "dismiss") => {
    setBusy(true);
    try {
      await v2Api.curatorDecide(proposalId, decision);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const triggerMismatchProbe = async () => {
    setBusy(true);
    try {
      const res = await v2Api.workspaceIngestDoc({
        external_doc_ref: "QEH-DC-MISMATCH-2026",
        doc_kind: "discharge_summary",
        source_org: "Queen Elizabeth Hospital",
        claimed_mrn_or_hkid: "PAT-62041",
        claimed_name: "CHAN Tai Man",
        claimed_dob: "1964-03-19",
        verbatim_quote: "Discharge Meds: Warfarin 3mg daily",
        page_ref: "p.2",
      });
      setBanner(`3-Point ID check (${res.checks.points_matched}/3): held for Nurse Rachel in #clinic-team.`);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return <div className="ws-shell"><div className="ws-content">Loading clinic workspace…</div></div>;
  }

  const activeOrders = activeCase ? (plans[activeCase.id] || []).map((o) => normalizeOrder(o)) : [];
  const selectedCount = activeOrders.filter((x) => x.checked).length;
  const groupedPlan: Record<string, Array<{ item: PlanOrder; idx: number }>> = {};
  activeOrders.forEach((item, idx) => {
    const grp = item.issue || "Additional Orders";
    if (!groupedPlan[grp]) groupedPlan[grp] = [];
    groupedPlan[grp].push({ item, idx });
  });

  const slashClean = slashInput.trim().toLowerCase();
  const showSlashMenu = slashClean.startsWith("/");
  const filteredSlash = showSlashMenu
    ? orderCatalog.filter((s) => s.cmd.toLowerCase().includes(slashClean) || s.label.toLowerCase().includes(slashClean.slice(1)))
    : [];
  const visibleSlash = showSlashMenu ? (filteredSlash.length ? filteredSlash : orderCatalog) : [];
  const activeSlashIdx = visibleSlash.length ? ((slashIdx % visibleSlash.length) + visibleSlash.length) % visibleSlash.length : 0;

  // Derived metadata for active case
  const activeAlerts: Array<Record<string, any>> = activeCase?.summary?.alerts || [];
  const highAlerts = activeAlerts.filter((a) => a.level === "high");
  const isThinIntake = activeCase?.summary?.intake_sufficiency === "insufficient";
  const proxyCtx = activeCase?.summary?.proxy_context || null;
  const activeCds = activeCase ? cdsFeedback[activeCase.id] : null;
  const activeReceipt = activeCase ? receipts[activeCase.id] : null;

  // Generalized MedDiff column headers
  const medDiff = activeCase?.summary?.med_diff || null;
  const medDiffRows: Array<Record<string, any>> = Array.isArray(medDiff?.rows) ? medDiff.rows : [];
  const hasLegacyQehKey = medDiffRows.some((r) => r.qeh && !r.external);
  const medDiffExternalLabel = medDiff?.external_label || (hasLegacyQehKey ? "QEH Discharge" : "External Record");
  const medDiffEmrLabel = medDiff?.emr_label || "Clinic EMR";

  // Investigations & Sources visibility (SourceClip unlocked for ALL cases)
  const hasInvestigationsOrSources = Boolean(
    activeCase?.summary?.lab_panel ||
      activeCase?.summary?.trend_chart ||
      (activeCase?.summary?.imaging_studies || []).length > 0 ||
      (activeCase?.summary?.attachments || []).length > 0,
  );

  // X-Ray telemetry helpers
  const xrayRuntime = data.xray?.llm_runtime || data.xray?.model_runtime || null;
  const xrayLinterSummary = data.xray?.linter_summary || null;
  const xrayCtx = activeCase?.compilation_telemetry?.context_window || data.xray?.latest_context_window || null;
  const ctxTokens = Number(
    xrayCtx?.total_tokens ?? xrayCtx?.estimated_tokens ?? xrayCtx?.token_count ?? activeCase?.compilation_telemetry?.total_tokens ?? 0,
  );
  const ctxBudget = Number(xrayCtx?.budget_tokens ?? xrayCtx?.max_tokens ?? 4096);
  const ctxPct = ctxBudget > 0 ? Math.min(100, Math.round((ctxTokens / ctxBudget) * 100)) : 0;

  const preVisitOrderId = activeCase ? `thin_q_${activeCase.id}` : "thin_q";
  const preVisitStaged = activeOrders.some((x) => x.id === preVisitOrderId);

  return (
    <div className="ws-shell">
      <header className="ws-topbar">
        <div className="ws-brand">
          <span>Central Medical</span>
          <span className="ws-pill">Connected to Clinic EMR (Sole SoT)</span>
          <span className="ws-pill ws-pill-jade">Practice Graph {graph?.version_tag}</span>
        </div>
        <div className="ws-top-actions">
          <button type="button" className={role === "locum" ? "ws-btn ws-btn-active" : "ws-btn"} onClick={() => { setRole("locum"); setView("my_work"); }}>Locum Dr. Sarah</button>
          <button type="button" className={role === "nurse" ? "ws-btn ws-btn-active" : "ws-btn"} onClick={() => { setRole("nurse"); setView("clinic_team"); }}>Nurse Rachel</button>
          <button
            type="button"
            className={qrModalOpen ? "ws-btn ws-btn-active" : "ws-btn"}
            onClick={() => setQrModalOpen(true)}
            data-testid="open-patient-qr-btn"
          >
            {liveSession?.phone_connected ? "Patient QR ●" : "Patient QR"}
          </button>
          <button type="button" className={showXray ? "ws-btn ws-btn-active" : "ws-btn"} onClick={() => setShowXray((x) => !x)} data-testid="toggle-architecture-xray">
            {showXray ? "Hide Under the Hood" : "Under the Hood"}
          </button>
          <button
            type="button"
            className="ws-btn-ghost"
            disabled={busy}
            onClick={handleResetDemo}
            data-testid="workspace-reset-btn"
          >
            Reset Demo
          </button>
        </div>
      </header>

      <div className={`ws-main ${showXray ? "has-xray" : ""}`}>
        {/* SIDEBAR */}
        <aside className="ws-sidebar">
          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Channels</span></div>
            <button type="button" className={`ws-nav-btn ${view === "my_work" ? "is-active" : ""}`} onClick={() => setView("my_work")}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <span># my-work</span>
                {data.counts.needs_decision > 0 && (
                  <span
                    aria-label="Unread patient inquiry"
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: "50%",
                      background: "#0f766e",
                      display: "inline-block",
                    }}
                  />
                )}
              </span>
              <span
                className="ws-badge"
                style={data.counts.needs_decision > 0 ? { background: "#0f766e", color: "#ffffff" } : undefined}
              >
                {data.counts.needs_decision}
              </span>
            </button>
            <button type="button" className={`ws-nav-btn ${view === "clinic_team" ? "is-active" : ""}`} onClick={() => setView("clinic_team")}>
              <span># clinic-team</span><span className="ws-badge">{data.team_handoffs.length}</span>
            </button>
            <button type="button" className={`ws-nav-btn ${view === "playbook" ? "is-active" : ""}`} onClick={() => { setView("playbook"); if (!playbookAns) runPlaybookQuery(playbookQ); }}>
              <span>@ Clinic Playbook</span><span className="ws-badge">{data.curator_proposals.filter((p: any) => p.status === "pending_lead_gp").length}</span>
            </button>
          </div>

          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Needs Decision</span><span>{data.counts.needs_decision}</span></div>
            {data.grouped.needs_decision.length === 0 ? (
              <div style={{ padding: "4px 8px", fontSize: 12, color: "var(--ws-ink-3)" }}>None pending</div>
            ) : data.grouped.needs_decision.map((c: any) => (
              <button key={c.id} type="button" className={`ws-nav-btn ${view === c.id ? "is-active" : ""}`} onClick={() => openCase(c.id)}>
                <div style={{ minWidth: 0 }}>
                  <div>{c.patient_name} ({c.patient_age_sex})</div>
                  <div className="ws-nav-sub">{c.next_checkpoint_label}</div>
                </div>
                <span className="ws-badge">1</span>
              </button>
            ))}
          </div>

          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Running · Today</span><span>{data.counts.running}</span></div>
            {data.grouped.running.length === 0 ? (
              <div style={{ padding: "4px 8px", fontSize: 12, color: "var(--ws-ink-3)" }}>Waiting for patient</div>
            ) : data.grouped.running.map((c: any) => (
              <button key={c.id} type="button" className={`ws-nav-btn ${view === c.id ? "is-active" : ""}`} onClick={() => openCase(c.id)}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span className="ws-live-dot" />
                    <span>{c.patient_name} ({c.patient_age_sex})</span>
                  </div>
                  <div className="ws-nav-sub">
                    {c.baton === "with_nurse"
                      ? "With Nurse Rachel"
                      : c.baton === "with_patient"
                        ? "Careline active · agents working"
                        : "Dispatched today"}
                  </div>
                </div>
              </button>
            ))}
          </div>

          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Seen Today · Done</span><span>{data.counts.scheduled}</span></div>
            {data.grouped.scheduled.map((c: any) => (
              <button key={c.id} type="button" className={`ws-nav-btn ${view === c.id ? "is-active" : ""}`} onClick={() => openCase(c.id)}>
                <div style={{ minWidth: 0 }}>
                  <div>{c.patient_name} ({c.patient_age_sex})</div>
                  <div className="ws-nav-sub">{c.next_checkpoint_label}</div>
                </div>
                {c.goal?.target && <span className="ws-badge">{c.goal.status === "on_track" ? "Goal ✓" : "!"}</span>}
              </button>
            ))}
          </div>

          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Closed</span><span>{data.counts.closed}</span></div>
            {data.grouped.closed.map((c: any) => (
              <button key={c.id} type="button" className={`ws-nav-btn ${view === c.id ? "is-active" : ""}`} onClick={() => openCase(c.id)}>
                <span>{c.patient_name}</span><span className="ws-badge">✓</span>
              </button>
            ))}
          </div>
        </aside>

        {/* CENTER WORKSPACE */}
        <main className="ws-content">
          {banner && <div className="ws-safety-gap">{banner}</div>}

          {view === "my_work" && cases.length === 0 && (
            <div className="ws-qr-stage" data-testid="workspace-blank-qr-stage">
              <div className="ws-qr-card">
                <div className="ws-section-kicker">Central Medical · Live Clinic Demo</div>
                <h1 className="ws-ai-title" style={{ marginTop: 4 }}>Scan QR on your phone to begin</h1>
                <p className="ws-lead" style={{ maxWidth: 480, textAlign: "center" }}>
                  This workspace starts blank. Once a patient scans the QR code and starts messaging the clinic AI, live threads, clinical widgets, and agent workflows populate here in real time.
                </p>

                <div className="ws-qr-box">
                  {qrDataUrl ? (
                    <img src={qrDataUrl} alt="Scan to open Central Medical patient chat" width={220} height={220} />
                  ) : (
                    <div style={{ width: 220, height: 220, display: "grid", placeItems: "center", color: "var(--ws-ink-3)", fontSize: 13 }}>
                      Generating QR…
                    </div>
                  )}
                </div>

                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
                  <span className="ws-pill ws-pill-jade" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <span className="ws-live-dot" />
                    <span>{liveSession?.phone_connected ? "Phone connected — starting thread…" : "Waiting for patient scan…"}</span>
                  </span>
                </div>

                <div className="ws-qr-steps">
                  <div><strong>1. Scan QR</strong><span>Opens empty chat → clinic typing bubble → welcome</span></div>
                  <div><strong>2. Chat naturally</strong><span>English or Cantonese · switch stories in phone sidebar</span></div>
                  <div><strong>3. Watch live here</strong><span>New threads &amp; agent workflows appear as patient talks</span></div>
                </div>

                <div className="ws-qr-url-bar">
                  {editingHost ? (
                    <div style={{ display: "flex", gap: 6, width: "100%", maxWidth: 460 }}>
                      <input
                        className="ws-text-input"
                        value={publicBaseUrl}
                        onChange={(e) => setPublicBaseUrl(e.target.value)}
                        placeholder="https://your-tunnel.trycloudflare.com or http://192.168.x.x:8080"
                        style={{ flex: 1, fontSize: 12.5 }}
                      />
                      <button type="button" className="ws-btn-primary" onClick={() => setEditingHost(false)}>Done</button>
                    </div>
                  ) : (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
                      <span className="ws-num" style={{ fontSize: 12, color: "var(--ws-ink-3)" }}>{phoneQrUrl}</span>
                      <button type="button" className="ws-btn" style={{ fontSize: 12, padding: "4px 9px" }} onClick={() => setEditingHost(true)}>
                        Edit host URL
                      </button>
                      <button type="button" className="ws-btn" style={{ fontSize: 12, padding: "4px 9px" }} onClick={copyQrUrl}>
                        {copiedQr ? "Copied ✓" : "Copy link"}
                      </button>
                      <a
                        href="/p/chat"
                        target="_blank"
                        rel="noreferrer"
                        className="ws-btn"
                        style={{ fontSize: 12, padding: "4px 9px", textDecoration: "none" }}
                      >
                        Open phone view ↗
                      </a>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {view === "my_work" && cases.length > 0 && (
            <div style={{ maxWidth: 820, display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <h1 className="ws-ai-title"># my-work</h1>
                <span className="ws-pill ws-pill-jade" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span className="ws-live-dot" />
                  <span>{cases.length} live patient {cases.length === 1 ? "thread" : "threads"}</span>
                </span>
              </div>

              {/* Compact Post-Sign Confirmation Toast (auto-dismisses after returning to #my-work) */}
              {postSignGuide && (
                <div
                  data-testid="post-sign-guide-banner"
                  style={{
                    background: "#eef8f5",
                    border: "1px solid #bde2d9",
                    borderRadius: 11,
                    padding: "9px 13px",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 10,
                    flexWrap: "wrap",
                  }}
                >
                  <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontWeight: 650, color: "#0f6b58", fontSize: 13 }}>
                    <span>✓ Plan signed &amp; sent to {postSignGuide.patientName} ({postSignGuide.confirmedAt})</span>
                  </div>
                  <button
                    type="button"
                    className="ws-btn-ghost"
                    style={{ fontSize: 12, padding: "2px 8px" }}
                    onClick={() => setPostSignGuide(null)}
                  >
                    Dismiss
                  </button>
                </div>
              )}

              {data.grouped.needs_decision.map((c: any) => {
                const latestPatMsg = [...(c.summary?.original_chat || [])]
                  .reverse()
                  .find((m: any) => m.actor !== "Sam" && m.role !== "doctor" && m.role !== "system");
                return (
                  <div key={c.id} className="ws-card" data-testid={`my-work-case-${c.case_key}`}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                      <span className="ws-num" style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12.5, color: "var(--ws-ink-3)" }}>
                        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#0f766e", display: "inline-block" }} />
                        <span>{c.patient_name} ({c.patient_age_sex}) · {c.next_checkpoint_label}</span>
                      </span>
                      {c.summary?.alerts?.[0] && (
                        <span className={`ws-pill ${c.summary.alerts[0].level === "high" ? "ws-pill-red" : "ws-pill-amber"}`}>
                          {c.summary.alerts[0].label}
                        </span>
                      )}
                    </div>
                    <h2 className="ws-ai-title" style={{ fontSize: 17.5 }}>{c.ai_title}</h2>
                    <p className="ws-lead">{c.lead_line}</p>
                    {latestPatMsg && (
                      <div style={{ fontSize: 13, color: "var(--ws-ink)", padding: "8px 11px", background: "#eef8f5", borderRadius: 8, border: "1px solid #cce6de" }}>
                        <strong style={{ color: "var(--ws-jade-dark)" }}>{latestPatMsg.actor} ({latestPatMsg.time}):</strong> “{latestPatMsg.text}”
                      </div>
                    )}
                    <div><button type="button" className="ws-btn-primary" onClick={() => openCase(c.id)}>Open Case →</button></div>
                  </div>
                );
              })}

              {data.grouped.running.length > 0 && (
                <>
                  <div className="ws-section-kicker" style={{ marginTop: 4 }}>Live Careline Intake · In Progress</div>
                  {data.grouped.running.map((c: any) => (
                    <div key={c.id} className="ws-card" data-testid={`running-case-${c.case_key}`}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 13, fontWeight: 600 }}>
                          <span className="ws-live-dot" />
                          <span>{c.patient_name} ({c.patient_age_sex})</span>
                        </span>
                        <span className="ws-pill ws-pill-jade">Gathering history</span>
                      </div>
                      <p className="ws-lead">{c.lead_line}</p>
                      <div><button type="button" className="ws-btn" onClick={() => openCase(c.id)}>Open Thread →</button></div>
                    </div>
                  ))}
                </>
              )}

              {data.grouped.needs_decision.length === 0 && data.grouped.running.length === 0 && (
                <div className="ws-card">
                  <div className="ws-lead">
                    Inbox clear — no patients waiting for your decision.
                  </div>
                </div>
              )}

              {/* Subtle Collapsible Seen Today / Done Archive (kept out of active inbox) */}
              {data.grouped.scheduled.length > 0 && (
                <div style={{ marginTop: 6, borderTop: "1px solid var(--ws-line)", paddingTop: 10 }}>
                  <button
                    type="button"
                    className="ws-btn-ghost"
                    style={{ fontSize: 12.5, color: "var(--ws-ink-3)", padding: "4px 6px" }}
                    onClick={() => setSeenTodayOpen((o) => !o)}
                    data-testid="toggle-seen-today-btn"
                  >
                    {seenTodayOpen ? "▾" : "▸"} Seen Today · Done ({data.grouped.scheduled.length})
                  </button>
                  {seenTodayOpen && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
                      {data.grouped.scheduled.map((c: any) => (
                        <div
                          key={c.id}
                          className="ws-card"
                          data-testid={`scheduled-case-${c.case_key}`}
                          style={{ padding: "10px 12px", background: "#fafaf9" }}
                        >
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--ws-ink-2)" }}>
                              {c.patient_name} ({c.patient_age_sex}) · <span style={{ fontWeight: 400 }}>{c.ai_title}</span>
                            </span>
                            <button type="button" className="ws-btn" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => openCase(c.id)}>
                              Open Thread →
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {view === "clinic_team" && (
            <div style={{ maxWidth: 820, display: "flex", flexDirection: "column", gap: 14 }}>
              <h1 className="ws-ai-title"># clinic-team</h1>
              {data.team_handoffs.length === 0 ? (
                <div className="ws-card"><div className="ws-lead">No open team tasks. Tasks delegated between doctors and nurses appear here.</div></div>
              ) : data.team_handoffs.map((h: any) => (
                <div key={h.id} className="ws-card">
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ws-ink-2)" }}>{h.route}</span>
                    <span className={`ws-pill ${h.status === "quarantined_nurse" ? "ws-pill-amber" : h.status === "done" ? "ws-pill-jade" : ""}`}>
                      {h.status === "quarantined_nurse" ? "ID Verification Needed" : h.status === "done" ? "Done" : "Open"}
                    </span>
                  </div>
                  <div>{h.task}</div>
                  {h.case_id && h.status === "pending_nurse" && (
                    <div><button type="button" className="ws-btn-primary" disabled={busy} onClick={() => runTick(h.case_id, "nurse_completed")}>Mark Complete →</button></div>
                  )}
                  {h.status === "quarantined_nurse" && h.doc_id && (
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button type="button" className="ws-btn-primary" disabled={busy} onClick={() => resolveQuarantinedDoc(h.doc_id, "approve")}>
                        Confirm Match &amp; Release
                      </button>
                      <button type="button" className="ws-btn" disabled={busy} onClick={() => resolveQuarantinedDoc(h.doc_id, "reject")}>
                        Reject &amp; Hold
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {view === "playbook" && (
            <div style={{ maxWidth: 820, display: "flex", flexDirection: "column", gap: 14 }}>
              <h1 className="ws-ai-title">@ Clinic Playbook &amp; Practice Standards</h1>
              <div className="ws-card">
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {["How do we handle QEH Warfarin discharges?", "What is our Ferritin 14 protocol?", "Harbour FinTech G6PD & URTI rules?"].map((q) => (
                    <button key={q} type="button" className="ws-btn" onClick={() => runPlaybookQuery(q)}>{q}</button>
                  ))}
                </div>
                {playbookAns && (
                  <div className="ws-attachment">
                    <div style={{ fontSize: 12, color: "var(--ws-ink-3)" }}>
                      Practice Graph {playbookAns.graph_version_tag} · Approved by {playbookAns.approved_by}
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>{(playbookAns.answer_bullets || []).map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
                  </div>
                )}
              </div>
              {data.curator_proposals.map((p: any) => (
                <div key={p.id} className="ws-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                    <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ws-ink-2)" }}>Clinic Curator · Observed {p.observed_count}×</span>
                    <span className={`ws-pill ${p.status === "approved" ? "ws-pill-jade" : "ws-pill-amber"}`}>
                      {p.status === "approved" ? `Published ${p.published_graph_version}` : "Lead GP Review"}
                    </span>
                  </div>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{p.title}</div>
                  <div className="ws-lead">{p.rationale}</div>
                  {p.status === "pending_lead_gp" && (
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button type="button" className="ws-btn-primary" disabled={busy} onClick={() => runCuratorDecision(p.id, "approve")}>Approve &amp; Publish to Practice Graph</button>
                      <button type="button" className="ws-btn" disabled={busy} onClick={() => runCuratorDecision(p.id, "dismiss")}>Dismiss</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ACTIVE CASE VIEW (Deterministic 14-Widget Clinical Design System: 9 Canvas + 5 Right Rail) */}
          {activeCase && (
            <div className="ws-case-grid">
              <div className="ws-col">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                  <div>
                    <div className="ws-num" style={{ fontSize: 12.5, color: "var(--ws-ink-3)" }}>{activeCase.patient_name} ({activeCase.patient_age_sex})</div>
                    <h1 className="ws-ai-title" style={{ marginTop: 3 }}>{activeCase.ai_title}</h1>
                  </div>
                  <span className="ws-pill" style={{ flexShrink: 0 }}>{activeCase.state === "running" ? "Running · Today" : activeCase.state.replace("_", " ")}</span>
                </div>

                {/* Narrow-screen (<1024px) high-alert SafetyCard promotion */}
                {highAlerts.length > 0 && (
                  <div className="ws-card ws-narrow-safety-promote" aria-label="High-priority safety alerts">
                    <div className="ws-section-kicker" style={{ color: "var(--ws-red)" }}>Safety &amp; Alerts</div>
                    {highAlerts.map((a: any, i: number) => (
                      <div key={i} className="ws-alert-item is-high">
                        <div style={{ fontWeight: 600 }}>{a.label}</div>
                        <div style={{ color: "var(--ws-ink-2)", fontSize: 12.5, marginTop: 2 }}>{a.detail}</div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Calm 1-Line Family-Proxy Context Strip */}
                {proxyCtx && (
                  <div className="ws-proxy-strip">
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <strong style={{ color: "var(--ws-ink)" }}>
                        Family Proxy · {proxyCtx.relationship || proxyCtx.relation || "Caregiver"}
                        {proxyCtx.proxy_name || proxyCtx.speaker_name ? ` (${proxyCtx.proxy_name || proxyCtx.speaker_name})` : ""}
                      </strong>
                      {proxyCtx.note && <span>· {proxyCtx.note}</span>}
                    </div>
                    <span className={`ws-pill ${proxyCtx.id_verified_3pt || proxyCtx.verified_3pt || proxyCtx.verification_status === "verified" ? "ws-pill-jade" : "ws-pill-amber"}`}>
                      {proxyCtx.id_verified_3pt || proxyCtx.verified_3pt || proxyCtx.verification_status === "verified"
                        ? "3-Pt ID Verified"
                        : "3-Pt ID Pending"}
                    </span>
                  </div>
                )}

                {activeCase.traces?.filter((t: any) => t.can_undo).map((t: any) => (
                  <div key={t.id} className="ws-undo-bar">
                    <span>Syncing confirmed plan to Clinic EMR in <span className="ws-num">{t.undo_seconds_left}s</span> — {t.doctor_outcome_summary}</span>
                    <button type="button" className="ws-btn" onClick={() => runUndo(activeCase.id, t.id)}>Undo</button>
                  </div>
                ))}

                {/* Origin & Elapsed Strip */}
                {activeCase.summary?.origin_strip && (
                  <div className="ws-wait-banner">
                    <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                      <span className="ws-amber-dot" style={{ marginTop: 5 }} />
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 13.5 }}>{activeCase.summary.origin_strip.why}</div>
                        <div style={{ fontSize: 12, color: "var(--ws-ink-3)", marginTop: 2 }}>
                          Origin: {activeCase.summary.origin_strip.from} · {activeCase.summary.origin_strip.chase}
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="ws-add-pill"
                      onClick={() =>
                        toggleOrderInPlan(activeCase.id, {
                          id: "lee_chase",
                          type: "nurse",
                          protocol: "booking_followup_call",
                          issue: activeCase.summary?.issues?.[0]?.name || "1. Borderline LDL cholesterol",
                          assignee: "Nurse Rachel",
                          language: "bilingual",
                          task: "Booking follow-up call (fasting lipid slot)",
                          due: "Today",
                        })
                      }
                    >
                      <span className="ws-add-pill-icon">+</span> Nurse follow-up
                    </button>
                  </div>
                )}

                {/* Canonical Widget 1: VitalsExamStrip */}
                {activeCase.summary?.vitals_exam?.items?.length > 0 && (() => {
                  const ve = activeCase.summary.vitals_exam;
                  const ctxLabel = ve.encounter_context || (activeCase.state === "scheduled" ? "Post-Care Follow-Up" : "Inbound Case");
                  return (
                    <div className="ws-card" style={{ padding: "11px 13px", gap: 8 }} data-testid="physical-exam-vitals-card">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                        <span className="ws-section-kicker">Physical Exam &amp; Vitals · {ve.source}</span>
                        <span className="ws-pill">{ctxLabel}</span>
                      </div>
                      <div className="ws-vitals-strip" style={{ padding: 0, border: "none", background: "transparent" }}>
                        {ve.items.map((v: any, idx: number) => (
                          <div key={idx} className="ws-vital-item">
                            <span className="ws-vital-label">{v.label}</span>
                            <span className="ws-vital-val">{v.val}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })()}

                {/* Canonical Widget 2: ClinicalScoreStrip (Deterministic Python Scores) */}
                {activeCase.summary?.clinical_scores?.length > 0 && (
                  <div className="ws-score-strip">
                    {activeCase.summary.clinical_scores.map((sc: any) => (
                      <div key={sc.id} className="ws-score-card">
                        <div>
                          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ws-ink)", display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                            <span>{sc.label} · <span style={{ color: sc.safety_flag ? "var(--ws-red)" : "var(--ws-jade)" }}>{sc.band}</span></span>
                            {sc.safety_flag && <span className="ws-pill ws-pill-red">{sc.safety_flag}</span>}
                          </div>
                          <div style={{ fontSize: 11.5, color: "var(--ws-ink-3)", marginTop: 2 }}>{sc.detail} · {sc.guideline}</div>
                        </div>
                        <span className="ws-score-num">{sc.value}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Canonical Widget 3: MedDiff (Generalized External vs Clinic EMR with qeh/clinic fallback) */}
                {medDiff && (
                  <div className="ws-card">
                    <div className="ws-section-kicker">Medication Comparison</div>
                    {medDiffRows.length > 0 ? (
                      <div className="ws-table-scroll">
                        <table className="ws-med-table">
                          <thead>
                            <tr>
                              <th>Medication</th>
                              <th>{medDiffExternalLabel}</th>
                              <th>{medDiffEmrLabel}</th>
                              <th>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {medDiffRows.map((r: any, idx: number) => (
                              <tr key={idx}>
                                <td style={{ fontWeight: 600 }}>{r.drug}</td>
                                <td style={{ fontWeight: 600, color: "var(--ws-jade-dark)" }}>{r.external ?? r.qeh ?? "—"}</td>
                                <td style={{ color: "var(--ws-ink-3)" }}>{r.emr ?? r.clinic ?? "—"}</td>
                                <td><span className="ws-pill ws-pill-amber">{r.tag}</span></td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <div className="ws-empty-note">No active medication discrepancies detected.</div>
                    )}
                  </div>
                )}

                {/* Canonical Widget 8: Atomic Clinical IssueCards (Zero essay headers; thin-intake safety gap when insufficient) */}
                <div className="ws-card">
                  <div className="ws-section-kicker">Clinical Issues</div>

                  {isThinIntake && (
                    <div className="ws-thin-intake-gap">
                      <div style={{ display: "flex", alignItems: "flex-start", gap: 8, flex: "1 1 260px" }}>
                        <span style={{ fontWeight: 700 }}>!</span>
                        <div>
                          <strong>Insufficient clinical intake for safe prescribing or certification.</strong>{" "}
                          Gather symptom onset, red flags, and current home medications before signing clinical orders.
                        </div>
                      </div>
                      <button
                        type="button"
                        className={`ws-add-pill ${preVisitStaged ? "is-added" : ""}`}
                        onClick={() =>
                          toggleOrderInPlan(activeCase.id, {
                            id: preVisitOrderId,
                            type: "nurse",
                            protocol: "pre_visit_questionnaire",
                            issue: activeCase.summary?.issues?.[0]?.name || "Pre-visit Intake",
                            assignee: `${aiName} / Nurse Rachel`,
                            language: "bilingual",
                            task: "Send structured pre-visit symptom & red-flag questionnaire via Sam",
                            due: "Now",
                          })
                        }
                      >
                        <span className="ws-add-pill-icon">{preVisitStaged ? "✓" : "+"}</span>
                        <span>Send pre-visit questionnaire via {aiName}</span>
                      </button>
                    </div>
                  )}

                  {(activeCase.summary?.issues || []).length === 0 ? (
                    <div className="ws-empty-note">No discrete clinical issues staged yet.</div>
                  ) : (
                    (activeCase.summary?.issues || []).map((iss: any) => (
                      <div key={iss.id || iss.name} className="ws-issue-box">
                        <div className="ws-issue-title">{iss.name}</div>
                        {iss.bullets?.length > 0 && (
                          <ul className="ws-issue-bullets">
                            {iss.bullets.map((b: string, i: number) => <li key={i}>{b}</li>)}
                          </ul>
                        )}
                        {iss.safety_gap && (
                          <div className="ws-safety-gap">
                            <span style={{ fontWeight: 700 }}>!</span>
                            <span>{iss.safety_gap}</span>
                          </div>
                        )}
                        <div className="ws-issue-actions">
                          {(iss.suggested_orders || []).map((rawOrd: Record<string, any>) => {
                            const ord = normalizeOrder(rawOrd, iss.name);
                            const isAdded = activeOrders.some((x) => x.id === ord.id);
                            return (
                              <button
                                key={ord.id}
                                type="button"
                                className={`ws-add-pill ${isAdded ? "is-added" : ""}`}
                                onClick={() => toggleOrderInPlan(activeCase.id, ord)}
                              >
                                <span className="ws-add-pill-icon">{isAdded ? "✓" : "+"}</span>
                                <span>{summarizeOrder(ord)}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))
                  )}
                </div>

                {/* Canonical Widgets 4, 5, 6, 7: ResultTable + TrendGraph + ImagingPreview + SourceClip (all cases) */}
                {hasInvestigationsOrSources && (
                  <div className="ws-card">
                    {activeCase.summary?.lab_panel && (
                      <>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                          <span className="ws-section-kicker">Investigations</span>
                          <span className="ws-attachment-link">{activeCase.summary.lab_panel.source}</span>
                        </div>
                        {(activeCase.summary.lab_panel.rows || []).length > 0 ? (
                          <div className="ws-lab-grid">
                            {activeCase.summary.lab_panel.rows.map((r: any, i: number) => (
                              <div key={i} className={`ws-lab-cell ${r.flag === "LOW" || r.flag === "HIGH" ? "is-abnormal" : ""}`}>
                                <div style={{ color: "var(--ws-ink-3)", fontSize: 11.5 }}>{r.test}</div>
                                <div className="ws-num" style={{ fontWeight: 700, fontSize: 14, marginTop: 2 }}>
                                  {r.val} {(r.flag === "LOW" || r.flag === "HIGH") && <span className="ws-pill ws-pill-amber" style={{ marginLeft: 4 }}>{r.flag}</span>}
                                </div>
                                <div className="ws-num" style={{ fontSize: 11.5, color: "var(--ws-ink-3)" }}>Ref {r.ref}</div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="ws-empty-note">No laboratory rows linked yet.</div>
                        )}
                      </>
                    )}

                    {activeCase.summary?.trend_chart && (
                      <TrendSvg
                        chart={activeCase.summary.trend_chart}
                        goalSys={goalOverrides[activeCase.id]?.sys || activeCase.goal?.target_sys}
                        goalDia={goalOverrides[activeCase.id]?.dia || activeCase.goal?.target_dia}
                      />
                    )}

                    {(activeCase.summary?.imaging_studies || []).map((img: any) => {
                      const badge = modalityBadgeCode(img.modality);
                      const isReportOpen = Boolean(expandedImaging[img.id]);
                      return (
                        <div key={img.id} className="ws-imaging-card">
                          <div className="ws-imaging-thumb" aria-hidden="true">
                            {badge.isEcg ? (
                              <svg width="42" height="22" viewBox="0 0 42 22">
                                <polyline fill="none" stroke="#16695D" strokeWidth="1.6" points="0,12 8,12 12,4 16,19 20,10 24,12 31,12 34,6 37,16 42,12" />
                              </svg>
                            ) : (
                              <span className="ws-imaging-badge">{badge.code}</span>
                            )}
                          </div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                              <strong style={{ fontSize: 13 }}>{img.modality} · <span className="ws-num">{img.date}</span></strong>
                              <span className="ws-pill ws-pill-jade">{img.status}</span>
                            </div>
                            <div style={{ fontSize: 12.5, color: "var(--ws-ink-2)", marginTop: 3 }}>{img.summary}</div>
                            {img.full_report && (
                              <div style={{ marginTop: 6 }}>
                                <button
                                  type="button"
                                  className="ws-btn-ghost"
                                  style={{ padding: "2px 6px", fontSize: 12, color: "var(--ws-jade-dark)" }}
                                  onClick={() => setExpandedImaging((m) => ({ ...m, [img.id]: !m[img.id] }))}
                                >
                                  {isReportOpen ? "▾ Hide full report" : "▸ Show full report"}
                                </button>
                                {isReportOpen && <div className="ws-imaging-report">{img.full_report}</div>}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}

                    {(activeCase.summary?.attachments || []).map((att: any) => {
                      const quote = String(att.quote || "");
                      const isLong = quote.length > 220;
                      const isExpanded = Boolean(expandedClips[att.id]);
                      const visibleQuote = isLong && !isExpanded ? `${quote.slice(0, 220).trim()}…` : quote;
                      return (
                        <div key={att.id} className="ws-attachment">
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                            <span className="ws-attachment-link">{att.label}</span>
                            {att.verified_3pt && <span className="ws-pill ws-pill-jade">3-Pt ID Verified</span>}
                          </div>
                          {quote && (
                            <blockquote className="ws-quote" lang={isCantoneseText(quote) ? "zh-HK" : undefined}>
                              “{visibleQuote}”
                            </blockquote>
                          )}
                          {isLong && (
                            <div>
                              <button
                                type="button"
                                className="ws-btn-ghost"
                                style={{ padding: "2px 6px", fontSize: 12, color: "var(--ws-jade-dark)" }}
                                onClick={() => setExpandedClips((m) => ({ ...m, [att.id]: !m[att.id] }))}
                              >
                                {isExpanded ? "Collapse quote" : "Expand quote"}
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Canonical Widget 9: MANAGEMENT PLAN (Starts empty · Builds from Issue pills · 8 Inline Editors · Zero-jank Overlay / Menu) */}
                <div className="ws-plan-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--ws-line)", paddingBottom: 10 }}>
                    <div>
                      <div className="ws-section-kicker" style={{ color: "var(--ws-ink)" }}>Management Plan</div>
                      <div style={{ fontSize: 12.5, color: "var(--ws-ink-3)", marginTop: 2 }}>Select items from Clinical Issues or Preventive Care, or type / to add an order</div>
                    </div>
                    <span className="ws-pill ws-num">{selectedCount} selected</span>
                  </div>

                  {/* Signed Receipt: MCHK PDF Certificate Badge/Hash + Additive EMR Addendum Confirmation */}
                  {activeReceipt && (
                    <div className="ws-receipt-card" data-testid="commit-receipt">
                      <div className="ws-receipt-row">
                        <strong>Plan confirmed · <span className="ws-num">{activeReceipt.confirmedAt}</span></strong>
                        {activeReceipt.emrAddendumSummary && (
                          <span className="ws-pill ws-pill-jade">✓ {activeReceipt.emrAddendumSummary}</span>
                        )}
                      </div>
                      {activeReceipt.certificates.map((cert, cIdx) => (
                        <div key={cert.id || cIdx} className="ws-receipt-row" style={{ borderTop: "1px solid var(--ws-jade-line)", paddingTop: 6 }}>
                          <span>
                            <strong>MCHK Signed PDF Certificate</strong> · <span className="ws-num">{cert.days_off || 2}d</span>
                            {cert.start_date ? ` (${cert.start_date}${cert.end_date && cert.end_date !== cert.start_date ? ` → ${cert.end_date}` : ""})` : ""}
                            {cert.omit_employer_diagnosis !== false ? " · Employer Dx omitted" : ""}
                          </span>
                          <span className="ws-pill ws-pill-jade ws-num">
                            {cert.mchk_reg_no ? `${cert.mchk_reg_no} · ` : ""}
                            Hash {(cert.verification_hash || "verified").slice(0, 12)}
                          </span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* HTTP 422 CDS Hard-Block / Override-Required Feedback */}
                  {activeCds && (
                    <div className={`ws-cds-panel ${activeCds.hardBlock ? "is-hard-block" : "is-override"}`} role="alert">
                      <div style={{ fontWeight: 600 }}>
                        {activeCds.hardBlock ? "Clinical Safety Hard Block — Cannot Sign" : "Clinical Safety Review — Override Rationale Required"}
                      </div>
                      <div>{activeCds.summary}</div>
                      {activeCds.items.length > 0 && (
                        <ul style={{ margin: "2px 0 0", paddingLeft: 18 }}>
                          {activeCds.items.map((it, i) => (
                            <li key={i}><strong>{it.title}:</strong> {it.detail}</li>
                          ))}
                        </ul>
                      )}
                      {activeCds.canOverride && (
                        <div className="ws-inline-row" style={{ marginTop: 4 }}>
                          <input
                            className="ws-inline-input"
                            style={{ flex: "1 1 220px" }}
                            value={overrideReasonMap[activeCase.id] || ""}
                            onChange={(e) => setOverrideReasonMap((prev) => ({ ...prev, [activeCase.id]: e.target.value }))}
                            placeholder="Document clinical override rationale…"
                          />
                          <button
                            type="button"
                            className="ws-btn-primary"
                            disabled={busy || !(overrideReasonMap[activeCase.id] || "").trim()}
                            onClick={() => signPlan(activeCase.id, overrideReasonMap[activeCase.id])}
                          >
                            Confirm with Override Rationale
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {Object.keys(groupedPlan).length === 0 ? (
                    <div style={{ padding: 14, borderRadius: 8, border: "1px dashed var(--ws-line-strong)", fontSize: 13, color: "var(--ws-ink-3)", background: "#FCFBF9" }}>
                      No items staged yet. Click + on any clinical issue above or type / below to build today&apos;s plan.
                    </div>
                  ) : (
                    Object.entries(groupedPlan).map(([issueName, entries]) => (
                      <div key={issueName} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ws-ink-3)" }}>{issueName}</div>
                        {entries.map(({ item, idx }) => (
                          <div key={item.id} className={`ws-order-item ${item.checked ? "" : "is-dim"}`}>
                            <input
                              type="checkbox"
                              checked={item.checked}
                              onChange={() => updateCasePlan(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, checked: !x.checked } : x)))}
                              style={{ marginTop: 4 }}
                            />
                            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                                <span className="ws-pill">{item.type.toUpperCase()}</span>
                                <button type="button" className="ws-btn-ghost" onClick={() => updateCasePlan(activeCase.id, (cur) => cur.filter((_, i) => i !== idx))}>Remove</button>
                              </div>

                              {/* 1. RX Inline Editor (action verb + drug + freq + days) */}
                              {item.type === "rx" && (
                                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                  <div className="ws-inline-row">
                                    <select
                                      className="ws-select"
                                      aria-label="Medication action"
                                      value={item.action || "start"}
                                      onChange={(e) => {
                                        const nextAction = e.target.value as RxVerb;
                                        patchOrderAt(activeCase.id, idx, {
                                          action: nextAction,
                                          freq: nextAction === "stop" ? "Discontinue" : item.freq === "Discontinue" ? "PO daily" : item.freq,
                                          days: nextAction === "stop" ? "—" : item.days === "—" ? "30d" : item.days,
                                        });
                                      }}
                                    >
                                      <option value="start">Start</option>
                                      <option value="update">Update</option>
                                      <option value="stop">Stop</option>
                                      <option value="continue">Continue</option>
                                    </select>
                                    <input
                                      className="ws-inline-input"
                                      style={{ flex: "1 1 170px", fontWeight: 600 }}
                                      placeholder="Single drug + strength"
                                      value={item.drug || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { drug: e.target.value })}
                                    />
                                    {item.action !== "stop" && (
                                      <>
                                        <input
                                          className="ws-inline-input"
                                          style={{ flex: "0 1 135px" }}
                                          placeholder="Frequency (e.g. QDS PRN)"
                                          value={item.freq || ""}
                                          onChange={(e) => patchOrderAt(activeCase.id, idx, { freq: e.target.value })}
                                        />
                                        <input
                                          className="ws-inline-input ws-num"
                                          style={{ flex: "0 1 78px" }}
                                          placeholder="Days"
                                          value={item.days || ""}
                                          onChange={(e) => patchOrderAt(activeCase.id, idx, { days: e.target.value })}
                                        />
                                      </>
                                    )}
                                  </div>
                                  {item.note && <div style={{ fontSize: 12, color: "var(--ws-jade-dark)" }}>{item.note}</div>}
                                </div>
                              )}

                              {/* 2. CERT Inline Editor (1–7d bounds, start_date, omit_employer_diagnosis default true) */}
                              {item.type === "cert" && (
                                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                  <div className="ws-inline-row">
                                    <strong>Sick Leave Certificate</strong>
                                    {[1, 2, 3, 5, 7].map((d) => (
                                      <button
                                        key={d}
                                        type="button"
                                        className={`ws-add-pill ws-num ${item.days_off === d ? "is-added" : ""}`}
                                        style={{ padding: "3px 9px" }}
                                        onClick={() => {
                                          const start = item.start_date || "2026-11-23";
                                          const rng = formatDateRange(start, d);
                                          patchOrderAt(activeCase.id, idx, { days_off: d, daysOff: d, end_date: rng.endIso, dates: rng.label });
                                        }}
                                      >
                                        {d}d
                                      </button>
                                    ))}
                                    <label className="ws-inline-row" style={{ gap: 5, fontSize: 12, color: "var(--ws-ink-2)" }}>
                                      <span>Days (1–7)</span>
                                      <input
                                        type="number"
                                        min={1}
                                        max={7}
                                        className="ws-inline-input ws-num"
                                        style={{ width: 56 }}
                                        value={item.days_off || 2}
                                        onChange={(e) => {
                                          const clamped = clampDaysOff(e.target.value);
                                          const start = item.start_date || "2026-11-23";
                                          const rng = formatDateRange(start, clamped);
                                          patchOrderAt(activeCase.id, idx, { days_off: clamped, daysOff: clamped, end_date: rng.endIso, dates: rng.label });
                                        }}
                                      />
                                    </label>
                                  </div>
                                  <div className="ws-inline-row">
                                    <label className="ws-inline-row" style={{ gap: 6, fontSize: 12, color: "var(--ws-ink-2)" }}>
                                      <span>Start date</span>
                                      <input
                                        type="date"
                                        className="ws-inline-input ws-num"
                                        style={{ width: 145 }}
                                        value={item.start_date || "2026-11-23"}
                                        onChange={(e) => {
                                          const nextStart = e.target.value || "2026-11-23";
                                          const rng = formatDateRange(nextStart, item.days_off || 2);
                                          patchOrderAt(activeCase.id, idx, { start_date: nextStart, startDate: nextStart, end_date: rng.endIso, dates: rng.label });
                                        }}
                                      />
                                    </label>
                                    <span className="ws-num" style={{ fontSize: 12, color: "var(--ws-ink-3)" }}>{item.dates}</span>
                                    <label style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--ws-ink-2)" }}>
                                      <input
                                        type="checkbox"
                                        checked={item.omit_employer_diagnosis !== false}
                                        onChange={(e) => patchOrderAt(activeCase.id, idx, { omit_employer_diagnosis: e.target.checked, omitDx: e.target.checked })}
                                      />
                                      Omit employer diagnosis (MCHK default)
                                    </label>
                                  </div>
                                </div>
                              )}

                              {/* 3. LAB Inline Editor (test_name, lab, timing, fasting) */}
                              {item.type === "lab" && (
                                <div className="ws-inline-grid">
                                  <label className="ws-inline-field is-full">
                                    <span className="ws-inline-label">Investigation</span>
                                    <input
                                      className="ws-inline-input"
                                      style={{ fontWeight: 600 }}
                                      value={item.test_name || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { test_name: e.target.value, testName: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Partner Lab</span>
                                    <input
                                      className="ws-inline-input"
                                      value={item.lab || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { lab: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Timing</span>
                                    <input
                                      className="ws-inline-input"
                                      value={item.when || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { when: e.target.value })}
                                    />
                                  </label>
                                  <div className="ws-inline-field is-full">
                                    <label style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--ws-ink-2)" }}>
                                      <input
                                        type="checkbox"
                                        checked={Boolean(item.fasting)}
                                        onChange={(e) => patchOrderAt(activeCase.id, idx, { fasting: e.target.checked })}
                                      />
                                      8h Fasting required
                                    </label>
                                  </div>
                                </div>
                              )}

                              {/* 4. IMAGING Inline Editor (modality, region, indication, urgency) */}
                              {item.type === "imaging" && (
                                <div className="ws-inline-grid">
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Modality</span>
                                    <input
                                      className="ws-inline-input"
                                      style={{ fontWeight: 600 }}
                                      placeholder="CXR, Ultrasound, Mammogram, ECG"
                                      value={item.modality || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { modality: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Anatomical Region</span>
                                    <input
                                      className="ws-inline-input"
                                      placeholder="e.g. Chest PA, Bilateral breast"
                                      value={item.region || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { region: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Clinical Indication</span>
                                    <input
                                      className="ws-inline-input"
                                      placeholder="Reason for imaging"
                                      value={item.indication || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { indication: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Urgency</span>
                                    <select
                                      className="ws-select"
                                      value={item.urgency || "routine"}
                                      onChange={(e) => {
                                        const u = normalizeUrgency(e.target.value);
                                        patchOrderAt(activeCase.id, idx, { urgency: u, priority: titleCase(u) });
                                      }}
                                    >
                                      <option value="routine">Routine</option>
                                      <option value="soon">Soon</option>
                                      <option value="urgent">Urgent</option>
                                    </select>
                                  </label>
                                </div>
                              )}

                              {/* 5. REFER Inline Editor (specialty, urgency, clinical_question, attach_labs) */}
                              {item.type === "refer" && (
                                <div className="ws-inline-grid">
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Specialty</span>
                                    <input
                                      className="ws-inline-input"
                                      style={{ fontWeight: 600 }}
                                      placeholder="e.g. Cardiology, Endocrinology"
                                      value={item.specialty || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { specialty: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Urgency</span>
                                    <select
                                      className="ws-select"
                                      value={item.urgency || "routine"}
                                      onChange={(e) => {
                                        const u = normalizeUrgency(e.target.value);
                                        patchOrderAt(activeCase.id, idx, { urgency: u, priority: titleCase(u) });
                                      }}
                                    >
                                      <option value="routine">Routine</option>
                                      <option value="soon">Soon</option>
                                      <option value="urgent">Urgent</option>
                                    </select>
                                  </label>
                                  <label className="ws-inline-field is-full">
                                    <span className="ws-inline-label">Clinical Question</span>
                                    <input
                                      className="ws-inline-input"
                                      placeholder="Specific question for specialist"
                                      value={item.clinical_question || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { clinical_question: e.target.value, reason: e.target.value })}
                                    />
                                  </label>
                                  <div className="ws-inline-field is-full">
                                    <label style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--ws-ink-2)" }}>
                                      <input
                                        type="checkbox"
                                        checked={item.attach_labs !== false}
                                        onChange={(e) => patchOrderAt(activeCase.id, idx, { attach_labs: e.target.checked, attachLabs: e.target.checked })}
                                      />
                                      Attach recent labs &amp; clinical summary
                                    </label>
                                  </div>
                                </div>
                              )}

                              {/* 6. NURSE Inline Editor (closed 6-protocol dropdown, assignee, language, task, due) */}
                              {item.type === "nurse" && (
                                <div className="ws-inline-grid">
                                  <label className="ws-inline-field is-full">
                                    <span className="ws-inline-label">Clinic Protocol</span>
                                    <select
                                      className="ws-select"
                                      value={item.protocol || "medication_adherence_check"}
                                      onChange={(e) => {
                                        const nextProto = e.target.value as NurseProtocol;
                                        const protoMeta = NURSE_PROTOCOLS.find((p) => p.value === nextProto);
                                        patchOrderAt(activeCase.id, idx, {
                                          protocol: nextProto,
                                          task: protoMeta ? protoMeta.defaultTask : item.task,
                                        });
                                      }}
                                    >
                                      {NURSE_PROTOCOLS.map((p) => (
                                        <option key={p.value} value={p.value}>{p.label}</option>
                                      ))}
                                    </select>
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Assignee</span>
                                    <input
                                      className="ws-inline-input"
                                      value={item.assignee || "Nurse Rachel"}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { assignee: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Language</span>
                                    <select
                                      className="ws-select"
                                      value={item.language || "bilingual"}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { language: e.target.value as NurseLanguage })}
                                    >
                                      <option value="en">English (en)</option>
                                      <option value="zh-HK">Cantonese (zh-HK)</option>
                                      <option value="bilingual">Bilingual (EN / 繁)</option>
                                    </select>
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Task Detail</span>
                                    <input
                                      className="ws-inline-input"
                                      value={item.task || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { task: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Due</span>
                                    <input
                                      className="ws-inline-input"
                                      value={item.due || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { due: e.target.value })}
                                    />
                                  </label>
                                </div>
                              )}

                              {/* 7. BOOK Inline Editor (visit_type, when, duration_min, note) */}
                              {item.type === "book" && (
                                <div className="ws-inline-grid">
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Visit Type</span>
                                    <input
                                      className="ws-inline-input"
                                      style={{ fontWeight: 600 }}
                                      placeholder="In-clinic review, Teleconsult, Nurse slot"
                                      value={item.visit_type || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { visit_type: e.target.value, visitType: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">When</span>
                                    <input
                                      className="ws-inline-input"
                                      placeholder="e.g. In 4 weeks, Fri 08:00"
                                      value={item.when || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { when: e.target.value })}
                                    />
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Duration</span>
                                    <select
                                      className="ws-select ws-num"
                                      value={item.duration_min || 15}
                                      onChange={(e) => {
                                        const mins = Number(e.target.value);
                                        patchOrderAt(activeCase.id, idx, { duration_min: mins, durationMin: mins });
                                      }}
                                    >
                                      <option value={10}>10 min</option>
                                      <option value={15}>15 min</option>
                                      <option value={20}>20 min</option>
                                      <option value={30}>30 min</option>
                                    </select>
                                  </label>
                                  <label className="ws-inline-field">
                                    <span className="ws-inline-label">Booking Note (optional)</span>
                                    <input
                                      className="ws-inline-input"
                                      placeholder="e.g. Bring home BP log"
                                      value={item.note || ""}
                                      onChange={(e) => patchOrderAt(activeCase.id, idx, { note: e.target.value })}
                                    />
                                  </label>
                                </div>
                              )}

                              {/* 7b. LEAFLET Inline Preview (AI-generated from clinic template — never BP slider!) */}
                              {item.type === "leaflet" && (
                                <div
                                  style={{
                                    padding: "8px 10px",
                                    borderRadius: 8,
                                    background: "#f7fcfa",
                                    border: "1px solid #cce6de",
                                    display: "flex",
                                    flexDirection: "column",
                                    gap: 4,
                                  }}
                                >
                                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                                    <strong style={{ fontSize: 12.5, color: "#0f6b58" }}>
                                      {item.title || "Patient Information Leaflet"}
                                    </strong>
                                    <span className="ws-pill ws-pill-jade">AI Personalised · Clinic Template</span>
                                  </div>
                                  <div style={{ fontSize: 11.5, color: "var(--ws-ink-3)" }}>
                                    Template: {item.template || "Central Medical · Care Sheet"}
                                  </div>
                                  <div style={{ fontSize: 12.5, color: "var(--ws-ink-2)", lineHeight: 1.4 }}>
                                    {item.target_text}
                                  </div>
                                </div>
                              )}

                              {/* 8. GOAL Inline Editor (BP slider + non-BP HbA1c / LDL / TSH / Patient Leaflet) */}
                              {item.type === "goal" && (() => {
                                const isBpMetric =
                                  (item.metric || "Blood Pressure").toLowerCase().includes("blood pressure") ||
                                  (item.metric || "").toLowerCase() === "bp";
                                return (
                                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                    <div className="ws-inline-row" style={{ justifyContent: "space-between" }}>
                                      <select
                                        className="ws-select"
                                        aria-label="Goal metric"
                                        value={isBpMetric ? "Blood Pressure" : item.metric || "HbA1c"}
                                        onChange={(e) => {
                                          const m = e.target.value;
                                          if (m === "Blood Pressure") {
                                            setGoalOverrides((g) => ({ ...g, [activeCase.id]: { sys: 135, dia: 85 } }));
                                            patchOrderAt(activeCase.id, idx, {
                                              metric: "Blood Pressure",
                                              target_sys: 135,
                                              targetSys: 135,
                                              target_dia: 85,
                                              targetDia: 85,
                                              target_value: undefined,
                                              targetValue: undefined,
                                              unit: "mmHg",
                                            });
                                          } else if (m === "HbA1c") {
                                            patchOrderAt(activeCase.id, idx, {
                                              metric: "HbA1c",
                                              target_sys: undefined,
                                              targetSys: undefined,
                                              target_dia: undefined,
                                              targetDia: undefined,
                                              target_value: 7.0,
                                              targetValue: 7.0,
                                              unit: "%",
                                              leaflet_id: "t2dm_lifestyle_hk",
                                              leafletId: "t2dm_lifestyle_hk",
                                            });
                                          } else if (m === "LDL Cholesterol") {
                                            patchOrderAt(activeCase.id, idx, {
                                              metric: "LDL Cholesterol",
                                              target_sys: undefined,
                                              targetSys: undefined,
                                              target_dia: undefined,
                                              targetDia: undefined,
                                              target_value: 2.6,
                                              targetValue: 2.6,
                                              unit: "mmol/L",
                                              leaflet_id: "heart_healthy_ldl_hk",
                                              leafletId: "heart_healthy_ldl_hk",
                                            });
                                          } else if (m === "TSH") {
                                            patchOrderAt(activeCase.id, idx, {
                                              metric: "TSH",
                                              target_sys: undefined,
                                              targetSys: undefined,
                                              target_dia: undefined,
                                              targetDia: undefined,
                                              target_value: 2.5,
                                              targetValue: 2.5,
                                              unit: "mIU/L",
                                            });
                                          } else {
                                            patchOrderAt(activeCase.id, idx, {
                                              metric: "Patient Leaflet",
                                              target_sys: undefined,
                                              targetSys: undefined,
                                              target_dia: undefined,
                                              targetDia: undefined,
                                              target_value: undefined,
                                              targetValue: undefined,
                                              unit: "",
                                              leaflet_id: item.leaflet_id || "dietary_iron_hk",
                                              leafletId: item.leaflet_id || "dietary_iron_hk",
                                            });
                                          }
                                        }}
                                      >
                                        <option value="Blood Pressure">Blood Pressure Target</option>
                                        <option value="HbA1c">HbA1c Target</option>
                                        <option value="LDL Cholesterol">LDL Cholesterol Target</option>
                                        <option value="TSH">TSH Target</option>
                                        <option value="Patient Leaflet">Clinic Patient Leaflet</option>
                                      </select>
                                      {isBpMetric && (
                                        <span className="ws-num" style={{ fontWeight: 600 }}>
                                          &lt; {item.target_sys || 135}/{item.target_dia || 85} mmHg
                                        </span>
                                      )}
                                    </div>

                                    {isBpMetric ? (
                                      <input
                                        type="range"
                                        min={120}
                                        max={145}
                                        step={5}
                                        value={item.target_sys || 135}
                                        onChange={(e) => {
                                          const sys = Number(e.target.value);
                                          const dia = Math.round((sys * 0.62) / 5) * 5;
                                          setGoalOverrides((g) => ({ ...g, [activeCase.id]: { sys, dia } }));
                                          patchOrderAt(activeCase.id, idx, { target_sys: sys, targetSys: sys, target_dia: dia, targetDia: dia });
                                        }}
                                      />
                                    ) : (
                                      <div className="ws-inline-grid">
                                        {item.metric !== "Patient Leaflet" && (
                                          <>
                                            <label className="ws-inline-field">
                                              <span className="ws-inline-label">Target Value</span>
                                              <input
                                                type="number"
                                                step="0.1"
                                                className="ws-inline-input ws-num"
                                                value={item.target_value ?? ""}
                                                onChange={(e) => {
                                                  const v = e.target.value === "" ? undefined : Number(e.target.value);
                                                  patchOrderAt(activeCase.id, idx, { target_value: v, targetValue: v });
                                                }}
                                              />
                                            </label>
                                            <label className="ws-inline-field">
                                              <span className="ws-inline-label">Unit</span>
                                              <input
                                                className="ws-inline-input"
                                                value={item.unit || ""}
                                                onChange={(e) => patchOrderAt(activeCase.id, idx, { unit: e.target.value })}
                                              />
                                            </label>
                                          </>
                                        )}
                                        <label className="ws-inline-field is-full">
                                          <span className="ws-inline-label">Clinic-Approved Patient Leaflet</span>
                                          <select
                                            className="ws-select"
                                            value={item.leaflet_id || ""}
                                            onChange={(e) => patchOrderAt(activeCase.id, idx, { leaflet_id: e.target.value || undefined, leafletId: e.target.value || undefined })}
                                          >
                                            {PATIENT_LEAFLETS.map((lf) => (
                                              <option key={lf.id} value={lf.id}>{lf.label}</option>
                                            ))}
                                          </select>
                                        </label>
                                      </div>
                                    )}
                                  </div>
                                );
                              })()}
                            </div>
                          </div>
                        ))}
                      </div>
                    ))
                  )}

                  {/* Floating Overlay / Command Bar (Zero layout-shift jank · Full Keyboard Nav) */}
                  <div className="ws-slash-wrap">
                    {showSlashMenu && (
                      <div className="ws-slash-menu" role="listbox" aria-label="Slash order commands">
                        {visibleSlash.map((sc: any, sIdx: number) => (
                          <button
                            key={sc.cmd}
                            type="button"
                            role="option"
                            aria-selected={sIdx === activeSlashIdx}
                            className={`ws-slash-item ${sIdx === activeSlashIdx ? "is-highlighted" : ""}`}
                            onMouseEnter={() => setSlashIdx(sIdx)}
                            onClick={() => applySlashCmd(activeCase.id, sc.cmd)}
                          >
                            <span><strong>{sc.cmd}</strong> · {sc.label}</span>
                            <span style={{ color: "var(--ws-ink-3)", fontSize: 12 }}>{sc.desc}</span>
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="ws-input-row">
                      <input
                        className="ws-text-input"
                        value={slashInput}
                        onChange={(e) => {
                          setSlashInput(e.target.value);
                          setSlashIdx(0);
                        }}
                        onKeyDown={(e) => {
                          if (!showSlashMenu || !visibleSlash.length) {
                            if (e.key === "Escape" && slashInput) {
                              e.preventDefault();
                              setSlashInput("");
                            }
                            return;
                          }
                          if (e.key === "ArrowDown") {
                            e.preventDefault();
                            setSlashIdx((i) => (i + 1) % visibleSlash.length);
                          } else if (e.key === "ArrowUp") {
                            e.preventDefault();
                            setSlashIdx((i) => (i - 1 + visibleSlash.length) % visibleSlash.length);
                          } else if (e.key === "Enter") {
                            e.preventDefault();
                            const chosen = visibleSlash[activeSlashIdx];
                            if (chosen) applySlashCmd(activeCase.id, chosen.cmd);
                          } else if (e.key === "Escape") {
                            e.preventDefault();
                            setSlashInput("");
                            setSlashIdx(0);
                          }
                        }}
                        placeholder="Type /prescribe, /order, /cert…"
                      />
                      <button
                        type="button"
                        className="ws-add-pill"
                        onClick={() => {
                          setSlashInput(slashInput.startsWith("/") ? "" : "/");
                          setSlashIdx(0);
                        }}
                      >
                        <span className="ws-add-pill-icon">+</span> Add order
                      </button>
                    </div>
                  </div>

                  {/* Built-in Autonomous AI Follow-Up Row (Highlighted in AI-Harness Teal — Zero Doctor Workload) */}
                  {(() => {
                    const aiFollowupEnabled = aiFollowupMap[activeCase.id] !== false;
                    return (
                      <label
                        data-testid="ai-3day-followup-row"
                        style={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: 10,
                          padding: "10px 12px",
                          borderRadius: 10,
                          background: aiFollowupEnabled ? "#eef7f5" : "#fafaf9",
                          border: aiFollowupEnabled ? "1px solid #99d5c9" : "1px dashed #d6d3d1",
                          cursor: "pointer",
                          marginTop: 4,
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={aiFollowupEnabled}
                          onChange={(e) =>
                            setAiFollowupMap((prev) => ({ ...prev, [activeCase.id]: e.target.checked }))
                          }
                          style={{ marginTop: 3, accentColor: "#0f766e" }}
                        />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                            <strong style={{ fontSize: 13, color: "#0f6b58" }}>
                              3-Day AI Agent Post-Visit Check-In
                            </strong>
                            <span
                              style={{
                                fontSize: 11,
                                fontWeight: 600,
                                padding: "2px 7px",
                                borderRadius: 999,
                                background: "#d1fae5",
                                color: "#065f46",
                              }}
                            >
                              AI Harness · Zero Doctor Workload
                            </span>
                          </div>
                          <div style={{ fontSize: 12, color: "#44403c", marginTop: 2, lineHeight: 1.4 }}>
                            Sam checks in automatically on Day 3 — handles recovery, preventive care &amp; admin rebooking autonomously; only escalates to you on red flags.
                          </div>
                        </div>
                      </label>
                    );
                  })()}

                  {/* Clean Commit Bar */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, paddingTop: 10, borderTop: "1px solid var(--ws-line)" }}>
                    <span style={{ fontSize: 12, color: "var(--ws-ink-3)" }}>
                      Syncs confirmed orders additively to Clinic EMR (Sole SoT)
                    </span>
                    <button
                      type="button"
                      className="ws-btn-primary"
                      disabled={busy || selectedCount === 0}
                      onClick={() => signPlan(activeCase.id)}
                      data-testid="confirm-sign-plan-btn"
                    >
                      Confirm &amp; Sign Plan ({selectedCount})
                    </button>
                  </div>
                </div>
              </div>

              {/* CLEAN RIGHT RAIL: Only show relevant clinical cards (developer Timeline lives in Under the Hood) */}
              <aside className="ws-col">
                {/* Canonical Widget 10: SafetyCard (only when active alerts exist) */}
                {activeAlerts.length > 0 && (
                  <div className="ws-card">
                    <div className="ws-section-kicker">Safety &amp; Alerts</div>
                    {activeAlerts.map((a: any, i: number) => (
                      <div key={i} className={`ws-alert-item ${a.level === "high" ? "is-high" : ""}`}>
                        <div style={{ fontWeight: 600 }}>{a.label}</div>
                        <div style={{ color: "var(--ws-ink-2)", fontSize: 12.5, marginTop: 2 }}>{a.detail}</div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Canonical Widget 11: PreventiveCard (only when relevant) */}
                {activeCase.summary?.preventive_care?.length > 0 && (
                  <div className="ws-card">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span className="ws-section-kicker">Preventive Care</span>
                      <span style={{ fontSize: 11.5, color: "var(--ws-ink-3)" }}>Age &amp; Risk</span>
                    </div>
                    {activeCase.summary.preventive_care.map((p: any) => {
                      const normPrevOrd = p.order ? normalizeOrder(p.order, "Preventive Care") : null;
                      const inPlan = normPrevOrd ? activeOrders.some((x) => x.id === normPrevOrd.id) : false;
                      return (
                        <div key={p.id} className="ws-alert-item" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                            <strong>{p.title}</strong>
                            <span className="ws-pill">{p.badge}</span>
                          </div>
                          <div style={{ fontSize: 12.5, color: "var(--ws-ink-2)" }}>{p.detail}</div>
                          {normPrevOrd && (
                            <div>
                              <button
                                type="button"
                                className={`ws-add-pill ${inPlan ? "is-added" : ""}`}
                                onClick={() => toggleOrderInPlan(activeCase.id, normPrevOrd)}
                              >
                                <span className="ws-add-pill-icon">{inPlan ? "✓" : "+"}</span>
                                <span>{inPlan ? "In Management Plan" : "Add to Plan"}</span>
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Canonical Widget 12: ScheduledCard (only when upcoming tasks exist) */}
                {(activeCase.summary?.scheduled_tasks || []).length > 0 && (
                  <div className="ws-card">
                    <div className="ws-section-kicker">Scheduled &amp; Upcoming</div>
                    {(activeCase.summary?.scheduled_tasks || []).map((t: any, i: number) => (
                      <div key={i} className="ws-alert-item" style={{ display: "flex", gap: 8 }}>
                        <span style={{ color: "var(--ws-jade)", fontWeight: 700 }}>{t.status === "booked" ? "•" : "◦"}</span>
                        <span>{t.label}</span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Canonical Widget 14: UnifiedThread (Private Doctor & Sam highlighted first; patient chat collapsed by default) */}
                <div className="ws-thread-card">
                  <div className="ws-thread-header">
                    <span style={{ fontWeight: 600, fontSize: 13 }}>Private · Doctor &amp; {aiName}</span>
                    {activeCase.summary?.sam_proactive_insight && !samReadMap[activeCase.id] && (
                      <span className="ws-pill ws-pill-jade" style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                        <span className="ws-unread-dot" /> Clinical note
                      </span>
                    )}
                  </div>
                  <div className="ws-thread-scroll" onClick={() => setSamReadMap((m) => ({ ...m, [activeCase.id]: true }))}>
                    {activeCase.summary?.sam_proactive_insight ? (
                      <div className="ws-sam-note" style={{ borderLeft: "3px solid #0f766e", background: "#eef8f5" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3 }}>
                          <span style={{ fontSize: 11.5, fontWeight: 700, color: "var(--ws-jade-dark)" }}>{aiName} · Clinical Note</span>
                          {!samReadMap[activeCase.id] && <span className="ws-unread-dot" title="Unread" />}
                        </div>
                        <div lang={isCantoneseText(activeCase.summary.sam_proactive_insight) ? "zh-HK" : "en"}>
                          {activeCase.summary.sam_proactive_insight}
                        </div>
                      </div>
                    ) : null}

                    {(activeCase.summary?.agent_thread || []).map((m: any, idx: number) => (
                      <div key={idx} className={m.who === "doctor" ? "ws-sam-doctor-msg" : "ws-sam-note"}>
                        <div style={{ fontSize: 11.5, fontWeight: 600, color: "var(--ws-ink-3)", marginBottom: 2 }}>
                          {m.actor}{m.time ? ` · ${m.time}` : ""}
                        </div>
                        <div lang={isCantoneseText(m.text) ? "zh-HK" : "en"}>{m.text}</div>
                      </div>
                    ))}

                    {!activeCase.summary?.sam_proactive_insight && (activeCase.summary?.agent_thread || []).length === 0 && (
                      <div style={{ fontSize: 12, color: "var(--ws-ink-3)", textAlign: "center", padding: "6px" }}>
                        Ask {aiName} privately below (not written to EMR).
                      </div>
                    )}

                    {/* Patient Careline history collapsed by default so Private Doctor & Sam stays focused */}
                    {(activeCase.summary?.original_chat || []).length > 0 && (
                      <div style={{ marginTop: 6, borderTop: "1px solid var(--ws-line)", paddingTop: 6 }}>
                        <button
                          type="button"
                          className="ws-btn-ghost"
                          style={{ fontSize: 12, color: "var(--ws-ink-3)", padding: "2px 4px", width: "100%", textAlign: "left" }}
                          onClick={() =>
                            setShowPatientChatMap((prev) => ({ ...prev, [activeCase.id]: !prev[activeCase.id] }))
                          }
                          data-testid="toggle-patient-chat-history"
                        >
                          {showPatientChatMap[activeCase.id] ? "▾ Hide" : "▸ Show"} patient conversation ({(activeCase.summary?.original_chat || []).length} messages)
                        </button>
                        {showPatientChatMap[activeCase.id] && (
                          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
                            {(activeCase.summary?.original_chat || []).map((m: any, i: number) => {
                              const isZhHk = m.lang === "zh-HK" || isCantoneseText(m.text);
                              return (
                                <div key={i} className={`ws-chat-bubble ${m.actor === aiName || m.actor === "Sam" ? "is-careline" : "is-patient"}`}>
                                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                                    <span style={{ fontSize: 11.5, color: "var(--ws-ink-3)", fontWeight: 600 }}>
                                      {m.actor} · <span className="ws-num">{m.time}</span>
                                    </span>
                                    {isZhHk && <span className="ws-lang-tag" lang="zh-HK">廣東話</span>}
                                  </div>
                                  <div style={{ marginTop: 2 }} lang={isZhHk ? "zh-HK" : "en"}>{m.text}</div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  <form
                    className="ws-thread-form"
                    onSubmit={(e) => { e.preventDefault(); sendSamMessage(activeCase.id); }}
                  >
                    <input
                      className="ws-text-input"
                      style={{ padding: "6px 10px", fontSize: 12.5 }}
                      value={samInput}
                      onChange={(e) => setSamInput(e.target.value)}
                      placeholder={`Ask ${aiName} privately (not written to EMR)…`}
                    />
                    <button type="submit" className="ws-btn-primary" style={{ padding: "6px 11px", fontSize: 12.5 }} disabled={busy || !samInput.trim()}>Send</button>
                  </form>
                </div>
              </aside>
            </div>
          )}
        </main>

        {/* HEAD OF AI ARCHITECTURE X-RAY DRAWER */}
        {showXray && (
          <aside className="ws-xray" data-testid="architecture-xray-drawer">
            <div className="ws-xray-card">
              <div className="ws-xray-title">1. Presentation Compiler &amp; Linter</div>
              {xrayLinterSummary && (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
                  <span style={{ color: "var(--ws-ink-2)" }}>Fleet Linter</span>
                  <span className={`ws-pill ws-num ${xrayLinterSummary.all_passed === false ? "ws-pill-amber" : "ws-pill-jade"}`}>
                    {xrayLinterSummary.cases_passed ?? xrayLinterSummary.passed ?? 0}/{xrayLinterSummary.cases_total ?? xrayLinterSummary.total ?? cases.length} cases PASS
                  </span>
                </div>
              )}
              {activeCase?.lint_report ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <strong>{activeCase.lint_report.passed ? "PASS" : "FAIL"}</strong>
                    <span className={`ws-pill ws-num ${activeCase.lint_report.passed ? "ws-pill-jade" : "ws-pill-amber"}`}>
                      {activeCase.lint_report.rules_passed}/{activeCase.lint_report.rules_total} anti-slop invariants
                    </span>
                  </div>
                  <div style={{ color: "var(--ws-ink-2)", fontSize: 12 }}>
                    Widget stack ({activeCase.widget_stack?.length || 0}): {(activeCase.widget_stack || []).join(" · ")}
                  </div>
                  {(activeCase.lint_report.violations || []).length > 0 && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                      {activeCase.lint_report.violations.map((v: any, idx: number) => (
                        <div key={idx} className="ws-safety-gap" style={{ fontSize: 11.5 }}>
                          <span><strong>{v.rule_id || v.rule}{v.sub_rule ? ` (${v.sub_rule})` : ""}:</strong> {v.message || v.detail}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ color: "var(--ws-ink-2)" }}>Open any case to inspect its deterministic widget stack &amp; 5-rule Presentation Linter report.</div>
              )}
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">2. Model Runtime &amp; &lt;4,096 Token Budget</div>
              {xrayRuntime ? (
                <div>
                  <strong>{xrayRuntime.label || xrayRuntime.model || "Vertex AI"}</strong>
                  <div style={{ color: "var(--ws-ink-2)", fontSize: 12 }}>
                    {xrayRuntime.provider || "vertex"} · {xrayRuntime.region || data.xray?.persistence?.model_region || "asia-east2"} · {xrayRuntime.live ? "Live LLM" : "Deterministic Fallback"}
                  </div>
                </div>
              ) : (
                <div style={{ color: "var(--ws-ink-2)" }}>{data.xray?.persistence?.model_region || "Vertex AI asia-east2 (Hong Kong)"}</div>
              )}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12, marginTop: 2 }}>
                <span style={{ color: "var(--ws-ink-2)" }}>Context Window</span>
                <span className={`ws-pill ws-num ${ctxTokens > ctxBudget ? "ws-pill-red" : ctxTokens > ctxBudget * 0.85 ? "ws-pill-amber" : "ws-pill-jade"}`}>
                  {ctxTokens.toLocaleString()} / {ctxBudget.toLocaleString()} tok
                </span>
              </div>
              <div className="ws-budget-bar" aria-label="Context token budget">
                <div
                  className={`ws-budget-fill ${ctxTokens > ctxBudget ? "is-over" : ctxTokens > ctxBudget * 0.85 ? "is-warn" : ""}`}
                  style={{ width: `${ctxPct}%` }}
                />
              </div>
              <div style={{ fontSize: 11.5, color: "var(--ws-ink-3)", display: "flex", justifyContent: "space-between" }}>
                <span>Live SSE Stream</span>
                <span>{sseConnected ? "Connected" : "Standby"}</span>
              </div>
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">3. Practice Knowledge Graph</div>
              <div>Active: <strong>{graph?.version_tag}</strong> · Hash <span className="ws-num">{graph?.content_hash}</span></div>
              <div className="ws-num">{graph?.stats?.total_nodes} pathway nodes · {graph?.stats?.canonical_widgets} canonical widgets · {graph?.stats?.order_primitives} /order primitives</div>
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">4. Dual-Store Persistence (Sole EMR SoT)</div>
              <div>{data.xray?.dual_store?.clinical_sot}</div>
              <div>{data.xray?.dual_store?.agent_memory_store}</div>
              <div className="ws-num">Additive EMR Writebacks: {data.xray?.dual_store?.emr_addenda_log?.length || 0}</div>
              {data.xray?.emr_bridge && (
                <div style={{ fontSize: 12, color: "var(--ws-ink-2)" }}>
                  EMR Bridge: {data.xray.emr_bridge.status || data.xray.emr_bridge.mode || "Additive connected"}
                </div>
              )}
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">5. Live Agent Tool Stream ({recentToolCalls.length})</div>
              {recentToolCalls.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--ws-ink-3)" }}>
                  Tool calls will stream here in real time as the patient scans the QR code and chats with Sam.
                </div>
              ) : (
                <div className="ws-tool-stream">
                  {recentToolCalls.slice(0, 12).map((tc: any, idx: number) => (
                    <div key={tc.id || idx} className="ws-tool-row">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                        <strong style={{ fontSize: 12 }}>{tc.agent}</strong>
                        <span
                          className={`ws-pill ${
                            tc.authority === "BLOCKED"
                              ? "ws-pill-red"
                              : tc.authority === "SIGNED_WRITE"
                                ? "ws-pill-jade"
                                : tc.authority === "CDS"
                                  ? "ws-pill-amber"
                                  : ""
                          }`}
                          style={{ fontSize: 10.5, padding: "1px 6px" }}
                        >
                          {tc.authority || "READ"}
                        </span>
                      </div>
                      <div className="ws-num" style={{ fontSize: 11.5, color: "var(--ws-ink-2)" }}>{tc.tool}</div>
                      <div style={{ fontSize: 11.5, color: "var(--ws-ink-3)" }}>{tc.summary}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">6. Live Engineering Probes &amp; Deep-Dive</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button type="button" className="ws-btn" disabled={busy} onClick={triggerAuthorityProbe}>
                  Probe Authority Guard (BLOCKED)
                </button>
                <button type="button" className="ws-btn" disabled={busy} onClick={triggerMismatchProbe}>
                  Test 3-Pt ID Mismatch → Nurse
                </button>
                {activeCase?.case_key === "wong" && (
                  <button type="button" className="ws-btn" disabled={busy} onClick={() => runTick(activeCase.id, "bp_threshold_breach")}>
                    Trigger BP 146/92 Wake
                  </button>
                )}
                <a href="/emr?mode=live" target="_blank" rel="noreferrer" className="ws-btn" style={{ textDecoration: "none" }}>
                  Open Live Clinic EMR ↗
                </a>
              </div>
            </div>
          </aside>
        )}
      </div>

      {/* PATIENT QR MODAL OVERLAY (accessible anytime mid-demo via topbar button) */}
      {qrModalOpen && (
        <div className="ws-qr-modal-backdrop" onClick={() => setQrModalOpen(false)} data-testid="patient-qr-modal">
          <div className="ws-qr-modal" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
              <div>
                <div className="ws-section-kicker">Physical Phone Check-In</div>
                <strong style={{ fontSize: 16 }}>Scan to open Clinic Patient Chat</strong>
              </div>
              <button type="button" className="ws-btn" onClick={() => setQrModalOpen(false)}>Close</button>
            </div>
            <div className="ws-qr-box">
              {qrDataUrl && <img src={qrDataUrl} alt="Scan to open patient chat" width={220} height={220} />}
            </div>
            <div style={{ width: "100%", display: "flex", gap: 6 }}>
              <input
                className="ws-text-input"
                value={publicBaseUrl}
                onChange={(e) => setPublicBaseUrl(e.target.value)}
                placeholder="https://your-tunnel.trycloudflare.com"
                style={{ flex: 1, fontSize: 12.5 }}
              />
              <button type="button" className="ws-btn" onClick={copyQrUrl}>
                {copiedQr ? "Copied ✓" : "Copy"}
              </button>
            </div>
            <div style={{ fontSize: 12, color: "var(--ws-ink-3)", textAlign: "center" }}>
              Scan on phone → opens empty chat → clinic typing bubble → welcome. Switch patient journeys anytime from the phone sidebar.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
