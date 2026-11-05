// The API's store (backend/api/store.py), in the browser, for the static build: the same views over a
// harness-run/1 file, so the follow-up list can be shared as a page with no server behind it. Closing an item
// works for the open page only.
import type { DocMeta, Evaluation, Feed, FeedAlarm, Item, ItemDetail, Doc, PatientView, Source, Step, Check } from "./api";

type RunItem = {
  id: string; patient_id: string; owner_id: string; family: string; reason: string; urgency_at_open: number;
  history: [string, string, number, number, string][]; opened_at: string; due: string | null; resolved_at: string | null;
  resolution: string | null; escalates: string | null; evidence: string[]; reasoning: Step[]; checks: Check[];
};
export type Run = {
  format: string;
  source: Omit<Source, "clock" | "staff"> & { start: string; end: string };
  staff: { id: string; name: string; role: string }[];
  patients: Record<string, any>;
  documents: Record<string, Doc>;
  items: RunItem[];
  feeds: FeedAlarm[];
  evaluation?: Evaluation;
};

const LABEL: Record<number, string> = { 4: "Critical", 3: "Urgent", 2: "Soon", 1: "Routine" };
const t = (s: string) => new Date(s + (s.length === 16 ? ":00" : ""));

export class LocalStore {
  run: Run;
  items: Map<string, RunItem>;
  copies = new Map<string, string[]>();
  staff: Map<string, { id: string; name: string; role: string }>;

  constructor(run: Run) {
    this.run = run;
    this.items = new Map(run.items.map((i) => [i.id, { ...i, history: [...i.history] }]));
    this.staff = new Map(run.staff.map((s) => [s.id, s]));
    for (const i of run.items) if (i.escalates) this.copies.set(i.escalates, [...(this.copies.get(i.escalates) ?? []), i.id]);
  }

  source(): Source {
    const s = this.run.source;
    return { ...s, clock: { start: s.start, end: s.end }, staff: this.run.staff } as Source;
  }

  view(it: RunItem, asOf: Date): Item {
    let u = it.urgency_at_open;
    const history = [];
    for (const h of it.history) {
      if (t(h[0]) <= asOf) { u = h[3]; history.push({ at: h[0], what: h[1], from: h[2], to: h[3], reason: h[4] }); }
    }
    const res = it.resolved_at ? t(it.resolved_at) : null;
    const status = res === null || res > asOf ? "open" : it.resolution ?? "closed";
    const p = this.run.patients[it.patient_id] ?? {};
    return {
      id: it.id, patient_id: it.patient_id,
      patient: { name_en: p.name_en, name_zh: p.name_zh, hkid: p.hkid, sex: p.sex, dob: p.dob, mrn: p.mrn, cdcc: p.cdcc },
      owner_id: it.owner_id, owner: this.staff.get(it.owner_id)?.name ?? it.owner_id, family: it.family, reason: it.reason,
      urgency: u, urgency_label: LABEL[u] ?? String(u), opened_at: it.opened_at, due: it.due,
      overdue: !!(it.due && t(it.due) < asOf && status === "open"), status,
      resolved_at: status !== "open" ? it.resolved_at : null, escalates: it.escalates,
      escalated_to: (this.copies.get(it.id) ?? []).filter((c) => t(this.items.get(c)!.opened_at) <= asOf),
      history, evidence: it.evidence, checks_passed: it.checks.every((c) => c.status === "pass" || c.status === "na"),
    };
  }

  list(asOf: string, owner?: string, patient?: string, status: "open" | "all" = "open"): Item[] {
    const at = t(asOf);
    const out: Item[] = [];
    for (const it of this.items.values()) {
      if (t(it.opened_at) > at || (owner && it.owner_id !== owner) || (patient && it.patient_id !== patient)) continue;
      const v = this.view(it, at);
      if (status === "open" && v.status !== "open") continue;
      out.push(v);
    }
    return out.sort((a, b) => Number(a.status !== "open") - Number(b.status !== "open") || b.urgency - a.urgency
      || (a.due ?? "9999").localeCompare(b.due ?? "9999") || a.opened_at.localeCompare(b.opened_at));
  }

  meta(id: string): DocMeta {
    const d = this.run.documents[id];
    return { id: d.id, kind: d.kind, channel: d.channel, available_at: d.available_at, author: d.author };
  }

  item(id: string, asOf: string): ItemDetail {
    const it = this.items.get(id);
    if (!it || t(it.opened_at) > t(asOf)) throw new Error("404 no such item at that time");
    return { ...this.view(it, t(asOf)), reasoning: it.reasoning, checks: it.checks,
             documents: it.evidence.filter((e) => this.run.documents[e]).map((e) => this.meta(e)) };
  }

  feeds(asOf: string): { down: Feed[]; all_clear_allowed: boolean } {
    const at = t(asOf);
    const down = new Map<string, Feed>();
    for (const f of this.run.feeds) {
      if (t(f.alarm_at) <= at && (!f.recovered_at || t(f.recovered_at) > at) && !down.has(f.channel))
        down.set(f.channel, { channel: f.channel, status: "down", since: f.alarm_at, detail: f.detail });
    }
    return { down: [...down.values()], all_clear_allowed: down.size === 0 };
  }

  patient(pid: string, asOf: string): PatientView {
    const at = t(asOf);
    const docs = Object.values(this.run.documents).filter((d) => d.patient_id === pid && t(d.available_at) <= at)
      .sort((a, b) => a.available_at.localeCompare(b.available_at));
    return { card: this.run.patients[pid], items: this.list(asOf, undefined, pid, "all"), documents: docs.map((d) => this.meta(d.id)),
             evaluation: this.run.evaluation?.patients?.[pid] ?? null };
  }

  close(id: string, asOf: string, resolution: string): Item {
    const it = this.items.get(id)!;
    if (this.view(it, t(asOf)).status !== "open") throw new Error("already closed");
    it.resolved_at = asOf;
    it.resolution = resolution;
    return this.view(it, t(asOf));
  }
}
