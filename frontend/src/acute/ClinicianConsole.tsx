import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertOctagon,
  AlertTriangle,
  Calendar,
  CheckCircle2,
  Clock,
  FileSignature,
  History,
  Lock,
  Pill,
  Plus,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Stethoscope,
  Trash2,
  XCircle,
} from "lucide-react";
import { acuteApi, HK_DIAGNOSES, HK_FORMULARY, tr } from "./api";
import type {
  AuditEventItem,
  AuthSession,
  Lang,
  PrescriptionItem,
  ProposalDetail,
  QueueProposalSummary,
  RxSafetyCheckResponse,
} from "./api";

interface ClinicianConsoleProps {
  lang: Lang;
  session: AuthSession | null;
  onSessionChange: (session: AuthSession) => void;
  onNotify: (msg: string, isError?: boolean) => void;
}

type ConsoleTab = "queue" | "availability" | "audit";

function formatSla(slaIso: string): { label: string; overdue: boolean } {
  const diffMin = Math.round((new Date(slaIso).getTime() - Date.now()) / 60000);
  if (diffMin < 0) {
    const abs = Math.abs(diffMin);
    return {
      label: abs >= 60 ? `Overdue ${Math.floor(abs / 60)}h ${abs % 60}m` : `Overdue ${abs}m`,
      overdue: true,
    };
  }
  if (diffMin < 60) return { label: `Due in ${diffMin}m`, overdue: false };
  return { label: `Due in ${Math.floor(diffMin / 60)}h ${diffMin % 60}m`, overdue: false };
}

function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + Math.max(0, days - 1));
  return d.toISOString().slice(0, 10);
}

const DEFAULT_ROSTER = [
  { day: "Mon–Fri", start: "09:00", end: "12:30", active: true },
  { day: "Mon–Fri", start: "14:00", end: "18:00", active: true },
  { day: "Sat", start: "09:30", end: "13:00", active: true },
  { day: "Evening Acute Flu Clinic", start: "19:00", end: "21:30", active: false },
];

