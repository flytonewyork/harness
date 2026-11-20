import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Building2,
  CheckCircle2,
  Cpu,
  FileCheck2,
  FileCode2,
  FileSpreadsheet,
  Globe2,
  History,
  Lock,
  Play,
  Plus,
  Power,
  QrCode,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
} from "lucide-react";
import { acuteApi, tr } from "./api";
import type {
  AdminDataRequestItem,
  AdminObservabilityResponse,
  AdminOverviewResponse,
  AuditEventItem,
  AuthSession,
  CorporateDashboardResponse,
  InvoicingResponse,
  Lang,
} from "./api";

interface AdminPortalProps {
  mode: "clinic_admin" | "corporate_admin";
  lang: Lang;
  session: AuthSession | null;
  onSessionChange: (session: AuthSession) => void;
  onOpenCampaignQr: (campaignCode: string) => void;
  onNotify: (msg: string, isError?: boolean) => void;
}

type AdminTab = "governance" | "observability" | "corporates" | "invoicing" | "audit";

function KAnonCell({ value, suppressed }: { value: number | string | undefined; suppressed?: boolean }) {
  const isSuppressed = suppressed || String(value) === "<5" || String(value).includes("<5");
  if (isSuppressed) {
    return (
      <span className="ac-badge warn ac-mono" data-testid="corp-k-anon-badge">
        &lt;5 (k-anon suppressed)
      </span>
    );
  }
  return <strong className="ac-mono">{value ?? 0}</strong>;
}

