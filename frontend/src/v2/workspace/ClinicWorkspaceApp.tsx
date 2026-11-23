import { useCallback, useEffect, useState } from "react";
import { v2Api } from "../api";
import "./workspace.css";

type NavView = "my_work" | "clinic_team" | "playbook" | "profile" | string;

interface CheckoutOrder {
  id: string;
  type: "rx" | "cert" | "lab" | "imaging" | "refer" | "goal" | "nurse" | "book";
  issue: string;
  checked: boolean;
  drug?: string;
  freq?: string;
  days?: string;
  note?: string;
  daysOff?: number;
  dates?: string;
  omitDx?: boolean;
  testName?: string;
  lab?: string;
  fasting?: boolean;
  when?: string;
  modality?: string;
  indication?: string;
  priority?: string;
  specialty?: string;
  reason?: string;
  metric?: string;
  targetSys?: number;
  targetDia?: number;
  assignee?: string;
  task?: string;
  due?: string;
  visitType?: string;
}

function summarizeOrder(item: CheckoutOrder): string {
  if (item.type === "cert") return `${item.daysOff || 2}-day Sick Leave Certificate (${item.dates || "23–24 Nov"})`;
  if (item.type === "rx") return `Rx: ${item.drug} (${item.freq}, ${item.days})`;
  if (item.type === "lab") return `Lab: ${item.testName} (${item.when}${item.fasting ? ", fasting" : ""})`;
  if (item.type === "goal") return `BP Target < ${item.targetSys}/${item.targetDia} mmHg`;
  if (item.type === "imaging") return `Imaging: ${item.modality}`;
  if (item.type === "refer") return `Referral: ${item.specialty}`;
  if (item.type === "nurse") return `${item.assignee || "Nurse Rachel"}: ${item.task}`;
  return `Booking: ${item.visitType} (${item.when})`;
}

