import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import QRCode from "qrcode";
import {
  Activity,
  AlertTriangle,
  Brain,
  CheckCircle2,
  ClipboardList,
  Cpu,
  ExternalLink,
  FileText,
  Lock,
  QrCode,
  RefreshCw,
  ShieldAlert,
  Smartphone,
  Terminal,
  Wrench,
} from "lucide-react";
import { v2Api } from "../api";
import type { EmrOverview, EmrPatient, ToolCallRecord } from "../api";
import { Paper } from "../shared/Paper";
import "./emr.css";

const WORKFLOW_STEPS = [
  { key: "qr_scanned", label: "1. QR Scan" },
  { key: "intake_submitted", label: "2. URTI Intake" },
  { key: "nurse_ready", label: "3. Nurse Prep" },
  { key: "doctor_signed", label: "4. Doctor Sign" },
  { key: "closed", label: "5. EMR + Phone" },
];

function stageRank(stage: string): number {
  switch (stage) {
    case "qr_scanned":
      return 1;
    case "intake_submitted":
      return 2;
    case "nurse_ready":
      return 3;
    case "doctor_signed":
      return 5;
    default:
      return 0;
  }
}

function authorityTagClass(auth: string): string {
  if (auth === "BLOCKED") return "emr-tag emr-tag-red";
  if (auth === "SIGNED_WRITE") return "emr-tag emr-tag-jade";
  if (auth === "CDS") return "emr-tag emr-tag-amber";
  if (auth === "MEMORY") return "emr-tag emr-tag-purple";
  if (auth === "DRAFT") return "emr-tag emr-tag-blue";
  return "emr-tag emr-tag-muted";
}

