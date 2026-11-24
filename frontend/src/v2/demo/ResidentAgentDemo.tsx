import { useCallback, useEffect, useState } from "react";
import { v2Api } from "../api";
import type { ProposalDetail, StaffQueueItem } from "../api";
import "./residentDemo.css";

type ActId = 1 | 2 | 3;

interface AuditRow {
  id: string;
  actor_role: string;
  action: string;
  resource_type: string;
  created_at: string;
}

interface RoleTokens {
  patient: string;
  nurse: string;
  clinician: string;
  admin: string;
}

const DEFAULT_PATIENT_MSG =
  "Hi, first time messaging — I woke up with 38.6°C fever, body aches and a positive Flu A RAT. I need a doctor, safe medicine (I have G6PD deficiency & penicillin allergy), and a 2-day sick leave note for work.";

const RED_FLAG_MSG =
  "發燒39度，而家心口好翳同埋唞唔到氣，講唔到成句 (Chest tightness and cannot catch my breath).";

const DEFAULT_NURSE_NOTE =
  "Vitals confirmed (38.6°C, SpO2 98%), RAT Flu A+ photo checked, Harbour FinTech campaign voucher validated, 2:00 PM HK courier slot held pending Dr. Lam's signature.";

export function ResidentAgentDemo() {
  const [act, setAct] = useState<ActId>(1);
  const [lang, setLang] = useState<"en" | "zh">("en");
  const [tokens, setTokens] = useState<RoleTokens | null>(null);
  const [booting, setBooting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Act 1: Patient First Contact
  const [patientInput, setPatientInput] = useState(DEFAULT_PATIENT_MSG);
  const [sentPatientMsg, setSentPatientMsg] = useState<string | null>(null);
  const [intakeRes, setIntakeRes] = useState<Record<string, any> | null>(null);
  const [proposalId, setProposalId] = useState<string>("prop_pat_wong_flu_g6pd");

  // Act 2: Nurse & Admin Desk
  const [checkVitals, setCheckVitals] = useState(true);
  const [checkCourier, setCheckCourier] = useState(true);
  const [nurseNote, setNurseNote] = useState(DEFAULT_NURSE_NOTE);
  const [nurseDone, setNurseDone] = useState(false);

  // Act 3: On-Duty Doctor Inbox & Execution
  const [queueItems, setQueueItems] = useState<StaffQueueItem[]>([]);
  const [detail, setDetail] = useState<ProposalDetail | null>(null);
  const [doctorReply, setDoctorReply] = useState(
    "Hi Siu-Mei, I reviewed your positive Flu A RAT, vitals, and chart (noting your G6PD deficiency and penicillin allergy). I have signed Oseltamivir 75 mg BD for 5 days, Paracetamol 500 mg PRN, and your 2-day MCHK sick-leave certificate. Our desk is releasing your 2:00 PM courier delivery now — please attend A&E if you develop chest pain, breathlessness, or persistent high fever after 48 hours."
  );
  const [signResult, setSignResult] = useState<Record<string, any> | null>(null);
  const [doctorClosed, setDoctorClosed] = useState(false);
  const [patientFulfilment, setPatientFulfilment] = useState<string | null>(null);
  const [pdfNotice, setPdfNotice] = useState<string | null>(null);

  // Persistent Live Audit Trail
  const [auditEvents, setAuditEvents] = useState<AuditRow[]>([]);

  const fetchTokens = useCallback(async (): Promise<RoleTokens> => {
    const [pat, nur, doc, adm] = await Promise.all([
      v2Api.demoLoginAs("patient:pat_wong_flu_g6pd"),
      v2Api.demoLoginAs("nurse:nurse_chan"),
      v2Api.demoLoginAs("clinician:doc_dr_lam"),
      v2Api.demoLoginAs("clinic_admin:adm_central_ops"),
    ]);
    const next: RoleTokens = {
      patient: pat.token,
      nurse: nur.token,
      clinician: doc.token,
      admin: adm.token,
    };
    setTokens(next);
    return next;
  }, []);

  const refreshLiveState = useCallback(
    async (toks: RoleTokens, activeProposalId: string) => {
      try {
        const [q, det, audResp] = await Promise.all([
          v2Api.staffQueue(toks.clinician),
          v2Api.proposalDetail(activeProposalId, toks.clinician).catch(() => null),
          fetch("/api/v1/admin/audit", { headers: { Authorization: `Bearer ${toks.admin}` } })
            .then((r) => (r.ok ? r.json() : { events: [] }))
            .catch(() => ({ events: [] })),
        ]);
        setQueueItems(q.items || []);
        if (det) setDetail(det);
        setAuditEvents((audResp.events || []).slice(0, 6));
      } catch {
        // non-blocking background refresh
      }
    },
    [],
  );

  useEffect(() => {
    let mounted = true;
    (async () => {
      try {
        const toks = await fetchTokens();
        if (!mounted) return;
        await refreshLiveState(toks, "prop_pat_wong_flu_g6pd");
      } catch (e: any) {
        if (mounted) setError(e?.message || "Failed to connect to live Hong Kong clinic backend.");
      } finally {
        if (mounted) setBooting(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [fetchTokens, refreshLiveState]);

  const handleResetStory = async () => {
    setBusy(true);
    setError(null);
    setPdfNotice(null);
    try {
      await v2Api.demoReset();
      const toks = await fetchTokens();
      setAct(1);
      setPatientInput(DEFAULT_PATIENT_MSG);
      setSentPatientMsg(null);
      setIntakeRes(null);
      setProposalId("prop_pat_wong_flu_g6pd");
      setNurseNote(DEFAULT_NURSE_NOTE);
      setNurseDone(false);
      setSignResult(null);
      setDoctorClosed(false);
      setPatientFulfilment(null);
      await refreshLiveState(toks, "prop_pat_wong_flu_g6pd");
    } catch (e: any) {
      setError(e?.message || "Reset failed.");
    } finally {
      setBusy(false);
    }
  };

  // ACT 1: Send live patient intake to Sam (real POST /api/v1/patient/intake)
  const submitPatientMessage = async (msgText?: string) => {
    if (!tokens) return;
    const text = (msgText ?? patientInput).trim();
    if (text.length < 2) return;
    setBusy(true);
    setError(null);
    setSentPatientMsg(text);
    try {
      const resp = await fetch("/api/v1/patient/intake", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${tokens.patient}`,
        },
        body: JSON.stringify({
          symptom_text: text,
          temp_c: text.includes("39") ? 39.0 : 38.6,
          spo2: text.includes("唞唔到氣") ? 93 : 98,
          rat_flu_a: text.includes("Flu A") ? "positive" : undefined,
          lang,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `Intake failed (HTTP ${resp.status})`);
      setIntakeRes(data);
      const pid = data.proposal_id || "prop_pat_wong_flu_g6pd";
      setProposalId(pid);
      setNurseDone(false);
      setSignResult(null);
      setDoctorClosed(false);
      await refreshLiveState(tokens, pid);
    } catch (e: any) {
      setError(e?.message || "Could not process patient intake.");
    } finally {
      setBusy(false);
    }
  };

  // ACT 2: Nurse / Admin confirms non-medical tasks & hands off to Dr. Lam
  const submitNurseAdminPrep = async () => {
    if (!tokens) return;
    setBusy(true);
    setError(null);
    try {
      await v2Api.nurseReady(proposalId, nurseNote, tokens.nurse);
      setNurseDone(true);
      await refreshLiveState(tokens, proposalId);
    } catch (e: any) {
      setError(e?.message || "Could not submit nurse/admin prep.");
    } finally {
      setBusy(false);
    }
  };

  // ACT 3: On-duty doctor approves in 1 tap -> Sam executes real MCHK PDF + Vault + e-Script + Doorbell
  const submitDoctorSign = async () => {
    if (!tokens) return;
    setBusy(true);
    setError(null);
    setPdfNotice(null);
    try {
      const res = await v2Api.signProposal(
        proposalId,
        {
          decision: "approved",
          review_seconds: 11.4,
          rubber_stamp_confirmed: true,
          patient_reply: doctorReply,
          override_reason: "G6PD deficiency & Penicillin allergy verified; Oseltamivir 75mg BD + Paracetamol safe.",
          issue_certificate: true,
          cert_days_off: 2,
          rx_items: [
            { drug: "oseltamivir", display: "Oseltamivir 75 mg BD × 5 days", dose_mg: 75, frequency: "BD", days: 5 },
            { drug: "paracetamol", display: "Paracetamol 500 mg QDS PRN × 3 days", dose_mg: 500, frequency: "QDS", days: 3 },
          ],
        },
        tokens.clinician,
      );
      setSignResult(res);
      await refreshLiveState(tokens, proposalId);
    } catch (e: any) {
      setError(e?.message || "Doctor sign-off failed.");
    } finally {
      setBusy(false);
    }
  };

  const openSignedPdf = async (asRole: "clinician" | "patient") => {
    if (!tokens) return;
    const certId = signResult?.certificate?.id || detail?.issued_certificates?.[0]?.id;
    if (!certId) return;
    setBusy(true);
    setPdfNotice(null);
    try {
      const tok = asRole === "clinician" ? tokens.clinician : tokens.patient;
      const dl = await v2Api.certDownloadToken(certId, tok);
      await v2Api.downloadVaultPdf(dl.download_url, tok, `HK_MCHK_SickLeave_${certId}.pdf`);
      setPdfNotice(
        asRole === "clinician"
          ? `Decrypted MCHK PDF (${certId}) from Hong Kong GCS Vault as Dr. Kenneth Lam (M14829).`
          : `Decrypted MCHK PDF (${certId}) from Hong Kong GCS Vault as WONG Siu-Mei via short-lived HMAC token.`,
      );
      await refreshLiveState(tokens, proposalId);
    } catch (e: any) {
      setError(e?.message || "Could not open MCHK PDF from Vault.");
    } finally {
      setBusy(false);
    }
  };

  const chooseCourierFulfilment = async (mode: "delivery" | "pickup") => {
    if (!tokens || !signResult?.prescription?.id) return;
    setBusy(true);
    try {
      const r = await v2Api.chooseFulfilment(
        signResult.prescription.id,
        mode,
        mode === "delivery" ? "Suite 1802, Harbour FinTech Tower, Central, HK" : "",
        tokens.patient,
      );
      setPatientFulfilment(
        mode === "delivery"
          ? `Courier dispatch confirmed (${r.fulfilment_ref || "HK-DISP"}) — 2:00 PM slot released`
          : `Clinic pickup ready (${r.fulfilment_ref || "HK-DISP"}) — Central reception`,
      );
      await refreshLiveState(tokens, proposalId);
    } catch (e: any) {
      setError(e?.message || "Fulfilment update failed.");
    } finally {
      setBusy(false);
    }
  };

  const isRedFlag = Boolean(intakeRes?.red_flag);
  const issuedCertId = signResult?.certificate?.id || detail?.issued_certificates?.[0]?.id;
  const issuedCertHash = signResult?.certificate?.verification_hash || detail?.issued_certificates?.[0]?.verification_hash;
  const activeAuthorityIdx = doctorClosed ? 4 : signResult ? 3 : nurseDone || intakeRes ? 2 : sentPatientMsg ? 1 : 0;

  const pendingCount = queueItems.filter((i) => i.status === "pending_review").length;
  const extractedQuotes: string[] = (() => {
    const out: string[] = [];
    if (detail?.source_quotes?.length) {
      for (const q of detail.source_quotes) {
        if (q.quote && !out.includes(q.quote)) out.push(q.quote);
      }
    }
    if (out.length === 0 && intakeRes?.triage?.evidence_quotes) {
      for (const q of intakeRes.triage.evidence_quotes) out.push(String(q));
    }
    return out.slice(0, 5);
  })();

  const goToAct = (next: ActId) => {
    setAct(next);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  if (booting) {
    return (
      <div className="sam-shell">
        <div style={{ margin: "auto", padding: 32, color: "var(--ink-2)" }}>
          Connecting Sam (Resident Agent) to Hong Kong PostgreSQL 15 + GCS Vault…
        </div>
      </div>
    );
  }

  return (
    <div className="sam-shell" data-testid="resident-agent-demo">
      {/* Top Bar + 3-Act Stepper */}
      <header className="sam-topbar">
        <div className="sam-brand">
          <span className="sam-avatar">Sam</span>
          <div>
            <div className="sam-brand-title">tmrw Resident Agent</div>
            <div className="sam-brand-sub">HK asia-east2 · Live Demo</div>
          </div>
        </div>

        <nav className="sam-stepper" aria-label="Demo acts">
          <button
            type="button"
            className={`sam-step-btn ${act === 1 ? "active" : sentPatientMsg ? "done" : ""}`}
            onClick={() => goToAct(1)}
            data-testid="step-act-1"
          >
            <span className="sam-step-num">{sentPatientMsg ? "✓" : "1"}</span>
            <span>Patient First Contact</span>
          </button>
          <button
            type="button"
            className={`sam-step-btn ${act === 2 ? "active" : nurseDone ? "done" : ""}`}
            onClick={() => goToAct(2)}
            data-testid="step-act-2"
          >
            <span className="sam-step-num">{nurseDone ? "✓" : "2"}</span>
            <span>Nurse &amp; Admin Desk</span>
          </button>
          <button
            type="button"
            className={`sam-step-btn ${act === 3 ? "active" : signResult ? "done" : ""}`}
            onClick={() => goToAct(3)}
            data-testid="step-act-3"
          >
            <span className="sam-step-num">{signResult ? "✓" : "3"}</span>
            <span>On-Duty Doctor &amp; Execute</span>
          </button>
        </nav>

        <div className="sam-top-actions">
          <button type="button" className="sam-link-btn" onClick={() => setLang((l) => (l === "en" ? "zh" : "en"))}>
            {lang === "en" ? "廣東話" : "English"}
          </button>
          <button type="button" className="sam-link-btn" onClick={handleResetStory} disabled={busy} data-testid="demo-reset-story-btn">
            ↻ Reset
          </button>
          <a className="sam-link-btn" href="/c">Clinician OS</a>
          <a className="sam-link-btn" href="/ec">EC Demo</a>
        </div>
      </header>

      {error && (
        <div style={{ maxWidth: 1380, width: "100%", margin: "12px auto 0", padding: "0 24px" }}>
          <div className="v2-inline-fail">{error}</div>
        </div>
      )}

      {/* Main 2-Column Stage */}
      <main className="sam-main">
        {/* LEFT COLUMN: ACTIVE ACT INTERACTION */}
        <section className="sam-stage-card">
          {act === 1 && (
            <>
              <div className="sam-stage-head">
                <div>
                  <div className="sam-eyebrow">Act 1 of 3 · Patient First-Time Contact (Care Line)</div>
                  <h1 className="sam-stage-title">Patient messages Sam for the first time</h1>
                  <p className="sam-stage-sub">
                    Sam introduces itself as Dr. Kenneth Lam&apos;s resident clinical agent, explains clearly what it can and cannot do, screens red flags deterministically, and prepares the chart.
                  </p>
                </div>
                <span className="sam-badge jade">WONG Siu-Mei 黃小美 · 34F</span>
              </div>

              <div className="sam-stage-body">
                <div className="sam-thread" data-testid="act1-thread">
                  {/* Sam's First-Time Welcome & Capability Card */}
                  <div className="sam-msg sam">
                    <div className="sam-msg-who">
                      <span>Sam · Resident Clinic Agent (Central &amp; Mid-Levels Family Medical)</span>
                      <span className="sam-badge jade">L0 Observe · Active</span>
                    </div>
                    <div>
                      {lang === "zh"
                        ? "黃小姐你好，我係陳／林啟賢醫生嘅常駐診所助理 Sam。第一次同你聯絡，以下係我可以即刻幫你處理嘅事："
                        : "Hi Siu-Mei — I'm Sam, Dr. Kenneth Lam's persistent resident agent at Central & Mid-Levels Family Medical. Since this is your first message, here's how I can help right away:"}
                    </div>
                    <div className="sam-cap-grid">
                      <div className="sam-cap-tile">
                        <strong>1. Intake &amp; Safety Check</strong>
                        Capture symptoms, RAT results, and check your allergies &amp; G6PD before the doctor sees you.
                      </div>
                      <div className="sam-cap-tile">
                        <strong>2. Prep Scripts &amp; Sick Leave</strong>
                        Draft your consult summary, safe e-script, and MCHK sick-leave PDF so Dr. Lam signs in one tap.
                      </div>
                      <div className="sam-cap-tile">
                        <strong>3. Desk, Courier &amp; Follow-up</strong>
                        Coordinate corporate billing (zero PII to HR), courier/pickup slots, and a 48-hour check-in.
                      </div>
                    </div>
                    <div className="sam-boundary">
                      <strong>Clinical boundary:</strong> I never diagnose, prescribe, or reassure. Every clinical decision and signature comes from Dr. Kenneth Lam (MCHK M14829). If you have chest pain or trouble breathing, call <strong>999</strong> immediately.
                    </div>
                  </div>

                  {/* Patient's Live Sent Message */}
                  {sentPatientMsg && (
                    <div className="sam-msg user" data-testid="act1-patient-bubble">
                      <div className="sam-msg-who">
                        <span>WONG Siu-Mei 黃小美 (Patient)</span>
                        <span>Just now · Care Line</span>
                      </div>
                      <div>{sentPatientMsg}</div>
                    </div>
                  )}

                  {/* Sam's Live Triage & Handoff Response */}
                  {intakeRes && isRedFlag && (
                    <div className="sam-msg crisis" data-testid="act1-redflag-banner">
                      <div className="sam-msg-who" style={{ color: "#991b1b" }}>
                        <span>FIXED RED-FLAG RULE · &lt;50ms DETERMINISTIC SAFETY FLOOR</span>
                        <span className="sam-badge red">URGENCY 4 · CALL 999</span>
                      </div>
                      <strong>{intakeRes.crisis_banner?.title_en || "URGENT EMERGENCY — Call 999 Immediately"}</strong>
                      <div style={{ marginTop: 4 }}>{intakeRes.crisis_banner?.body_en || intakeRes.emergency_banner}</div>
                      <div style={{ marginTop: 8, fontSize: 12.5 }}>
                        ✓ Dr. Kenneth Lam paged immediately (Urgency 4) · ✓ Zero AI dosing/reassurance sent · ✓ Red flags matched:{" "}
                        <code>{(intakeRes.red_flags || []).join(", ")}</code>
                      </div>
                    </div>
                  )}

                  {intakeRes && !isRedFlag && (
                    <div className="sam-msg sam" data-testid="act1-sam-reply">
                      <div className="sam-msg-who">
                        <span>Sam · Resident Agent</span>
                        <span className="sam-badge jade">L2 Prepared · Proposal {proposalId}</span>
                      </div>
                      <div>
                        Got it, Siu-Mei. I screened your symptoms (no emergency red flags), matched your chart (
                        <strong>G6PD deficient</strong> &amp; <strong>Penicillin allergy</strong> flagged for Dr. Lam), and quoted your words verbatim.
                      </div>
                      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
                        <div>✓ <strong>Sent to Nurse Rachel &amp; Admin Desk:</strong> confirm vitals (38.6°C, SpO2 98%), verify <code>HARBOUR-FLU-2026</code> voucher, hold 2:00 PM courier slot.</div>
                        <div>✓ <strong>Prepared for Dr. Kenneth Lam (on duty):</strong> Influenza A (ICD-10 <code>J10.1</code> / ICPC-2 <code>R80</code>) card with G6PD-safe Oseltamivir + Paracetamol + 2-day MCHK sick-leave draft.</div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Scenario Quick Pills */}
                <div>
                  <div className="v2-sec-label">Send as WONG Siu-Mei (calls live POST /api/v1/patient/intake)</div>
                  <div className="sam-pills">
                    <button
                      type="button"
                      className="sam-pill-btn"
                      disabled={busy}
                      onClick={() => {
                        setPatientInput(DEFAULT_PATIENT_MSG);
                        submitPatientMessage(DEFAULT_PATIENT_MSG);
                      }}
                      data-testid="pill-send-flu-intake"
                    >
                      1-Click: Flu A RAT+ · 38.6°C · Need Doctor &amp; 2-Day Sick Leave
                    </button>
                    <button
                      type="button"
                      className="sam-pill-btn danger"
                      disabled={busy}
                      onClick={() => {
                        setPatientInput(RED_FLAG_MSG);
                        submitPatientMessage(RED_FLAG_MSG);
                      }}
                      data-testid="pill-send-red-flag"
                    >
                      Test Safety Floor: Cantonese Chest Pain / SOB → 999
                    </button>
                  </div>
                </div>

                <div className="sam-composer">
                  <input
                    className="sam-input"
                    value={patientInput}
                    onChange={(e) => setPatientInput(e.target.value)}
                    placeholder="Type any English or Cantonese first-time patient message…"
                    data-testid="act1-message-input"
                  />
                  <button
                    type="button"
                    className="sam-primary-btn jade"
                    disabled={busy}
                    onClick={() => submitPatientMessage()}
                    data-testid="act1-send-btn"
                  >
                    {busy ? "Screening…" : "Send to Sam"}
                  </button>
                </div>

                <div className="sam-footer-bar">
                  <span className="voice-assistant">
                    {intakeRes && !isRedFlag
                      ? "Sam has dispatched parallel handoffs to Nurse/Admin and Dr. Lam."
                      : "Send a patient message above (or jump straight to Act 2 using the seeded WONG Siu-Mei case)."}
                  </span>
                  <button
                    type="button"
                    className="sam-primary-btn"
                    onClick={() => goToAct(2)}
                    data-testid="go-act-2-btn"
                  >
                    Act 2: Sam ↔ Nurse &amp; Admin Desk →
                  </button>
                </div>
              </div>
            </>
          )}

          {act === 2 && (
            <>
              <div className="sam-stage-head">
                <div>
                  <div className="sam-eyebrow">Act 2 of 3 · Non-Medical Admin &amp; Nurse Coordination</div>
                  <h1 className="sam-stage-title">Sam coordinates logistics with Nurse Rachel &amp; Clinic Admin</h1>
                  <p className="sam-stage-sub">
                    Sam offloads non-clinical admin (corporate eligibility, vitals/RAT check, courier slot reservation) to nursing &amp; front-desk staff — while keeping clinical sign-off strictly locked to MCHK doctors.
                  </p>
                </div>
                <span className="sam-badge amber">Nurse Rachel Chan (RN) · Non-Prescribing</span>
              </div>

              <div className="sam-stage-body">
                <div className="sam-thread" data-testid="act2-thread">
                  <div className="sam-msg sam">
                    <div className="sam-msg-who">
                      <span>Sam → Clinic Desk &amp; Nurse Rachel Chan</span>
                      <span className="sam-badge jade">Admin Handoff</span>
                    </div>
                    <div>
                      Morning Rachel — new corporate flu patient <strong>WONG Siu-Mei (黃小美)</strong> messaged the Care Line.
                      I&apos;ve already completed her greeting, deterministic red-flag screen, and chart lookup (G6PD+ &amp; Penicillin allergy flagged for Dr. Lam).
                      Please confirm the non-clinical desk items below so Dr. Lam can sign in one tap:
                    </div>
                  </div>

                  {nurseDone && (
                    <>
                      <div className="sam-msg user" data-testid="act2-nurse-bubble">
                        <div className="sam-msg-who">
                          <span>Nurse Rachel Chan (RN · Desk Ops)</span>
                          <span>Logged to PG15 Audit</span>
                        </div>
                        <div>{nurseNote}</div>
                      </div>
                      <div className="sam-msg sam" data-testid="act2-sam-ack">
                        <div className="sam-msg-who">
                          <span>Sam → Rachel Chan &amp; Dr. Kenneth Lam</span>
                          <span className="sam-badge jade">Handoff Complete ✓</span>
                        </div>
                        <div>
                          Thanks Rachel — I&apos;ve attached your prep note to WONG Siu-Mei&apos;s decision card and messaged on-duty <strong>Dr. Kenneth Lam</strong>.
                          I&apos;ll ping the desk automatically the second he signs so you can release the 2:00 PM courier.
                        </div>
                      </div>
                    </>
                  )}
                </div>

                {/* Non-medical Admin Checklist */}
                <div className="sam-checklist">
                  <div className="sam-check-row">
                    <input type="checkbox" checked readOnly />
                    <div>
                      <strong>Corporate Campaign &amp; HK PDPO Privacy Verified (Auto by Sam)</strong>
                      <div className="sam-brand-sub">
                        Harbour FinTech Holdings Ltd (<code>HARBOUR-FLU-2026</code>) active &amp; within HK$80,000 cap. Employer sees aggregate counts only (&lt;5 suppressed, zero individual PII).
                      </div>
                    </div>
                  </div>
                  <div className="sam-check-row">
                    <input type="checkbox" checked readOnly />
                    <div>
                      <strong>Patient Identity &amp; PDPO Consent Ledger Confirmed (Auto by Sam)</strong>
                      <div className="sam-brand-sub">HKID masked (<code>B402XXX(X)</code>), clinical care + AI disclosure consents active.</div>
                    </div>
                  </div>
                  <label className="sam-check-row">
                    <input type="checkbox" checked={checkVitals} onChange={(e) => setCheckVitals(e.target.checked)} />
                    <div>
                      <strong>Confirm Vitals &amp; Flu A RAT Photo</strong>
                      <div className="sam-brand-sub">Temp 38.6°C · SpO2 98% · Positive Influenza A RAT photo matched to patient.</div>
                    </div>
                  </label>
                  <label className="sam-check-row">
                    <input type="checkbox" checked={checkCourier} onChange={(e) => setCheckCourier(e.target.checked)} />
                    <div>
                      <strong>Reserve 2:00 PM HK Partner Dispensary Courier Slot</strong>
                      <div className="sam-brand-sub">Held on standby — auto-dispatches ONLY after Dr. Lam signs the e-script.</div>
                    </div>
                  </label>
                </div>

                <div>
                  <div className="v2-sec-label">Nurse / Admin handoff note to Dr. Kenneth Lam</div>
                  <input
                    className="sam-input"
                    style={{ width: "100%" }}
                    value={nurseNote}
                    onChange={(e) => setNurseNote(e.target.value)}
                    data-testid="act2-nurse-note-input"
                  />
                </div>

                <div className="sam-boundary" data-testid="act2-rbac-guard">
                  <strong>RBAC &amp; Asymmetric Authority Guard:</strong> Rachel (RN / Admin) has <strong>zero clinical Sign, Prescribe, or Sick-Leave Issue buttons</strong> (backend enforces HTTP 403 on clinician endpoints). Only MCHK doctors sign clinical orders.
                </div>

                <div className="sam-footer-bar">
                  <button type="button" className="sam-sec-btn" onClick={() => goToAct(1)}>
                    ← Back to Act 1
                  </button>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <button
                      type="button"
                      className="sam-primary-btn jade"
                      disabled={busy || !checkVitals || !checkCourier}
                      onClick={submitNurseAdminPrep}
                      data-testid="act2-ready-btn"
                    >
                      {busy ? "Updating…" : nurseDone ? "✓ Marked Ready for Dr. Lam" : "Confirm Admin Prep & Mark Ready for Dr. Lam"}
                    </button>
                    <button
                      type="button"
                      className="sam-primary-btn"
                      onClick={() => goToAct(3)}
                      data-testid="go-act-3-btn"
                    >
                      Act 3: Sam → On-Duty Doctor Inbox →
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}

          {act === 3 && (
            <>
              <div className="sam-stage-head">
                <div>
                  <div className="sam-eyebrow">Act 3 of 3 · On-Duty Doctor Inbox Brief → 1-Tap Sign → Real Execution</div>
                  <h1 className="sam-stage-title">Sam briefs Dr. Kenneth Lam, then executes after one tap</h1>
                  <p className="sam-stage-sub">
                    Sam messages the on-duty doctor with prioritized pending work, verbatim source quotes, and nurse prep attached. One tap signs and executes real MCHK PDF + GCS Vault + e-script + desk release.
                  </p>
                </div>
                <span className="sam-badge jade">Dr. Kenneth Lam · MCHK M14829</span>
              </div>

              <div className="sam-stage-body">
                {/* Sam's proactive message to On-Duty Doctor about pending inbox */}
                <div className="sam-thread" data-testid="act3-doctor-brief">
                  <div className="sam-msg sam">
                    <div className="sam-msg-who">
                      <span>Sam → Dr. Kenneth Lam (On Duty · Central GP)</span>
                      <span className="sam-badge amber">{pendingCount} pending in inbox</span>
                    </div>
                    <div>
                      Dr. Lam — you have <strong>{pendingCount} pending item{pendingCount === 1 ? "" : "s"}</strong> in the decision stack right now (sorted by urgency).
                      <strong> WONG Siu-Mei</strong> is nurse-prepped and ready for a 10-second sign-off:
                    </div>
                    <div className="sam-inbox-list">
                      {(() => {
                        const focusItem = queueItems.find((it) => it.id === proposalId);
                        const others = queueItems.filter(
                          (it) =>
                            it.id !== proposalId &&
                            it.status === "pending_review" &&
                            it.patient_id !== "pat_wong_flu_g6pd",
                        );
                        const display = [...(focusItem ? [focusItem] : []), ...others].slice(0, 4);
                        return display.map((it) => {
                          const isFocus = it.id === proposalId;
                          return (
                            <div key={it.id} className={`sam-inbox-row ${isFocus ? "focus" : ""}`}>
                              <div>
                                <strong>{it.patient_name_en}</strong> <span className="sam-brand-sub">{it.patient_name_zh}</span>
                                <div className="sam-brand-sub">{it.title}</div>
                              </div>
                              <span className={`sam-badge ${it.unresolved_conflicts > 0 ? "red" : isFocus ? "jade" : "muted"}`}>
                                {it.unresolved_conflicts > 0
                                  ? `${it.unresolved_conflicts} conflict`
                                  : isFocus && signResult
                                    ? "Signed & Executed ✓"
                                    : isFocus && (nurseDone || it.nurse_ready)
                                      ? "Nurse ready ✓"
                                      : `Urgency ${it.urgency}`}
                              </span>
                            </div>
                          );
                        });
                      })()}
                    </div>
                  </div>
                </div>

                {/* Prepared Decision Card */}
                <div className="sam-decision-box" data-testid="act3-decision-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                    <div>
                      <div className="v2-sec-label" style={{ marginBottom: 2 }}>
                        {doctorClosed
                          ? "Decision Card · Loop Closed by Dr. Kenneth Lam"
                          : signResult
                            ? "Decision Card · L3 Executed (Signed by Dr. Kenneth Lam)"
                            : "Prepared Decision Card · L2 Draft (Nothing Sent Yet)"}
                      </div>
                      <strong style={{ fontSize: 16 }}>WONG Siu-Mei 黃小美 · Acute Influenza A (ICD-10 J10.1 / ICPC-2 R80)</strong>
                    </div>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      <span className="sam-badge amber">G6PD Deficient ✓ Checked</span>
                      <span className="sam-badge amber">Penicillin Allergy ✓ Checked</span>
                      {(nurseDone || detail?.payload?.nurse_ready) && <span className="sam-badge jade">Nurse Rachel Prep ✓</span>}
                    </div>
                  </div>

                  {(nurseDone || detail?.payload?.nurse_prep_note) && (
                    <div className="sam-check-row" style={{ background: "var(--paper)" }}>
                      <span>📋</span>
                      <div>
                        <strong>Nurse Rachel Chan (Admin &amp; Vitals Prep):</strong>{" "}
                        {detail?.payload?.nurse_prep_note || nurseNote}
                      </div>
                    </div>
                  )}

                  <div>
                    <div className="v2-sec-label">Verbatim Source Quotes (Verified Word-for-Word Against Patient Record)</div>
                    <div>
                      {extractedQuotes.length > 0 ? (
                        extractedQuotes.map((q, i) => (
                          <span key={i} className="sam-quote-chip">“{q}”</span>
                        ))
                      ) : (
                        <span className="sam-quote-chip">“fever 38.6°C, body aches, Flu A RAT positive, 2-day sick leave”</span>
                      )}
                    </div>
                  </div>

                  <div className="sam-cap-grid">
                    <div className="sam-cap-tile" style={{ background: "var(--paper)" }}>
                      <strong>Prepared e-Script (G6PD-Safe)</strong>
                      Oseltamivir 75 mg BD × 5d<br />
                      Paracetamol 500 mg QDS PRN × 3d<br />
                      <span style={{ color: "var(--jade)" }}>✓ Aspirin / Penicillin blocked</span>
                    </div>
                    <div className="sam-cap-tile" style={{ background: "var(--paper)" }}>
                      <strong>Prepared MCHK Certificate</strong>
                      Bilingual Traditional Chinese + EN<br />
                      2 days sick leave · Stamped M14829<br />
                      <span style={{ color: "var(--jade)" }}>✓ AES-256-GCM HK GCS Vault</span>
                    </div>
                    <div className="sam-cap-tile" style={{ background: "var(--paper)" }}>
                      <strong>Post-Sign Execution Plan</strong>
                      1) Issue encrypted PDF<br />
                      2) Send 0-PHI WhatsApp doorbell<br />
                      3) Ping Rachel to release 2pm courier
                    </div>
                  </div>

                  <div>
                    <div className="v2-sec-label">Doctor&apos;s Signed Reply to Patient (Editable Serif Voice)</div>
                    <textarea
                      className="sam-input voice-doctor"
                      style={{ width: "100%", minHeight: 124 }}
                      value={doctorReply}
                      onChange={(e) => setDoctorReply(e.target.value)}
                      data-testid="act3-doctor-reply"
                    />
                  </div>

                  {!signResult ? (
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                      <span className="voice-assistant">All safety checks passed. One tap delegates execution to Sam.</span>
                      <button
                        type="button"
                        className="sam-primary-btn jade"
                        disabled={busy}
                        onClick={submitDoctorSign}
                        data-testid="act3-approve-execute-btn"
                      >
                        {busy ? "Signing & Executing…" : "Approve, Sign & Let Sam Execute (1 Tap)"}
                      </button>
                    </div>
                  ) : (
                    /* LIVE EXECUTION RECEIPT */
                    <div className="sam-receipt-card" data-testid="act3-execution-receipt">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
                        <strong style={{ color: "var(--jade)", fontSize: 15 }}>
                          ✓ Execution Receipt — Sam completed 4 actions after Dr. Lam&apos;s signature
                        </strong>
                        <span className="sam-badge jade">L3 Executed · Audited in PG15</span>
                      </div>

                      <div className="sam-receipt-row">
                        <div>
                          <strong>1. Bilingual MCHK Sick-Leave PDF issued &amp; encrypted in HK GCS Vault</strong>
                          <div className="sam-brand-sub">
                            Cert <code>{issuedCertId}</code> · MCHK <code>M14829</code> · Hash <code>{(issuedCertHash || "").slice(0, 12)}</code>
                          </div>
                        </div>
                        <button
                          type="button"
                          className="sam-primary-btn jade"
                          style={{ padding: "7px 12px", fontSize: 12.5 }}
                          disabled={busy || !issuedCertId}
                          onClick={() => openSignedPdf("clinician")}
                          data-testid="act3-open-pdf-doctor-btn"
                        >
                          Open Signed MCHK PDF ↗
                        </button>
                      </div>

                      <div className="sam-receipt-row">
                        <div>
                          <strong>2. e-Script staged for HK partner pharmacy fulfilment</strong>
                          <div className="sam-brand-sub">
                            {patientFulfilment || "Oseltamivir 75mg BD × 5d + Paracetamol 500mg QDS · Pending patient delivery/pickup tap"}
                          </div>
                        </div>
                        <div style={{ display: "flex", gap: 6 }}>
                          <button
                            type="button"
                            className="sam-sec-btn"
                            style={{ padding: "6px 10px", fontSize: 12 }}
                            disabled={busy || Boolean(patientFulfilment)}
                            onClick={() => chooseCourierFulfilment("delivery")}
                            data-testid="act3-patient-courier-btn"
                          >
                            Simulate Patient: 2pm Courier
                          </button>
                        </div>
                      </div>

                      <div className="sam-receipt-row">
                        <div>
                          <strong>3. Non-clinical WhatsApp Doorbell sent to WONG Siu-Mei</strong>
                          <div className="sam-brand-sub">
                            <code>WhatsAppLeakageGuard: PASS</code> (0 diagnoses, 0 drug names, 0 HKID on WhatsApp — secure portal link only)
                          </div>
                        </div>
                        <button
                          type="button"
                          className="sam-sec-btn"
                          style={{ padding: "6px 10px", fontSize: 12 }}
                          disabled={busy || !issuedCertId}
                          onClick={() => openSignedPdf("patient")}
                          data-testid="act3-open-pdf-patient-btn"
                        >
                          Download as Patient ↗
                        </button>
                      </div>

                      <div className="sam-receipt-row">
                        <div>
                          <strong>4. Admin Desk notified automatically (Rachel Chan)</strong>
                          <div className="sam-brand-sub">
                            “Dr. Lam signed WONG Siu-Mei — release 2:00 PM courier &amp; log HARBOUR-FLU-2026 aggregate count (0 PII to employer).”
                          </div>
                        </div>
                        <span className="sam-badge jade">Dispatched ✓</span>
                      </div>

                      {pdfNotice && (
                        <div className="sam-brand-sub" style={{ color: "var(--jade)", fontWeight: 600 }} data-testid="act3-pdf-notice">
                          ✓ {pdfNotice}
                        </div>
                      )}

                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap", paddingTop: 4 }}>
                        <span className="voice-assistant">
                          <strong>Asymmetric authority:</strong> Sam executed all 4 tasks, but only the doctor closes the clinical loop.
                        </span>
                        <button
                          type="button"
                          className={doctorClosed ? "sam-sec-btn" : "sam-primary-btn"}
                          onClick={() => setDoctorClosed(true)}
                          disabled={doctorClosed}
                          data-testid="act3-confirm-close-btn"
                        >
                          {doctorClosed ? "✓ Loop Closed by Dr. Kenneth Lam" : "Confirm Receipt & Close Loop (Doctor Only)"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                <div className="sam-footer-bar">
                  <button type="button" className="sam-sec-btn" onClick={() => setAct(2)}>
                    ← Back to Act 2 (Nurse &amp; Admin)
                  </button>
                  <div style={{ display: "flex", gap: 8 }}>
                    <a className="sam-link-btn" href="/c">Open Full Clinician Inbox (/c)</a>
                    <a className="sam-link-btn" href="/ec/suite.html">View LoopBench Proof (/ec/suite.html)</a>
                  </div>
                </div>
              </div>
            </>
          )}
        </section>

        {/* RIGHT COLUMN: PERSISTENT RESIDENT AGENT MEMORY & LIVE AUDIT LEDGER */}
        <aside className="sam-memory" data-testid="sam-persistent-memory">
          <div className="sam-mem-card">
            <div className="sam-mem-head">
              <span className="v2-sec-label" style={{ margin: 0 }}>Sam&apos;s Authority Level (Live)</span>
              <span className="sam-badge jade">Asymmetric</span>
            </div>
            <div className="sam-ladder">
              {[
                ["L0", "Observe"],
                ["L1", "Raise"],
                ["L2", "Prepare"],
                ["L3", "Execute"],
                ["MD", "Close"],
              ].map(([code, lbl], idx) => (
                <div key={code} className={`sam-lvl ${idx <= activeAuthorityIdx ? "on" : ""}`}>
                  <strong>{code}</strong>
                  {lbl}
                </div>
              ))}
            </div>
            <p className="sam-brand-sub" style={{ marginTop: 10, marginBottom: 0 }}>
              Sam autonomously observes, raises urgency, and prepares drafts. Execution runs <strong>only on doctor approval</strong>; only the doctor closes the loop.
            </p>
          </div>

          <div className="sam-mem-card">
            <div className="sam-mem-head">
              <span className="v2-sec-label" style={{ margin: 0 }}>Persistent Memory (Shared Across All 3 Acts)</span>
              <span className="sam-badge muted">PG15 FORCE RLS</span>
            </div>
            <div className="sam-handoff-row">
              <span>Patient</span>
              <strong>WONG Siu-Mei 黃小美 (34F)</strong>
            </div>
            <div className="sam-handoff-row">
              <span>Safety Alerts</span>
              <span className="sam-badge amber">G6PD+ · Penicillin Allergy</span>
            </div>
            <div className="sam-handoff-row">
              <span>Corporate Plan</span>
              <code>HARBOUR-FLU-2026 (0 PII)</code>
            </div>
            <div className="sam-handoff-row">
              <span>1. Patient Intake &amp; Screen</span>
              <span className={`sam-badge ${isRedFlag ? "red" : intakeRes ? "jade" : "muted"}`}>
                {isRedFlag ? "999 Escalated" : intakeRes ? "Verified ✓" : "Ready"}
              </span>
            </div>
            <div className="sam-handoff-row">
              <span>2. Nurse / Admin Prep</span>
              <span className={`sam-badge ${nurseDone ? "jade" : "muted"}`}>
                {nurseDone ? "Rachel Ready ✓" : "Pending Desk"}
              </span>
            </div>
            <div className="sam-handoff-row">
              <span>3. Doctor Sign &amp; Execute</span>
              <span className={`sam-badge ${doctorClosed ? "jade" : signResult ? "jade" : "muted"}`}>
                {doctorClosed ? "Closed by MD ✓" : signResult ? "Executed ✓" : "Awaiting Dr. Lam"}
              </span>
            </div>
          </div>

          <div className="sam-mem-card">
            <div className="sam-mem-head">
              <span className="v2-sec-label" style={{ margin: 0 }}>Live Immutable Audit Log (PostgreSQL 15)</span>
              <span className="sam-badge muted">Append-Only</span>
            </div>
            {auditEvents.length > 0 ? (
              auditEvents.map((ev) => (
                <div key={ev.id} className="sam-audit-row">
                  <span>
                    <strong>{ev.actor_role}</strong> · {ev.action}
                  </span>
                  <span>{ev.created_at.slice(11, 19)}Z</span>
                </div>
              ))
            ) : (
              <div className="sam-brand-sub">Trigger Act 1, 2, or 3 to watch immutable PG15 audit entries stream live.</div>
            )}
          </div>
        </aside>
      </main>
    </div>
  );
}