export function ClinicianConsole({ lang, session, onSessionChange, onNotify }: ClinicianConsoleProps) {
  // Login form (if not signed in as clinician)
  const [loginEmail, setLoginEmail] = useState("dr.lam@central-gp.tmrw.hk");
  const [loginPass, setLoginPass] = useState("DemoClinic!2026");
  const [loginBusy, setLoginBusy] = useState(false);

  const [tab, setTab] = useState<ConsoleTab>("queue");
  const [queue, setQueue] = useState<QueueProposalSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProposalDetail | null>(null);
  const [loadingQueue, setLoadingQueue] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Clinician editable decision state
  const [clinicalNote, setClinicalNote] = useState("");
  const [initialDraftNote, setInitialDraftNote] = useState("");
  const [icd10Code, setIcd10Code] = useState("J11.1");
  const [issueCert, setIssueCert] = useState(true);
  const [certDays, setCertDays] = useState(2);
  const [certStart, setCertStart] = useState(() => new Date().toISOString().slice(0, 10));

  // e-Script Builder + Live Safety Check
  const [rxItems, setRxItems] = useState<PrescriptionItem[]>([]);
  const [drugToAdd, setDrugToAdd] = useState("paracetamol");
  const [safetyResult, setSafetyResult] = useState<RxSafetyCheckResponse | null>(null);
  const [safetyBusy, setSafetyBusy] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");

  // Automation-bias friction & escalation
  const openedAtRef = useRef<number>(Date.now());
  const [elapsedSec, setElapsedSec] = useState(0);
  const [frictionRequired, setFrictionRequired] = useState(false);
  const [frictionConfirmed, setFrictionConfirmed] = useState(false);
  const [showEscalateBox, setShowEscalateBox] = useState(false);
  const [escalationReason, setEscalationReason] = useState("");
  const [deciding, setDeciding] = useState(false);

  // Availability & Audit tabs
  const [roster, setRoster] = useState(DEFAULT_ROSTER);
  const [rosterBusy, setRosterBusy] = useState(false);
  const [auditEvents, setAuditEvents] = useState<AuditEventItem[]>([]);
  const [auditBusy, setAuditBusy] = useState(false);

  const isClinician = session?.role === "clinician" || session?.role === "clinic_admin";

  const sortedQueue = useMemo(() => {
    return [...queue].sort((a, b) => {
      if (b.urgency !== a.urgency) return b.urgency - a.urgency;
      return new Date(a.sla_due_at).getTime() - new Date(b.sla_due_at).getTime();
    });
  }, [queue]);

  const loadQueue = async () => {
    if (!isClinician) return;
    setLoadingQueue(true);
    try {
      const list = await acuteApi.clinicianQueue();
      setQueue(list);
      if (list.length > 0 && (!selectedId || !list.some((x) => x.id === selectedId))) {
        const sorted = [...list].sort((a, b) =>
          b.urgency !== a.urgency ? b.urgency - a.urgency : new Date(a.sla_due_at).getTime() - new Date(b.sla_due_at).getTime()
        );
        setSelectedId(sorted[0].id);
      } else if (list.length === 0) {
        setSelectedId(null);
        setDetail(null);
      }
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to load clinician queue", true);
    } finally {
      setLoadingQueue(false);
    }
  };

  useEffect(() => {
    if (isClinician) {
      void loadQueue();
    }
  }, [session?.access_token, isClinician]);

  // Load proposal detail when selection changes
  useEffect(() => {
    if (!isClinician || !selectedId) {
      setDetail(null);
      return;
    }
    setLoadingDetail(true);
    openedAtRef.current = Date.now();
    setElapsedSec(0);
    setFrictionConfirmed(false);
    setOverrideReason("");
    setShowEscalateBox(false);
    setEscalationReason("");
    setSafetyResult(null);

    acuteApi
      .clinicianProposal(selectedId)
      .then((d) => {
        setDetail(d);
        const draft = d.payload?.draft_clinical_note || d.payload?.clinical_note || d.encounter?.clinical_note || d.payload?.summary || "";
        setClinicalNote(draft);
        setInitialDraftNote(draft);
        const diag = d.payload?.suggested_icd10 || d.encounter?.icd10_code || "J11.1";
        setIcd10Code(HK_DIAGNOSES.some((x) => x.icd10 === diag) ? diag : "J11.1");
        setIssueCert(d.urgency < 4);
        setCertDays(d.payload?.recommended_sick_leave_days || 2);
        setRxItems(Array.isArray(d.payload?.suggested_rx) ? d.payload.suggested_rx : []);
        setFrictionRequired(d.urgency >= 4);
      })
      .catch((e) => onNotify(e instanceof Error ? e.message : "Failed to load proposal", true))
      .finally(() => setLoadingDetail(false));
  }, [selectedId, isClinician]);

  // Live elapsed timer for automation-bias check
  useEffect(() => {
    if (!detail) return;
    const id = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - openedAtRef.current) / 1000));
    }, 500);
    return () => window.clearInterval(id);
  }, [detail?.id]);

  // Live Rx safety check whenever rxItems change
  const runRxSafetyCheck = async (patientId: string, items: PrescriptionItem[]) => {
    if (items.length === 0) {
      setSafetyResult(null);
      return;
    }
    setSafetyBusy(true);
    try {
      const res = await acuteApi.clinicianRxSafetyCheck({ patient_id: patientId, items });
      setSafetyResult(res);
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Rx safety check failed", true);
    } finally {
      setSafetyBusy(false);
    }
  };

  useEffect(() => {
    if (!detail?.patient?.id || rxItems.length === 0) {
      setSafetyResult(null);
      return;
    }
    const timer = window.setTimeout(() => {
      void runRxSafetyCheck(detail.patient.id, rxItems);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [detail?.patient?.id, rxItems]);

  // Load clinician audit trail when switching to audit tab
  useEffect(() => {
    if (tab === "audit" && isClinician) {
      setAuditBusy(true);
      acuteApi
        .clinicianAudit()
        .then(setAuditEvents)
        .catch((e) => onNotify(e instanceof Error ? e.message : "Failed to load audit", true))
        .finally(() => setAuditBusy(false));
    }
  }, [tab, isClinician]);

  // Keyboard-first navigation: j/k navigate queue, a approves
  const approveHandlerRef = useRef<() => void>(() => {});
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!isClinician || tab !== "queue") return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "j" && sortedQueue.length > 0) {
        e.preventDefault();
        const idx = sortedQueue.findIndex((q) => q.id === selectedId);
        const next = sortedQueue[Math.min(sortedQueue.length - 1, idx + 1)];
        if (next) setSelectedId(next.id);
      } else if (e.key === "k" && sortedQueue.length > 0) {
        e.preventDefault();
        const idx = sortedQueue.findIndex((q) => q.id === selectedId);
        const prev = sortedQueue[Math.max(0, idx - 1)];
        if (prev) setSelectedId(prev.id);
      } else if (e.key === "a" && detail) {
        e.preventDefault();
        approveHandlerRef.current();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isClinician, tab, sortedQueue, selectedId, detail]);

  const handleLogin = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setLoginBusy(true);
    try {
      const auth = await acuteApi.authLogin(loginEmail.trim(), loginPass);
      onSessionChange({ ...auth, access_token: auth.access_token || auth.token || "" });
      onNotify(tr(lang, "Signed in to Clinician Console.", "已登入醫生審核控制台。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Login failed", true);
    } finally {
      setLoginBusy(false);
    }
  };

  const handleAddDrug = () => {
    const entry = HK_FORMULARY.find((d) => d.key === drugToAdd);
    if (!entry) return;
    setRxItems((prev) => [
      ...prev,
      {
        drug: entry.key,
        display: entry.display,
        display_zh: entry.display_zh,
        rxnorm: entry.rxnorm,
        atc: entry.atc,
        hk_schedule: entry.hk_schedule,
        dose_mg: entry.default_dose_mg,
        frequency: entry.default_frequency,
        days: entry.default_days,
      },
    ]);
  };

  const handleUpdateRxItem = (idx: number, patch: Partial<PrescriptionItem>) => {
    setRxItems((prev) => prev.map((it, i) => (i === idx ? { ...it, ...patch } : it)));
  };

  const handleRemoveRxItem = (idx: number) => {
    setRxItems((prev) => prev.filter((_, i) => i !== idx));
  };

  const selectedDiag = HK_DIAGNOSES.find((d) => d.icd10 === icd10Code) || HK_DIAGNOSES[0];
  const certEnd = addDays(certStart, certDays);

  const hasHardBlock = Boolean(
    safetyResult?.has_hard_block ||
      safetyResult?.status === "hard_block" ||
      (safetyResult?.hard_blocks && safetyResult.hard_blocks.length > 0) ||
      safetyResult?.findings?.some((f) => f.severity.toLowerCase() === "hard_block")
  );
  const requiresOverride = Boolean(
    safetyResult?.requires_override ||
      safetyResult?.status === "override_required" ||
      (safetyResult?.overrides_required && safetyResult.overrides_required.length > 0) ||
      safetyResult?.findings?.some((f) => f.severity.toLowerCase() === "override_required")
  );

  const handleApprove = async () => {
    if (!detail) return;
    const reviewSeconds = Math.max(0.1, (Date.now() - openedAtRef.current) / 1000);
    const needsFriction = detail.urgency >= 4 || reviewSeconds < 3;

    if (needsFriction && !frictionConfirmed) {
      setFrictionRequired(true);
      onNotify(
        tr(
          lang,
          "Automation-bias safety check: please verify the patient evidence and tick the confirmation checkbox before signing.",
          "防自動化偏誤安全確認：簽署前請核實病人證據並剔選獨立核對確認欄。"
        ),
        true
      );
      return;
    }

    if (hasHardBlock) {
      onNotify(
        tr(
          lang,
          "Cannot sign: Cap. 134 Dangerous Drug HARD BLOCK detected. Remove blocked item first.",
          "無法簽署：偵測到香港法例第 134 章危險藥物硬性封鎖，請先移除該藥物。"
        ),
        true
      );
      return;
    }

    if (requiresOverride && overrideReason.trim().length < 10) {
      onNotify(
        tr(
          lang,
          "Clinical override justification (>= 10 characters) is mandatory for flagged allergy/G6PD/interaction/dose alerts.",
          "處方觸發過敏／蠶豆症／交互作用／劑量警告，必須填寫至少 10 字元的醫生臨床覆蓋理由。"
        ),
        true
      );
      return;
    }

    const wasEdited = clinicalNote.trim() !== initialDraftNote.trim();
    setDeciding(true);
    try {
      const res = await acuteApi.clinicianDecide(detail.id, {
        decision: wasEdited ? "edited" : "approved",
        clinical_note: clinicalNote.trim(),
        icd10_code: selectedDiag.icd10,
        icpc2_code: selectedDiag.icpc2,
        issue_certificate: issueCert,
        cert_days_off: issueCert ? certDays : undefined,
        cert_start_date: issueCert ? certStart : undefined,
        cert_end_date: issueCert ? certEnd : undefined,
        rx_items: rxItems,
        override_reason: requiresOverride ? overrideReason.trim() : null,
        review_seconds: Number(reviewSeconds.toFixed(2)),
        rubber_stamp_confirmed: frictionConfirmed,
        edit_diff: wasEdited ? { clinical_note_changed: true } : {},
      });
      onNotify(
        tr(
          lang,
          `Signed & approved (${res.certificate ? "Cert issued" : "No cert"}${res.prescription ? " + e-Script issued" : ""}).`,
          `已完成醫生審核及簽署（${res.certificate ? "已簽發病假紙" : "無病假紙"}${res.prescription ? " + 已簽發電子處方" : ""}）。`
        )
      );
      await loadQueue();
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to sign proposal", true);
    } finally {
      setDeciding(false);
    }
  };

  approveHandlerRef.current = () => void handleApprove();

  const handleEscalateOrReject = async (decision: "escalated" | "rejected") => {
    if (!detail) return;
    if (decision === "escalated" && escalationReason.trim().length < 5) {
      setShowEscalateBox(true);
      onNotify(tr(lang, "Please enter a mandatory clinical escalation / referral reason.", "請填寫必須的升級／轉介理由。"), true);
      return;
    }
    const reviewSeconds = Math.max(0.1, (Date.now() - openedAtRef.current) / 1000);
    setDeciding(true);
    try {
      await acuteApi.clinicianDecide(detail.id, {
        decision,
        clinical_note: clinicalNote.trim(),
        escalation_reason: escalationReason.trim() || "Rejected by reviewing clinician",
        review_seconds: Number(reviewSeconds.toFixed(2)),
        rubber_stamp_confirmed: frictionConfirmed,
      });
      onNotify(
        decision === "escalated"
          ? tr(lang, "Case escalated / referred with audit record.", "個案已升級／轉介並寫入不可篡改審計日誌。")
          : tr(lang, "Proposal rejected.", "已拒絕該項 AI 建議。")
      );
      await loadQueue();
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Decision failed", true);
    } finally {
      setDeciding(false);
    }
  };

  const handleSaveAvailability = async () => {
    setRosterBusy(true);
    try {
      await acuteApi.clinicianAvailability(roster);
      onNotify(tr(lang, "Clinician teleconsult roster saved.", "醫生遙距診症當值表已更新。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to save availability", true);
    } finally {
      setRosterBusy(false);
    }
  };

  // ========================================================================= UNAUTHENTICATED VIEW
  if (!isClinician) {
    return (
      <div className="ac-container-narrow" data-testid="surface-clinician-login">
        <form className="ac-card" onSubmit={(e) => void handleLogin(e)}>
          <div className="ac-card-head">
            <div>
              <h2 className="ac-card-title">
                <Stethoscope size={18} color="var(--ac-teal)" />
                {tr(lang, "HK Registered Clinician Sign-In", "香港註冊西醫登入")}
              </h2>
              <p className="ac-card-sub">
                {tr(
                  lang,
                  "Sign in with your clinic account or use the Demo Control Bar persona switcher above.",
                  "請以診所帳戶登入，或使用頂部示範列一鍵切換醫生帳戶。"
                )}
              </p>
            </div>
          </div>
          <div className="ac-field">
            <label htmlFor="doc-email">Email</label>
            <input id="doc-email" type="email" className="ac-input" data-testid="clinician-login-email" value={loginEmail} onChange={(e) => setLoginEmail(e.target.value)} />
          </div>
          <div className="ac-field">
            <label htmlFor="doc-pass">Password</label>
            <input id="doc-pass" type="password" className="ac-input" data-testid="clinician-login-pass" value={loginPass} onChange={(e) => setLoginPass(e.target.value)} />
          </div>
          <button type="submit" className="ac-btn primary" data-testid="clinician-login-btn" disabled={loginBusy}>
            <Lock size={14} /> {loginBusy ? tr(lang, "Signing in…", "登入中…") : tr(lang, "Sign In to Clinician Console", "登入醫生審核控制台")}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="ac-console-shell" data-testid="surface-clinician">
      {/* Clinician Header Bar */}
      <div className="ac-console-bar">
        <div style={{ display: "flex", alignItems: "center", gap: "0.7rem", flexWrap: "wrap" }}>
          <Stethoscope size={18} color="var(--ac-teal)" />
          <strong>
            {session?.name_en || "Dr. Wong"} {session?.name_zh ? <span lang="zh-Hant">{session.name_zh}</span> : null}
          </strong>
          <span className="ac-badge teal ac-mono" data-testid="clinician-mchk-badge">
            MCHK {session?.mchk_reg_no || "M12345"}
          </span>
          <span style={{ fontSize: "12px", color: "var(--ac-ink-3)" }}>
            <span className="ac-kbd">j</span>/<span className="ac-kbd">k</span> {tr(lang, "navigate", "切換")},{" "}
            <span className="ac-kbd">a</span> {tr(lang, "approve", "簽署")}
          </span>
        </div>

        <div className="ac-btn-row">
          <button type="button" className={`ac-btn sm${tab === "queue" ? " primary" : ""}`} data-testid="clinician-tab-queue" onClick={() => setTab("queue")}>
            {tr(lang, `Review Queue (${queue.length})`, `待審核隊列 (${queue.length})`)}
          </button>
          <button type="button" className={`ac-btn sm${tab === "availability" ? " primary" : ""}`} data-testid="clinician-tab-availability" onClick={() => setTab("availability")}>
            <Calendar size={13} /> {tr(lang, "Availability & Roster", "應診當值表")}
          </button>
          <button type="button" className={`ac-btn sm${tab === "audit" ? " primary" : ""}`} data-testid="clinician-tab-audit" onClick={() => setTab("audit")}>
            <History size={13} /> {tr(lang, "Personal Audit Trail", "個人審計日誌")}
          </button>
          <button type="button" className="ac-btn sm" onClick={() => void loadQueue()} disabled={loadingQueue} data-testid="clinician-refresh-btn">
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* TAB 1: SPLIT-PANE REVIEW QUEUE */}
      {tab === "queue" && (
        <div className="ac-console-split">
          {/* Left Pane: Risk & SLA-Sorted Queue */}
          <aside className="ac-queue-col" aria-label="Clinician Review Queue" data-testid="clinician-queue-list">
            <div style={{ padding: "0.65rem 0.95rem", borderBottom: "1px solid var(--ac-border)", fontSize: "12px", fontWeight: 700, color: "var(--ac-ink-2)", display: "flex", justifyContent: "space-between" }}>
              <span>{tr(lang, "SLA & Risk-Sorted Queue", "風險及時限排序隊列")}</span>
              <span>{sortedQueue.length} {tr(lang, "pending", "待處理")}</span>
            </div>

            {sortedQueue.length === 0 && (
              <div style={{ padding: "1.25rem", color: "var(--ac-ink-3)", fontSize: "13px" }}>
                {tr(lang, "Queue clear — no pending AI proposals awaiting clinician sign-off.", "目前沒有待審核的 AI 建議。")}
              </div>
            )}

            {sortedQueue.map((item) => {
              const sla = formatSla(item.sla_due_at);
              const passCount = item.checks_summary?.passed ?? item.checks?.filter((c) => c.status === "pass").length ?? 7;
              const totalCount = item.checks_summary?.total ?? item.checks?.length ?? 7;
              const failCount = item.checks_summary?.failures ?? item.checks?.filter((c) => c.status === "fail").length ?? 0;

              return (
                <button
                  key={item.id}
                  type="button"
                  className={`ac-queue-item urg-${item.urgency}${selectedId === item.id ? " selected" : ""}`}
                  data-testid={`queue-item-${item.id}`}
                  data-urgency={item.urgency}
                  onClick={() => setSelectedId(item.id)}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.4rem" }}>
                    <span className={`ac-badge ${item.urgency >= 4 ? "crit" : item.urgency === 3 ? "warn" : item.urgency === 2 ? "info" : ""}`}>
                      {item.urgency >= 4 ? "CRITICAL RED FLAG" : item.urgency === 3 ? "URGENT" : item.urgency === 2 ? "SOON" : "ROUTINE"}
                    </span>
                    <span style={{ fontSize: "11px", fontWeight: 700, color: sla.overdue ? "var(--ac-crit)" : "var(--ac-ink-3)", display: "inline-flex", alignItems: "center", gap: "0.2rem" }}>
                      <Clock size={11} /> {sla.label}
                    </span>
                  </div>

                  <div style={{ fontWeight: 700, fontSize: "13.5px", color: "var(--ac-ink)" }}>
                    {item.patient_name_en} {item.patient_name_zh ? <span lang="zh-Hant">{item.patient_name_zh}</span> : null}
                  </div>

                  <div style={{ fontSize: "12px", color: "var(--ac-ink-2)" }}>
                    {item.chief_complaint ? item.chief_complaint.slice(0, 72) : item.kind}
                  </div>

                  <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", marginTop: "0.15rem" }}>
                    <span className={`ac-badge ${failCount > 0 ? "crit" : "ok"}`} style={{ fontSize: "10.5px", padding: "0.05rem 0.42rem" }}>
                      <ShieldCheck size={10} /> {passCount}/{totalCount} Checks
                    </span>
                    {item.g6pd_deficient && (
                      <span className="ac-badge warn" style={{ fontSize: "10.5px", padding: "0.05rem 0.42rem" }}>
                        G6PD
                      </span>
                    )}
                    {(item.allergies?.length || 0) > 0 && (
                      <span className="ac-badge crit" style={{ fontSize: "10.5px", padding: "0.05rem 0.42rem" }}>
                        Allergy: {item.allergies![0]}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </aside>

          {/* Right Pane: Clinical Evidence & Decision Workspace */}
          <main className="ac-workspace-col" data-testid="clinician-workspace">
            {loadingDetail && <div className="ac-card">{tr(lang, "Loading clinical evidence…", "正在載入臨床證據…")}</div>}

            {!loadingDetail && !detail && (
              <div className="ac-card">
                <p style={{ margin: 0, color: "var(--ac-ink-3)" }}>
                  {tr(lang, "Select an item from the review queue to inspect verbatim patient evidence and sign.", "請從左側隊列選擇個案以審閱原文證據及簽署。")}
                </p>
              </div>
            )}

            {!loadingDetail && detail && (
              <>
                {/* Patient Card Header */}
                <section className="ac-card" data-testid="clinician-patient-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.75rem", flexWrap: "wrap" }}>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "0.55rem", flexWrap: "wrap" }}>
                        <h2 style={{ margin: 0, fontSize: "1.2rem" }}>
                          {detail.patient.name_en}{" "}
                          {detail.patient.name_zh ? <span lang="zh-Hant">{detail.patient.name_zh}</span> : null}
                        </h2>
                        <span className={`ac-badge ${detail.urgency >= 4 ? "crit" : "teal"}`}>
                          {detail.kind} • {detail.protocol_id}@{detail.protocol_version}
                        </span>
                      </div>
                      <div className="ac-mono" style={{ fontSize: "12px", color: "var(--ac-ink-3)", marginTop: "0.2rem" }}>
                        {detail.patient.sex} • DOB {detail.patient.dob} • HKID {detail.patient.hkid_masked}
                        {detail.patient.weight_kg ? ` • ${detail.patient.weight_kg}kg` : ""}
                        {detail.patient.egfr ? ` • eGFR ${detail.patient.egfr}` : ""}
                      </div>
                    </div>
                    <div style={{ textAlign: "right", fontSize: "12px" }}>
                      <div style={{ fontWeight: 700, color: "var(--ac-teal)" }}>
                        Signer: MCHK {session?.mchk_reg_no || detail.practitioner?.mchk_reg_no || "M12345"}
                      </div>
                      <div style={{ color: "var(--ac-ink-3)" }}>
                        {tr(lang, "Review time:", "審閱計時：")} <span className="ac-mono">{elapsedSec}s</span>
                      </div>
                    </div>
                  </div>

                  {/* Clinical Safety Badges: G6PD, Allergies, Current Meds */}
                  <div style={{ display: "flex", gap: "0.45rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
                    <span
                      className={`ac-badge ${detail.patient.g6pd_deficient ? "crit" : "ok"}`}
                      data-testid="clinician-g6pd-badge"
                    >
                      <ShieldAlert size={13} />
                      {detail.patient.g6pd_deficient
                        ? tr(lang, "HK G6PD DEFICIENT (蠶豆症 — Avoid Sulfa/Aspirin/Nitrofurantoin)", "香港蠶豆症 (G6PD 缺乏症 — 忌用磺胺/阿士匹靈)")
                        : tr(lang, "G6PD Status: Normal", "G6PD 狀態：正常")}
                    </span>

                    {(detail.patient.allergies || []).length > 0 ? (
                      detail.patient.allergies.map((alg) => (
                        <span key={alg} className="ac-badge crit" data-testid="clinician-allergy-badge">
                          <AlertTriangle size={12} /> Allergy: {alg}
                        </span>
                      ))
                    ) : (
                      <span className="ac-badge ok">NKDA (No Known Drug Allergies)</span>
                    )}

                    {(detail.patient.current_meds || []).map((med) => (
                      <span key={med} className="ac-badge warn" data-testid="clinician-current-med-badge">
                        <Pill size={12} /> Current Med: {med}
                      </span>
                    ))}
                  </div>

                  {/* 7-Check Verification Strip */}
                  {detail.checks && detail.checks.length > 0 && (
                    <div style={{ marginTop: "0.75rem", paddingTop: "0.65rem", borderTop: "1px solid #edf2f5" }}>
                      <div style={{ fontSize: "11.5px", fontWeight: 700, color: "var(--ac-ink-3)", marginBottom: "0.35rem" }}>
                        {tr(lang, "7-Check Harness Verification Gates:", "7 項臨床安全閘門驗證：")}
                      </div>
                      <div className="ac-checks-strip" data-testid="clinician-checks-strip">
                        {detail.checks.map((c) => (
                          <span
                            key={c.id}
                            className={`ac-badge ${c.status === "pass" ? "ok" : c.status === "warn" ? "warn" : c.status === "fail" ? "crit" : ""}`}
                            title={c.detail}
                          >
                            {c.status === "pass" ? "✓" : c.status === "warn" ? "!" : c.status === "fail" ? "✕" : "–"}{" "}
                            {c.label || c.name || c.id}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </section>

                {/* Side-by-Side Verbatim Source Quotes + Structured Intake */}
                <div className="ac-evidence-grid" style={{ marginTop: "1rem" }}>
                  <section className="ac-card" data-testid="clinician-source-quotes">
                    <h3 className="ac-card-title" style={{ marginBottom: "0.6rem" }}>
                      {tr(lang, "Verbatim Patient Source Quotes", "病人原文陳述引錄")}
                    </h3>
                    {(detail.source_quotes || []).length === 0 ? (
                      <div className="ac-quote-box">
                        <mark>“{detail.encounter.chief_complaint_text}”</mark>
                      </div>
                    ) : (
                      detail.source_quotes.map((sq, i) => (
                        <div key={i} className="ac-quote-box">
                          {sq.field && (
                            <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--ac-ink-3)", marginBottom: "0.2rem" }}>
                              {sq.field} {sq.source ? `• ${sq.source}` : ""}
                            </div>
                          )}
                          <div>
                            <mark>“{sq.quote}”</mark>
                          </div>
                        </div>
                      ))
                    )}
                    {detail.agent_run && (
                      <div style={{ fontSize: "11.5px", color: "var(--ac-ink-3)", marginTop: "0.6rem" }}>
                        AI Run: <span className="ac-mono">{detail.agent_run.model_id}</span> • Region{" "}
                        <span className="ac-mono">{detail.agent_run.region}</span> •{" "}
                        {detail.agent_run.redacted ? "PHI Redacted" : "In-Region HK"} • {detail.agent_run.latency_ms}ms
                      </div>
                    )}
                  </section>

                  <section className="ac-card" data-testid="clinician-structured-intake">
                    <h3 className="ac-card-title" style={{ marginBottom: "0.6rem" }}>
                      {tr(lang, "Structured Intake (SNOMED CT / LOINC / UCUM)", "結構化病歷（SNOMED / LOINC / UCUM）")}
                    </h3>

                    {(detail.payload?.red_flags?.length || 0) > 0 && (
                      <div className="ac-alert crit" style={{ marginBottom: "0.65rem" }}>
                        <AlertOctagon size={16} />
                        <div>
                          <strong>Red Flags Triggered:</strong> {detail.payload.red_flags!.join(", ")}
                        </div>
                      </div>
                    )}

                    {(detail.observations || []).length > 0 && (
                      <div style={{ marginBottom: "0.65rem" }}>
                        <div style={{ fontSize: "11.5px", fontWeight: 700, color: "var(--ac-ink-3)", marginBottom: "0.3rem" }}>
                          LOINC + UCUM Observations
                        </div>
                        <table className="ac-table">
                          <thead>
                            <tr>
                              <th>LOINC</th>
                              <th>Observation</th>
                              <th>Value</th>
                            </tr>
                          </thead>
                          <tbody>
                            {detail.observations.map((obs) => (
                              <tr key={obs.id}>
                                <td className="ac-mono">{obs.loinc_code}</td>
                                <td>{obs.display}</td>
                                <td className="ac-mono">
                                  <strong>
                                    {obs.value_num !== null && obs.value_num !== undefined ? obs.value_num : obs.value_text}
                                  </strong>{" "}
                                  {obs.ucum_unit}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}

                    {(detail.payload?.snomed_symptoms?.length || 0) > 0 && (
                      <div>
                        <div style={{ fontSize: "11.5px", fontWeight: 700, color: "var(--ac-ink-3)", marginBottom: "0.3rem" }}>
                          SNOMED CT Symptoms
                        </div>
                        <div className="ac-pills">
                          {detail.payload.snomed_symptoms!.map((s) => (
                            <span key={s.code} className="ac-badge info">
                              <span className="ac-mono">{s.code}</span> {s.display}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </section>
                </div>

                {/* Editable Clinical Note & Dual Diagnosis Picker */}
                <section className="ac-card">
                  <h3 className="ac-card-title" style={{ marginBottom: "0.65rem" }}>
                    <FileSignature size={16} color="var(--ac-teal)" />
                    {tr(lang, "Clinical Note, Dual ICD-10 / ICPC-2 Diagnosis & Sick-Leave Signer", "醫生病歷、雙編碼診斷及病假證明書簽署")}
                  </h3>

                  <div className="ac-field">
                    <label htmlFor="doc-note">{tr(lang, "Editable Clinical Assessment & Plan", "可編輯臨床評估及治療方案")}</label>
                    <textarea
                      id="doc-note"
                      className="ac-textarea"
                      data-testid="clinician-note-input"
                      style={{ minHeight: 96 }}
                      value={clinicalNote}
                      onChange={(e) => setClinicalNote(e.target.value)}
                    />
                  </div>

                  <div className="ac-grid-2">
                    <div className="ac-field">
                      <label htmlFor="doc-diag">{tr(lang, "Working Diagnosis (HK Dual ICD-10 / ICPC-2)", "臨床診斷（ICD-10 / ICPC-2 雙編碼）")}</label>
                      <select
                        id="doc-diag"
                        className="ac-select"
                        data-testid="clinician-diagnosis-select"
                        value={icd10Code}
                        onChange={(e) => setIcd10Code(e.target.value)}
                      >
                        {HK_DIAGNOSES.map((d) => (
                          <option key={d.icd10} value={d.icd10}>
                            {d.icd10} / {d.icpc2} — {d.icd10_display} ({d.display_zh})
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="ac-field">
                      <label>{tr(lang, "Sick-Leave Certificate (Encrypted Vault PDF)", "醫生病假證明書（加密保險庫 PDF）")}</label>
                      <label style={{ display: "flex", alignItems: "center", gap: "0.45rem", fontWeight: 600, marginTop: "0.35rem" }}>
                        <input
                          type="checkbox"
                          data-testid="clinician-issue-cert-toggle"
                          checked={issueCert}
                          onChange={(e) => setIssueCert(e.target.checked)}
                        />
                        <span>{tr(lang, "Issue signed medical sick-leave certificate", "簽發醫生簽署病假證明書")}</span>
                      </label>
                    </div>
                  </div>

                  {issueCert && (
                    <div className="ac-grid-3">
                      <div className="ac-field">
                        <label htmlFor="cert-days">{tr(lang, "Days Off", "病假日數")}</label>
                        <input
                          id="cert-days"
                          type="number"
                          min={1}
                          max={7}
                          className="ac-input"
                          data-testid="clinician-cert-days"
                          value={certDays}
                          onChange={(e) => setCertDays(Math.max(1, Math.min(7, Number(e.target.value) || 1)))}
                        />
                      </div>
                      <div className="ac-field">
                        <label htmlFor="cert-start">{tr(lang, "Start Date", "開始日期")}</label>
                        <input id="cert-start" type="date" className="ac-input" data-testid="clinician-cert-start" value={certStart} onChange={(e) => setCertStart(e.target.value)} />
                      </div>
                      <div className="ac-field">
                        <label htmlFor="cert-end">{tr(lang, "End Date (Inclusive)", "結束日期（包括首尾）")}</label>
                        <input id="cert-end" type="date" className="ac-input" value={certEnd} readOnly />
                      </div>
                    </div>
                  )}
                </section>

                {/* e-Script Builder wired LIVE to /api/v1/clinician/rx-safety-check */}
                <section className="ac-card" data-testid="clinician-rx-builder">
                  <div className="ac-card-head">
                    <div>
                      <h3 className="ac-card-title">
                        <Pill size={16} color="var(--ac-teal)" />
                        {tr(lang, "e-Script Builder & Live Deterministic Rx Safety Engine", "電子處方編製及即時藥物安全檢查引擎")}
                      </h3>
                      <p className="ac-card-sub">
                        {tr(
                          lang,
                          "Checks HK Cap. 134 Dangerous Drugs (HARD BLOCK), G6PD hemolysis, allergies, warfarin interactions, and max daily dose in real time.",
                          "即時檢測香港法例第 134 章危險藥物（硬性封鎖）、蠶豆症風險、過敏、華法林交互作用及每日最高劑量。"
                        )}
                      </p>
                    </div>
                    <button
                      type="button"
                      className="ac-btn sm"
                      data-testid="clinician-run-rx-safety-btn"
                      disabled={safetyBusy || rxItems.length === 0}
                      onClick={() => void runRxSafetyCheck(detail.patient.id, rxItems)}
                    >
                      <ShieldCheck size={13} /> {safetyBusy ? tr(lang, "Checking…", "檢查中…") : tr(lang, "Run Safety Check", "執行安全檢查")}
                    </button>
                  </div>

                  <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem", flexWrap: "wrap" }}>
                    <select
                      className="ac-select"
                      style={{ flex: 1, minWidth: 240 }}
                      data-testid="clinician-add-drug-select"
                      value={drugToAdd}
                      onChange={(e) => setDrugToAdd(e.target.value)}
                    >
                      {HK_FORMULARY.map((d) => (
                        <option key={d.key} value={d.key}>
                          {d.display} [{d.hk_schedule}]
                        </option>
                      ))}
                    </select>
                    <button type="button" className="ac-btn primary sm" data-testid="clinician-add-drug-btn" onClick={handleAddDrug}>
                      <Plus size={14} /> {tr(lang, "Add Medication", "加入藥物")}
                    </button>
                  </div>

                  {rxItems.length === 0 ? (
                    <p style={{ fontSize: "12.5px", color: "var(--ac-ink-3)", margin: 0 }}>
                      {tr(lang, "No medications added. Add from the HK Acute Formulary above to run live safety verification.", "尚未加入處方藥物。可從香港急性處方集加入藥物並即時檢測。")}
                    </p>
                  ) : (
                    rxItems.map((item, idx) => (
                      <div key={`${item.drug}-${idx}`} className="ac-rx-row" data-testid={`rx-row-${item.drug}`}>
                        <div>
                          <div style={{ fontWeight: 700, fontSize: "13px" }}>{item.display || item.drug}</div>
                          <div className="ac-mono" style={{ fontSize: "11px", color: "var(--ac-ink-3)" }}>
                            {item.rxnorm ? `RxNorm ${item.rxnorm}` : item.drug} {item.hk_schedule ? `• ${item.hk_schedule}` : ""}
                          </div>
                        </div>
                        <input
                          type="number"
                          className="ac-input"
                          aria-label="Dose mg"
                          data-testid={`rx-dose-${idx}`}
                          value={item.dose_mg}
                          onChange={(e) => handleUpdateRxItem(idx, { dose_mg: Number(e.target.value) || 0 })}
                          title="Dose (mg)"
                        />
                        <select
                          className="ac-select"
                          aria-label="Frequency"
                          data-testid={`rx-freq-${idx}`}
                          value={item.frequency}
                          onChange={(e) => handleUpdateRxItem(idx, { frequency: e.target.value })}
                        >
                          <option value="QD">QD (1x/d)</option>
                          <option value="BD">BD (2x/d)</option>
                          <option value="TDS">TDS (3x/d)</option>
                          <option value="QDS">QDS (4x/d)</option>
                          <option value="QID">QID (4x/d)</option>
                          <option value="PRN">PRN</option>
                          <option value="ON">ON (nocte)</option>
                        </select>
                        <input
                          type="number"
                          min={1}
                          max={14}
                          className="ac-input"
                          aria-label="Days"
                          value={item.days}
                          onChange={(e) => handleUpdateRxItem(idx, { days: Math.max(1, Number(e.target.value) || 1) })}
                          title="Days"
                        />
                        <button
                          type="button"
                          className="ac-btn sm"
                          data-testid={`rx-remove-${item.drug}`}
                          onClick={() => handleRemoveRxItem(idx)}
                          title="Remove"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    ))
                  )}

                  {/* Live Safety Check Findings */}
                  {safetyResult && (
                    <div style={{ marginTop: "0.75rem" }} data-testid="rx-safety-findings">
                      {safetyResult.findings.map((f, i) => {
                        const sev = f.severity.toUpperCase();
                        return (
                          <div
                            key={i}
                            className={`ac-safety-finding ${sev}`}
                            data-testid={
                              sev === "HARD_BLOCK"
                                ? "rx-safety-hard-block"
                                : sev === "OVERRIDE_REQUIRED"
                                ? "rx-safety-override-required"
                                : `rx-safety-${sev.toLowerCase()}`
                            }
                          >
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" }}>
                              <strong>
                                [{sev}] {f.drug} — {f.rule || f.rule_ref || f.code || "Safety Check"}
                              </strong>
                            </div>
                            <div style={{ marginTop: "0.2rem" }}>{lang === "zh" && f.message_zh ? f.message_zh : f.message}</div>
                          </div>
                        );
                      })}

                      {safetyResult.findings.length === 0 && (
                        <div className="ac-safety-finding PASS" data-testid="rx-safety-all-pass">
                          <strong>[PASS]</strong>{" "}
                          {tr(lang, "All deterministic HK safety checks passed (Cap. 134, G6PD, Allergies, Interactions, Max Dose).", "所有香港處方安全檢查均已通過（第134章、蠶豆症、過敏、交互作用、劑量上限）。")}
                        </div>
                      )}

                      {requiresOverride && !hasHardBlock && (
                        <div className="ac-field" style={{ marginTop: "0.75rem" }}>
                          <label htmlFor="override-reason" style={{ color: "var(--ac-warn)" }}>
                            {tr(lang, "Mandatory Clinical Override Justification (Logged to Immutable Audit Trail)", "必須填寫臨床覆蓋理由（將寫入不可篡改審計日誌）")}
                          </label>
                          <textarea
                            id="override-reason"
                            className="ac-textarea"
                            data-testid="clinician-override-reason"
                            style={{ minHeight: 62 }}
                            value={overrideReason}
                            onChange={(e) => setOverrideReason(e.target.value)}
                            placeholder={tr(lang, "Document clinical rationale for overriding safety warning…", "記錄醫生覆蓋安全警告的臨床理據…")}
                          />
                        </div>
                      )}
                    </div>
                  )}
                </section>

                {/* Automation-Bias Friction & Final Decision Footer */}
                <section className="ac-card" data-testid="clinician-decision-footer">
                  {(frictionRequired || detail.urgency >= 4 || elapsedSec < 3) && (
                    <div className="ac-friction-box" data-testid="clinician-friction-box">
                      <label style={{ display: "flex", alignItems: "flex-start", gap: "0.55rem", cursor: "pointer", fontWeight: 600, fontSize: "13px" }}>
                        <input
                          type="checkbox"
                          data-testid="clinician-friction-checkbox"
                          checked={frictionConfirmed}
                          onChange={(e) => setFrictionConfirmed(e.target.checked)}
                          style={{ marginTop: "0.2rem", width: 16, height: 16 }}
                        />
                        <span>
                          {tr(
                            lang,
                            "I have independently verified the patient's evidence, verbatim quotes, allergies/G6PD status, and clinical safety checks.",
                            "本人已獨立核實病人之臨床證據、原文陳述、過敏／蠶豆症記錄及處方安全檢查結果。"
                          )}
                        </span>
                      </label>
                    </div>
                  )}

                  {showEscalateBox && (
                    <div className="ac-field">
                      <label htmlFor="esc-reason" style={{ color: "var(--ac-crit)" }}>
                        {tr(lang, "Mandatory Escalation / Referral Reason (A&E / Specialist)", "必須填寫升級／急症室或專科轉介理由")}
                      </label>
                      <input
                        id="esc-reason"
                        className="ac-input"
                        data-testid="clinician-escalation-reason"
                        value={escalationReason}
                        onChange={(e) => setEscalationReason(e.target.value)}
                        placeholder={tr(lang, "e.g. Red-flag dyspnoea + SpO2 89% — advised immediate 999 / HA A&E transfer", "例如：出現氣促及血氧 89% 危急症狀 — 已指示即時往急症室求醫")}
                      />
                    </div>
                  )}

                  <div className="ac-btn-row" style={{ justifyContent: "space-between" }}>
                    <div className="ac-btn-row">
                      <button
                        type="button"
                        className="ac-btn primary"
                        data-testid="clinician-approve-btn"
                        disabled={deciding || hasHardBlock}
                        onClick={() => void handleApprove()}
                      >
                        <CheckCircle2 size={15} />
                        <span>
                          {deciding
                            ? tr(lang, "Signing…", "簽署中…")
                            : tr(lang, "Sign & Approve (a)", "醫生簽署並批准 (a)")}
                        </span>
                      </button>

                      <button
                        type="button"
                        className="ac-btn warn"
                        data-testid="clinician-escalate-btn"
                        disabled={deciding}
                        onClick={() => {
                          if (!showEscalateBox) {
                            setShowEscalateBox(true);
                          } else {
                            void handleEscalateOrReject("escalated");
                          }
                        }}
                      >
                        <AlertTriangle size={15} />
                        <span>{tr(lang, "Escalate / Refer", "升級／轉介")}</span>
                      </button>

                      <button
                        type="button"
                        className="ac-btn"
                        data-testid="clinician-reject-btn"
                        disabled={deciding}
                        onClick={() => void handleEscalateOrReject("rejected")}
                      >
                        <XCircle size={15} />
                        <span>{tr(lang, "Reject", "拒絕")}</span>
                      </button>
                    </div>

                    <span style={{ fontSize: "11.5px", color: "var(--ac-ink-3)" }}>
                      {tr(lang, "Asymmetric authority: AI proposes only; licensed MCHK doctor signs.", "權責分離原則：AI 僅作建議，由註冊西醫獨立簽署。")}
                    </span>
                  </div>
                </section>
              </>
            )}
          </main>
        </div>
      )}

      {/* TAB 2: AVAILABILITY & ROSTER */}
      {tab === "availability" && (
        <div className="ac-container" data-testid="clinician-panel-availability">
          <div className="ac-card" style={{ maxWidth: 680 }}>
            <div className="ac-card-head">
              <div>
                <h2 className="ac-card-title">{tr(lang, "Teleconsult Availability & Shift Roster", "遙距診症應診時段及當值表")}</h2>
                <p className="ac-card-sub">
                  {tr(lang, "Active shifts immediately publish bookable teleconsult slots in the Patient PWA.", "啟用的當值時段將即時開放予病人於病人端預約。")}
                </p>
              </div>
            </div>

            {roster.map((slot, idx) => (
              <div
                key={idx}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0.65rem 0.8rem",
                  border: "1px solid var(--ac-border)",
                  borderRadius: "8px",
                  marginBottom: "0.55rem",
                }}
              >
                <div>
                  <strong>{slot.day}</strong>{" "}
                  <span className="ac-mono" style={{ color: "var(--ac-ink-2)" }}>
                    {slot.start} – {slot.end} HKT
                  </span>
                </div>
                <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontWeight: 600 }}>
                  <input
                    type="checkbox"
                    checked={slot.active}
                    onChange={(e) =>
                      setRoster((prev) => prev.map((r, i) => (i === idx ? { ...r, active: e.target.checked } : r)))
                    }
                  />
                  <span>{slot.active ? tr(lang, "On Duty", "當值") : tr(lang, "Off", "休班")}</span>
                </label>
              </div>
            ))}

            <button
              type="button"
              className="ac-btn primary"
              data-testid="clinician-save-availability-btn"
              disabled={rosterBusy}
              onClick={() => void handleSaveAvailability()}
            >
              <CheckCircle2 size={15} /> {rosterBusy ? tr(lang, "Saving…", "儲存中…") : tr(lang, "Publish Roster", "儲存並發佈當值表")}
            </button>
          </div>
        </div>
      )}

      {/* TAB 3: PERSONAL AUDIT TRAIL */}
      {tab === "audit" && (
        <div className="ac-container" data-testid="clinician-panel-audit">
          <div className="ac-card">
            <div className="ac-card-head">
              <div>
                <h2 className="ac-card-title">{tr(lang, "Clinician Personal Immutable Audit Trail", "醫生個人不可篡改審計日誌")}</h2>
                <p className="ac-card-sub">
                  {tr(lang, "Protected by PostgreSQL BEFORE UPDATE OR DELETE immutability trigger.", "受 PostgreSQL 不可篡改觸發器保護（僅可附加寫入）。")}
                </p>
              </div>
            </div>

            {auditBusy ? (
              <p>{tr(lang, "Loading audit events…", "正在載入審計記錄…")}</p>
            ) : (
              <div className="ac-table-wrap">
                <table className="ac-table" data-testid="clinician-audit-table">
                  <thead>
                    <tr>
                      <th>Time (HKT)</th>
                      <th>Action</th>
                      <th>Resource</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditEvents.map((ev) => (
                      <tr key={ev.id}>
                        <td className="ac-mono" style={{ whiteSpace: "nowrap" }}>{new Date(ev.created_at).toLocaleString()}</td>
                        <td><span className="ac-badge teal">{ev.action}</span></td>
                        <td className="ac-mono">{ev.resource_type}/{ev.resource_id}</td>
                        <td className="ac-mono" style={{ fontSize: "11.5px" }}>{JSON.stringify(ev.metadata || {})}</td>
                      </tr>
                    ))}
                    {auditEvents.length === 0 && (
                      <tr>
                        <td colSpan={4} style={{ color: "var(--ac-ink-3)" }}>No audit events recorded yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