export function AdminPortal({
  mode,
  lang,
  session,
  onSessionChange,
  onOpenCampaignQr,
  onNotify,
}: AdminPortalProps) {
  // Login state when unauthenticated for this mode
  const [loginEmail, setLoginEmail] = useState(
    mode === "clinic_admin" ? "ops@central-gp.tmrw.hk" : "hr@harbour-fintech.example.hk"
  );
  const [loginPass, setLoginPass] = useState("DemoClinic!2026");
  const [loginBusy, setLoginBusy] = useState(false);

  // Clinic Ops Admin state
  const [adminTab, setAdminTab] = useState<AdminTab>("governance");
  const [overview, setOverview] = useState<AdminOverviewResponse | null>(null);
  const [observability, setObservability] = useState<AdminObservabilityResponse | null>(null);
  const [dataRequests, setDataRequests] = useState<AdminDataRequestItem[]>([]);
  const [invoicing, setInvoicing] = useState<InvoicingResponse | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEventItem[]>([]);
  const [auditFilter, setAuditFilter] = useState("");
  const [expandedProtocol, setExpandedProtocol] = useState<string | null>(null);
  const [killReason, setKillReason] = useState("");
  const [busy, setBusy] = useState(false);

  // New Corporate & Campaign forms (Clinic Admin)
  const [corpName, setCorpName] = useState("");
  const [corpCap, setCorpCap] = useState("60000");
  const [corpBillingEmail, setCorpBillingEmail] = useState("");
  const [corpAdminEmail, setCorpAdminEmail] = useState("");

  const [campCorpId, setCampCorpId] = useState("");
  const [campCode, setCampCode] = useState("");
  const [campName, setCampName] = useState("");
  const [campCap, setCampCap] = useState("25000");
  const [campFee, setCampFee] = useState("420");

  // Corporate Admin state
  const [corpDash, setCorpDash] = useState<CorporateDashboardResponse | null>(null);
  const [newCorpCampCode, setNewCorpCampCode] = useState("");
  const [newCorpCampName, setNewCorpCampName] = useState("");
  const [newCorpCampCap, setNewCorpCampCap] = useState("15000");

  useEffect(() => {
    setLoginEmail(mode === "clinic_admin" ? "ops@central-gp.tmrw.hk" : "hr@harbour-fintech.example.hk");
  }, [mode]);

  const isAuthorized = session?.role === mode;

  const loadClinicAdmin = async () => {
    if (session?.role !== "clinic_admin") return;
    setBusy(true);
    try {
      const [ov, inv, aud, obs, dreqs] = await Promise.all([
        acuteApi.adminOverview(),
        acuteApi.adminInvoicing().catch(() => ({ rows: [] })),
        acuteApi.adminAudit(100).catch(() => []),
        acuteApi.adminObservability().catch(() => null),
        acuteApi.adminDataRequests().catch(() => []),
      ]);
      setOverview(ov);
      setInvoicing(inv);
      setAuditEvents(aud);
      setObservability(obs);
      setDataRequests(dreqs);
      if (ov.corporates?.length > 0 && !campCorpId) {
        setCampCorpId(ov.corporates[0].id);
      }
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to load Clinic Ops Admin overview", true);
    } finally {
      setBusy(false);
    }
  };

  const loadCorporateAdmin = async () => {
    if (session?.role !== "corporate_admin") return;
    setBusy(true);
    try {
      const dash = await acuteApi.corporateDashboard();
      setCorpDash(dash);
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to load Corporate Admin dashboard", true);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (mode === "clinic_admin" && session?.role === "clinic_admin") {
      void loadClinicAdmin();
    } else if (mode === "corporate_admin" && session?.role === "corporate_admin") {
      void loadCorporateAdmin();
    }
  }, [mode, session?.access_token, session?.role]);

  const handleLogin = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setLoginBusy(true);
    try {
      const auth = await acuteApi.authLogin(loginEmail.trim(), loginPass);
      onSessionChange({ ...auth, access_token: auth.access_token || auth.token || "" });
      onNotify(tr(lang, "Signed in successfully.", "已成功登入。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Sign-in failed", true);
    } finally {
      setLoginBusy(false);
    }
  };

  // Clinic Admin Actions
  const handleToggleKillSwitch = async () => {
    if (!overview) return;
    const nextEnabled = !overview.tenant.ai_enabled;
    setBusy(true);
    try {
      await acuteApi.adminKillSwitch({
        ai_enabled: nextEnabled,
        reason: killReason.trim() || (nextEnabled ? "Re-enabling AI harness after governance check" : "Emergency clinical ops AI kill-switch engaged"),
      });
      await loadClinicAdmin();
      setKillReason("");
      onNotify(
        nextEnabled
          ? tr(lang, "AI Harness RE-ENABLED for tenant.", "已重新啟用 AI 輔助引擎。")
          : tr(lang, "AI KILL SWITCH ENGAGED — all intakes now route directly to deterministic clinician-only flow.", "已啟動 AI 緊急開關 — 所有個案即時轉入醫生全人手確定性流程。")
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Kill switch update failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleApproveProtocol = async (protocolVersionId: string) => {
    setBusy(true);
    try {
      await acuteApi.adminApproveProtocol(protocolVersionId);
      await loadClinicAdmin();
      onNotify(tr(lang, "Protocol version approved & cryptographically pinned.", "臨床方案版本已批核並鎖定雜湊指紋。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Protocol approval failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleProcessOutbox = async () => {
    setBusy(true);
    try {
      const res = await acuteApi.adminProcessOutbox();
      await loadClinicAdmin();
      onNotify(
        tr(
          lang,
          `Outbox sweep complete: ${res.batch.succeeded} succeeded, ${res.batch.retried} retried, ${res.batch.dlq} DLQ.`,
          `背景工作佇列已處理：成功 ${res.batch.succeeded} 項，重試 ${res.batch.retried} 項，DLQ ${res.batch.dlq} 項。`
        )
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Outbox processing failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleResolveDcr = async (reqId: string, action: "approved" | "rejected") => {
    setBusy(true);
    try {
      const note =
        action === "approved"
          ? "Verified by HK DPO / Clinic Ops and recorded as append-only PDPO DPP6 addendum."
          : "Correction declined with written explanation under HK PDPO s.23; original record retained.";
      await acuteApi.adminResolveDataRequest(reqId, { action, resolution_note: note });
      await loadClinicAdmin();
      onNotify(
        action === "approved"
          ? tr(lang, "PDPO Data Correction approved as append-only addendum.", "已批核 PDPO 資料更正並寫入附加紀錄。")
          : tr(lang, "PDPO Data Correction request rejected with audit note.", "已拒絕 PDPO 資料更正要求並記錄審計理由。")
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to resolve data request", true);
    } finally {
      setBusy(false);
    }
  };

  const handleCreateCorporate = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!corpName.trim() || !corpBillingEmail.trim() || !corpAdminEmail.trim()) return;
    setBusy(true);
    try {
      await acuteApi.adminCreateCorporate({
        name: corpName.trim(),
        spend_cap_hkd: Number(corpCap) || 50000,
        billing_email: corpBillingEmail.trim(),
        admin_email: corpAdminEmail.trim(),
      });
      setCorpName("");
      setCorpBillingEmail("");
      setCorpAdminEmail("");
      await loadClinicAdmin();
      onNotify(tr(lang, "Corporate client created with spend cap.", "已建立企業客戶及支出上限。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to create corporate", true);
    } finally {
      setBusy(false);
    }
  };

  const handleAdminCreateCampaign = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!campCorpId || !campCode.trim() || !campName.trim()) return;
    setBusy(true);
    try {
      await acuteApi.adminCreateCampaign({
        corporate_id: campCorpId,
        code: campCode.trim().toUpperCase(),
        name: campName.trim(),
        spend_cap_hkd: Number(campCap) || 20000,
        unit_fee_hkd: Number(campFee) || 420,
      });
      setCampCode("");
      setCampName("");
      await loadClinicAdmin();
      onNotify(tr(lang, "QR campaign created.", "已建立企業二維碼計劃。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to create campaign", true);
    } finally {
      setBusy(false);
    }
  };

  // Corporate Admin Actions
  const handleCorpCreateCampaign = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!newCorpCampCode.trim() || !newCorpCampName.trim()) return;
    setBusy(true);
    try {
      await acuteApi.corporateCreateCampaign({
        code: newCorpCampCode.trim().toUpperCase(),
        name: newCorpCampName.trim(),
        spend_cap_hkd: Number(newCorpCampCap) || 15000,
      });
      setNewCorpCampCode("");
      setNewCorpCampName("");
      await loadCorporateAdmin();
      onNotify(tr(lang, "Corporate QR campaign created.", "已建立新的企業二維碼計劃。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Failed to create corporate campaign", true);
    } finally {
      setBusy(false);
    }
  };

  // ========================================================================= LOGIN GATE
  if (!isAuthorized) {
    return (
      <div className="ac-container-narrow" data-testid={`surface-${mode}-login`}>
        <form className="ac-card" onSubmit={(e) => void handleLogin(e)}>
          <div className="ac-card-head">
            <div>
              <h2 className="ac-card-title">
                {mode === "clinic_admin" ? <ShieldCheck size={18} color="var(--ac-teal)" /> : <Building2 size={18} color="var(--ac-teal)" />}
                {mode === "clinic_admin"
                  ? tr(lang, "Clinic Ops Admin Sign-In", "診所營運管理員登入")
                  : tr(lang, "Corporate Benefits Admin Sign-In", "企業福利管理員登入")}
              </h2>
              <p className="ac-card-sub">
                {tr(
                  lang,
                  "Role-segregated access enforced by RBAC & PostgreSQL RLS. Use credentials or Demo Bar persona switcher.",
                  "嚴格角色權限隔離及 PostgreSQL RLS 保護。可使用帳戶登入或頂部示範列一鍵切換。"
                )}
              </p>
            </div>
          </div>
          <div className="ac-field">
            <label htmlFor="adm-email">Email</label>
            <input id="adm-email" type="email" className="ac-input" data-testid="admin-login-email" value={loginEmail} onChange={(e) => setLoginEmail(e.target.value)} />
          </div>
          <div className="ac-field">
            <label htmlFor="adm-pass">Password</label>
            <input id="adm-pass" type="password" className="ac-input" data-testid="admin-login-pass" value={loginPass} onChange={(e) => setLoginPass(e.target.value)} />
          </div>
          <button type="submit" className="ac-btn primary" data-testid="admin-login-btn" disabled={loginBusy}>
            <Lock size={14} /> {loginBusy ? tr(lang, "Signing in…", "登入中…") : tr(lang, "Sign In", "登入")}
          </button>
        </form>
      </div>
    );
  }

  // ========================================================================= CORPORATE ADMIN VIEW
  if (mode === "corporate_admin") {
    const agg = corpDash?.aggregates;
    const pct =
      agg && agg.spend_cap_hkd > 0 ? Math.min(100, Math.round((agg.total_spent_hkd / agg.spend_cap_hkd) * 100)) : 0;

    return (
      <div className="ac-container" data-testid="surface-corporate">
        <div className="ac-page-header">
          <div>
            <h1>
              {corpDash?.corporate?.name || tr(lang, "Corporate Flu Care Portal", "企業流感福利管理後台")}
            </h1>
            <p>
              {tr(
                lang,
                "Aggregate-only utilization, campaign spend caps, and office QR deployment.",
                "僅限匿名匯總使用數據、計劃預算上限及辦公室二維碼管理。"
              )}
            </p>
          </div>
          <button type="button" className="ac-btn sm" onClick={() => void loadCorporateAdmin()} disabled={busy}>
            <RefreshCw size={13} /> {tr(lang, "Refresh", "重新整理")}
          </button>
        </div>

        {/* Explicit PDPO Employer Privacy Guarantee Banner */}
        <div className="ac-alert ok" style={{ marginBottom: "1rem" }} data-testid="corp-privacy-guarantee">
          <ShieldCheck size={18} />
          <div>
            <strong>{tr(lang, "HK PDPO Employer Privacy Guarantee (Zero Individual PHI)", "香港 PDPO 僱主私隱保障承諾（零個人醫療資料）")}</strong>
            <div style={{ fontSize: "12.5px", marginTop: "0.2rem" }}>
              {corpDash?.privacy_guarantee ||
                tr(
                  lang,
                  "By law and architecture, corporate admins NEVER have access to individual employee identities, symptoms, diagnoses, clinical notes, or prescriptions. Any aggregate count below 5 is automatically suppressed (<5) to prevent re-identification.",
                  "根據法例及系統架構，企業管理員絕不能查閱任何個別員工身份、症狀、診斷、病歷或處方。所有少於 5 宗的統計數值均自動以「<5」匿名屏蔽，防止辨識個人身份。"
                )}
            </div>
          </div>
        </div>

        {agg && (
          <div className="ac-grid-4" style={{ marginBottom: "1rem" }}>
            <div className="ac-kpi">
              <div className="ac-kpi-label">{tr(lang, "Spend / Cap (HKD)", "已用金額／預算上限")}</div>
              <div className="ac-kpi-value">
                HK${agg.total_spent_hkd.toLocaleString()}
              </div>
              <div className="ac-kpi-sub">Cap: HK${agg.spend_cap_hkd.toLocaleString()} ({pct}%)</div>
              <div className="ac-progress">
                <div className={`ac-progress-fill ${pct >= 90 ? "crit" : pct >= 70 ? "warn" : ""}`} style={{ width: `${pct}%` }} />
              </div>
            </div>

            <div className="ac-kpi">
              <div className="ac-kpi-label">{tr(lang, "Completed Teleconsults", "已完成遙距診症")}</div>
              <div className="ac-kpi-value">
                <KAnonCell value={agg.total_visits} suppressed={agg.visits_suppressed} />
              </div>
              <div className="ac-kpi-sub">k-anonymity threshold: {corpDash?.k_anonymity_threshold ?? 5}</div>
            </div>

            <div className="ac-kpi">
              <div className="ac-kpi-label">{tr(lang, "Sick-Leave Certificates", "已簽發病假證明")}</div>
              <div className="ac-kpi-value">
                <KAnonCell value={agg.sick_leave_certificates} suppressed={agg.certificates_suppressed} />
              </div>
              <div className="ac-kpi-sub">{tr(lang, "Aggregate count only", "僅限匯總數字")}</div>
            </div>

            <div className="ac-kpi">
              <div className="ac-kpi-label">{tr(lang, "Flu Vaccinations", "流感疫苗接種")}</div>
              <div className="ac-kpi-value">
                <KAnonCell value={agg.flu_vaccinations} suppressed={agg.vaccinations_suppressed} />
              </div>
              <div className="ac-kpi-sub">{tr(lang, "Partner clinics (CVX)", "合作診所接種")}</div>
            </div>
          </div>
        )}

        <div className="ac-card">
          <div className="ac-card-head">
            <div>
              <h2 className="ac-card-title">{tr(lang, "QR Campaigns & Spend Caps", "企業二維碼計劃及支出上限")}</h2>
            </div>
          </div>
          <div className="ac-table-wrap">
            <table className="ac-table" data-testid="corp-campaigns-table">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Campaign</th>
                  <th>Visits</th>
                  <th>Certs</th>
                  <th>Flu Vax</th>
                  <th>Spend / Cap (HKD)</th>
                  <th>Poster</th>
                </tr>
              </thead>
              <tbody>
                {(corpDash?.campaigns || []).map((c) => {
                  const cpct = c.spend_cap_hkd > 0 ? Math.min(100, Math.round((c.spent_hkd / c.spend_cap_hkd) * 100)) : 0;
                  return (
                    <tr key={c.id}>
                      <td className="ac-mono"><strong>{c.code}</strong></td>
                      <td>{c.name}</td>
                      <td><KAnonCell value={c.visits_display} suppressed={c.visits_suppressed} /></td>
                      <td><KAnonCell value={c.certs_display} suppressed={c.certs_suppressed} /></td>
                      <td><KAnonCell value={c.vax_display} suppressed={c.vax_suppressed} /></td>
                      <td style={{ minWidth: 160 }}>
                        <div className="ac-mono" style={{ fontSize: "12px" }}>
                          HK${c.spent_hkd.toLocaleString()} / ${c.spend_cap_hkd.toLocaleString()}
                        </div>
                        <div className="ac-progress">
                          <div className={`ac-progress-fill ${cpct >= 90 ? "crit" : cpct >= 70 ? "warn" : ""}`} style={{ width: `${cpct}%` }} />
                        </div>
                      </td>
                      <td>
                        <button type="button" className="ac-btn sm" onClick={() => onOpenCampaignQr(c.code)}>
                          <QrCode size={13} /> QR
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <form className="ac-card" onSubmit={(e) => void handleCorpCreateCampaign(e)}>
          <h3 className="ac-card-title" style={{ marginBottom: "0.7rem" }}>
            <Plus size={16} color="var(--ac-teal)" /> {tr(lang, "Launch New Office QR Campaign", "建立新辦公室二維碼計劃")}
          </h3>
          <div className="ac-grid-3">
            <div className="ac-field">
              <label htmlFor="cc-code">{tr(lang, "Campaign Code", "計劃代碼")}</label>
              <input id="cc-code" className="ac-input ac-mono" data-testid="corp-camp-code" required value={newCorpCampCode} onChange={(e) => setNewCorpCampCode(e.target.value)} placeholder="ACME-Q4-2026" />
            </div>
            <div className="ac-field">
              <label htmlFor="cc-name">{tr(lang, "Campaign Name", "計劃名稱")}</label>
              <input id="cc-name" className="ac-input" data-testid="corp-camp-name" required value={newCorpCampName} onChange={(e) => setNewCorpCampName(e.target.value)} placeholder="Central Office Winter Wave" />
            </div>
            <div className="ac-field">
              <label htmlFor="cc-cap">{tr(lang, "Spend Cap (HKD)", "支出上限（港幣）")}</label>
              <input id="cc-cap" type="number" className="ac-input" data-testid="corp-camp-cap" required value={newCorpCampCap} onChange={(e) => setNewCorpCampCap(e.target.value)} />
            </div>
          </div>
          <button type="submit" className="ac-btn primary" data-testid="corp-create-campaign-btn" disabled={busy}>
            <Plus size={14} /> {tr(lang, "Create QR Campaign", "建立二維碼計劃")}
          </button>
        </form>
      </div>
    );
  }

  // ========================================================================= CLINIC OPS ADMIN VIEW
  const tenant = overview?.tenant;
  const filteredAudit = auditEvents.filter((ev) => {
    if (!auditFilter.trim()) return true;
    const q = auditFilter.toLowerCase();
    return (
      ev.action.toLowerCase().includes(q) ||
      ev.actor_role.toLowerCase().includes(q) ||
      ev.resource_type.toLowerCase().includes(q) ||
      ev.actor_id.toLowerCase().includes(q)
    );
  });

  return (
    <div className="ac-container" data-testid="surface-admin">
      <div className="ac-page-header">
        <div>
          <h1>{tr(lang, "Clinic Operations & AI Governance Admin", "診所營運及 AI 安全管治控制台")}</h1>
          <p>
            {tenant?.name || "Central GP Clinic"} •{" "}
            {tr(lang, "Hong Kong data residency, AI kill switch, protocol approval, corporate invoicing & immutable audit log.", "香港數據主權、AI 緊急開關、方案審批、企業對帳及不可篡改審計日誌。")}
          </p>
        </div>
        <button type="button" className="ac-btn sm" onClick={() => void loadClinicAdmin()} disabled={busy}>
          <RefreshCw size={13} /> {tr(lang, "Refresh", "重新整理")}
        </button>
      </div>

      {/* Top Governance KPI Row */}
      {overview?.metrics && (
        <div className="ac-grid-4" style={{ marginBottom: "1rem" }}>
          <div className="ac-kpi">
            <div className="ac-kpi-label">{tr(lang, "Pending Reviews", "待醫生審核")}</div>
            <div className="ac-kpi-value">{overview.metrics.pending_proposals ?? 0}</div>
            <div className="ac-kpi-sub">Red flags: {overview.metrics.red_flag_escalations ?? 0}</div>
          </div>
          <div className="ac-kpi">
            <div className="ac-kpi-label">{tr(lang, "Registered Patients", "已登記病人")}</div>
            <div className="ac-kpi-value">{overview.metrics.total_patients ?? 0}</div>
            <div className="ac-kpi-sub">Completed consults: {overview.metrics.completed_encounters ?? 0}</div>
          </div>
          <div className="ac-kpi">
            <div className="ac-kpi-label">{tr(lang, "Doctor-Signed Docs", "醫生已簽發文件")}</div>
            <div className="ac-kpi-value">
              {(overview.metrics.signed_certificates ?? 0) + (overview.metrics.signed_prescriptions ?? 0)}
            </div>
            <div className="ac-kpi-sub">
              Certs: {overview.metrics.signed_certificates ?? 0} • Rx: {overview.metrics.signed_prescriptions ?? 0}
            </div>
          </div>
          <div className="ac-kpi">
            <div className="ac-kpi-label">{tr(lang, "Flu Vaccinations", "流感疫苗預約")}</div>
            <div className="ac-kpi-value">{overview.metrics.flu_vaccinations ?? 0}</div>
            <div className="ac-kpi-sub">Outbox DLQ: {overview.metrics.outbox_dlq ?? 0}</div>
          </div>
        </div>
      )}

      {/* Sub-navigation */}
      <nav className="ac-tabs">
        <button type="button" className={`ac-tab${adminTab === "governance" ? " active" : ""}`} data-testid="admin-tab-governance" onClick={() => setAdminTab("governance")}>
          <ShieldCheck size={14} /> {tr(lang, "Residency, Kill Switch & Protocols", "數據駐留、AI 開關與方案")}
        </button>
        <button type="button" className={`ac-tab${adminTab === "observability" ? " active" : ""}`} data-testid="admin-tab-observability" onClick={() => setAdminTab("observability")}>
          <Cpu size={14} /> {tr(lang, "Observability & PDPO Ops", "可觀測性與 PDPO 營運")}
        </button>
        <button type="button" className={`ac-tab${adminTab === "corporates" ? " active" : ""}`} data-testid="admin-tab-corporates" onClick={() => setAdminTab("corporates")}>
          <Building2 size={14} /> {tr(lang, "Corporates & QR Campaigns", "企業客戶與二維碼計劃")}
        </button>
        <button type="button" className={`ac-tab${adminTab === "invoicing" ? " active" : ""}`} data-testid="admin-tab-invoicing" onClick={() => setAdminTab("invoicing")}>
          <FileSpreadsheet size={14} /> {tr(lang, "Corporate Invoicing", "企業結算對帳")}
        </button>
        <button type="button" className={`ac-tab${adminTab === "audit" ? " active" : ""}`} data-testid="admin-tab-audit" onClick={() => setAdminTab("audit")}>
          <History size={14} /> {tr(lang, "Immutable Audit Log", "不可篡改審計日誌")}
        </button>
      </nav>

      {/* TAB 1: RESIDENCY, KILL SWITCH & PROTOCOLS */}
      {adminTab === "governance" && (
        <section>
          <div className="ac-grid-2" style={{ marginBottom: "1rem" }}>
            {/* Data Residency & Cross-Border Policy Card */}
            <div className="ac-card" data-testid="admin-residency-card">
              <div className="ac-card-head">
                <div>
                  <h3 className="ac-card-title">
                    <Globe2 size={16} color="var(--ac-teal)" />
                    {tr(lang, "HK Data Residency & Cross-Border PHI Policy", "香港數據駐留及跨境私隱政策")}
                  </h3>
                </div>
                <span className="ac-badge ok ac-mono">{tenant?.region || "asia-east2"}</span>
              </div>
              <div style={{ display: "grid", gap: "0.45rem", fontSize: "13px" }}>
                <div>
                  <strong>Primary Cloud Region:</strong> <span className="ac-mono">{tenant?.region || "asia-east2"} (Hong Kong)</span>
                </div>
                <div>
                  <strong>Allowed Inference Regions:</strong>{" "}
                  <span className="ac-mono">{(tenant?.allowed_regions || ["asia-east2"]).join(", ")}</span>
                </div>
                <div>
                  <strong>Cross-Border PHI Inference:</strong>{" "}
                  <span className={`ac-badge ${tenant?.allow_cross_border_inference ? "warn" : "ok"}`}>
                    {tenant?.allow_cross_border_inference
                      ? "Permitted ONLY with Mandatory PHI Scrubber"
                      : "Strictly Blocked (HK In-Region Only)"}
                  </span>
                </div>
                <div>
                  <strong>Multi-Tenant Isolation:</strong>{" "}
                  <span className="ac-badge ok">PostgreSQL 15 FORCE ROW LEVEL SECURITY</span>
                </div>
                <div>
                  <strong>DPO Contact:</strong> <span className="ac-mono">{tenant?.dpo_contact || "dpo@tmrw-clinic.hk"}</span>
                </div>
              </div>
            </div>

            {/* AI Kill Switch Toggle Card */}
            <div className="ac-card" data-testid="admin-kill-switch-card">
              <div className="ac-card-head">
                <div>
                  <h3 className="ac-card-title">
                    {tenant?.ai_enabled ? <Activity size={16} color="var(--ac-ok)" /> : <ShieldOff size={16} color="var(--ac-crit)" />}
                    {tr(lang, "Tenant AI Harness Kill Switch", "租戶 AI 系統緊急開關")}
                  </h3>
                  <p className="ac-card-sub">
                    {tr(
                      lang,
                      "Instantly halts all LLM drafting & structuring. Deterministic safety rules & clinician-only care continue uninterrupted.",
                      "即時停止所有 AI 模型運算。確定性危急規則及醫生診症流程保持不受影響。"
                    )}
                  </p>
                </div>
                <span
                  className={`ac-badge ${tenant?.ai_enabled ? "ok" : "crit"}`}
                  data-testid="admin-ai-status-badge"
                >
                  {tenant?.ai_enabled ? "AI ACTIVE" : "KILL SWITCH ENGAGED"}
                </span>
              </div>

              <div className="ac-field">
                <label htmlFor="ks-reason">{tr(lang, "Governance Audit Reason (Optional)", "操作審計理由（選填）")}</label>
                <input
                  id="ks-reason"
                  className="ac-input"
                  data-testid="admin-kill-switch-reason"
                  value={killReason}
                  onChange={(e) => setKillReason(e.target.value)}
                  placeholder="e.g. Incident review / protocol audit"
                />
              </div>

              <button
                type="button"
                className={`ac-btn ${tenant?.ai_enabled ? "danger" : "primary"}`}
                data-testid="admin-kill-switch-btn"
                disabled={busy || !tenant}
                onClick={() => void handleToggleKillSwitch()}
              >
                <Power size={15} />
                <span>
                  {tenant?.ai_enabled
                    ? tr(lang, "Engage AI Kill Switch (Disable AI)", "啟動 AI 緊急停用開關")
                    : tr(lang, "Re-Enable AI Harness", "重新啟用 AI 系統")}
                </span>
              </button>
            </div>
          </div>

          {/* Protocol Versions Approval Workflow */}
          <div className="ac-card" data-testid="admin-protocols-section">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <FileCode2 size={16} color="var(--ac-teal)" />
                  {tr(lang, "Clinical Protocol Versions (Git-Backed YAML + SHA-256 Approval)", "臨床方案版本審批（YAML + SHA-256 指紋）")}
                </h3>
                <p className="ac-card-sub">
                  {tr(
                    lang,
                    "Every agent run pins protocol_id, version, and SHA-256 hash. Draft protocols require explicit clinic admin approval before activation.",
                    "每次 AI 執行均鎖定方案版本及 SHA-256 雜湊值。草稿方案須經診所管理員批核方能生效。"
                  )}
                </p>
              </div>
            </div>

            <div className="ac-table-wrap">
              <table className="ac-table">
                <thead>
                  <tr>
                    <th>Protocol</th>
                    <th>Version</th>
                    <th>Autonomy</th>
                    <th>SHA-256 Hash</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(overview?.protocols || []).map((p) => (
                    <tr key={p.id} data-testid={`protocol-row-${p.id}`}>
                      <td><strong>{p.protocol_id}</strong></td>
                      <td className="ac-mono">{p.version}</td>
                      <td><span className="ac-badge info ac-mono">{p.autonomy_level}</span></td>
                      <td className="ac-mono" style={{ fontSize: "11.5px" }}>{p.content_hash.slice(0, 20)}…</td>
                      <td>
                        <span className={`ac-badge ${p.status === "approved" ? "ok" : p.status === "draft" ? "warn" : ""}`}>
                          {p.status}
                        </span>
                      </td>
                      <td>
                        <div className="ac-btn-row">
                          <button
                            type="button"
                            className="ac-btn sm"
                            onClick={() => setExpandedProtocol(expandedProtocol === p.id ? null : p.id)}
                          >
                            {expandedProtocol === p.id ? "Hide YAML" : "View YAML"}
                          </button>
                          {p.status !== "approved" && (
                            <button
                              type="button"
                              className="ac-btn primary sm"
                              data-testid={`admin-approve-protocol-btn-${p.id}`}
                              disabled={busy}
                              onClick={() => void handleApproveProtocol(p.id)}
                            >
                              <CheckCircle2 size={13} /> {tr(lang, "Approve", "批核")}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {expandedProtocol && (
              <pre className="ac-code-pre" style={{ marginTop: "0.75rem" }}>
                {overview?.protocols.find((p) => p.id === expandedProtocol)?.yaml_content || ""}
              </pre>
            )}
          </div>
        </section>
      )}

      {/* TAB 1B: PRODUCTION OBSERVABILITY, DURABLE OUTBOX & HK PDPO DCR QUEUE */}
      {adminTab === "observability" && (
        <section data-testid="admin-observability-panel">
          {observability && (
            <>
              {/* Live SLO KPI Strip */}
              <div className="ac-grid-4" style={{ marginBottom: "1rem" }}>
                <div className="ac-kpi">
                  <div className="ac-kpi-label">{tr(lang, "Process Uptime", "系統運行時間")}</div>
                  <div className="ac-kpi-value ac-mono">{Math.round(observability.telemetry.uptime_seconds)}s</div>
                  <div className="ac-kpi-sub">
                    Vault: <span className={`ac-badge ${observability.vault.ok ? "ok" : "crit"}`}>{observability.vault.ok ? observability.vault.backend || "OK" : "DEGRADED"}</span>
                  </div>
                </div>
                <div className="ac-kpi">
                  <div className="ac-kpi-label">{tr(lang, "HTTP Requests", "API 總請求量")}</div>
                  <div className="ac-kpi-value ac-mono">{observability.telemetry.http.requests_total}</div>
                  <div className="ac-kpi-sub">
                    5xx Error Rate: <strong className="ac-mono">{observability.telemetry.http.error_rate_5xx_pct}%</strong>
                  </div>
                </div>
                <div className="ac-kpi">
                  <div className="ac-kpi-label">{tr(lang, "Latency P50 / P95 / P99", "延遲百分位 (ms)")}</div>
                  <div className="ac-kpi-value ac-mono" style={{ fontSize: "1.1rem" }}>
                    {observability.telemetry.http.latency_ms.p50} / {observability.telemetry.http.latency_ms.p95} / {observability.telemetry.http.latency_ms.p99}ms
                  </div>
                  <div className="ac-kpi-sub">Prometheus: <span className="ac-mono">/api/v1/metrics</span></div>
                </div>
                <div className="ac-kpi">
                  <div className="ac-kpi-label">{tr(lang, "AI Gateway Resilience", "模型閘道韌性")}</div>
                  <div className="ac-kpi-value ac-mono">{observability.gateway.fallback_count}</div>
                  <div className="ac-kpi-sub">
                    Fallbacks • Breaker trips: {observability.gateway.circuit_breaker_trips}
                  </div>
                </div>
              </div>

              {/* Clinical Safety Gate Counters + Outbox Worker Controls */}
              <div className="ac-grid-2" style={{ marginBottom: "1rem" }}>
                <div className="ac-card" data-testid="admin-safety-counters-card">
                  <h3 className="ac-card-title" style={{ marginBottom: "0.65rem" }}>
                    <Activity size={16} color="var(--ac-teal)" />
                    {tr(lang, "Clinical Safety & PDPO Counters", "臨床安全及 PDPO 指標")}
                  </h3>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem", fontSize: "12.5px" }}>
                    <div>Intakes processed: <strong className="ac-mono">{observability.telemetry.clinical_safety.intakes_total ?? 0}</strong></div>
                    <div>Red flags escalated: <strong className="ac-mono" style={{ color: "var(--ac-crit)" }}>{observability.telemetry.clinical_safety.red_flags_escalated_total ?? 0}</strong></div>
                    <div>Prompt injections neutralized: <strong className="ac-mono">{observability.telemetry.clinical_safety.prompt_injections_neutralized_total ?? 0}</strong></div>
                    <div>Rx safety checks: <strong className="ac-mono">{observability.telemetry.clinical_safety.rx_safety_checks_total ?? 0}</strong></div>
                    <div>Rx Cap. 134 / Hard blocks: <strong className="ac-mono" style={{ color: "var(--ac-crit)" }}>{observability.telemetry.clinical_safety.rx_hard_blocks_total ?? 0}</strong></div>
                    <div>Certs / Rx signed: <strong className="ac-mono">{observability.telemetry.clinical_safety.certs_signed_total ?? 0} / {observability.telemetry.clinical_safety.prescriptions_signed_total ?? 0}</strong></div>
                    <div>WA Doorbell sends (0 PHI): <strong className="ac-mono">{observability.telemetry.clinical_safety.wa_messages_sent_total ?? 0}</strong></div>
                    <div>PDPO DAR / DCR / Holds: <strong className="ac-mono">{observability.telemetry.clinical_safety.pdpo_dar_exports_total ?? 0} / {observability.telemetry.clinical_safety.pdpo_dcr_submitted_total ?? 0} / {observability.telemetry.clinical_safety.pdpo_deletions_total ?? 0}</strong></div>
                  </div>
                </div>

                <div className="ac-card" data-testid="admin-outbox-card">
                  <div className="ac-card-head">
                    <div>
                      <h3 className="ac-card-title">
                        <Cpu size={16} color="var(--ac-teal)" />
                        {tr(lang, "Durable PostgreSQL Outbox Worker", "持久化 PostgreSQL 工作佇列")}
                      </h3>
                      <p className="ac-card-sub">
                        {tr(
                          lang,
                          "Reminders, 48h check-in nudges, and SLA escalations survive container restarts.",
                          "預約提醒、48小時跟進及 SLA 升級均持久化儲存，無懼重啟。"
                        )}
                      </p>
                    </div>
                    <button
                      type="button"
                      className="ac-btn primary sm"
                      data-testid="admin-process-outbox-btn"
                      disabled={busy}
                      onClick={() => void handleProcessOutbox()}
                    >
                      <Play size={13} /> {tr(lang, "Process Due Jobs", "立即執行到期任務")}
                    </button>
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginBottom: "0.6rem" }}>
                    <span className="ac-badge info ac-mono">Pending: {observability.outbox.counts.pending}</span>
                    <span className="ac-badge ok ac-mono">Done: {observability.outbox.counts.done}</span>
                    <span className={`ac-badge ${observability.outbox.counts.dlq > 0 ? "crit" : "ok"} ac-mono`}>DLQ: {observability.outbox.counts.dlq}</span>
                    <span className={`ac-badge ${observability.outbox.worker.enabled ? "ok" : "warn"}`}>
                      Worker: {observability.outbox.worker.enabled ? `AUTO (${observability.outbox.worker.poll_interval_seconds}s)` : "ON-DEMAND"}
                    </span>
                  </div>
                  {observability.outbox.recent_events.length > 0 && (
                    <div style={{ fontSize: "11.5px", color: "var(--ac-ink-2)" }}>
                      {observability.outbox.recent_events.slice(0, 4).map((ev) => (
                        <div key={ev.id} style={{ display: "flex", justifyContent: "space-between", padding: "0.22rem 0", borderTop: "1px solid #edf2f5" }}>
                          <span className="ac-mono">{ev.event_type}</span>
                          <span className={`ac-badge ${ev.status === "done" ? "ok" : ev.status === "dlq" ? "crit" : "info"}`} style={{ fontSize: "10px" }}>
                            {ev.status} ({ev.attempts}/{ev.max_attempts})
                          </span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}

          {/* HK PDPO DPP6 Data Correction Request (DCR) Governance Queue */}
          <div className="ac-card" data-testid="admin-dcr-section">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <FileCheck2 size={16} color="var(--ac-teal)" />
                  {tr(lang, "HK PDPO DPP6 Data Correction & Rights Queue", "香港 PDPO DPP6 資料更正及私隱請求隊列")}
                </h3>
                <p className="ac-card-sub">
                  {tr(
                    lang,
                    "Resolutions append audited addenda under Hong Kong PDPO s.22–23 without mutating historical clinical records.",
                    "審批結果依香港 PDPO 第22–23條寫入不可篡改附加紀錄，絕不覆寫歷史病歷。"
                  )}
                </p>
              </div>
            </div>

            <div className="ac-table-wrap">
              <table className="ac-table" data-testid="admin-dcr-table">
                <thead>
                  <tr>
                    <th>Patient</th>
                    <th>Type</th>
                    <th>Details</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {dataRequests.map((r) => (
                    <tr key={r.id} data-testid={`admin-dcr-row-${r.id}`}>
                      <td>
                        <strong>{r.patient_name_en || r.patient_id}</strong>
                        {r.hkid_masked ? <div className="ac-mono" style={{ fontSize: "11px", color: "var(--ac-ink-3)" }}>{r.hkid_masked}</div> : null}
                      </td>
                      <td className="ac-mono">{r.request_type}</td>
                      <td className="ac-mono" style={{ fontSize: "11.5px", maxWidth: 260, wordBreak: "break-word" }}>
                        {JSON.stringify(r.details || {})}
                      </td>
                      <td>
                        <span className={`ac-badge ${r.status === "approved_addendum" ? "ok" : r.status === "queued_for_review" ? "warn" : "info"}`}>
                          {r.status}
                        </span>
                      </td>
                      <td>
                        {r.status === "queued_for_review" ? (
                          <div className="ac-btn-row">
                            <button
                              type="button"
                              className="ac-btn primary sm"
                              data-testid={`admin-dcr-approve-${r.id}`}
                              disabled={busy}
                              onClick={() => void handleResolveDcr(r.id, "approved")}
                            >
                              <CheckCircle2 size={12} /> {tr(lang, "Approve Addendum", "批核附加紀錄")}
                            </button>
                            <button
                              type="button"
                              className="ac-btn sm"
                              data-testid={`admin-dcr-reject-${r.id}`}
                              disabled={busy}
                              onClick={() => void handleResolveDcr(r.id, "rejected")}
                            >
                              {tr(lang, "Reject", "拒絕")}
                            </button>
                          </div>
                        ) : (
                          <span style={{ fontSize: "11.5px", color: "var(--ac-ink-3)" }}>{r.resolution_note || "Finalized"}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {dataRequests.length === 0 && (
                    <tr>
                      <td colSpan={5} style={{ color: "var(--ac-ink-3)" }}>
                        {tr(lang, "No patient PDPO data correction or deletion requests logged yet.", "暫無病人 PDPO 資料更正或刪除請求。")}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {/* TAB 2: CORPORATES & QR CAMPAIGNS */}
      {adminTab === "corporates" && (
        <section>
          <div className="ac-grid-2" style={{ marginBottom: "1rem" }}>
            <form className="ac-card" onSubmit={(e) => void handleCreateCorporate(e)}>
              <h3 className="ac-card-title" style={{ marginBottom: "0.65rem" }}>
                <Plus size={16} color="var(--ac-teal)" /> {tr(lang, "Onboard Corporate Client", "新增企業客戶")}
              </h3>
              <div className="ac-field">
                <label htmlFor="ac-cname">{tr(lang, "Company Name", "公司名稱")}</label>
                <input id="ac-cname" className="ac-input" data-testid="admin-corp-name" required value={corpName} onChange={(e) => setCorpName(e.target.value)} placeholder="Pacific Legal Partners HK" />
              </div>
              <div className="ac-field">
                <label htmlFor="ac-ccap">{tr(lang, "Total Spend Cap (HKD)", "總支出上限（港幣）")}</label>
                <input id="ac-ccap" type="number" className="ac-input" data-testid="admin-corp-cap" required value={corpCap} onChange={(e) => setCorpCap(e.target.value)} />
              </div>
              <div className="ac-grid-2">
                <div className="ac-field">
                  <label htmlFor="ac-cbill">{tr(lang, "Billing Email", "帳單電郵")}</label>
                  <input id="ac-cbill" type="email" className="ac-input" data-testid="admin-corp-billing" required value={corpBillingEmail} onChange={(e) => setCorpBillingEmail(e.target.value)} placeholder="ap@company.hk" />
                </div>
                <div className="ac-field">
                  <label htmlFor="ac-cadm">{tr(lang, "Corporate Admin Email", "企業管理員電郵")}</label>
                  <input id="ac-cadm" type="email" className="ac-input" data-testid="admin-corp-admin" required value={corpAdminEmail} onChange={(e) => setCorpAdminEmail(e.target.value)} placeholder="hr@company.hk" />
                </div>
              </div>
              <button type="submit" className="ac-btn primary" data-testid="admin-create-corp-btn" disabled={busy}>
                <Plus size={14} /> {tr(lang, "Create Corporate", "建立企業客戶")}
              </button>
            </form>

            <form className="ac-card" onSubmit={(e) => void handleAdminCreateCampaign(e)}>
              <h3 className="ac-card-title" style={{ marginBottom: "0.65rem" }}>
                <QrCode size={16} color="var(--ac-teal)" /> {tr(lang, "Create Corporate QR Campaign", "建立企業二維碼計劃")}
              </h3>
              <div className="ac-field">
                <label htmlFor="ac-camp-corp">{tr(lang, "Corporate Client", "所屬企業")}</label>
                <select id="ac-camp-corp" className="ac-select" value={campCorpId} onChange={(e) => setCampCorpId(e.target.value)}>
                  {(overview?.corporates || []).map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
              </div>
              <div className="ac-grid-2">
                <div className="ac-field">
                  <label htmlFor="ac-camp-code">{tr(lang, "QR Campaign Code", "計劃代碼")}</label>
                  <input id="ac-camp-code" className="ac-input ac-mono" data-testid="admin-camp-code" required value={campCode} onChange={(e) => setCampCode(e.target.value)} placeholder="PAC-FLU-2026" />
                </div>
                <div className="ac-field">
                  <label htmlFor="ac-camp-name">{tr(lang, "Campaign Title", "計劃名稱")}</label>
                  <input id="ac-camp-name" className="ac-input" data-testid="admin-camp-name" required value={campName} onChange={(e) => setCampName(e.target.value)} placeholder="2026 Flu Season Care" />
                </div>
              </div>
              <div className="ac-grid-2">
                <div className="ac-field">
                  <label htmlFor="ac-camp-cap">{tr(lang, "Campaign Cap (HKD)", "計劃上限（港幣）")}</label>
                  <input id="ac-camp-cap" type="number" className="ac-input" value={campCap} onChange={(e) => setCampCap(e.target.value)} />
                </div>
                <div className="ac-field">
                  <label htmlFor="ac-camp-fee">{tr(lang, "Unit Consult Fee (HKD)", "單次診金（港幣）")}</label>
                  <input id="ac-camp-fee" type="number" className="ac-input" value={campFee} onChange={(e) => setCampFee(e.target.value)} />
                </div>
              </div>
              <button type="submit" className="ac-btn primary" data-testid="admin-create-campaign-btn" disabled={busy || !campCorpId}>
                <Plus size={14} /> {tr(lang, "Create QR Campaign", "建立二維碼計劃")}
              </button>
            </form>
          </div>

          <div className="ac-card">
            <h3 className="ac-card-title" style={{ marginBottom: "0.6rem" }}>
              {tr(lang, "Active Corporate QR Campaigns", "生效中之企業二維碼計劃")}
            </h3>
            <div className="ac-table-wrap">
              <table className="ac-table">
                <thead>
                  <tr>
                    <th>Code</th>
                    <th>Name</th>
                    <th>Unit Fee</th>
                    <th>Spend / Cap (HKD)</th>
                    <th>QR Poster</th>
                  </tr>
                </thead>
                <tbody>
                  {(overview?.campaigns || []).map((c) => (
                    <tr key={c.id}>
                      <td className="ac-mono"><strong>{c.code}</strong></td>
                      <td>{c.name}</td>
                      <td className="ac-mono">HK${c.unit_fee_hkd}</td>
                      <td className="ac-mono">HK${c.spent_hkd.toLocaleString()} / ${c.spend_cap_hkd.toLocaleString()}</td>
                      <td>
                        <button type="button" className="ac-btn sm" onClick={() => onOpenCampaignQr(c.code)}>
                          <QrCode size={13} /> Open Poster
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}

      {/* TAB 3: CORPORATE INVOICING & RECONCILIATION */}
      {adminTab === "invoicing" && (
        <section className="ac-card" data-testid="admin-invoicing-section">
          <div className="ac-card-head">
            <div>
              <h3 className="ac-card-title">
                <FileSpreadsheet size={16} color="var(--ac-teal)" />
                {tr(lang, "Corporate Billing & Spend-Cap Reconciliation", "企業帳單及預算上限對帳表")}
              </h3>
              <p className="ac-card-sub">
                {tr(lang, "Aggregate billing ledger — zero individual patient names or diagnoses included on corporate invoices.", "匿名匯總結算 — 企業帳單絕不包含個別員工姓名或診斷。")}
              </p>
            </div>
            {invoicing?.total_billable_hkd !== undefined && (
              <span className="ac-badge teal ac-mono">
                Total Billable: HK${invoicing.total_billable_hkd.toLocaleString()}
              </span>
            )}
          </div>

          <div className="ac-table-wrap">
            <table className="ac-table" data-testid="admin-invoicing-table">
              <thead>
                <tr>
                  <th>Corporate</th>
                  <th>Campaign</th>
                  <th>Billable Visits</th>
                  <th>Unit Fee</th>
                  <th>Total (HKD)</th>
                  <th>Cap Utilization</th>
                </tr>
              </thead>
              <tbody>
                {(invoicing?.rows || []).map((r, i) => {
                  const pct = r.utilization_pct ?? (r.spend_cap_hkd > 0 ? Math.round((r.total_hkd / r.spend_cap_hkd) * 100) : 0);
                  return (
                    <tr key={`${r.campaign_code}-${i}`}>
                      <td><strong>{r.corporate_name}</strong></td>
                      <td className="ac-mono">{r.campaign_code}</td>
                      <td className="ac-mono">{r.billable_visits}</td>
                      <td className="ac-mono">HK${r.unit_fee_hkd}</td>
                      <td className="ac-mono"><strong>HK${r.total_hkd.toLocaleString()}</strong></td>
                      <td style={{ minWidth: 160 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "11.5px" }}>
                          <span className="ac-mono">{pct}% of ${r.spend_cap_hkd.toLocaleString()}</span>
                          {r.cap_reached && (
                            <span className="ac-badge crit">
                              <AlertTriangle size={10} /> Cap Reached
                            </span>
                          )}
                        </div>
                        <div className="ac-progress">
                          <div className={`ac-progress-fill ${pct >= 90 ? "crit" : pct >= 70 ? "warn" : ""}`} style={{ width: `${Math.min(100, pct)}%` }} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
                {(invoicing?.rows || []).length === 0 && (
                  <tr>
                    <td colSpan={6} style={{ color: "var(--ac-ink-3)" }}>No billable corporate rows yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* TAB 4: IMMUTABLE AUDIT LOG VIEWER */}
      {adminTab === "audit" && (
        <section className="ac-card" data-testid="admin-audit-section">
          <div className="ac-card-head">
            <div>
              <h3 className="ac-card-title">
                <History size={16} color="var(--ac-teal)" />
                {tr(lang, "Tenant Immutable Audit Log (PostgreSQL Append-Only Trigger)", "租戶不可篡改審計日誌")}
              </h3>
            </div>
            <input
              className="ac-input"
              style={{ maxWidth: 260 }}
              data-testid="admin-audit-filter"
              value={auditFilter}
              onChange={(e) => setAuditFilter(e.target.value)}
              placeholder={tr(lang, "Filter by action, role, resource…", "按操作、角色或資源篩選…")}
            />
          </div>

          <div className="ac-table-wrap">
            <table className="ac-table" data-testid="admin-audit-table">
              <thead>
                <tr>
                  <th>Time (HKT)</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Resource</th>
                  <th>Metadata</th>
                </tr>
              </thead>
              <tbody>
                {filteredAudit.map((ev) => (
                  <tr key={ev.id}>
                    <td className="ac-mono" style={{ whiteSpace: "nowrap" }}>{new Date(ev.created_at).toLocaleString()}</td>
                    <td>
                      <span className="ac-badge">{ev.actor_role}</span>{" "}
                      <span className="ac-mono" style={{ fontSize: "11.5px" }}>{ev.actor_id}</span>
                    </td>
                    <td>
                      <span className={`ac-badge ${ev.break_glass ? "crit" : "teal"}`}>{ev.action}</span>
                    </td>
                    <td className="ac-mono">{ev.resource_type}/{ev.resource_id}</td>
                    <td className="ac-mono" style={{ fontSize: "11px", maxWidth: 320, wordBreak: "break-all" }}>
                      {JSON.stringify(ev.metadata || {})}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
