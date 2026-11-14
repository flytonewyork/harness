import { useEffect, useMemo, useRef, useState } from "react";
import { api, STATIC } from "./api";
import type { CaseAct, CaseLine, CaseProjection, CaseQuote, Doc, Evaluation, Feed, FeedAlarm, Item, ItemDetail, PatientView, Source, Step } from "./api";

// ------------------------------------------------------------------------------------------- words
const FAMILY: Record<string, string> = {
  abnormal_result: "Abnormal result", test_never_resulted: "Test not resulted", followup_recommended: "Follow-up owed",
  positive_screen: "Positive screen", referral: "Referral reply", drug_monitoring: "Monitoring bloods", planned_review: "Planned review",
};
const STEP: Record<string, string> = {
  source: "Where it came from", identity: "Whose it is", finding: "What it shows", context: "What else is on file",
  assessment: "Working problem and plan", loop_status: "Whether anyone has acted", routing: "Who it went to", request: "What is asked",
};
const CHECK: Record<string, string> = {
  evidence: "Evidence was visible", quotes: "Quotes match word for word", identity: "Right patient", searches: "Chart searches re-run",
  duplicate: "Not a duplicate", owner: "Owner is rostered", reasoning: "Reasoning covers the evidence",
};
const KIND: Record<string, string> = {
  lab_report: "Lab report", consult_note: "Consultation note", referral_letter: "Referral letter", specialist_letter: "Specialist letter",
  imaging_report: "Imaging report", discharge_summary: "Discharge summary", aed_summary: "A&E summary", screening_result: "Screening result",
  dispensing: "Dispensing", lab_request: "Lab request", phone_memo: "Phone message", message: "WhatsApp message", action_note: "Action note",
};
const CHANNEL = (c: string) =>
  c.startsWith("portal:") ? `${c.slice(7).replace("pathlab", "PathLab").replace("crcsp", "Colorectal screening")} portal`
    : ({ cms: "clinic system", fax: "fax", courier: "courier, scanned", ehealth: "eHealth", whatsapp: "WhatsApp",
         patient_carried: "brought by the patient", phone: "phone" } as Record<string, string>)[c] ?? c;
const RESOLUTION: Record<string, string> = { acted: "Done", already_done: "Already done", not_relevant: "Not relevant",
  linked_done: "Handled through its escalation", duplicate: "Duplicate", skimmed: "Skimmed" };
const SITE: Record<string, string> = {
  gp: "This clinic", in_group: "Group clinic", ha_sopc: "Hospital Authority",
  private_hospital: "Private hospital", ehealth: "eHealth", patient: "Patient",
};
const CASE_KEYS = ["medications", "conflicts", "not_dispensed_here", "referrals", "admissions"];