function defaultOrderForCmd(cmd: string, issue: string): CheckoutOrder {
  const id = `ord_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
  switch (cmd) {
    case "/prescribe":
      return { id, type: "rx", issue, checked: true, drug: "Paracetamol 500mg", freq: "QDS PRN", days: "5d", note: "G6PD-safe · Formulary verified" };
    case "/cert":
      return { id, type: "cert", issue, checked: true, daysOff: 2, dates: "23–24 Nov 2026", omitDx: true };
    case "/order":
      return { id, type: "lab", issue, checked: true, testName: "Serum Ferritin + FBC", lab: "KingMed", fasting: false, when: "Feb 2027" };
    case "/imaging":
      return { id, type: "imaging", issue, checked: true, modality: "Bilateral Screening Mammogram", indication: "HK biennial breast screening", priority: "Routine" };
    case "/refer":
      return { id, type: "refer", issue, checked: true, specialty: "Cardiology", reason: "Post-discharge AFib follow-up" };
    case "/goal":
      return { id, type: "goal", issue, checked: true, metric: "Blood Pressure", targetSys: 130, targetDia: 80 };
    case "/nurse":
      return { id, type: "nurse", issue, checked: true, assignee: "Nurse Rachel", task: "Follow-up phone call (Cantonese)", due: "Today" };
    default:
      return { id, type: "book", issue, checked: true, visitType: "10-min Nurse Preventive Visit", when: "Next available" };
  }
}

function TrendSvg({ chart, goalSys, goalDia }: { chart: Record<string, any>; goalSys?: number; goalDia?: number }) {
  if (!chart?.points?.length) return null;
  if (chart.kind === "bp") {
    const pts = chart.points as Array<{ label: string; sys: number; dia: number }>;
    const xs = pts.map((_, i) => (pts.length === 1 ? 210 : 60 + (i * 290) / (pts.length - 1)));
    const mapY = (v: number) => Math.max(14, Math.min(96, 108 - ((v - 65) / 100) * 94));
    const sysLine = pts.map((p, i) => `${xs[i]},${mapY(p.sys)}`).join(" ");
    const diaLine = pts.map((p, i) => `${xs[i]},${mapY(p.dia)}`).join(" ");
    const tSys = goalSys || chart.target_sys || 135;
    const tDia = goalDia || 85;
    const targetY = mapY(tSys);
    return (
      <div className="ws-attachment">
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, fontFamily: "var(--font-mono)" }}>
          <strong>{chart.title}</strong>
          <span style={{ color: "var(--jade)", fontWeight: 600 }}>Target &lt; {tSys}/{tDia}</span>
        </div>
        <svg viewBox="0 0 410 118" style={{ width: "100%", height: 110, display: "block" }}>
          <line x1="36" y1={targetY} x2="390" y2={targetY} stroke="var(--jade)" strokeDasharray="4 4" strokeWidth="1.4" />
          <text x="6" y={targetY + 4} fontSize="10" fontWeight="700" fill="var(--jade)" fontFamily="monospace">{tSys}</text>
          <polyline fill="none" stroke="var(--ink)" strokeWidth="2.4" points={sysLine} />
          <polyline fill="none" stroke="var(--ink-3)" strokeWidth="1.6" strokeDasharray="3 2" points={diaLine} />
          {pts.map((p, i) => (
            <g key={i}>
              <circle cx={xs[i]} cy={mapY(p.sys)} r="4" fill={p.sys > 140 ? "var(--red)" : "var(--ink)"} />
              <circle cx={xs[i]} cy={mapY(p.dia)} r="3" fill="var(--ink-3)" />
              <text x={xs[i] - 18} y={mapY(p.sys) - 8} fontSize="10" fontWeight="600" fill={p.sys > 140 ? "var(--red)" : "var(--ink)"} fontFamily="monospace">{p.sys}/{p.dia}</text>
              <text x={xs[i] - 18} y="112" fontSize="10" fill="var(--ink-3)" fontFamily="monospace">{p.label}</text>
            </g>
          ))}
        </svg>
      </div>
    );
  }

  if (chart.kind === "inr") {
    const pts = chart.points as Array<{ label: string; v: number }>;
    const xs = [68, 205, 342];
    const mapY = (v: number) => Math.max(14, Math.min(82, 92 - ((v - 1.5) / 2.2) * 76));
    return (
      <div className="ws-attachment">
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, fontFamily: "var(--font-mono)" }}>
          <strong>{chart.title}</strong>
          <span style={{ color: "var(--ink-3)" }}>{chart.target_label}</span>
        </div>
        <svg viewBox="0 0 410 98" style={{ width: "100%", height: 92, display: "block" }}>
          <rect x="36" y={mapY(3.0)} width="356" height={mapY(2.0) - mapY(3.0)} fill="var(--jade-soft)" opacity="0.65" />
          <line x1="36" y1={mapY(3.0)} x2="390" y2={mapY(3.0)} stroke="var(--jade)" strokeDasharray="3 3" strokeWidth="1" />
          <line x1="36" y1={mapY(2.0)} x2="390" y2={mapY(2.0)} stroke="var(--jade)" strokeDasharray="3 3" strokeWidth="1" />
          <text x="6" y={mapY(3.0) + 4} fontSize="10" fill="var(--jade)" fontFamily="monospace">3.0</text>
          <text x="6" y={mapY(2.0) + 4} fontSize="10" fill="var(--jade)" fontFamily="monospace">2.0</text>
          <polyline fill="none" stroke="var(--ink)" strokeWidth="2.4" points={pts.map((p, i) => `${xs[i]},${mapY(p.v)}`).join(" ")} />
          {pts.map((p, i) => (
            <g key={i}>
              <circle cx={xs[i]} cy={mapY(p.v)} r="4" fill="var(--ink)" />
              <text x={xs[i] - 10} y={mapY(p.v) - 8} fontSize="10" fontWeight="600" fill="var(--ink)" fontFamily="monospace">{p.v}</text>
              <text x={xs[i] - 24} y="94" fontSize="10" fill="var(--ink-3)" fontFamily="monospace">{p.label}</text>
            </g>
          ))}
        </svg>
      </div>
    );
  }

  const pts = chart.points as Array<{ label: string; v: number }>;
  const xs = pts.map((_, i) => (pts.length === 1 ? 210 : 70 + (i * 270) / (pts.length - 1)));
  const mapY = (v: number) => Math.max(16, Math.min(82, 92 - (v / 42) * 76));
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
        <polyline fill="none" stroke="var(--ink)" strokeWidth="2.4" points={pts.map((p, i) => `${xs[i]},${mapY(p.v)}`).join(" ")} />
        {pts.map((p, i) => (
          <g key={i}>
            <circle cx={xs[i]} cy={mapY(p.v)} r="4" fill={p.v < 15 ? "var(--amber)" : "var(--ink)"} />
            <text x={xs[i] - 10} y={mapY(p.v) - 8} fontSize="10" fontWeight="600" fill="var(--ink)" fontFamily="monospace">{p.v}</text>
            <text x={xs[i] - 18} y="96" fontSize="10" fill="var(--ink-3)" fontFamily="monospace">{p.label}</text>
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
  const [prestageEnabled, setPrestageEnabled] = useState(true);
  const [showXray, setShowXray] = useState(false);
  const [checkouts, setCheckouts] = useState<Record<string, CheckoutOrder[]>>({});
  const [goalOverrides, setGoalOverrides] = useState<Record<string, { sys: number; dia: number }>>({});
  const [slashInput, setSlashInput] = useState("");
  const [showPatientChat, setShowPatientChat] = useState(false);
  const [expandedTraces, setExpandedTraces] = useState<Record<string, boolean>>({});
  const [samOpen, setSamOpen] = useState(false);
  const [samReadMap, setSamReadMap] = useState<Record<string, boolean>>({});
  const [samInput, setSamInput] = useState("");
  const [playbookQ, setPlaybookQ] = useState("How do we handle QEH Warfarin discharges?");
  const [playbookAns, setPlaybookAns] = useState<Record<string, any> | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const hydrateCheckouts = useCallback((ov: Record<string, any>, prestage: boolean) => {
    setCheckouts((prev) => {
      const next = { ...prev };
      for (const c of ov.cases || []) {
        if (!next[c.id]) {
          next[c.id] = prestage
            ? (c.summary?.issues || []).flatMap((iss: any) =>
                (iss.suggested_orders || []).map((o: any) => ({ ...o, checked: false })),
              )
            : [];
        }
      }
      return next;
    });
  }, []);

  const refresh = useCallback(async () => {
    const ov = await v2Api.workspaceOverview();
    setData(ov);
    hydrateCheckouts(ov, prestageEnabled);
  }, [hydrateCheckouts, prestageEnabled]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const hasUndo = data?.cases?.some((c: any) => c.traces?.some((t: any) => t.can_undo));
    if (!hasUndo) return;
    const t = setInterval(() => { refresh(); }, 1500);
    return () => clearInterval(t);
  }, [data, refresh]);

  const cases: Array<Record<string, any>> = data?.cases || [];
  const activeCase = cases.find((c) => c.id === view) || null;
  const graph = data?.practice_graph;
  const orderCatalog: Array<Record<string, any>> = graph?.order_catalog || [];
  const aiName = data?.careline_profile?.name || "Sam";

  const openCase = (caseId: string) => {
    setView(caseId);
    setSlashInput("");
    setShowPatientChat(false);
    setSamOpen(false);
    setBanner(null);
  };

  const togglePrestage = () => {
    const nextVal = !prestageEnabled;
    setPrestageEnabled(nextVal);
    if (data) {
      const rebuilt: Record<string, CheckoutOrder[]> = {};
      for (const c of data.cases || []) {
        rebuilt[c.id] = nextVal
          ? (c.summary?.issues || []).flatMap((iss: any) => (iss.suggested_orders || []).map((o: any) => ({ ...o, checked: false })))
          : [];
      }
      setCheckouts(rebuilt);
    }
  };

  const updateCaseCheckout = (caseId: string, updater: (cur: CheckoutOrder[]) => CheckoutOrder[]) => {
    setCheckouts((prev) => ({ ...prev, [caseId]: updater(prev[caseId] || []) }));
  };

  const toggleIssueOrder = (caseId: string, ord: CheckoutOrder) => {
    updateCaseCheckout(caseId, (cur) => {
      const idx = cur.findIndex((x) => x.id === ord.id);
      if (idx >= 0) {
        return cur.map((x, i) => (i === idx ? { ...x, checked: !x.checked } : x));
      }
      return [...cur, { ...ord, checked: true }];
    });
  };

  const togglePreventiveOrder = (caseId: string, ord: CheckoutOrder) => {
    updateCaseCheckout(caseId, (cur) => {
      const exists = cur.some((x) => x.id === ord.id);
      if (exists) return cur.filter((x) => x.id !== ord.id);
      return [...cur, { ...ord, checked: true }];
    });
  };

  const applySlashCmd = (caseId: string, cmd: string) => {
    const issueName = activeCase?.summary?.issues?.[0]?.name || "Additional Orders";
    updateCaseCheckout(caseId, (cur) => [...cur, defaultOrderForCmd(cmd, issueName)]);
    setSlashInput("");
  };

  const signCheckoutPlan = async (caseId: string) => {
    const selected = (checkouts[caseId] || []).filter((x) => x.checked);
    if (!selected.length) return;
    const compiledText = selected.map(summarizeOrder).join("; ");
    setBusy(true);
    try {
      await v2Api.workspaceCommit(caseId, compiledText, role === "locum" ? "Locum Dr. Sarah Wong" : "Nurse Rachel Chan (RN)");
      updateCaseCheckout(caseId, (cur) => cur.filter((x) => !x.checked));
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
    try {
      await v2Api.workspaceAsk(caseId, txt, role === "locum" ? "Locum Dr. Sarah Wong" : "Nurse Rachel Chan (RN)");
      const low = txt.toLowerCase();
      if (low.includes("3 day") || low.includes("3d")) {
        updateCaseCheckout(caseId, (cur) =>
          cur.map((x) => (x.type === "cert" ? { ...x, daysOff: 3, dates: "23–25 Nov 2026", checked: true } : x)),
        );
      } else if (low.includes("tsh") || low.includes("thyroid")) {
        const issueName = activeCase?.summary?.issues?.[0]?.name || "Additional Orders";
        updateCaseCheckout(caseId, (cur) => [
          ...cur,
          { id: `ord_${Date.now()}`, type: "lab", issue: issueName, checked: true, testName: "Serum TSH + Free T4", lab: "KingMed", fasting: false, when: "With next blood draw" },
        ]);
      } else if (low.includes("130")) {
        setGoalOverrides((g) => ({ ...g, [caseId]: { sys: 130, dia: 80 } }));
        updateCaseCheckout(caseId, (cur) =>
          cur.map((x) => (x.type === "goal" ? { ...x, targetSys: 130, targetDia: 80, checked: true } : x)),
        );
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

  const activeOrders = activeCase ? checkouts[activeCase.id] || [] : [];
  const selectedCount = activeOrders.filter((x) => x.checked).length;
  const groupedCheckout: Record<string, Array<{ item: CheckoutOrder; idx: number }>> = {};
  activeOrders.forEach((item, idx) => {
    const grp = item.issue || "Additional Orders";
    if (!groupedCheckout[grp]) groupedCheckout[grp] = [];
    groupedCheckout[grp].push({ item, idx });
  });

  const slashClean = slashInput.trim().toLowerCase();
  const showSlashMenu = slashClean.startsWith("/");
  const filteredSlash = showSlashMenu
    ? orderCatalog.filter((s) => s.cmd.includes(slashClean) || s.label.toLowerCase().includes(slashClean.slice(1)))
    : [];

  return (
    <div className="ws-shell">
      <header className="ws-topbar">
        <div className="ws-brand">
          <span>Central Medical</span>
          <span className="ws-pill">Connected to Clinic EMR (Sole SoT)</span>
          <span className="ws-pill ws-pill-jade">Practice Graph {graph?.version_tag}</span>
        </div>
        <div className="ws-top-actions">
          <button type="button" className={role === "locum" ? "v2-btn-primary" : "v2-btn-secondary"} onClick={() => { setRole("locum"); setView("my_work"); }}>Locum Dr. Sarah</button>
          <button type="button" className={role === "nurse" ? "v2-btn-primary" : "v2-btn-secondary"} onClick={() => { setRole("nurse"); setView("clinic_team"); }}>Nurse Rachel</button>
          <button type="button" className="v2-btn-secondary" onClick={togglePrestage}>Pre-stage Plan: {prestageEnabled ? "ON" : "OFF"}</button>
          <button type="button" className={showXray ? "v2-btn-jade" : "v2-btn-secondary"} onClick={() => setShowXray((x) => !x)} data-testid="toggle-architecture-xray">
            {showXray ? "Hide Architecture X-Ray" : "Architecture X-Ray"}
          </button>
          <button type="button" className="v2-text-btn" onClick={async () => { await v2Api.workspaceReset(); setCheckouts({}); setGoalOverrides({}); setBanner(null); await refresh(); }}>Reset</button>
        </div>
      </header>

      <div className={`ws-main ${showXray ? "has-xray" : ""}`}>
        {/* SIDEBAR */}
        <aside className="ws-sidebar">
          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Channels</span></div>
            <button type="button" className={`ws-nav-btn ${view === "my_work" ? "is-active" : ""}`} onClick={() => setView("my_work")}>
              <span># my-work</span><span className="ws-badge">{data.counts.needs_decision}</span>
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
            <div className="ws-nav-heading"><span>Running · Today</span><span>{data.counts.running}</span></div>
            {data.grouped.running.length === 0 ? (
              <div style={{ padding: "4px 8px", fontSize: 12, color: "var(--ink-3)", fontFamily: "var(--font-mono)" }}>None triggered today</div>
            ) : data.grouped.running.map((c: any) => (
              <button key={c.id} type="button" className={`ws-nav-btn ${view === c.id ? "is-active" : ""}`} onClick={() => openCase(c.id)}>
                <div style={{ minWidth: 0 }}>
                  <div>{c.patient_name} ({c.patient_age_sex})</div>
                  <div className="ws-nav-sub">{c.baton === "with_nurse" ? "With Nurse Rachel" : "Dispatched today"}</div>
                </div>
              </button>
            ))}
          </div>

          <div className="ws-nav-group">
            <div className="ws-nav-heading"><span>Scheduled / Waiting</span><span>{data.counts.scheduled}</span></div>
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
          {banner && <div className="v2-inline-warn" style={{ marginTop: 0 }}>{banner}</div>}

          {view === "my_work" && (
            <div style={{ maxWidth: 820, display: "flex", flexDirection: "column", gap: 14 }}>
              <h1 className="ws-ai-title"># my-work</h1>
              {data.grouped.needs_decision.length === 0 && <div className="ws-card">All pending decisions cleared.</div>}
              {data.grouped.needs_decision.map((c: any) => (
                <div key={c.id} className="ws-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>{c.patient_name} ({c.patient_age_sex}) · {c.next_checkpoint_label}</span>
                    {c.summary?.alerts?.[0] && <span className="ws-pill ws-pill-amber">{c.summary.alerts[0].label}</span>}
                  </div>
                  <h2 className="ws-ai-title" style={{ fontSize: 18 }}>{c.ai_title}</h2>
                  <p className="ws-lead">{c.summary?.issues?.[0]?.assessment || c.lead_line}</p>
                  <div><button type="button" className="v2-btn-primary" onClick={() => openCase(c.id)}>Open Case →</button></div>
                </div>
              ))}
            </div>
          )}

          {view === "clinic_team" && (
            <div style={{ maxWidth: 820, display: "flex", flexDirection: "column", gap: 14 }}>
              <h1 className="ws-ai-title"># clinic-team</h1>
              {data.team_handoffs.length === 0 ? (
                <div className="ws-card"><div className="ws-lead">No open team tasks. Tasks delegated between doctors and nurses appear here.</div></div>
              ) : data.team_handoffs.map((h: any) => (
                <div key={h.id} className="ws-card">
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600 }}>{h.route}</span>
                    <span className={`ws-pill ${h.status === "quarantined_nurse" ? "ws-pill-amber" : h.status === "done" ? "ws-pill-jade" : ""}`}>
                      {h.status === "quarantined_nurse" ? "ID Verification Needed" : h.status === "done" ? "Done" : "Open"}
                    </span>
                  </div>
                  <div>{h.task}</div>
                  {h.case_id && h.status === "pending_nurse" && (
                    <div><button type="button" className="v2-btn-primary" onClick={() => runTick(h.case_id, "nurse_completed")}>Mark Complete →</button></div>
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
                    <button key={q} type="button" className="v2-btn-secondary" onClick={() => runPlaybookQuery(q)}>{q}</button>
                  ))}
                </div>
                {playbookAns && (
                  <div className="ws-attachment">
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>
                      Practice Graph {playbookAns.graph_version_tag} · Approved by {playbookAns.approved_by}
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>{(playbookAns.answer_bullets || []).map((b: string, i: number) => <li key={i}>{b}</li>)}</ul>
                  </div>
                )}
              </div>
              {data.curator_proposals.map((p: any) => (
                <div key={p.id} className="ws-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600 }}>Clinic Curator · Observed {p.observed_count}×</span>
                    <span className={`ws-pill ${p.status === "approved" ? "ws-pill-jade" : "ws-pill-amber"}`}>
                      {p.status === "approved" ? `Published ${p.published_graph_version}` : "Lead GP Review"}
                    </span>
                  </div>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{p.title}</div>
                  <div className="ws-lead">{p.rationale}</div>
                  {p.status === "pending_lead_gp" && (
                    <div style={{ display: "flex", gap: 8 }}>
                      <button type="button" className="v2-btn-primary" disabled={busy} onClick={() => runCuratorDecision(p.id, "approve")}>Approve &amp; Publish to Practice Graph</button>
                      <button type="button" className="v2-btn-secondary" disabled={busy} onClick={() => runCuratorDecision(p.id, "dismiss")}>Dismiss</button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ACTIVE CASE VIEW */}
          {activeCase && (
            <div className="ws-case-grid">
              <div className="ws-col">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                  <div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>{activeCase.patient_name} ({activeCase.patient_age_sex})</div>
                    <h1 className="ws-ai-title" style={{ marginTop: 3 }}>{activeCase.ai_title}</h1>
                  </div>
                  <span className="ws-pill" style={{ flexShrink: 0 }}>{activeCase.state === "running" ? "Running · Today" : activeCase.state.replace("_", " ")}</span>
                </div>

                {activeCase.traces?.filter((t: any) => t.can_undo).map((t: any) => (
                  <div key={t.id} className="ws-undo-bar">
                    <span>Syncing confirmed plan to Clinic EMR in {t.undo_seconds_left}s — {t.doctor_outcome_summary}</span>
                    <button type="button" className="v2-btn-secondary" onClick={() => runUndo(activeCase.id, t.id)}>Undo</button>
                  </div>
                ))}

                {/* Origin & Elapsed Strip (e.g. Lee Ming Kit) */}
                {activeCase.summary?.origin_strip && (
                  <div className="ws-wait-banner">
                    <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                      <span className="ws-amber-dot" style={{ marginTop: 5 }} />
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 13.5 }}>{activeCase.summary.origin_strip.why}</div>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
                          Origin: {activeCase.summary.origin_strip.from} · {activeCase.summary.origin_strip.chase}
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="v2-btn-secondary"
                      onClick={() => toggleIssueOrder(activeCase.id, { id: "lee_chase", type: "nurse", issue: "1. Borderline LDL", checked: true, assignee: "Nurse Rachel", task: "Phone chase to confirm Feb fasting lipid slot", due: "Today" })}
                    >
                      + Nurse to chase
                    </button>
                  </div>
                )}

                {/* Medication Comparison Widget (Chan Tai Man — no awkward top CTA) */}
                {activeCase.summary?.med_diff && (
                  <div className="ws-card">
                    <div className="ws-section-kicker">Medication Comparison</div>
                    <div className="ws-med-diff">
                      <div style={{ border: "1.5px solid var(--ink)", borderRadius: 8, padding: "10px 12px", background: "var(--subtle)" }}>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700, marginBottom: 4 }}>QEH Discharge (22 Nov)</div>
                        {activeCase.summary.med_diff.qeh.map((m: string, i: number) => <div key={i}>• {m}</div>)}
                      </div>
                      <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", color: "var(--ink-2)" }}>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Clinic EMR Active List (14 Sep)</div>
                        {activeCase.summary.med_diff.clinic.map((m: string, i: number) => <div key={i}>• {m}</div>)}
                      </div>
                    </div>
                  </div>
                )}

                {/* Issues & Assessment (Idempotent toggle chips -> Management Plan Checkout) */}
                <div className="ws-card">
                  <div className="ws-section-kicker">Issues &amp; Assessment</div>
                  {(activeCase.summary?.issues || []).map((iss: any) => (
                    <div key={iss.id || iss.name} className="ws-issue-box">
                      <div className="ws-issue-title">{iss.name}</div>
                      <div><strong style={{ color: "var(--ink-2)" }}>History:</strong> {iss.subjective}</div>
                      {iss.red_flags && <div><strong style={{ color: "var(--ink-2)" }}>Red flags:</strong> {iss.red_flags}</div>}
                      {iss.reasoning && <div><strong style={{ color: "var(--ink-2)" }}>Clinical reasoning:</strong> {iss.reasoning}</div>}
                      <div><strong style={{ color: "var(--ink-2)" }}>Assessment:</strong> {iss.assessment}</div>
                      <div className="ws-issue-actions">
                        {(iss.suggested_orders || []).map((ord: CheckoutOrder) => {
                          const match = activeOrders.find((x) => x.id === ord.id);
                          const isChecked = Boolean(match?.checked);
                          return (
                            <button
                              key={ord.id}
                              type="button"
                              className={isChecked ? "v2-btn-primary" : "v2-btn-secondary"}
                              onClick={() => toggleIssueOrder(activeCase.id, ord)}
                            >
                              {isChecked ? "✓" : "+"} {summarizeOrder(ord)}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>

                {/* Full Blood Panel + Trend Graph + Source Attachment */}
                {(activeCase.summary?.lab_panel || activeCase.summary?.trend_chart) && (
                  <div className="ws-card">
                    {activeCase.summary?.lab_panel && (
                      <>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                          <span className="ws-section-kicker">Investigations</span>
                          <span className="ws-attachment-link">📎 {activeCase.summary.lab_panel.source}</span>
                        </div>
                        <div className="ws-lab-grid">
                          {activeCase.summary.lab_panel.rows.map((r: any, i: number) => (
                            <div key={i} className={`ws-lab-cell ${r.flag === "LOW" || r.flag === "HIGH" ? "is-abnormal" : ""}`}>
                              <div style={{ color: "var(--ink-3)", fontSize: 11.5 }}>{r.test}</div>
                              <div style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 13.5, marginTop: 2 }}>
                                {r.val} {(r.flag === "LOW" || r.flag === "HIGH") && <span className="ws-pill ws-pill-amber" style={{ marginLeft: 4 }}>{r.flag}</span>}
                              </div>
                              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>Ref {r.ref}</div>
                            </div>
                          ))}
                        </div>
                      </>
                    )}
                    {activeCase.summary?.trend_chart && (
                      <TrendSvg
                        chart={activeCase.summary.trend_chart}
                        goalSys={goalOverrides[activeCase.id]?.sys || activeCase.goal?.target_sys}
                        goalDia={goalOverrides[activeCase.id]?.dia || activeCase.goal?.target_dia}
                      />
                    )}
                    {activeCase.case_key === "chan" && (activeCase.summary?.attachments || []).map((att: any) => (
                      <div key={att.id} className="ws-attachment">
                        <span className="ws-attachment-link">📎 {att.label}</span>
                        <blockquote className="ws-quote">“{att.quote}”</blockquote>
                      </div>
                    ))}
                  </div>
                )}

                {/* MANAGEMENT PLAN CHECKOUT (Idempotent · Grouped by Issue · Polymorphic · / Slash Autofill) */}
                <div className="ws-checkout-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--line)", paddingBottom: 10 }}>
                    <div>
                      <div className="ws-section-kicker" style={{ color: "var(--ink)" }}>Management Plan Checkout</div>
                      <div style={{ fontSize: 12.5, color: "var(--ink-3)", marginTop: 2 }}>Tick staged items, edit inline, or type <code>/</code> to add from the clinical order catalog</div>
                    </div>
                    <span className="ws-pill">{selectedCount} selected</span>
                  </div>

                  {Object.keys(groupedCheckout).length === 0 ? (
                    <div style={{ padding: 12, borderRadius: 8, border: "1px dashed var(--line-strong)", fontSize: 13, color: "var(--ink-3)" }}>
                      Plan is empty. Click <code>+</code> on any issue above, add a Preventive Care item on the right, or type <code>/</code> below.
                    </div>
                  ) : (
                    Object.entries(groupedCheckout).map(([issueName, entries]) => (
                      <div key={issueName} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, color: "var(--ink-3)" }}>{issueName}</div>
                        {entries.map(({ item, idx }) => (
                          <div key={item.id} className={`ws-order-item ${item.checked ? "is-checked" : "is-dim"}`}>
                            <input
                              type="checkbox"
                              checked={item.checked}
                              onChange={() => updateCaseCheckout(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, checked: !x.checked } : x)))}
                              style={{ marginTop: 4 }}
                            />
                            <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                                <span className="ws-pill">{item.type.toUpperCase()}</span>
                                <button type="button" className="v2-text-btn" style={{ padding: "2px 6px", fontSize: 12 }} onClick={() => updateCaseCheckout(activeCase.id, (cur) => cur.filter((_, i) => i !== idx))}>Remove</button>
                              </div>

                              {item.type === "cert" && (
                                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                                  <strong>Sick Leave Certificate</strong>
                                  {[1, 2, 3].map((d) => (
                                    <button
                                      key={d}
                                      type="button"
                                      className={item.daysOff === d ? "v2-btn-primary" : "v2-btn-secondary"}
                                      style={{ padding: "3px 8px", fontSize: 12 }}
                                      onClick={() => updateCaseCheckout(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, daysOff: d, checked: true } : x)))}
                                    >
                                      {d}d
                                    </button>
                                  ))}
                                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>{item.dates}</span>
                                  <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--ink-2)" }}>
                                    <input
                                      type="checkbox"
                                      checked={Boolean(item.omitDx)}
                                      onChange={(e) => updateCaseCheckout(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, omitDx: e.target.checked, checked: true } : x)))}
                                    />
                                    Omit employer diagnosis
                                  </label>
                                </div>
                              )}

                              {item.type === "rx" && (
                                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                                    <input className="ws-text-input" style={{ padding: "5px 8px", fontWeight: 600 }} value={item.drug || ""} onChange={(e) => updateCaseCheckout(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, drug: e.target.value, checked: true } : x)))} />
                                    <input className="ws-text-input" style={{ padding: "5px 8px", maxWidth: 165, fontFamily: "var(--font-mono)", fontSize: 12 }} value={item.freq || ""} onChange={(e) => updateCaseCheckout(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, freq: e.target.value, checked: true } : x)))} />
                                  </div>
                                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--jade)" }}>✓ {item.note}</div>
                                </div>
                              )}

                              {item.type === "lab" && (
                                <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                                  <strong>{item.testName}</strong>
                                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--ink-3)" }}>{item.lab} · {item.when}</span>
                                  <label style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}>
                                    <input
                                      type="checkbox"
                                      checked={Boolean(item.fasting)}
                                      onChange={(e) => updateCaseCheckout(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, fasting: e.target.checked, checked: true } : x)))}
                                    />
                                    8h Fasting
                                  </label>
                                </div>
                              )}

                              {item.type === "goal" && (
                                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                  <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 600 }}>
                                    <span>Blood Pressure Target</span>
                                    <span style={{ fontFamily: "var(--font-mono)" }}>&lt; {item.targetSys}/{item.targetDia} mmHg</span>
                                  </div>
                                  <input
                                    type="range"
                                    min={120}
                                    max={145}
                                    step={5}
                                    value={item.targetSys || 135}
                                    onChange={(e) => {
                                      const sys = Number(e.target.value);
                                      const dia = Math.round((sys * 0.62) / 5) * 5;
                                      setGoalOverrides((g) => ({ ...g, [activeCase.id]: { sys, dia } }));
                                      updateCaseCheckout(activeCase.id, (cur) => cur.map((x, i) => (i === idx ? { ...x, targetSys: sys, targetDia: dia, checked: true } : x)));
                                    }}
                                  />
                                </div>
                              )}

                              {(item.type === "imaging" || item.type === "refer" || item.type === "nurse" || item.type === "book") && (
                                <div>{summarizeOrder(item)}</div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    ))
                  )}

                  {/* / Slash-Command Autofill Bar */}
                  <div>
                    <div className="ws-input-row">
                      <input
                        className="ws-text-input"
                        style={{ fontFamily: "var(--font-mono)", fontSize: 12.5 }}
                        value={slashInput}
                        onChange={(e) => setSlashInput(e.target.value)}
                        placeholder="Type / for /prescribe, /order, /cert, /imaging, /refer, /goal, /nurse, /book…"
                      />
                      <button type="button" className="v2-btn-secondary" onClick={() => setSlashInput(slashInput.startsWith("/") ? "" : "/")}>+ / Add</button>
                    </div>
                    {showSlashMenu && (
                      <div className="ws-slash-menu">
                        {(filteredSlash.length ? filteredSlash : orderCatalog).map((sc: any) => (
                          <button key={sc.cmd} type="button" className="ws-slash-item" onClick={() => applySlashCmd(activeCase.id, sc.cmd)}>
                            <span><strong style={{ fontFamily: "var(--font-mono)" }}>{sc.cmd}</strong> · {sc.label}</span>
                            <span style={{ color: "var(--ink-3)", fontSize: 12 }}>{sc.desc}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Clean Commit Bar (Zero "Irreversible" wording · Sole EMR SoT sync footer) */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--ink-3)" }}>
                      Writes compact confirmed orders to Central Medical EMR (Sole SoT)
                    </span>
                    <button type="button" className="v2-btn-primary" disabled={busy || selectedCount === 0} onClick={() => signCheckoutPlan(activeCase.id)}>
                      Confirm &amp; Sign Plan ({selectedCount})
                    </button>
                  </div>
                </div>

                {/* Timeline & Folded Agent Steps */}
                <div className="ws-card">
                  <div className="ws-section-kicker">Timeline</div>
                  <div className="ws-timeline">
                    {(activeCase.timeline || []).map((item: any, idx: number) => {
                      const matchedTrace = item.trace_id ? activeCase.traces?.find((t: any) => t.id === item.trace_id) : null;
                      const isOpen = matchedTrace ? Boolean(expandedTraces[matchedTrace.id]) : false;
                      return (
                        <div key={idx} className="ws-tl-item">
                          <div><span className="ws-tl-date">{item.date}</span><span>{item.label}</span></div>
                          {matchedTrace && (
                            <div className="ws-trace-fold" style={{ marginTop: 6 }}>
                              <button type="button" className="ws-trace-btn" onClick={() => setExpandedTraces((m) => ({ ...m, [matchedTrace.id]: !m[matchedTrace.id] }))}>
                                <span>{isOpen ? "▾" : "▸"} {matchedTrace.folded_steps?.length || 0} agent steps</span>
                                <span>{matchedTrace.status === "undo_window" ? "Undo window" : "Synced to EMR"}</span>
                              </button>
                              {isOpen && (
                                <div className="ws-trace-steps">
                                  {(matchedTrace.folded_steps || []).map((st: any, j: number) => <div key={j}><strong>{st.tool}</strong> — {st.detail}</div>)}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>

              {/* STANDARD RIGHT RAIL: Safety Alerts + Always-On Preventive Care + Scheduled + Patient Chat */}
              <aside className="ws-col">
                <div className="ws-card">
                  <div className="ws-section-kicker">Safety &amp; Alerts</div>
                  {(activeCase.summary?.alerts || []).map((a: any, i: number) => (
                    <div key={i} className={`ws-alert-item ${a.level === "high" ? "is-high" : ""}`}>
                      <div style={{ fontWeight: 600 }}>{a.label}</div>
                      <div style={{ color: "var(--ink-2)", fontSize: 12.5, marginTop: 2 }}>{a.detail}</div>
                    </div>
                  ))}
                </div>

                {/* Always-On Preventive Care Layer */}
                {activeCase.summary?.preventive_care?.length > 0 && (
                  <div className="ws-card">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <span className="ws-section-kicker">Preventive Care</span>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>Age &amp; Risk</span>
                    </div>
                    {activeCase.summary.preventive_care.map((p: any) => {
                      const inPlan = activeOrders.some((x) => x.id === p.order?.id && x.checked);
                      return (
                        <div key={p.id} className="ws-alert-item" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                            <strong>{p.title}</strong>
                            <span className="ws-pill">{p.badge}</span>
                          </div>
                          <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>{p.detail}</div>
                          <div>
                            <button
                              type="button"
                              className={inPlan ? "v2-btn-primary" : "v2-btn-secondary"}
                              style={{ padding: "4px 10px", fontSize: 12 }}
                              onClick={() => togglePreventiveOrder(activeCase.id, p.order)}
                            >
                              {inPlan ? "✓ In Plan Checkout" : "+ Add to Plan"}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                <div className="ws-card">
                  <div className="ws-section-kicker">Scheduled &amp; Upcoming</div>
                  {(activeCase.summary?.scheduled_tasks || []).map((t: any, i: number) => (
                    <div key={i} className="ws-alert-item" style={{ display: "flex", gap: 8 }}>
                      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--ink-3)" }}>{t.status === "booked" ? "📅" : "●"}</span>
                      <span>{t.label}</span>
                    </div>
                  ))}
                </div>

                {activeCase.summary?.original_chat?.length > 0 && (
                  <div className="ws-card">
                    <button type="button" className="v2-text-btn" style={{ padding: 0, display: "flex", justifyContent: "space-between", width: "100%", fontWeight: 600 }} onClick={() => setShowPatientChat((s) => !s)}>
                      <span>{activeCase.summary.patient_chat_label}</span>
                      <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{showPatientChat ? "Hide" : "Open"}</span>
                    </button>
                    {showPatientChat && (
                      <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 8, borderTop: "1px solid var(--line)" }}>
                        {activeCase.summary.original_chat.map((m: any, i: number) => (
                          <div key={i} style={{ fontSize: 12.5, background: "var(--subtle)", padding: "7px 9px", borderRadius: 6 }}>
                            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)", fontWeight: 600 }}>{m.actor} · {m.time}</div>
                            <div style={{ marginTop: 2 }}>{m.text}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </aside>
            </div>
          )}
        </main>

        {/* HEAD OF AI ARCHITECTURE X-RAY DRAWER */}
        {showXray && (
          <aside className="ws-xray" data-testid="architecture-xray-drawer">
            <div className="ws-xray-card">
              <div className="ws-xray-title">1. Practice Knowledge Graph (Runtime Brain)</div>
              <div>Active: <strong>{graph?.version_tag}</strong> · Hash <code>{graph?.content_hash}</code></div>
              <div>{graph?.stats?.total_nodes} pathway nodes · {graph?.stats?.order_primitives} typed /order primitives · {graph?.stats?.preventive_rules} preventive rules</div>
            </div>
            <div className="ws-xray-card">
              <div className="ws-xray-title">2. Dual-Store Persistence (Sole EMR SoT)</div>
              <div>{data.xray?.dual_store?.clinical_sot}</div>
              <div>{data.xray?.dual_store?.agent_memory_store}</div>
              <div>Additive EMR Writebacks: {data.xray?.dual_store?.emr_addenda_log?.length || 0}</div>
            </div>
            <div className="ws-xray-card">
              <div className="ws-xray-title">3. Live Engineering Probes</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <button type="button" className="ws-xray-btn" onClick={triggerMismatchProbe}>Test 3-Pt ID Mismatch → Nurse</button>
                {activeCase?.case_key === "wong" && (
                  <button type="button" className="ws-xray-btn" onClick={() => runTick(activeCase.id, "bp_threshold_breach")}>Trigger BP 146/92 Wake</button>
                )}
              </div>
            </div>
          </aside>
        )}
      </div>

      {/* BOTTOM-RIGHT PROACTIVE EPHEMERAL REGISTRAR DOCK (Sam) */}
      {activeCase && (
        <div className="ws-sam-dock">
          {!samOpen ? (
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button
                type="button"
                className="ws-sam-pill-btn"
                onClick={() => { setSamOpen(true); setSamReadMap((m) => ({ ...m, [activeCase.id]: true })); }}
              >
                <span>{aiName} · Clinical Assistant</span>
                {!samReadMap[activeCase.id] && activeCase.summary?.sam_proactive_insight && (
                  <span style={{ background: "#FFFFFF", color: "var(--ink)", borderRadius: 999, padding: "1px 7px", fontFamily: "var(--font-mono)", fontSize: 11.5 }}>
                    1 insight
                  </span>
                )}
              </button>
            </div>
          ) : (
            <div className="ws-sam-panel">
              <div style={{ padding: "10px 14px", background: "var(--subtle)", borderBottom: "1px solid var(--line)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{aiName} · Clinical Assistant</div>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)" }}>Ephemeral chat · not written to EMR record</div>
                </div>
                <button type="button" className="v2-btn-secondary" style={{ padding: "3px 8px", fontSize: 12 }} onClick={() => setSamOpen(false)}>Close</button>
              </div>
              <div style={{ padding: 12, maxHeight: 260, overflowY: "auto", display: "flex", flexDirection: "column", gap: 8, background: "var(--staff-bg)" }}>
                {activeCase.summary?.sam_proactive_insight && (
                  <div className="ws-msg-bubble is-agent" style={{ marginRight: 10 }}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)", fontWeight: 600 }}>{aiName} · Proactive insight</div>
                    <div style={{ marginTop: 2 }}>{activeCase.summary.sam_proactive_insight}</div>
                  </div>
                )}
                {(activeCase.summary?.agent_thread || []).map((m: any, idx: number) => (
                  <div key={idx} className={`ws-msg-bubble ${m.who === "doctor" ? "is-doctor" : "is-agent"}`}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--ink-3)", fontWeight: 600 }}>{m.actor}</div>
                    <div style={{ marginTop: 2 }}>{m.text}</div>
                  </div>
                ))}
              </div>
              <form
                onSubmit={(e) => { e.preventDefault(); sendSamMessage(activeCase.id); }}
                style={{ padding: 10, borderTop: "1px solid var(--line)", display: "flex", gap: 6, background: "var(--paper)" }}
              >
                <input
                  className="ws-text-input"
                  style={{ padding: "7px 10px", fontSize: 12.5 }}
                  value={samInput}
                  onChange={(e) => setSamInput(e.target.value)}
                  placeholder="e.g. Change sick leave to 3 days or add TSH…"
                />
                <button type="submit" className="v2-btn-primary" style={{ padding: "6px 12px", fontSize: 12.5 }} disabled={busy || !samInput.trim()}>Send</button>
              </form>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
