// The follow-up list API (backend/api/app.py). Read calls take `as_of`, the moment the list is shown at.

export type Cite = { record: string; quote?: string | null };
export type Step = { step: string; text: string; cites: Cite[]; search?: Record<string, unknown> };
export type Check = { id: string; status: "pass" | "warn" | "fail" | "na"; detail?: string };
export type Patient = { name_en: string; name_zh: string; hkid: string; sex: string; dob: string; mrn: string; cdcc: boolean };
export type Raise = { at: string; what: string; from: number; to: number; reason: string };

export type Item = {
  id: string; patient_id: string; patient: Patient; owner_id: string; owner: string; family: string; reason: string;
  urgency: number; urgency_label: string; opened_at: string; due: string | null; overdue: boolean; status: string;
  resolved_at: string | null; escalates: string | null; escalated_to: string[]; history: Raise[]; evidence: string[];
  checks_passed: boolean;
};
export type DocMeta = { id: string; kind: string; channel: string; available_at: string; author: string | null };
export type ItemDetail = Item & { reasoning: Step[]; checks: Check[]; documents: DocMeta[]; case: CaseProjection };
export type Doc = DocMeta & { text: string; patient_id: string | null; data: Record<string, unknown> };
export type Feed = { channel: string; status: string; since: string; detail: string };
export type Staff = { id: string; name: string; role: string };
export type Source = {
  kind: string; simulated: boolean; clinic_name: string; system: string; harness: string;
  clock: { start: string; end: string }; staff: Staff[];
};
export type LoopRow = {
  family: string; label: string; opens_at: string; due: string;
  off: { closed_at: string | null; by: string | null; on_time: boolean };
  on: { closed_at: string | null; by: string | null; on_time: boolean };
};
export type Evaluation = {
  headline?: {
    off_per_1000py: number; on_per_1000py: number; reduction_per_1000py: number; ci95: [number, number];
    revenue?: { recovered_visits: number; net_visits: number; net_hkd: [number, number]; fee_basis: string };
    hard_fails: number; share_all_checks_pass: number;
  };
  patients?: Record<string, { scenario: string; loops: LoopRow[] }>;
  note?: string;
};
export type FeedAlarm = { channel: string; alarm_at: string; recovered_at: string | null; detail: string };
export type CaseQuote = { fact_id?: string; quote: string; record_id: string; provenance: string; text?: string };
export type CaseLine = {
  text: string; quote: string; record_id: string; provenance: string; at: string; fact_id: string; quotes?: CaseQuote[];
};
export type CaseSection = { id: string; title: string; lines: CaseLine[] };
export type CaseAct = "confirm" | "reject" | "close";
export type CaseProjection = {
  format: "case-projection/1";
  patient_id: string;
  as_of: string;
  medications: Record<string, unknown>[];
  conflicts: Record<string, unknown>[];
  not_dispensed_here: Record<string, unknown>[];
  referrals: Record<string, unknown>[];
  admissions: Record<string, unknown>[];
  conditions: Record<string, unknown>[];
  allergies: Record<string, unknown>[];
  observations: Record<string, unknown>[];
  strip: CaseSection[];
};
export type PatientView = { card: Patient & { registered_doctor: string }; items: Item[]; documents: DocMeta[];
                            evaluation: { scenario: string; loops: LoopRow[] } | null; case: CaseProjection };

// Static build (VITE_STATIC=1): no server; the run file ships beside the page and is read in the browser.
import { LocalStore, type Run } from "./store";
export const STATIC = import.meta.env.VITE_STATIC === "1";
let local: Promise<LocalStore> | null = null;
const store = () => (local ??= fetch("./run.json").then((r) => r.json()).then((run: Run) => new LocalStore(run)));

async function get<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T> {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
  const r = await fetch(`${path}${q.toString() ? "?" + q : ""}`);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

const remote = {
  source: () => get<Source>("/api/source"),
  items: (as_of: string, owner?: string) =>
    get<{ as_of: string; count: number; items: Item[] }>("/api/items", { as_of, owner, status: "open" }),
  item: (id: string, as_of: string) => get<ItemDetail>(`/api/items/${id}`, { as_of }),
  doc: (id: string) => get<Doc>(`/api/documents/${id}`),
  feeds: (as_of: string) => get<{ down: Feed[]; all_clear_allowed: boolean }>("/api/feeds", { as_of }),
  evaluation: () => get<Evaluation>("/api/evaluation"),
  patient: (id: string, as_of: string) => get<PatientView>(`/api/patients/${id}`, { as_of }),
  alarms: () => get<FeedAlarm[]>("/api/alarms"),
  close: async (id: string, as_of: string, resolution: string) => {
    const r = await fetch(`/api/doctor/items/${id}/close?as_of=${encodeURIComponent(as_of)}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Role": "doctor", "X-Doctor": "doctor" },
      body: JSON.stringify({ resolution }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json() as Promise<Item>;
  },
  caseAct: async (patientId: string, factId: string, action: CaseAct, as_of: string, record_id: string, quote: string) => {
    const r = await fetch(`/api/doctor/patients/${patientId}/facts/${factId}/${action}?as_of=${encodeURIComponent(as_of)}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Role": "doctor", "X-Doctor": "doctor" },
      body: JSON.stringify({ record_id, quote }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return r.json() as Promise<CaseProjection>;
  },
};

const offline: typeof remote = {
  source: async () => (await store()).source(),
  items: async (as_of, owner) => {
    const items = (await store()).list(as_of, owner);
    return { as_of, count: items.length, items };
  },
  item: async (id, as_of) => (await store()).item(id, as_of),
  doc: async (id) => (await store()).run.documents[id],
  feeds: async (as_of) => (await store()).feeds(as_of),
  evaluation: async () => (await store()).run.evaluation ?? {},
  patient: async (id, as_of) => (await store()).patient(id, as_of),
  alarms: async () => (await store()).run.feeds,
  close: async (id, as_of, resolution) => (await store()).close(id, as_of, resolution),
  caseAct: async () => { throw new Error("the static page has no case log to write"); },
};

export const api = STATIC ? offline : remote;