// ------------------------------------------------------------------------------------------- time
const DAY = 86400000;
const d = (s: string) => new Date(s + (s.length === 16 ? ":00" : ""));
const iso = (t: Date) => {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}T${p(t.getHours())}:${p(t.getMinutes())}`;
};
const fmtDay = (s: string) => d(s).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const fmtWhen = (s: string) => `${fmtDay(s)}, ${s.slice(11, 16)}`;
const ago = (s: string, now: string) => {
  const days = Math.floor((d(now).getTime() - d(s).getTime()) / DAY);
  return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
};

// --------------------------------------------------------------------------------- highlighting
function ranges(text: string, quotes: string[]): [number, number][] {
  const out: [number, number][] = [];
  for (const q of quotes) {
    const toks = q.trim().split(/\s+/).filter(Boolean).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!toks.length) continue;
    const m = new RegExp(toks.join("\\s+")).exec(text);
    if (m) out.push([m.index, m.index + m[0].length]);
  }
  out.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const r of out) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  return merged;
}

function Marked({ text, quotes, focus }: { text: string; quotes: string[]; focus: string | null }) {
  const rs = ranges(text, quotes);
  const focusR = focus ? ranges(text, [focus])[0] : undefined;
  const parts: React.ReactNode[] = [];
  let at = 0;
  rs.forEach(([a, b], i) => {
    if (a > at) parts.push(text.slice(at, a));
    const on = focusR && focusR[0] >= a && focusR[1] <= b;
    parts.push(<mark key={i} className={on ? "focus" : undefined} data-focus={on ? "1" : undefined}>{text.slice(a, b)}</mark>);
    at = b;
  });
  parts.push(text.slice(at));
  return <pre className="sheet-text">{parts}</pre>;
}

// --------------------------------------------------------------------------------- pieces
function Urgency({ u, label }: { u: number; label: string }) {
  return <span className={`urg u${u}`}><span className="urg-dot" aria-hidden />{label}</span>;
}

function FeedBanner({ down }: { down: Feed[] }) {
  if (!down.length) return null;
  return (
    <div className="banner" role="alert">
      {down.map((f) => (
        <p key={f.channel}>
          <strong>{CHANNEL(f.channel)[0].toUpperCase() + CHANNEL(f.channel).slice(1)} is not responding</strong> (since {fmtWhen(f.since)}).
          {" "}Its results can't reach this list until it is back, so nothing from it counts as all clear.
        </p>
      ))}
    </div>
  );
}

type Moment = { at: string; label: string; item?: string };

function YearStrip({ start, end, asOf, alarms, moments, onPick }: {
  start: string; end: string; asOf: string; alarms: FeedAlarm[]; moments: Moment[]; onPick: (t: string) => void;
}) {
  const t0 = d(start).getTime(), t1 = d(end).getTime();
  const x = (s: string) => Math.max(0, Math.min(1, (d(s).getTime() - t0) / (t1 - t0))) * 1000;
  const months = Array.from({ length: 12 }, (_, i) => new Date(d(start).getFullYear(), i, 1));
  return (
    <svg className="strip" viewBox="0 0 1000 34" preserveAspectRatio="none" role="img" aria-label="The year, with feed outages and key moments"
      onClick={(e) => {
        const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
        const f = (e.clientX - r.left) / r.width;
        const t = new Date(t0 + f * (t1 - t0));
        t.setHours(10, 0, 0, 0);
        onPick(iso(t));
      }}>
      <rect x="0" y="8" width="1000" height="14" className="strip-bg" />
      {alarms.map((a, i) => (
        <rect key={i} x={x(a.alarm_at)} y="8" width={Math.max(3, x(a.recovered_at ?? a.alarm_at) - x(a.alarm_at))} height="14" className="strip-alarm">
          <title>{CHANNEL(a.channel)} down from {fmtWhen(a.alarm_at)}</title>
        </rect>
      ))}
      {months.map((m, i) => (
        <text key={i} x={x(iso(m)) + 3} y="33" className="strip-month">{m.toLocaleDateString("en-GB", { month: "short" })}</text>
      ))}
      {moments.map((m, i) => <line key={i} x1={x(m.at)} x2={x(m.at)} y1="4" y2="26" className="strip-moment" />)}
      <line x1={x(asOf)} x2={x(asOf)} y1="0" y2="30" className="strip-cursor" />
    </svg>
  );
}

function Steps({ steps, onQuote, focus }: { steps: Step[]; onQuote: (record: string, quote: string) => void; focus: string | null }) {
  return (
    <ol className="steps">
      {steps.map((s, i) => (
        <li key={i}>
          <h4>{STEP[s.step] ?? s.step}</h4>
          <p>{s.text}</p>
          {s.cites.filter((c) => c.quote).map((c, j) => (
            <button key={j} className={`quote${focus === c.quote ? " on" : ""}`} onClick={() => onQuote(c.record, c.quote!)}
              title="Show it in the document">
              “{c.quote}”
            </button>
          ))}
        </li>
      ))}
    </ol>
  );
}

function CaseStrip({ projection, asOf, onChanged }: { projection: CaseProjection | undefined; asOf: string; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const strip = projection?.format === "case-projection/1" ? projection.strip : [];
  const filled = strip.some((s) => s.lines.length > 0);
  const shown = filled ? strip.filter((s) => CASE_KEYS.includes(s.id) || s.lines.length > 0) : [];
  const act = async (factId: string, action: CaseAct, recordId: string, quote: string) => {
    if (!projection) return;
    setBusy(factId + action);
    setError(null);
    try {
      await api.caseAct(projection.patient_id, factId, action, asOf, recordId, quote);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not record that");
    } finally {
      setBusy(null);
    }
  };
  const buttons = (factId: string | undefined, recordId: string, quote: string, section: string) => {
    if (STATIC || !factId) return null;
    const closeable = section === "referrals" || section === "admissions";
    return (
      <div className="case-acts">
        <button type="button" disabled={busy !== null} onClick={() => act(factId, "confirm", recordId, quote)}>Confirm</button>
        <button type="button" disabled={busy !== null} onClick={() => act(factId, "reject", recordId, quote)}>Reject</button>
        {closeable && <button type="button" disabled={busy !== null} onClick={() => act(factId, "close", recordId, quote)}>Close</button>}
      </div>
    );
  };
  const sides = (line: CaseLine): CaseQuote[] =>
    (line.quotes && line.quotes.length > 1) ? line.quotes : [{ fact_id: line.fact_id, quote: line.quote, record_id: line.record_id, provenance: line.provenance, text: line.text }];
  return (
    <section className="case" aria-label="Case">
      <h3>Case</h3>
      {!filled && <p className="none">No case facts recorded at this moment.</p>}
      {shown.map((section) => (
        <div key={section.id} className={section.id === "conflicts" ? "case-block conflict" : "case-block"} data-section={section.id}>
          <h4>{section.title}</h4>
          {!section.lines.length && <p className="none">None at this moment.</p>}
          {section.lines.map((line) => (
            <div key={line.fact_id + line.record_id} className="case-line">
              <p className="case-text">{line.text}</p>
              {sides(line).map((side, i) => (
                <div key={(side.fact_id ?? line.fact_id) + String(i)}>
                  <p>
                    <span className={`site site-${side.provenance}`}>{SITE[side.provenance] ?? side.provenance}</span>{" "}
                    <q>{side.quote}</q>
                  </p>
                  {buttons(side.fact_id ?? (i === 0 ? line.fact_id : undefined), side.record_id, side.quote, section.id)}
                </div>
              ))}
            </div>
          ))}
        </div>
      ))}
      {filled && !STATIC && <p className="fine">Confirm, reject or close adds a doctor fact. It does not change this item's reasoning, and it does not close the queue item.</p>}
      {error && <p className="case-error" role="alert">{error}</p>}
    </section>
  );
}

function Review({ item, asOf, onClosed, onPatient, onItem, onCase }: { item: ItemDetail; asOf: string; onClosed: () => void;
                                                       onPatient: (id: string) => void; onItem: (id: string) => void; onCase: () => void }) {
  const [docId, setDocId] = useState<string | null>(item.documents[0]?.id ?? null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const sheet = useRef<HTMLDivElement>(null);
  useEffect(() => { setDocId(item.documents[0]?.id ?? null); setFocus(null); }, [item.id]);
  useEffect(() => { if (docId) api.doc(docId).then(setDoc); else setDoc(null); }, [docId]);
  useEffect(() => {
    sheet.current?.querySelector("mark[data-focus]")?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focus, doc]);
  const quotes = useMemo(() => item.reasoning.flatMap((s) => s.cites.filter((c) => c.record === docId && c.quote).map((c) => c.quote!)), [item, docId]);
  const close = async (resolution: string) => {
    setBusy(true);
    try { await api.close(item.id, asOf, resolution); onClosed(); } finally { setBusy(false); }
  };
  const open = item.status === "open";
  return (
    <article className="review">
      <header className="review-head">
        <div>
          <button className="who" onClick={() => onPatient(item.patient_id)}>
            {item.patient.name_en} <span lang="zh-Hant">{item.patient.name_zh}</span>
          </button>
          <span className="who-meta">{item.patient.sex}, born {item.patient.dob.slice(0, 4)}, HKID {item.patient.hkid}</span>
        </div>
        <h2>{item.reason}</h2>
        <p className="review-meta">
          <Urgency u={item.urgency} label={item.urgency_label} /> {FAMILY[item.family] ?? item.family} for {item.owner}.
          Opened {fmtWhen(item.opened_at)}{item.due ? `; due ${fmtWhen(item.due)}` : ""}.
          {item.overdue && <strong className="late"> Overdue.</strong>}
        </p>
        {(item.history.length > 0 || item.escalates || item.escalated_to.length > 0) && (
          <ul className="trail">
            {item.history.map((h, i) => <li key={i}>{fmtWhen(h.at)}: {h.reason}</li>)}
            {item.escalated_to.length > 0 && <li>Copied to the doctor on duty because {item.owner} is away.{" "}
              <button className="link" onClick={() => onItem(item.escalated_to[0])}>See the copy</button></li>}
            {item.escalates && <li>An escalation: the original item's owner is away, so it came to {item.owner}.{" "}
              <button className="link" onClick={() => onItem(item.escalates!)}>See the original</button></li>}
          </ul>
        )}
      </header>
      <div className="review-body">
        <section className="why" aria-label="Why it is here">
          <h3>Why it's here</h3>
          <Steps steps={item.reasoning} focus={focus} onQuote={(r, q) => { setDocId(r); setFocus(q); }} />
        </section>
        <section className="evidence" aria-label="Original document">
          <h3>Original document</h3>
          {item.documents.length > 1 && (
            <div className="tabs" role="tablist">
              {item.documents.map((m) => (
                <button key={m.id} role="tab" aria-selected={m.id === docId} onClick={() => { setDocId(m.id); setFocus(null); }}>
                  {KIND[m.kind] ?? m.kind}, {CHANNEL(m.channel)}
                </button>
              ))}
            </div>
          )}
          {doc && (
            <div className="sheet" ref={sheet}>
              <p className="sheet-meta">{KIND[doc.kind] ?? doc.kind} from the {CHANNEL(doc.channel)}, {fmtWhen(doc.available_at)}{doc.author ? `, ${doc.author}` : ""}</p>
              <Marked text={doc.text} quotes={quotes} focus={focus} />
            </div>
          )}
        </section>
      </div>
      <CaseStrip projection={item.case} asOf={asOf} onChanged={onCase} />
      <section className="checks" aria-label="Checks the platform ran">
        <h3>Checks the platform ran on this item</h3>
        <ul>
          {item.checks.map((c) => (
            <li key={c.id} className={`chk ${c.status}`} title={c.detail}>
              <span aria-hidden>{c.status === "pass" ? "✓" : c.status === "warn" ? "!" : c.status === "fail" ? "✕" : "–"}</span>
              {CHECK[c.id] ?? c.id}
              {c.status === "warn" && c.detail && <em>{c.detail}</em>}
            </li>
          ))}
        </ul>
      </section>
      <footer className="act">
        {open ? (
          <>
            <span>Close this item:</span>
            <button disabled={busy} onClick={() => close("acted")}>Done</button>
            <button disabled={busy} onClick={() => close("already_done")}>Already done</button>
            <button disabled={busy} onClick={() => close("not_relevant")}>Not relevant</button>
            <small>Only a doctor can close an item. The agent can open items and raise their urgency, nothing else.</small>
          </>
        ) : (
          <span>Closed: {RESOLUTION[item.status] ?? item.status}{item.resolved_at ? `, ${fmtWhen(item.resolved_at)}` : ""}.</span>
        )}
      </footer>
    </article>
  );
}

function PatientPanel({ view, asOf, onClose, onItem, onChanged }: { view: PatientView; asOf: string; onClose: () => void;
                                                           onItem: (id: string) => void; onChanged: () => void }) {
  const c = view.card;
  return (
    <aside className="drawer" aria-label={`${c.name_en}'s year`}>
      <button className="drawer-close" onClick={onClose}>Close</button>
      <h2>{c.name_en} <span lang="zh-Hant">{c.name_zh}</span></h2>
      <p className="who-meta">{c.sex}, born {c.dob}, HKID {c.hkid}. Registered with {c.registered_doctor}.</p>
      <CaseStrip projection={view.case} asOf={asOf} onChanged={onChanged} />
      <h3>Items this year</h3>
      <ul className="mini">
        {view.items.map((it) => (
          <li key={it.id}>
            <button onClick={() => onItem(it.id)}>
              <Urgency u={it.urgency} label={it.urgency_label} /> {it.reason}
              <span className="mini-meta">{fmtWhen(it.opened_at)}, {it.owner}: {it.status === "open" ? "open" : RESOLUTION[it.status] ?? it.status}</span>
            </button>
          </li>
        ))}
        {!view.items.length && <li className="empty">No items yet at this point in the year.</li>}
      </ul>
      {view.evaluation && (
        <>
          <h3>With and without this list (simulation)</h3>
          <table className="offon">
            <thead><tr><th>Follow-up</th><th>Due</th><th>Without</th><th>With</th></tr></thead>
            <tbody>
              {view.evaluation.loops.map((l, i) => (
                <tr key={i}>
                  <td>{l.label}</td><td>{fmtDay(l.due)}</td>
                  {[l.off, l.on].map((o, j) => (
                    <td key={j} className={o.on_time ? "ok" : "miss"}>
                      {o.on_time ? `On time, ${fmtDay(o.closed_at!)}` : o.closed_at ? `Late, ${fmtDay(o.closed_at)}` : "Missed"}
                      {o.on_time && <small>{o.by === "native" ? " by the clinic" : " from this list"}</small>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="fine">The answer key comes from the simulator. A real clinic has no answer key; that is what the Phase 0 look-back estimates.</p>
        </>
      )}
    </aside>
  );
}

// ------------------------------------------------------------------------------------------ app
export default function App() {
  const [src, setSrc] = useState<Source | null>(null);
  const [ev, setEv] = useState<Evaluation | null>(null);
  const [alarms, setAlarms] = useState<FeedAlarm[]>([]);
  const [asOf, setAsOf] = useState<string>("");
  const [owner, setOwner] = useState<string>("");
  const [items, setItems] = useState<Item[]>([]);
  const [down, setDown] = useState<Feed[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [patient, setPatient] = useState<PatientView | null>(null);
  const [moments, setMoments] = useState<Moment[]>([]);
  const [tick, setTick] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.source().then((s) => {
      setSrc(s);
      // the moment shown travels in the link as #at-2027-06-13-0840 (letters, digits and hyphens only)
      const m = /^#at-(\d{4}-\d\d-\d\d)-(\d\d)(\d\d)$/.exec(location.hash);
      setAsOf(m ? `${m[1]}T${m[2]}:${m[3]}` : s.clock.end);
      if (s.simulated) api.evaluation().then(setEv).catch(() => setEv(null));
    }).catch((e) => setError(String(e)));
    api.alarms().then(setAlarms).catch(() => setAlarms([]));
  }, []);

  // demo moments: Mr Chan's year (from the simulator's scenario list) and the first lab-portal outage
  useEffect(() => {
    if (!src) return;
    const out: Moment[] = [];
    const portal = alarms.find((a) => a.channel.startsWith("portal:") && /unavailable/.test(a.detail));
    if (portal) out.push({ at: iso(new Date(d(portal.alarm_at).getTime() + 90 * 60000)), label: "Lab portal down" });
    const chan = Object.entries(ev?.patients ?? {}).find(([, p]) => p.scenario === "dev_mr_chan")?.[0];
    if (chan) {
      api.patient(chan, src.clock.end).then((pv) => {
        const its = [...pv.items].sort((a, b) => a.opened_at.localeCompare(b.opened_at));
        const plus = (s: string, min: number) => iso(new Date(d(s).getTime() + min * 60000));
        const crit = its.find((i) => i.family === "abnormal_result");
        if (crit) out.push({ at: plus(crit.opened_at, 10), label: "Mr Chan: critical potassium", item: crit.id });
        const ref = its.find((i) => i.family === "referral" && !i.escalates);
        if (ref) {
          out.push({ at: plus(ref.opened_at, 60), label: "Mr Chan: referral reply chased", item: ref.id });
          if (ref.history[0]) out.push({ at: plus(ref.history[0].at, 5), label: "Day 7: raised", item: ref.id });
          const copy = its.find((i) => i.escalates === ref.id);
          if (copy) out.push({ at: plus(copy.opened_at, 5), label: "Day 14: copied to the locum", item: copy.id });
        }
        const a1c = its.find((i) => /HbA1c/i.test(i.reason));
        if (a1c) out.push({ at: plus(a1c.opened_at, 30), label: "Mr Chan: HbA1c overdue", item: a1c.id });
        out.sort((a, b) => a.at.localeCompare(b.at));
        setMoments(out);
      });
    } else setMoments(out);
  }, [src, ev, alarms]);

  useEffect(() => {
    if (!asOf) return;
    try { history.replaceState(null, "", `#at-${asOf.slice(0, 10)}-${asOf.slice(11, 13)}${asOf.slice(14, 16)}`); } catch { /* framed */ }
    api.items(asOf, owner || undefined).then((r) => setItems(r.items)).catch((e) => setError(String(e)));
    api.feeds(asOf).then((r) => setDown(r.down));
  }, [asOf, owner, tick]);

  useEffect(() => {
    if (sel && asOf) api.item(sel, asOf).then(setDetail).catch(() => setDetail(null));
    else setDetail(null);
  }, [sel, asOf, tick]);

  useEffect(() => { if (!sel && items.length) setSel(items[0].id); }, [items, sel]);

  const jump = (m: Moment) => {
    setAsOf(m.at);
    setSel(m.item ?? null);
  };
  const step = (days: number) => setAsOf(iso(new Date(d(asOf).getTime() + days * DAY)));
  const reloadOpenCase = (pid: string) => {
    if (patient?.case.patient_id === pid) api.patient(pid, asOf).then(setPatient);
    if (detail?.patient_id === pid) api.item(detail.id, asOf).then(setDetail);
  };

  if (error) return <main className="fail"><p>The follow-up service is not answering: {error}</p></main>;
  if (!src || !asOf) return <main className="fail"><p>Loading the follow-up list…</p></main>;
  const h = ev?.headline;
  return (
    <div className="app">
      <FeedBanner down={down} />
      <header className="top">
        <div className="brand">
          <h1>Follow-ups</h1>
          <p>{src.simulated ? `${src.clinic_name}, a simulated clinic${src.system ? `; its clinic system is modelled on ${src.system} from public information` : ""}`
                            : `${src.clinic_name}${src.system ? ` on ${src.system}` : ""}`}</p>
        </div>
        <label className="who-filter">
          <span>Showing</span>
          <select value={owner} onChange={(e) => { setOwner(e.target.value); setSel(null); }}>
            <option value="">all doctors</option>
            {src.staff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        <div className="clock" aria-label="Replay the recorded year">
          {src.simulated && <span className="sim">Simulated year</span>}
          <button onClick={() => step(-1)} aria-label="One day back">−1 day</button>
          <input type="datetime-local" value={asOf} min={src.clock.start} max={src.clock.end} onChange={(e) => e.target.value && setAsOf(e.target.value)} />
          <button onClick={() => step(1)} aria-label="One day on">+1 day</button>
        </div>
      </header>
      <nav className="year">
        <YearStrip start={src.clock.start} end={src.clock.end} asOf={asOf} alarms={alarms} moments={moments} onPick={(t) => { setAsOf(t); setSel(null); }} />
        <div className="moments">
          {moments.map((m) => <button key={m.label} className={asOf === m.at ? "on" : undefined} onClick={() => jump(m)}>{m.label}</button>)}
        </div>
      </nav>
      <main className="work">
        <section className="list" aria-label="Open follow-ups">
          <h2>{items.length} open at {fmtWhen(asOf)}</h2>
          {!items.length && (down.length
            ? <p className="empty warn">Nothing open{owner ? " for this doctor" : ""}, but {CHANNEL(down[0].channel)} is down: this is not an all-clear.</p>
            : <p className="empty">Nothing open{owner ? " for this doctor" : ""} at this moment.</p>)}
          <ul>
            {items.map((it) => (
              <li key={it.id}>
                <button className={`row${sel === it.id ? " sel" : ""} b${it.urgency}`} onClick={() => setSel(it.id)} aria-current={sel === it.id}>
                  <span className="row-top"><Urgency u={it.urgency} label={it.urgency_label} />
                    <span className="row-who">{it.patient.name_en} <span lang="zh-Hant">{it.patient.name_zh}</span></span></span>
                  <span className="row-reason">{it.reason}</span>
                  <span className="row-meta">
                    {FAMILY[it.family] ?? it.family}, {it.owner}, opened {ago(it.opened_at, asOf)}
                    {it.overdue && <strong className="late">, overdue</strong>}
                    {it.history.length > 0 && <span className="tag">raised</span>}
                    {it.escalated_to.length > 0 && <span className="tag">escalated</span>}
                    {it.escalates && <span className="tag">escalation</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section className="pane">
          {detail ? <Review item={detail} asOf={asOf} onClosed={() => setTick((x) => x + 1)} onItem={setSel}
                            onPatient={(pid) => api.patient(pid, asOf).then(setPatient)}
                            onCase={() => reloadOpenCase(detail.patient_id)} />
            : <p className="empty">Pick an item to see why it is here and the document it came from.</p>}
        </section>
      </main>
      {h && (
        <footer className="headline">
          <p>
            <b>{h.off_per_1000py.toFixed(1)}</b> harm-weighted missed follow-ups per 1,000 patient-years without this list, <b>{h.on_per_1000py.toFixed(1)}</b> with it
            {h.revenue ? <>; <b>{h.revenue.net_visits}</b> consultations recovered, HK${h.revenue.net_hkd[0].toLocaleString()}–{h.revenue.net_hkd[1].toLocaleString()} a year</> : null}.
            {" "}{h.hard_fails === 0 ? "No safety gate failed." : `${h.hard_fails} safety gates failed.`}
          </p>
          <p className="fine">{ev?.note} Harness: {src.harness}.</p>
        </footer>
      )}
      {patient && <PatientPanel view={patient} asOf={asOf} onClose={() => setPatient(null)}
                                onItem={(id) => { setSel(id); setPatient(null); }}
                                onChanged={() => reloadOpenCase(patient.case.patient_id)} />}
    </div>
  );
}
