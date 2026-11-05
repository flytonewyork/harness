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
export type ItemDetail = Item & { reasoning: Step[]; checks: Check[]; documents: DocMeta[] };
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
export type PatientView = { card: Patient & { registered_doctor: string }; items: Item[]; documents: DocMeta[];
                            evaluation: { scenario: string; loops: LoopRow[] } | null };

async function get<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T> {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
  const r = await fetch(`${path}${q.toString() ? "?" + q : ""}`);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export const api = {
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
};
