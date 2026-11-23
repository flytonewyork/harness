import { useCallback, useEffect, useRef, useState } from "react";
import { v2Api } from "../api";
import "./workspace.css";

type NavView = "my_work" | "clinic_team" | "playbook" | "profile" | string;

function TrendSvg({ chart }: { chart: Record<string, any> }) {
  if (!chart?.points?.length) return null;
  if (chart.kind === "bp") {
    const pts = chart.points as Array<{ label: string; sys: number; dia: number }>;
    const xs = pts.map((_, i) => (pts.length === 1 ? 210 : 60 + (i * 290) / (pts.length - 1)));
    const mapY = (v: number) => Math.max(14, Math.min(96, 108 - ((v - 65) / 100) * 94));
    const sysLine = pts.map((p, i) => `${xs[i]},${mapY(p.sys)}`).join(" ");
    const diaLine = pts.map((p, i) => `${xs[i]},${mapY(p.dia)}`).join(" ");
    const targetY = mapY(chart.target_sys || 135);
    return (
      <div className="ws-attachment">
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, fontFamily: "var(--font-mono)" }}>
          <strong>{chart.title}</strong>
          <span style={{ color: "var(--ink-3)" }}>{chart.target_label}</span>
        </div>
        <svg viewBox="0 0 410 118" style={{ width: "100%", height: 110, display: "block" }}>
          <line x1="36" y1={targetY} x2="390" y2={targetY} stroke="var(--jade)" strokeDasharray="4 4" strokeWidth="1.2" />
          <text x="6" y={targetY + 4} fontSize="10" fill="var(--jade)" fontFamily="monospace">135</text>
          <polyline fill="none" stroke="var(--ink)" strokeWidth="2.4" points={sysLine} />
          <polyline fill="none" stroke="var(--ink-3)" strokeWidth="1.6" strokeDasharray="3 2" points={diaLine} />
          {pts.map((p, i) => (
            <g key={i}>
              <circle cx={xs[i]} cy={mapY(p.sys)} r="4" fill={p.sys > 140 ? "var(--red)" : "var(--ink)"} />
              <circle cx={xs[i]} cy={mapY(p.dia)} r="3" fill="var(--ink-3)" />
              <text x={xs[i] - 18} y={mapY(p.sys) - 8} fontSize="10" fontWeight="600" fill={p.sys > 140 ? "var(--red)" : "var(--ink)"} fontFamily="monospace">
                {p.sys}/{p.dia}
              </text>
              <text x={xs[i] - 18} y="112" fontSize="10" fill="var(--ink-3)" fontFamily="monospace">
                {p.label}
              </text>
            </g>
          ))}
        </svg>
      </div>
    );
  }

  const pts = chart.points as Array<{ label: string; v: number }>;
  const xs = pts.map((_, i) => (pts.length === 1 ? 210 : 70 + (i * 270) / (pts.length - 1)));
  const mapY = (v: number) => Math.max(16, Math.min(82, 92 - (v / 42) * 76));
  const line = pts.map((p, i) => `${xs[i]},${mapY(p.v)}`).join(" ");
  const refY = mapY(chart.target_value || 15);
  return (
    <div className="ws-attachment">
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, fontFamily: "var(--font-mono)" }}>
        <strong>{chart.title}</strong>
        <span style={{ color: "var(--ink-3)" }}>{chart.target_label}</span>
      </div>
      <svg viewBox="0 0 410 100" style={{ width: "100%", height: 92, display: "block" }}>
        <line x1="36" y1={refY} x2="390" y2={refY} stroke="var(--amber)" strokeDasharray="4 4" strokeWidth="1.2" />
        <text x="6" y={refY + 4} fontSize="10" fill="var(--amber)" fontFamily="monospace">15</text>
        <polyline fill="none" stroke="var(--ink)" strokeWidth="2.4" points={line} />
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={xs[i]} cy={mapY(p.v)} r="4" fill={p.v < 15 ? "var(--amber)" : "var(--ink)"} />
            <text x={xs[i] - 10} y={mapY(p.v) - 8} fontSize="10" fontWeight="600" fill="var(--ink)" fontFamily="monospace">
              {p.v}
            </text>
            <text x={xs[i] - 18} y="96" fontSize="10" fill="var(--ink-3)" fontFamily="monospace">
              {p.label}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export function ClinicWorkspaceApp() {
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [view, setView] = useState<NavView>("my_work");
  const [role, setRole] = useState<"locum" | "nurse">("locum");
  const [showXray, setShowXray] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<Record<string, any> | null>(null);
  const [showOriginalChat, setShowOriginalChat] = useState(false);
  const [expandedTraces, setExpandedTraces] = useState<Record<string, boolean>>({});
  const [playbookQ, setPlaybookQ] = useState("How do we handle QEH Warfarin discharges?");
  const [playbookAns, setPlaybookAns] = useState<Record<string, any> | null>(null);
  const [triageInput, setTriageInput] = useState("Woke up with 38.2 fever, can I take aspirin and get a 2-day sick note?");
  const [triageResult, setTriageResult] = useState<Record<string, any> | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const refresh = useCallback(async () => {
    const ov = await v2Api.workspaceOverview();
    setData(ov);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Countdown tick for active 10-second Undo bars
  useEffect(() => {
    const hasUndo = data?.cases?.some((c: any) => c.traces?.some((t: any) => t.can_undo));
    if (!hasUndo) return;
    const t = setInterval(() => {
      refresh();
    }, 1500);
    return () => clearInterval(t);
  }, [data, refresh]);

  const cases: Array<Record<string, any>> = data?.cases || [];
  const activeCase = cases.find((c) => c.id === view) || null;
  const graph = data?.practice_graph;
  const aiName = data?.careline_profile?.name || "Sam";

  const openCase = (caseId: string) => {
    setView(caseId);
    setPreview(null);
    setInstruction("");
    setShowOriginalChat(false);
    setBanner(null);
  };

  const runPreview = async (caseId: string, text: string) => {
    const clean = text.trim();
    if (!clean) return;
    setBusy(true);
    setBanner(null);
    try {
      const p = await v2Api.workspacePreview(caseId, clean);
      setInstruction(clean);
      setPreview(p);
    } catch (e: any) {
      setBanner(e?.message || "Failed to compile preview");
    } finally {
      setBusy(false);
    }
  };

  const runCommit = async (caseId: string) => {
    if (!preview) return;
    setBusy(true);
    try {
      await v2Api.workspaceCommit(
        caseId,
        preview.instruction_text,
        role === "locum" ? "Locum Dr. Sarah Wong" : "Nurse Rachel Chan (RN)",
      );
      setPreview(null);
      setInstruction("");
      await refresh();
    } catch (e: any) {
      setBanner(e?.message || "Commit failed");
    } finally {
      setBusy(false);
    }
  };

  const runUndo = async (caseId: string, traceId: string) => {
    setBusy(true);
    try {
      await v2Api.workspaceUndo(caseId, traceId);
      setBanner("Action undone inside 10-second safety window.");
      await refresh();
    } catch (e: any) {
      setBanner(e?.message || "Undo expired");
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

  const runPlaybookQuery = async (q: string) => {
    setPlaybookQ(q);
    const res = await v2Api.graphQuery(q);
    setPlaybookAns(res);
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

  const runProfileRename = async (newName: string) => {
    setBusy(true);
    setBanner(null);
    try {
      await v2Api.graphOnboard({ careline_profile_patch: { name: newName }, change_summary: `Renamed Careline AI to ${newName}` });
      await refresh();
    } catch (e: any) {
      setBanner(e?.message || "Blocked by Immutable Safety Floor");
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
      setBanner(`3-Point ID Gate (${res.checks.points_matched}/3): quarantined strictly to Nurse Rachel.`);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const batonLabel = (b: string) => {
    if (b === "with_nurse") return "With Nurse";
    if (b === "with_patient") return "With Patient";
    if (b === "syncing_emr") return "Syncing EMR";
    if (b === "with_doctor") return "Needs Doctor";
    return "Idle";
  };

  if (!data) {
    return <div className="ws-shell"><div className="ws-content">Loading clinic execution layer…</div></div>;
  }

  return (
    <div className="ws-shell">
      <header className="ws-topbar">
        <div className="ws-brand">
          <span>Central Medical</span>
          <span className="ws-pill">{aiName} · Clinic Careline AI</span>
          <span className="ws-pill ws-pill-jade">Graph {graph?.version_tag}</span>
        </div>
        <div className="ws-top-actions">
          <button
            type="button"
            className={role === "locum" ? "v2-btn-primary" : "v2-btn-secondary"}
            onClick={() => { setRole("locum"); setView("my_work"); }}
          >
            Locum Dr. Sarah
          </button>
          <button
            type="button"
            className={role === "nurse" ? "v2-btn-primary" : "v2-btn-secondary"}
            onClick={() => { setRole("nurse"); setView("clinic_team"); }}
          >
            Nurse Rachel
          </button>
          <button
            type="button"
            className={showXray ? "v2-btn-jade" : "v2-btn-secondary"}
            onClick={() => setShowXray((x) => !x)}
            data-testid="toggle-architecture-xray"
          >
            {showXray ? "Hide Architecture X-Ray" : "Architecture X-Ray"}
          </button>
          <button
            type="button"
            className="v2-text-btn"
            onClick={async () => { await v2Api.workspaceReset(); setPreview(null); setBanner(null); await refresh(); }}
          >
            Reset
          </button>
        </div>
      </header>

      <div className={`ws-main ${showXray ? "has-xray" : ""}`}>
        {/* SIDEBAR: Channels + Clean 4-State Queue */}
        <aside className="ws-sidebar">
          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Workspace</span></div>
            <button type="button" className={`ws-nav-btn ${view === "my_work" ? "is-active" : ""}`} onClick={() => setView("my_work")}>
              <span># my-work</span>
              <span className="ws-badge">{data.counts.needs_decision}</span>
            </button>
            <button type="button" className={`ws-nav-btn ${view === "clinic_team" ? "is-active" : ""}`} onClick={() => setView("clinic_team")}>
              <span># clinic-team</span>
              <span className="ws-badge">{data.team_handoffs.length}</span>
            </button>
            <button type="button" className={`ws-nav-btn ${view === "playbook" ? "is-active" : ""}`} onClick={() => { setView("playbook"); if (!playbookAns) runPlaybookQuery(playbookQ); }}>
              <span>@ Clinic Playbook</span>
              <span className="ws-badge">{data.curator_proposals.filter((p: any) => p.status === "pending_lead_gp").length}</span>
            </button>
            <button type="button" className={`ws-nav-btn ${view === "profile" ? "is-active" : ""}`} onClick={() => setView("profile")}>
              <span>Careline Profile ({aiName})</span>
            </button>
          </div>

          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Needs Decision</span><span>{data.counts.needs_decision}</span></div>
            {data.grouped.needs_decision.map((c: any) => (
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
            <div className="ws-nav-heading"><span>Running</span><span>{data.counts.running}</span></div>
            {data.grouped.running.map((c: any) => (
              <button key={c.id} type="button" className={`ws-nav-btn ${view === c.id ? "is-active" : ""}`} onClick={() => openCase(c.id)}>
                <div style={{ minWidth: 0 }}>
                  <div>{c.patient_name} ({c.patient_age_sex})</div>
                  <div className="ws-nav-sub">{batonLabel(c.baton)}</div>
                </div>
                <span className="ws-badge">{batonLabel(c.baton)}</span>
              </button>
            ))}
          </div>

          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Scheduled</span><span>{data.counts.scheduled}</span></div>
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
                <span>{c.patient_name}</span>
                <span className="ws-badge">✓</span>
              </button>
            ))}
          </div>
        </aside>

        {/* CENTER WORKSPACE */}
        <main className="ws-content">
          {banner && (
            <div className="v2-inline-warn" style={{ marginTop: 0 }}>{banner}</div>
          )}

          {/* # my-work */}
          {view === "my_work" && (
            <>
              <h1 className="ws-ai-title"># my-work</h1>
              {data.grouped.needs_decision.length === 0 && (
                <div className="ws-card">
                  <div style={{ fontWeight: 500 }}>All pending decisions cleared.</div>
                  <div className="ws-lead">In-flight tasks sit in Running until complete, then move automatically to Scheduled or Closed.</div>
                </div>
              )}
              {data.grouped.needs_decision.map((c: any) => (
                <div key={c.id} className="ws-card">
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>
                        {c.patient_name} ({c.patient_age_sex}) · {c.next_checkpoint_label}
                      </div>
                      <h2 className="ws-ai-title" style={{ fontSize: 18, marginTop: 3 }}>{c.ai_title}</h2>
                    </div>
                    <span className="ws-pill ws-pill-amber">Needs Decision</span>
                  </div>
                  <p className="ws-lead">{c.lead_line}</p>
                  <ul className="ws-bullets">
                    {(c.summary?.bullets || []).slice(0, 4).map((b: string, i: number) => <li key={i}>{b}</li>)}
                  </ul>
                  <div>
                    <button type="button" className="v2-btn-primary" onClick={() => openCase(c.id)}>
                      Open &amp; Decide →
                    </button>
                  </div>
                </div>
              ))}
            </>
          )}

          {/* # clinic-team (Selective Delegated Tasks & Nurse Quarantine ONLY) */}
          {view === "clinic_team" && (
            <>
              <h1 className="ws-ai-title"># clinic-team</h1>
              <p className="ws-lead">Delegated Doctor ↔ Nurse tasks and quarantined document ID checks.</p>
              {data.team_handoffs.length === 0 ? (
                <div className="ws-card">
                  <div>No delegated nurse tasks yet. Confirm a case in # my-work or test a 3-point ID mismatch in Architecture X-Ray.</div>
                </div>
              ) : (
                data.team_handoffs.map((h: any) => (
                  <div key={h.id} className="ws-card">
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600 }}>{h.route}</span>
                      <span className={`ws-pill ${h.status === "quarantined_nurse" ? "ws-pill-amber" : h.status === "done" ? "ws-pill-jade" : ""}`}>
                        {h.status === "quarantined_nurse" ? "Quarantined (ID Check)" : h.status === "done" ? "Done" : "Pending Nurse"}
                      </span>
                    </div>
                    <div>{h.task}</div>
                    {h.case_id && h.status === "pending_nurse" && (
                      <div>
                        <button type="button" className="v2-btn-primary" onClick={() => runTick(h.case_id, "nurse_completed")}>
                          Mark Nurse Task Complete →
                        </button>
                      </div>
                    )}
                  </div>
                ))
              )}
            </>
          )}

          {/* @ Clinic Playbook + Clinic Curator */}
          {view === "playbook" && (
            <>
              <h1 className="ws-ai-title">@ Clinic Playbook &amp; Practice Standards</h1>
              <div className="ws-card">
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {[
                    "How do we handle QEH Warfarin discharges?",
                    "What is our Ferritin 14 protocol?",
                    "What are our Harbour FinTech G6PD & URTI rules?",
                  ].map((q) => (
                    <button key={q} type="button" className="v2-btn-secondary" onClick={() => runPlaybookQuery(q)}>{q}</button>
                  ))}
                </div>
                <form className="ws-input-row" onSubmit={(e) => { e.preventDefault(); runPlaybookQuery(playbookQ); }}>
                  <input className="ws-text-input" value={playbookQ} onChange={(e) => setPlaybookQ(e.target.value)} placeholder="Ask how this practice handles a clinical workflow…" />
                  <button type="submit" className="v2-btn-primary">Ask</button>
                </form>
                {playbookAns && (
                  <div className="ws-attachment">
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>
                      Traversed Practice Graph {playbookAns.graph_version_tag} ({playbookAns.graph_content_hash?.slice(0, 8)}) · Approved by {playbookAns.approved_by}
                    </div>
                    <ul className="ws-bullets">
                      {(playbookAns.answer_bullets || []).map((b: string, i: number) => <li key={i}>{b}</li>)}
                    </ul>
                  </div>
                )}
              </div>

              {data.curator_proposals.map((p: any) => (
                <div key={p.id} className="ws-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600 }}>Clinic Curator · Observed {p.observed_count}×</span>
                    <span className={`ws-pill ${p.status === "approved" ? "ws-pill-jade" : "ws-pill-amber"}`}>
                      {p.status === "approved" ? `Published ${p.published_graph_version}` : p.status === "dismissed" ? "Dismissed" : "Lead GP Review"}
                    </span>
                  </div>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{p.title}</div>
                  <div className="ws-lead">{p.rationale}</div>
                  {p.status === "pending_lead_gp" && (
                    <div style={{ display: "flex", gap: 8 }}>
                      <button type="button" className="v2-btn-primary" disabled={busy} onClick={() => runCuratorDecision(p.id, "approve")}>
                        Approve &amp; Publish to Practice Graph
                      </button>
                      <button type="button" className="v2-btn-secondary" disabled={busy} onClick={() => runCuratorDecision(p.id, "dismiss")}>
                        Dismiss
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </>
          )}

          {/* Careline AI Profile Setup */}
          {view === "profile" && (
            <>
              <h1 className="ws-ai-title">Careline AI Profile &amp; Onboarding</h1>
              <div className="ws-card">
                <div style={{ fontWeight: 600 }}>Display Name ({aiName} · Clinic Careline AI)</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {["Sam", "Maya", "Central Careline"].map((nm) => (
                    <button key={nm} type="button" className={aiName === nm ? "v2-btn-primary" : "v2-btn-secondary"} disabled={busy} onClick={() => runProfileRename(nm)}>
                      {nm}
                    </button>
                  ))}
                  <button type="button" className="v2-btn-secondary" disabled={busy} onClick={() => runProfileRename("Resident Sam")}>
                    Test Guard: Try “Resident Sam” (Blocked)
                  </button>
                </div>
                <div className="ws-lead">{data.careline_profile?.disclosure_en}</div>
              </div>
            </>
          )}

          {/* ACTIVE PATIENT CASE (Zero-Slop Summary + Trend Chart + Attachment + Categorised Preview + Irreversible Commit + 10s Undo + Folded Trace) */}
          {activeCase && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                <div>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>
                    {activeCase.patient_name} ({activeCase.patient_age_sex}) · {activeCase.next_checkpoint_label}
                  </div>
                  <h1 className="ws-ai-title" style={{ marginTop: 4 }}>{activeCase.ai_title}</h1>
                </div>
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  {activeCase.goal?.target && (
                    <span className={`ws-pill ${activeCase.goal.status === "on_track" ? "ws-pill-jade" : "ws-pill-amber"}`}>
                      Goal: {activeCase.goal.target}
                    </span>
                  )}
                  <span className="ws-pill">{activeCase.state === "running" ? `Running · ${batonLabel(activeCase.baton)}` : activeCase.state.replace("_", " ")}</span>
                </div>
              </div>

              {/* Active 10-Second Undo Bar */}
              {activeCase.traces?.filter((t: any) => t.can_undo).map((t: any) => (
                <div key={t.id} className="ws-undo-bar">
                  <span>Dispatching signed instruction in {t.undo_seconds_left}s — {t.doctor_outcome_summary}</span>
                  <button type="button" className="v2-btn-secondary" onClick={() => runUndo(activeCase.id, t.id)}>
                    Undo
                  </button>
                </div>
              ))}

              {/* Summary Card (AI title above + 1-line lead + 3-4 implicit bullets + chart + attachment) */}
              <div className="ws-card">
                <p className="ws-lead">{activeCase.lead_line}</p>
                <ul className="ws-bullets">
                  {(activeCase.summary?.bullets || []).map((b: string, i: number) => <li key={i}>{b}</li>)}
                </ul>

                {activeCase.summary?.trend_chart && <TrendSvg chart={activeCase.summary.trend_chart} />}

                {(activeCase.summary?.attachments || []).map((att: any) => (
                  <div key={att.id} className="ws-attachment">
                    <span className="ws-attachment-link">📎 {att.label}</span>
                    <blockquote className="ws-quote">“{att.quote}”</blockquote>
                  </div>
                ))}

                {activeCase.summary?.original_chat?.length > 0 && (
                  <div>
                    <button type="button" className="v2-text-btn" style={{ paddingLeft: 0 }} onClick={() => setShowOriginalChat((s) => !s)}>
                      {showOriginalChat ? "Hide original chat" : "See original chat"}
                    </button>
                    {showOriginalChat && (
                      <div className="ws-attachment" style={{ marginTop: 6 }}>
                        {activeCase.summary.original_chat.map((m: any, i: number) => (
                          <div key={i} style={{ fontSize: 13 }}>
                            <strong>{m.actor}</strong> <span style={{ color: "var(--ink-3)", fontFamily: "var(--font-mono)", fontSize: 11.5 }}>{m.time}</span>: {m.text}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Longitudinal Timeline + Folded Claude-Code Execution Traces */}
              <div className="ws-card">
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, color: "var(--ink-3)" }}>TIMELINE</div>
                <div className="ws-timeline">
                  {(activeCase.timeline || []).map((item: any, idx: number) => {
                    const matchedTrace = item.trace_id ? activeCase.traces?.find((t: any) => t.id === item.trace_id) : null;
                    const isOpen = matchedTrace ? Boolean(expandedTraces[matchedTrace.id]) : false;
                    return (
                      <div key={idx} className="ws-tl-item">
                        <div>
                          <span className="ws-tl-date">{item.date}</span>
                          <span>{item.label}</span>
                        </div>
                        {matchedTrace && (
                          <div className="ws-trace-fold" style={{ marginTop: 6 }}>
                            <button
                              type="button"
                              className="ws-trace-btn"
                              onClick={() => setExpandedTraces((m) => ({ ...m, [matchedTrace.id]: !m[matchedTrace.id] }))}
                            >
                              <span>{isOpen ? "▾" : "▸"} {matchedTrace.folded_steps?.length || 0} agent steps</span>
                              <span>{matchedTrace.status === "undo_window" ? "Undo window" : "Done"}</span>
                            </button>
                            {isOpen && (
                              <div className="ws-trace-steps">
                                {(matchedTrace.folded_steps || []).map((st: any, j: number) => (
                                  <div key={j}><strong>{st.tool}</strong> — {st.detail}</div>
                                ))}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Loop 3 Bottom Decision & Free-Text Trust Dock */}
              {preview ? (
                <div className={`ws-commit-card ${preview.requires_irreversible_commit ? "is-irreversible" : ""}`}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                    <strong style={{ fontSize: 14 }}>
                      {preview.requires_irreversible_commit ? "Review & Sign Action" : "Confirm Tasks"}
                    </strong>
                    {preview.requires_irreversible_commit && (
                      <span className="ws-pill ws-pill-amber">Irreversible · {preview.undo_window_seconds}s Undo</span>
                    )}
                  </div>

                  {preview.categorised_plan.clinical_advice?.length > 0 && (
                    <div className="ws-cat-block is-clinical">
                      <div className="ws-cat-label">Clinical Advice</div>
                      {preview.categorised_plan.clinical_advice.map((x: any, i: number) => <div key={i}>• {x.text}</div>)}
                    </div>
                  )}
                  {preview.categorised_plan.lab_imaging?.length > 0 && (
                    <div className="ws-cat-block">
                      <div className="ws-cat-label">Lab / Imaging</div>
                      {preview.categorised_plan.lab_imaging.map((x: any, i: number) => <div key={i}>• {x.text}</div>)}
                    </div>
                  )}
                  {preview.categorised_plan.patient_task?.length > 0 && (
                    <div className="ws-cat-block">
                      <div className="ws-cat-label">Patient Task</div>
                      {preview.categorised_plan.patient_task.map((x: any, i: number) => <div key={i}>• {x.text}</div>)}
                    </div>
                  )}
                  {preview.categorised_plan.admin_booking?.length > 0 && (
                    <div className="ws-cat-block">
                      <div className="ws-cat-label">Admin / Booking</div>
                      {preview.categorised_plan.admin_booking.map((x: any, i: number) => <div key={i}>• {x.text}</div>)}
                    </div>
                  )}

                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button type="button" className="v2-btn-primary" disabled={busy} onClick={() => runCommit(activeCase.id)}>
                      {preview.requires_irreversible_commit ? "Sign & Dispatch" : "Confirm & Run"}
                    </button>
                    <button
                      type="button"
                      className="v2-btn-secondary"
                      onClick={() => {
                        setPreview(null);
                        setTimeout(() => inputRef.current?.focus(), 20);
                      }}
                    >
                      Edit instruction
                    </button>
                  </div>
                </div>
              ) : (
                <div className="ws-card">
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {(activeCase.summary?.quick_options || []).map((opt: any, i: number) => (
                      <button
                        key={i}
                        type="button"
                        className={i === 0 && activeCase.state === "needs_decision" ? "v2-btn-primary" : "v2-btn-secondary"}
                        disabled={busy}
                        onClick={() => runPreview(activeCase.id, opt.instruction)}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                  <form className="ws-input-row" onSubmit={(e) => { e.preventDefault(); runPreview(activeCase.id, instruction); }}>
                    <input
                      ref={inputRef}
                      className="ws-text-input"
                      value={instruction}
                      onChange={(e) => setInstruction(e.target.value)}
                      placeholder={`Tell ${aiName} what to do next…`}
                    />
                    <button type="submit" className="v2-btn-secondary" disabled={busy || !instruction.trim()}>
                      Preview →
                    </button>
                  </form>
                </div>
              )}
            </>
          )}
        </main>

        {/* HEAD OF AI ARCHITECTURE X-RAY DRAWER (Hidden by default for CEO!) */}
        {showXray && (
          <aside className="ws-xray" data-testid="architecture-xray-drawer">
            <div className="ws-xray-card">
              <div className="ws-xray-title">1. Practice Knowledge Graph (Runtime Brain)</div>
              <div>Active: <strong>{graph?.version_tag}</strong> · Hash <code>{graph?.content_hash}</code></div>
              <div>Nodes: {graph?.stats?.total_nodes} total · {graph?.stats?.safety_floor_nodes} Immutable Safety Floor · {graph?.stats?.practice_customized_nodes} Practice Overlays</div>
              <pre className="ws-xray-pre">
                {JSON.stringify(
                  (preview?.traversed_nodes || graph?.nodes?.slice(0, 4) || []).map((n: any) => ({
                    id: n.id,
                    provenance: n.provenance,
                    safety_floor: n.safety_floor,
                  })),
                  null,
                  2,
                )}
              </pre>
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">2. Bounded Context Window Compiler</div>
              <pre className="ws-xray-pre">
                {JSON.stringify(
                  preview?.context_snapshot || data.xray?.latest_context_window || {
                    note: "Click Preview on any case to inspect exact subgraph pruning & token slots.",
                    model_routing: { region: "asia-east2 (HK)", model: "gemini-2.5-flash", persistence: data.xray?.persistence },
                  },
                  null,
                  2,
                )}
              </pre>
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">3. Dual-Store: EMR SoT vs. Agent Memory</div>
              <div>{data.xray?.dual_store?.clinical_sot}</div>
              <div>{data.xray?.dual_store?.agent_memory_store}</div>
              <div>Additive EMR Writebacks: {data.xray?.dual_store?.emr_addenda_log?.length || 0}</div>
            </div>

            <div className="ws-xray-card">
              <div className="ws-xray-title">4. Live Loop 1, 2 &amp; 4 Engineering Probes</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button type="button" className="ws-xray-btn" onClick={triggerMismatchProbe}>
                  Test 3-Pt ID Mismatch → Quarantine
                </button>
                {activeCase?.state === "scheduled" && activeCase.case_key === "wong" && (
                  <button type="button" className="ws-xray-btn" onClick={() => runTick(activeCase.id, "bp_threshold_breach")}>
                    Trigger BP 146/92 Wake
                  </button>
                )}
                {activeCase?.state === "running" && (
                  <button type="button" className="ws-xray-btn" onClick={() => runTick(activeCase.id, "stall_timeout")}>
                    Simulate 72h Stall Timeout
                  </button>
                )}
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                <input
                  style={{ flex: 1, background: "#101115", color: "#E8E6DF", border: "1px solid #3C414D", borderRadius: 6, padding: "5px 8px", fontSize: 12 }}
                  value={triageInput}
                  onChange={(e) => setTriageInput(e.target.value)}
                />
                <button
                  type="button"
                  className="ws-xray-btn"
                  onClick={async () => setTriageResult(await v2Api.workspaceTriageSim(triageInput, 24, true))}
                >
                  Run Loop 1
                </button>
              </div>
              {triageResult && <pre className="ws-xray-pre">{JSON.stringify(triageResult, null, 2)}</pre>}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