export function EmrHarnessApp() {
  const [data, setData] = useState<EmrOverview | null>(null);
  const [emrMode, setEmrMode] = useState<"live" | "seeded">(() =>
    new URLSearchParams(window.location.search).get("mode") === "live" ? "live" : "seeded"
  );
  const [selectedId, setSelectedId] = useState<string>("pat_lam_urti_demo");
  const [emrTab, setEmrTab] = useState<"overview" | "docs" | "encounters" | "audit">("overview");
  const [inspectorTab, setInspectorTab] = useState<"tools" | "memory" | "context">("tools");
  const [sseStatus, setSseStatus] = useState<"connecting" | "live" | "reconnecting">("connecting");
  const [publicBaseUrl, setPublicBaseUrl] = useState<string>(() => window.location.origin);
  const [editingBaseUrl, setEditingBaseUrl] = useState(false);
  const [qrDataUrl, setQrDataUrl] = useState<string>("");
  const [expandedToolIds, setExpandedToolIds] = useState<Record<string, boolean>>({});
  const [selectedDocIdx, setSelectedDocIdx] = useState(0);
  const [flashMsg, setFlashMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const emrModeRef = useRef(emrMode);
  emrModeRef.current = emrMode;

  const loadOverview = useCallback(async (pid?: string, modeOverride?: "live" | "seeded") => {
    const target = pid || selectedIdRef.current;
    const queryPid = target.startsWith("lb_") ? undefined : target;
    const m = modeOverride || emrModeRef.current;
    const res = await v2Api.emrOverview(queryPid, m);
    setData(res);
  }, []);

  useEffect(() => {
    void loadOverview(selectedId, emrMode);
    setSelectedDocIdx(0);
  }, [selectedId, emrMode, loadOverview]);

  // Real-time SSE sync across Phone (/p/chat), Doctor Workspace (/workspace), and Clinic EMR (/emr)
  useEffect(() => {
    let es: EventSource | null = null;
    let closed = false;
    let retryTimer: number | null = null;

    const connect = () => {
      if (closed) return;
      es = new EventSource("/api/v1/live/stream");
      const refreshOn = (evName: string) => {
        es?.addEventListener(evName, (evt: MessageEvent) => {
          try {
            const parsed = JSON.parse(evt.data);
            if (evName === "phone.connected") {
              setFlashMsg(`Real phone connected via QR · ${parsed.data?.patient_name || "Patient"}`);
            } else if (evName === "emr.mutated") {
              setFlashMsg(`Live EMR update: ${parsed.data?.summary || parsed.data?.entity || "record updated"}`);
            } else if (evName === "staff.queue_updated") {
              setFlashMsg(`Clinic workflow: ${parsed.data?.stage || "updated"}`);
            } else if (evName === "tool.call" && parsed.data?.authority === "BLOCKED") {
              setFlashMsg(`Guardrail enforced: ${parsed.data?.tool} BLOCKED (Asymmetric Authority)`);
            }
          } catch {
            // ignore malformed event data
          }
          void loadOverview();
        });
      };

      es.addEventListener("stream.connected", () => setSseStatus("live"));
      es.onopen = () => setSseStatus("live");
      [
        "phone.connected",
        "case.updated",
        "agent.step",
        "tool.call",
        "memory.updated",
        "context.assembled",
        "emr.mutated",
        "staff.queue_updated",
        "patient.care_plan_delivered",
        "demo.reset",
      ].forEach(refreshOn);

      es.onerror = () => {
        setSseStatus("reconnecting");
        es?.close();
        if (!closed) {
          retryTimer = window.setTimeout(connect, 2000);
        }
      };
    };

    connect();
    const pollTimer = window.setInterval(() => {
      if (!closed) void loadOverview();
    }, 4000);
    return () => {
      closed = true;
      es?.close();
      if (retryTimer) window.clearTimeout(retryTimer);
      window.clearInterval(pollTimer);
    };
  }, [loadOverview]);

  const selectedPatient: EmrPatient | null = useMemo(() => {
    if (!data) return null;
    return data.patients.find((p) => p.id === selectedId) || data.patients[0] || null;
  }, [data, selectedId]);

  const qrPatientId = useMemo(() => {
    if (selectedPatient && selectedPatient.source !== "loopbench_sim") {
      return selectedPatient.id;
    }
    return data?.hero_patient_id || "pat_lam_urti_demo";
  }, [selectedPatient, data]);

  const phoneQrUrl = useMemo(() => {
    const cleanBase = publicBaseUrl.trim().replace(/\/+$/, "") || window.location.origin;
    return `${cleanBase}/p/qr/${qrPatientId}`;
  }, [publicBaseUrl, qrPatientId]);

  useEffect(() => {
    QRCode.toDataURL(phoneQrUrl, {
      width: 220,
      margin: 1,
      color: { dark: "#14191F", light: "#FFFFFF" },
    })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(""));
  }, [phoneQrUrl]);

  const handleResetUrti = async () => {
    setBusy(true);
    try {
      await v2Api.emrResetUrti(qrPatientId);
      setFlashMsg("URTI demo reset to clean pre-QR state. Ready to scan QR on phone!");
      await loadOverview(qrPatientId);
    } finally {
      setBusy(false);
    }
  };

  const handleAuthorityProbe = async () => {
    setBusy(true);
    try {
      await v2Api.emrSimulateAuthorityProbe(qrPatientId);
      setInspectorTab("tools");
      await loadOverview(qrPatientId);
    } finally {
      setBusy(false);
    }
  };

  const handleCopyLink = async () => {
    try {
      await navigator.clipboard.writeText(phoneQrUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // ignore clipboard restrictions
    }
  };

  if (!data) {
    return (
      <div className="emr-shell">
        <header className="emr-topbar">
          <div className="emr-brand">ClinicSolution X · Clinic EMR</div>
        </header>
        <div style={{ padding: 28 }} className="voice-assistant">
          Loading Hong Kong clinic EMR…
        </div>
      </div>
    );
  }

  if (!selectedPatient) {
    return (
      <div className="emr-shell" data-testid="emr-harness-app">
        <header className="emr-topbar">
          <div className="emr-topbar-left">
            <div className="emr-brand">
              <Activity size={16} color="#34D399" />
              <span>{data.clinic.emr_profile}</span>
              <span className="emr-brand-sub">{data.clinic.region}</span>
            </div>
            <span className="emr-live-pill" data-testid="emr-sse-status">
              <span className="emr-live-dot" />
              {sseStatus === "live" ? "SSE Live Sync" : sseStatus}
            </span>
          </div>
          <div className="emr-topbar-right">
            <button
              type="button"
              className="emr-top-btn"
              onClick={() => setEmrMode(emrMode === "live" ? "seeded" : "live")}
              data-testid="emr-mode-toggle"
            >
              {emrMode === "live" ? "Show All Sim Records" : "Switch to Live Session"}
            </button>
            <a className="emr-top-btn emr-top-btn-jade" href="/workspace" target="_blank" rel="noopener noreferrer">
              <ClipboardList size={13} />
              Doctor Workspace
              <ExternalLink size={11} />
            </a>
          </div>
        </header>
        <div
          style={{
            maxWidth: 560,
            margin: "72px auto",
            padding: "28px 32px",
            background: "#FFFFFF",
            border: "1px solid #E5E7EB",
            borderRadius: 16,
            textAlign: "center",
          }}
          data-testid="emr-blank-waiting"
        >
          <div style={{ display: "inline-flex", padding: 12, borderRadius: 999, background: "#ECFDF5", color: "#059669", marginBottom: 14 }}>
            <Activity size={24} />
          </div>
          <h2 style={{ margin: "0 0 8px", fontSize: 19, color: "#111827" }}>Clinic EMR Ready — Blank Live Session</h2>
          <p style={{ margin: "0 0 18px", fontSize: 14, color: "#6B7280", lineHeight: 1.55 }}>
            No live patient records or doctor-signed EMR addenda yet. Scan the clinic QR code on your phone to begin a patient conversation — charts and additive EMR addenda will populate here in real time (zero private AI chat leak).
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
            <a className="emr-top-btn emr-top-btn-jade" href="/workspace">
              Open Doctor Workspace & QR
            </a>
            <button
              type="button"
              className="emr-top-btn"
              onClick={() => setEmrMode("seeded")}
            >
              Browse All Clinic Records
            </button>
          </div>
        </div>
      </div>
    );
  }

  const livePatients = data.patients.filter((p) => p.source !== "loopbench_sim");
  const loopbenchPatients = data.patients.filter((p) => p.source === "loopbench_sim");
  const agentState = data.agent_state;
  const rank = stageRank(agentState.workflow_stage || "idle");
  const toolCalls: ToolCallRecord[] = [...(agentState.tool_calls || [])].reverse();
  const lastHighlight = agentState.last_emr_highlight?.target || "";

  return (
    <div className="emr-shell" data-testid="emr-harness-app">
      {/* Dedicated Top Bar */}
      <header className="emr-topbar">
        <div className="emr-topbar-left">
          <div className="emr-brand">
            <Activity size={16} color="#34D399" />
            <span>{data.clinic.emr_profile}</span>
            <span className="emr-brand-sub">{data.clinic.region}</span>
          </div>
          <span className="emr-live-pill" data-testid="emr-sse-status">
            <span className="emr-live-dot" />
            {sseStatus === "live" ? "SSE Live Sync" : sseStatus}
          </span>
          <span className="emr-brand-sub" title={data.engine.label}>
            {data.engine.live ? `Live LLM: ${data.engine.model}` : "Tool Harness Engine"}
          </span>
        </div>

        <div className="emr-topbar-right">
          <button
            type="button"
            className="emr-top-btn"
            onClick={() => setEmrMode(emrMode === "live" ? "seeded" : "live")}
            data-testid="emr-mode-toggle"
          >
            {emrMode === "live" ? "Mode: Live Session" : "Mode: All Records"}
          </button>
          <a className="emr-top-btn" href="/workspace" target="_blank" rel="noopener noreferrer">
            <ClipboardList size={13} />
            Doctor Workspace
            <ExternalLink size={11} />
          </a>
          <a className="emr-top-btn" href={`/p/chat`} target="_blank" rel="noopener noreferrer">
            <Smartphone size={13} />
            Patient Phone
            <ExternalLink size={11} />
          </a>
          <button type="button" className="emr-top-btn" disabled={busy} onClick={handleResetUrti} data-testid="emr-reset-urti-btn">
            <RefreshCw size={12} />
            Reset Case
          </button>
        </div>
      </header>

      <div className="emr-grid">
        {/* Column 1: Simulated EMR Patient Roster */}
        <aside className="emr-roster-col" data-testid="emr-patient-roster">
          <div className="emr-roster-group">Live Clinic · Phone Sync ({livePatients.length})</div>
          {livePatients.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`emr-pat-btn ${selectedPatient.id === p.id ? "active" : ""}`}
              onClick={() => setSelectedId(p.id)}
              data-testid={`emr-roster-${p.id}`}
            >
              <div className="emr-pat-row-top">
                <span className="emr-pat-name">{p.name_en}</span>
                {p.phone_connected && <span className="emr-tag emr-tag-jade">Phone</span>}
              </div>
              <div className="emr-pat-sub">
                <span>{p.name_zh}</span>
                <span>· {p.age ? `${p.age}${p.sex}` : p.sex}</span>
                {p.g6pd_deficient && <span className="emr-tag emr-tag-red">G6PD</span>}
              </div>
              <div className="emr-pat-sub">
                <span>{p.badge}</span>
                <span>· {p.open_loops.filter((l) => l.status !== "closed").length} open</span>
              </div>
            </button>
          ))}

          <div className="emr-roster-group">LoopBench Sim Panel ({loopbenchPatients.length})</div>
          {loopbenchPatients.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`emr-pat-btn ${selectedPatient.id === p.id ? "active" : ""}`}
              onClick={() => setSelectedId(p.id)}
              data-testid={`emr-roster-${p.id}`}
            >
              <div className="emr-pat-row-top">
                <span className="emr-pat-name">{p.name_en}</span>
                <span className="emr-tag emr-tag-muted">LB</span>
              </div>
              <div className="emr-pat-sub">
                <span>{p.name_zh}</span>
                <span>· {p.age ? `${p.age}${p.sex}` : p.sex}</span>
                <span>· {p.documents.length} doc{p.documents.length === 1 ? "" : "s"}</span>
              </div>
            </button>
          ))}
        </aside>

        {/* Column 2: Simulated Clinic EMR Chart */}
        <main className="emr-chart-col" data-testid="emr-chart-pane">
          {flashMsg && (
            <div className="emr-flash-banner" data-testid="emr-live-flash">
              <span>{flashMsg}</span>
              <button type="button" className="v2-text-btn" onClick={() => setFlashMsg(null)}>
                Dismiss
              </button>
            </div>
          )}

          <section className={`emr-patient-header ${lastHighlight === "patient_chart" ? "highlight-pulse" : ""}`}>
            <div className="emr-header-top">
              <div>
                <h1 className="emr-patient-title">
                  {selectedPatient.name_en} {selectedPatient.name_zh}
                </h1>
                <div className="emr-meta-line">
                  <span>{selectedPatient.age ? `${selectedPatient.age}y ${selectedPatient.sex}` : selectedPatient.sex}</span>
                  <span>DOB {selectedPatient.dob}</span>
                  <span className="voice-quote">HKID {selectedPatient.hkid_masked}</span>
                  <span>{selectedPatient.employer}</span>
                </div>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <span className={`emr-tag ${selectedPatient.source === "loopbench_sim" ? "emr-tag-muted" : "emr-tag-jade"}`}>
                  {selectedPatient.badge}
                </span>
                {selectedPatient.flu_vax_status === "due_2026_27" && (
                  <span className="emr-tag emr-tag-amber">2026/27 Flu Vax Due</span>
                )}
                {selectedPatient.flu_vax_status === "deferred_acute" && (
                  <span className="emr-tag emr-tag-amber">Flu Vax Deferred 14d (Acute URTI)</span>
                )}
                {selectedPatient.flu_vax_status === "booked" && (
                  <span className="emr-tag emr-tag-jade">Flu Vax Booked</span>
                )}
              </div>
            </div>

            {(selectedPatient.g6pd_deficient || selectedPatient.allergies.length > 0) && (
              <div className="emr-alert-strip">
                <AlertTriangle size={14} />
                {selectedPatient.g6pd_deficient && <strong>G6PD DEFICIENT (Avoid Aspirin / Co-trimoxazole)</strong>}
                {selectedPatient.g6pd_deficient && selectedPatient.allergies.length > 0 && <span>·</span>}
                {selectedPatient.allergies.length > 0 && (
                  <strong>Allergies: {selectedPatient.allergies.join(", ")}</strong>
                )}
              </div>
            )}
          </section>

          <div className="emr-tabs">
            <button type="button" className={`emr-tab ${emrTab === "overview" ? "active" : ""}`} onClick={() => setEmrTab("overview")}>
              Overview &amp; Loops ({selectedPatient.open_loops.length})
            </button>
            <button type="button" className={`emr-tab ${emrTab === "docs" ? "active" : ""}`} onClick={() => setEmrTab("docs")}>
              Labs &amp; Notes ({selectedPatient.documents.length})
            </button>
            <button type="button" className={`emr-tab ${emrTab === "encounters" ? "active" : ""}`} onClick={() => setEmrTab("encounters")}>
              Encounters &amp; Schedule ({selectedPatient.encounters.length + selectedPatient.immunizations.length})
            </button>
            <button type="button" className={`emr-tab ${emrTab === "audit" ? "active" : ""}`} onClick={() => setEmrTab("audit")}>
              PDPO Audit ({data.audit_log.length})
            </button>
          </div>

          {emrTab === "overview" && (
            <>
              {/* Live Signed Clinical Outputs (Rx + MCHK Sick Leave Cert) */}
              {(selectedPatient.certificates.length > 0 || selectedPatient.prescriptions.length > 0) && (
                <section
                  className={`emr-card ${lastHighlight === "signed_orders" ? "highlight-pulse" : ""}`}
                  style={{ borderColor: "#6EE7B7", background: "#F4FBF8" }}
                  data-testid="emr-signed-outputs"
                >
                  <div className="emr-card-title" style={{ color: "#065F46" }}>
                    <span>Signed Clinical Orders Written Back to EMR</span>
                    <span className="emr-tag emr-tag-jade">MCHK Signed</span>
                  </div>
                  {selectedPatient.certificates.map((c) => (
                    <div key={c.id} className="emr-loop-row">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <strong>
                          MCHK Sick-Leave Certificate · {c.days_off} day{c.days_off > 1 ? "s" : ""} ({c.start_date} → {c.end_date})
                        </strong>
                        <span className="voice-quote">{c.mchk_reg_no} · {c.signed_at}</span>
                      </div>
                      <div className="voice-quote" style={{ color: "var(--ink-3)" }}>
                        ICD-10 {c.diagnosis_icd10 || "J06.9"} · Vault SHA256 {c.verification_hash}…
                      </div>
                    </div>
                  ))}
                  {selectedPatient.prescriptions.map((r) => (
                    <div key={r.id} className="emr-loop-row">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <strong>
                          Signed Prescription ({r.items.map((i) => i.display || `${i.drug} ${i.dose_mg || ""}mg`).join(" + ")})
                        </strong>
                        <span className="voice-quote">{r.mchk_reg_no} · {r.signed_at}</span>
                      </div>
                      <div className="voice-assistant" style={{ fontSize: 12 }}>
                        Fulfilment: {r.fulfilment_mode} ({r.fulfilment_status})
                      </div>
                    </div>
                  ))}
                </section>
              )}

              {/* Open & Tracked Follow-Up Loops */}
              <section className={`emr-card ${lastHighlight === "open_loops" || lastHighlight === "proposal_queue" ? "highlight-pulse" : ""}`}>
                <div className="emr-card-title">
                  <span>Clinical Follow-up Loops &amp; Active Queue Items</span>
                  <span className="voice-quote">{selectedPatient.open_loops.length} tracked</span>
                </div>
                {selectedPatient.open_loops.length === 0 ? (
                  <div className="voice-assistant">No open loops on record.</div>
                ) : (
                  selectedPatient.open_loops.map((loop) => (
                    <div key={loop.id} className="emr-loop-row">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                        <strong style={{ fontSize: 13 }}>{loop.title}</strong>
                        <span
                          className={`emr-tag ${
                            loop.status === "closed"
                              ? "emr-tag-jade"
                              : loop.status === "nurse_ready"
                                ? "emr-tag-blue"
                                : loop.status === "deferred"
                                  ? "emr-tag-amber"
                                  : "emr-tag-muted"
                          }`}
                        >
                          {loop.status}
                        </span>
                      </div>
                      <div className="voice-assistant" style={{ fontSize: 12 }}>
                        {loop.source} · {loop.family}
                      </div>
                      {loop.quote && <div className="v2-quote-link" style={{ cursor: "default" }}>“{loop.quote}”</div>}
                    </div>
                  ))
                )}
              </section>

              {/* Key Vitals & Lab Observations */}
              <section className="emr-card">
                <div className="emr-card-title">
                  <span>Structured Measurements &amp; Chart Baseline</span>
                  <span className="voice-quote">LOINC / FHIR R4</span>
                </div>
                <div className="emr-kv-grid">
                  <div className="emr-kv-box">
                    <div className="emr-kv-label">eGFR (CKD-EPI)</div>
                    <div className="emr-kv-val">{selectedPatient.egfr ? `${selectedPatient.egfr} mL/min` : "Not recorded"}</div>
                  </div>
                  <div className="emr-kv-box">
                    <div className="emr-kv-label">Weight</div>
                    <div className="emr-kv-val">{selectedPatient.weight_kg ? `${selectedPatient.weight_kg} kg` : "—"}</div>
                  </div>
                  <div className="emr-kv-box">
                    <div className="emr-kv-label">Active Medications</div>
                    <div className="emr-kv-val">{selectedPatient.current_meds.length ? selectedPatient.current_meds.join(", ") : "None"}</div>
                  </div>
                  {selectedPatient.observations.slice(0, 5).map((o) => (
                    <div key={o.id} className="emr-kv-box">
                      <div className="emr-kv-label">{o.display}</div>
                      <div className="emr-kv-val">{o.value}</div>
                      <div style={{ fontSize: 10.5, color: "var(--ink-3)", marginTop: 2 }}>{o.recorded_at}</div>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}

          {emrTab === "docs" && (
            <section className={`emr-card ${lastHighlight === "documents" ? "highlight-pulse" : ""}`}>
              <div className="emr-card-title">
                <span>Source Clinic Documents &amp; LoopBench Lab Reports</span>
                <span className="voice-quote">Verbatim Provenance</span>
              </div>
              {selectedPatient.documents.length === 0 ? (
                <div className="voice-assistant">No raw lab reports or consult notes attached for this patient.</div>
              ) : (
                <>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
                    {selectedPatient.documents.map((d, idx) => (
                      <button
                        key={d.id}
                        type="button"
                        className={`emr-tab ${selectedDocIdx === idx ? "active" : ""}`}
                        onClick={() => setSelectedDocIdx(idx)}
                      >
                        <FileText size={11} style={{ marginRight: 4 }} />
                        {d.date} · {d.kind.replace("_", " ")}
                      </button>
                    ))}
                  </div>
                  {selectedPatient.documents[selectedDocIdx] && (
                    <Paper
                      title={selectedPatient.documents[selectedDocIdx].title}
                      subtitle={`${selectedPatient.documents[selectedDocIdx].lab_name} · ${selectedPatient.documents[selectedDocIdx].date}`}
                      text={selectedPatient.documents[selectedDocIdx].text}
                      quotes={
                        selectedPatient.documents[selectedDocIdx].highlight_quote
                          ? [{ quote: selectedPatient.documents[selectedDocIdx].highlight_quote }]
                          : []
                      }
                      focusedQuote={selectedPatient.documents[selectedDocIdx].highlight_quote || null}
                    />
                  )}
                </>
              )}
            </section>
          )}

          {emrTab === "encounters" && (
            <>
              <section className={`emr-card ${lastHighlight === "schedule" ? "highlight-pulse" : ""}`}>
                <div className="emr-card-title">
                  <span>Clinic Schedule &amp; Preventive Bookings</span>
                </div>
                {selectedPatient.booked_slot ? (
                  <div className="emr-loop-row">
                    <strong>2026/27 Flu Vaccination Booked · {selectedPatient.booked_slot.label_en}</strong>
                    <div className="voice-assistant">
                      {selectedPatient.booked_slot.provider} · {selectedPatient.booked_slot.location}
                    </div>
                  </div>
                ) : selectedPatient.immunizations.length > 0 ? (
                  selectedPatient.immunizations.map((im) => (
                    <div key={im.id} className="emr-loop-row">
                      <strong>{im.vaccine}</strong>
                      <div className="voice-quote">{im.status} · {im.scheduled_at}</div>
                    </div>
                  ))
                ) : (
                  <div className="voice-assistant">
                    {selectedPatient.flu_vax_status === "deferred_acute"
                      ? "Flu vaccination deferred 14 days by protocol due to active febrile URTI."
                      : "No upcoming preventive slots booked yet."}
                  </div>
                )}
              </section>

              <section className="emr-card">
                <div className="emr-card-title">
                  <span>Clinical Encounters ({selectedPatient.encounters.length})</span>
                </div>
                {selectedPatient.encounters.length === 0 ? (
                  <div className="voice-assistant">No encounters recorded yet.</div>
                ) : (
                  selectedPatient.encounters.map((enc) => (
                    <div key={enc.id} className="emr-loop-row">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                        <strong>
                          {enc.type.replace(/_/g, " ")} · {enc.icd10} / {enc.icpc2}
                        </strong>
                        <span className="emr-tag emr-tag-muted">{enc.status}</span>
                      </div>
                      <div className="voice-quote" style={{ color: "var(--ink-3)" }}>{enc.created_at}</div>
                      <div className="voice-assistant" style={{ whiteSpace: "pre-wrap", fontSize: 12.5 }}>
                        {enc.clinical_note || enc.chief_complaint}
                      </div>
                    </div>
                  ))
                )}
              </section>
            </>
          )}

          {emrTab === "audit" && (
            <section className="emr-card">
              <div className="emr-card-title">
                <span>Append-Only HK PDPO Audit Ledger</span>
                <span className="emr-tag emr-tag-muted">PostgreSQL Trigger Protected</span>
              </div>
              {data.audit_log.map((a) => (
                <div key={a.id} className="emr-loop-row">
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <span className="emr-tool-name">{a.action}</span>
                    <span className="voice-quote">{a.created_at}</span>
                  </div>
                  <div className="voice-assistant" style={{ fontSize: 12 }}>
                    actor: <span className="voice-quote">{a.actor_role}:{a.actor_id}</span> · {a.resource_type} ({a.resource_id})
                  </div>
                </div>
              ))}
            </section>
          )}
        </main>

        {/* Column 3: Live Multi-Agent Harness Inspector */}
        <section className="emr-inspector-col" data-testid="emr-agent-inspector">
          {/* Real Phone QR Sync Header */}
          <div className="emr-qr-panel">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <strong style={{ fontSize: 13, display: "inline-flex", alignItems: "center", gap: 6 }}>
                <QrCode size={14} color="#16695D" />
                Real Phone Sync · Scan to Open Care AI
              </strong>
              <span className={`emr-tag ${agentState.phone_connected ? "emr-tag-jade" : "emr-tag-muted"}`} data-testid="emr-phone-status">
                {agentState.phone_connected ? `Connected ${agentState.phone_connected_at || ""}` : "Waiting for scan"}
              </span>
            </div>

            <div className="emr-qr-row">
              <div className="emr-qr-box" data-testid="emr-qr-code">
                {qrDataUrl ? <img src={qrDataUrl} alt={`QR code for ${qrPatientId}`} /> : <span className="voice-quote">QR</span>}
              </div>
              <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
                  Scans straight into <strong>LAM Wing-Yan (32F · Working-Age URTI)</strong> on your phone — zero login friction.
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <button type="button" className="v2-kbd-chip" onClick={handleCopyLink}>
                    {copied ? "Copied link ✓" : "Copy URL"}
                  </button>
                  <button type="button" className="v2-kbd-chip" onClick={() => setEditingBaseUrl((v) => !v)}>
                    {editingBaseUrl ? "Hide host" : "Set LAN / Tunnel URL"}
                  </button>
                  <button
                    type="button"
                    className="v2-kbd-chip"
                    style={{ borderColor: "var(--red-line)", color: "var(--red)", background: "var(--red-bg)" }}
                    disabled={busy}
                    onClick={handleAuthorityProbe}
                    data-testid="probe-authority-btn"
                  >
                    <Lock size={11} style={{ marginRight: 3 }} />
                    Probe Guardrail
                  </button>
                </div>
              </div>
            </div>

            {editingBaseUrl && (
              <div>
                <div style={{ fontSize: 11, color: "var(--ink-3)", marginBottom: 3 }}>
                  Public HTTPS / Cloud Run / ngrok / LAN IP for physical phone scanning:
                </div>
                <input
                  className="emr-url-input"
                  value={publicBaseUrl}
                  onChange={(e) => setPublicBaseUrl(e.target.value)}
                  placeholder="https://your-service.run.app or http://192.168.1.x:8080"
                />
              </div>
            )}
          </div>

          {/* 5-Step Live Workflow Strip */}
          <div className="emr-workflow-bar" data-testid="emr-workflow-strip">
            {WORKFLOW_STEPS.map((step, i) => {
              const stepNum = i + 1;
              const cls = rank >= stepNum ? "done" : rank + 1 === stepNum ? "current" : "";
              return (
                <div key={step.key} className={`emr-step-pill ${cls}`}>
                  {step.label}
                </div>
              );
            })}
          </div>

          {/* Inspector Sub-Tabs */}
          <div className="emr-inspector-tabs">
            <button
              type="button"
              className={`emr-tab ${inspectorTab === "tools" ? "active" : ""}`}
              onClick={() => setInspectorTab("tools")}
              data-testid="inspector-tab-tools"
            >
              <Wrench size={12} style={{ marginRight: 4 }} />
              Tool Calls ({toolCalls.length})
            </button>
            <button
              type="button"
              className={`emr-tab ${inspectorTab === "memory" ? "active" : ""}`}
              onClick={() => setInspectorTab("memory")}
              data-testid="inspector-tab-memory"
            >
              <Brain size={12} style={{ marginRight: 4 }} />
              Memory ({agentState.memory?.episodic_memory?.length || 0})
            </button>
            <button
              type="button"
              className={`emr-tab ${inspectorTab === "context" ? "active" : ""}`}
              onClick={() => setInspectorTab("context")}
              data-testid="inspector-tab-context"
            >
              <Cpu size={12} style={{ marginRight: 4 }} />
              Context
            </button>
          </div>

          <div className="emr-inspector-body">
            {inspectorTab === "tools" && (
              <>
                {toolCalls.length === 0 ? (
                  <div className="emr-card">
                    <div className="voice-assistant" style={{ fontSize: 12.5 }}>
                      <strong>Ready for live trace.</strong> Scan the QR code above on your phone (or click{" "}
                      <em>Open Phone View</em>) to watch <code>LoopWatcher</code>, <code>SafetySentinel</code>,{" "}
                      <code>CareAgent</code>, and <code>ProtocolCDSAgent</code> invoke real EMR tools in real time.
                    </div>
                  </div>
                ) : (
                  toolCalls.map((tc) => {
                    const expanded = Boolean(expandedToolIds[tc.id]);
                    const cardCls =
                      tc.authority === "BLOCKED"
                        ? "emr-tool-card blocked"
                        : tc.authority === "SIGNED_WRITE"
                          ? "emr-tool-card signed"
                          : "emr-tool-card";
                    return (
                      <div key={tc.id} className={cardCls} data-testid={`tool-call-${tc.tool}`}>
                        <div className="emr-tool-head">
                          <span className="emr-tool-name">{tc.tool}</span>
                          <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
                            <span className={authorityTagClass(tc.authority)}>{tc.authority}</span>
                            <span className="voice-quote" style={{ fontSize: 10.5, color: "var(--ink-3)" }}>
                              {tc.duration_ms}ms
                            </span>
                          </div>
                        </div>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11.5, color: "var(--ink-2)" }}>
                          <span>
                            Agent: <strong>{tc.agent}</strong>
                          </span>
                          <button
                            type="button"
                            className="v2-text-btn"
                            style={{ padding: "1px 6px", fontSize: 11 }}
                            onClick={() => setExpandedToolIds((m) => ({ ...m, [tc.id]: !m[tc.id] }))}
                          >
                            {expanded ? "Hide JSON" : "Inspect I/O"}
                          </button>
                        </div>
                        {tc.authority === "BLOCKED" && (
                          <div style={{ fontSize: 12, color: "var(--red)", display: "flex", gap: 5, alignItems: "flex-start" }}>
                            <ShieldAlert size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                            <span>{tc.result?.error}</span>
                          </div>
                        )}
                        {expanded && (
                          <div>
                            <div className="emr-kv-label" style={{ marginTop: 4 }}>Arguments</div>
                            <pre className="emr-json-pre">{JSON.stringify(tc.args, null, 2)}</pre>
                            <div className="emr-kv-label" style={{ marginTop: 6 }}>Return Payload</div>
                            <pre className="emr-json-pre">{JSON.stringify(tc.result, null, 2)}</pre>
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </>
            )}

            {inspectorTab === "memory" && (
              <>
                <div className="emr-card-title">
                  <span>1. Episodic Clinical Memory (Quote-Verified)</span>
                  <span className="voice-quote">{agentState.memory?.episodic_memory?.length || 0} facts</span>
                </div>
                {(agentState.memory?.episodic_memory || []).length === 0 ? (
                  <div className="voice-assistant" style={{ fontSize: 12.5 }}>
                    No episodic facts written yet — facts populate as the patient scans QR and chats.
                  </div>
                ) : (
                  agentState.memory.episodic_memory.map((f, idx) => (
                    <div key={`${f.key}-${idx}`} className="emr-mem-item">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                        <strong className="voice-quote" style={{ fontSize: 12 }}>{f.key}</strong>
                        <span className="emr-tag emr-tag-jade">
                          <CheckCircle2 size={10} style={{ marginRight: 2 }} />
                          Quote Verified
                        </span>
                      </div>
                      <div style={{ fontSize: 12.5 }}>{f.value}</div>
                      {f.verbatim_quote && (
                        <div className="v2-quote-link" style={{ cursor: "default", fontSize: 11 }}>
                          “{f.verbatim_quote}”
                        </div>
                      )}
                      <div style={{ fontSize: 11, color: "var(--ink-3)" }}>Source: {f.source}</div>
                    </div>
                  ))
                )}

                <div className="emr-card-title" style={{ marginTop: 10 }}>
                  <span>2. Hydrated EMR Chart Memory</span>
                </div>
                <pre className="emr-json-pre">
                  {JSON.stringify(agentState.memory?.emr_memory || { status: "awaiting_qr_scan" }, null, 2)}
                </pre>

                <div className="emr-card-title" style={{ marginTop: 10 }}>
                  <span>3. Active Clinic Protocol Memory</span>
                </div>
                {data.protocols.map((prot) => (
                  <div key={prot.id} className="emr-mem-item">
                    <strong style={{ fontSize: 12 }}>{prot.id} · {prot.name}</strong>
                    <ul style={{ margin: "4px 0 0", paddingLeft: 16, fontSize: 11.5, color: "var(--ink-2)" }}>
                      {prot.rules.map((r, i) => (
                        <li key={i} style={{ marginBottom: 3 }}>{r}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </>
            )}

            {inspectorTab === "context" && (
              <>
                <div className="emr-card">
                  <div className="emr-card-title">
                    <span><Terminal size={11} style={{ marginRight: 4 }} />Runtime &amp; Context Budget</span>
                    {agentState.context_window?.estimated_tokens && (
                      <span className="emr-tag emr-tag-blue">~{agentState.context_window.estimated_tokens} tokens</span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
                    <strong>Engine:</strong> {data.engine.label}
                  </div>
                  {agentState.context_window && (
                    <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 4 }}>
                      Last trigger: <span className="voice-quote">{agentState.context_window.trigger}</span> ·{" "}
                      {agentState.context_window.conversation_turns} turns in window
                    </div>
                  )}
                </div>

                {agentState.context_window ? (
                  <>
                    <div className="emr-card-title" style={{ marginTop: 6 }}>
                      <span>System Prompt &amp; Guardrails</span>
                    </div>
                    <pre className="emr-json-pre">{agentState.context_window.system_prompt}</pre>

                    <div className="emr-card-title" style={{ marginTop: 6 }}>
                      <span>Registered Tool Schemas ({data.tool_schemas.length})</span>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                      {data.tool_schemas.map((t) => (
                        <span key={t.name} className={authorityTagClass(t.authority)} title={t.description}>
                          {t.name} [{t.authority}]
                        </span>
                      ))}
                    </div>

                    <div className="emr-card-title" style={{ marginTop: 8 }}>
                      <span>Assembled Context Payload (JSON)</span>
                    </div>
                    <pre className="emr-json-pre">
                      {JSON.stringify(
                        {
                          trigger: agentState.context_window.trigger,
                          active_protocols: agentState.context_window.active_protocols,
                          emr_memory: agentState.context_window.emr_memory,
                          episodic_memory: agentState.context_window.episodic_memory,
                          conversation_preview: agentState.context_window.conversation_preview,
                          current_input: agentState.context_window.current_input,
                        },
                        null,
                        2,
                      )}
                    </pre>
                  </>
                ) : (
                  <div className="voice-assistant" style={{ fontSize: 12.5 }}>
                    Context window will populate automatically on the first QR scan or patient chat turn.
                  </div>
                )}
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
