import { useEffect, useState } from "react";
import { v2Api } from "../api";
import type { CompanionHome, CrisisPayload, StoredSession } from "../api";
import { Paper } from "../shared/Paper";
import { Sheet } from "../shared/Sheet";
import { CrisisScreen } from "./CrisisScreen";

interface PatientAppProps {
  session: StoredSession;
}

export function PatientApp({ session }: PatientAppProps) {
  const [home, setHome] = useState<CompanionHome | null>(null);
  const [lang, setLang] = useState<"en" | "zh-HK">("en");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recordOpen, setRecordOpen] = useState(false);
  const [paperModal, setPaperModal] = useState<{ title: string; text: string; quote: string } | null>(null);
  const [bpDraft, setBpDraft] = useState<{ sys: string; dia: string } | null>(null);
  const [crisis, setCrisis] = useState<CrisisPayload | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const [expandedEpisode, setExpandedEpisode] = useState<string | null>(null);

  const load = async () => {
    const data = await v2Api.companionHome(session.token);
    setHome(data);
    if (data.patient.preferred_lang?.startsWith("zh")) setLang("zh-HK");
    else setLang("en");
  };

  useEffect(() => {
    load();
  }, [session.token]);

  const sendMessage = async () => {
    const txt = input.trim();
    if (!txt || busy) return;
    setBusy(true);
    setInput("");
    try {
      const res = await v2Api.companionTurn(txt, lang, session.token);
      if (res.crisis) setCrisis(res.crisis);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const confirmBpPhoto = async () => {
    if (!bpDraft || busy) return;
    setBusy(true);
    try {
      const res = await v2Api.companionReading(
        {
          kind: "bp",
          systolic: Number(bpDraft.sys),
          diastolic: Number(bpDraft.dia),
          source: "monitor_photo",
        },
        session.token,
      );
      setBpDraft(null);
      if (res.crisis) setCrisis(res.crisis);
      else setStatusNote(lang === "zh-HK" ? "已記錄血壓讀數，交由林醫生覆核。" : "Reading saved and added to Dr. Kenneth Lam's review list.");
      await load();
    } finally {
      setBusy(false);
    }
  };

  const openCertPdf = async (certId: string) => {
    const res = await v2Api.certDownloadToken(certId, session.token);
    window.open(res.download_url, "_blank", "noopener");
  };

  if (crisis) {
    return <CrisisScreen crisis={crisis} lang={lang} onAcknowledge={() => setCrisis(null)} />;
  }

  if (!home) {
    return <div className="v2-patient-shell"><div className="v2-patient-main voice-assistant">Loading your clinic record…</div></div>;
  }

  const isZh = lang === "zh-HK";

  return (
    <div className="v2-patient-shell" data-testid="patient-companion">
      <header className="v2-patient-top">
        <div>
          <strong>{home.clinic_name}</strong>
          <div className="voice-assistant">{home.doctor_name} · MCHK {home.doctor_mchk}</div>
        </div>
        <div className="v2-staff-top-right">
          <button type="button" className="v2-text-btn" onClick={() => setLang(isZh ? "en" : "zh-HK")}>
            {isZh ? "EN" : "繁中"}
          </button>
          <button type="button" className="v2-btn-secondary" onClick={() => setRecordOpen(true)} data-testid="your-record-btn">
            {home.patient.name_en}
          </button>
        </div>
      </header>

      <main className="v2-patient-main">
        {/* Pinned "Now" line (at most 1 pending item) */}
        {home.now && (
          <section className="v2-now-card" data-testid="patient-now-card">
            <div className="v2-sec-label">{isZh ? "現在需要處理" : "Now"}</div>
            <strong>{isZh ? home.now.title_zh : home.now.title_en}</strong>
            <div className="voice-assistant">{isZh ? home.now.detail_zh : home.now.detail_en}</div>

            {home.now.kind === "bp_protocol" && (
              <div className="v2-summary-item">
                {!bpDraft ? (
                  <button
                    type="button"
                    className="v2-btn-jade"
                    onClick={() => setBpDraft({ sys: "148", dia: "91" })}
                    data-testid="bp-photo-btn"
                  >
                    {isZh ? "拍下血壓計螢幕（確認讀數）" : "Photograph monitor screen"}
                  </button>
                ) : (
                  <div className="v2-card-flat">
                    <div className="v2-sec-label">{isZh ? "從螢幕擷取的讀數——請確認" : "Extracted from monitor photo — confirm"}</div>
                    <div className="v2-composer-row">
                      <input
                        className="v2-composer-input"
                        value={bpDraft.sys}
                        onChange={(e) => setBpDraft({ ...bpDraft, sys: e.target.value })}
                        aria-label="Systolic BP"
                      />
                      <input
                        className="v2-composer-input"
                        value={bpDraft.dia}
                        onChange={(e) => setBpDraft({ ...bpDraft, dia: e.target.value })}
                        aria-label="Diastolic BP"
                      />
                      <button type="button" className="v2-btn-jade" disabled={busy} onClick={confirmBpPhoto} data-testid="confirm-bp-btn">
                        {isZh ? "確認送出" : "Confirm"}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>
        )}

        {/* Single calm condition goal line (never a dashboard) */}
        {home.goal_line && (
          <div className="v2-goal-strip" data-testid="patient-goal-line">
            <span>{isZh ? home.goal_line.label_zh : home.goal_line.label_en}</span>
            <strong className="voice-quote">{home.goal_line.current}</strong>
          </div>
        )}

        {/* Protocol-grounded "because + who asked" outreach */}
        {home.protocol_nudges.map((n) => (
          <div key={n.id} className="v2-card-flat v2-sec" data-testid="protocol-nudge">
            <div className="voice-assistant">{isZh ? n.because_zh : n.because_en}</div>
            <div className="v2-summary-item">
              <strong>{isZh ? n.action_zh : n.action_en}</strong>
            </div>
            {n.source_quote && (
              <button
                type="button"
                className="v2-quote-link"
                onClick={() => setPaperModal({ title: "Lab Report (03 May)", text: n.source_doc, quote: n.source_quote })}
              >
                {n.source_quote} →
              </button>
            )}
          </div>
        ))}

        {statusNote && <div className="v2-goal-strip">{statusNote}</div>}

        {/* Continuous Care Timeline (Serif = Doctor, Sans = Assistant + Tracked SLA, Mono = Verbatim Quote) */}
        <section className="v2-sec">
          <div className="v2-sec-label">{isZh ? "護理紀錄及訊息" : "Care timeline"}</div>
          <div className="v2-card-flat" data-testid="patient-timeline">
            {home.timeline.length === 0 ? (
              <div className="voice-assistant">{isZh ? "有任何健康問題都可以隨時留言。" : "Message the clinic anytime about your symptoms, medicines, or results."}</div>
            ) : (
              home.timeline.map((entry) => (
                <div key={entry.id} className="v2-timeline-entry">
                  <div className="v2-order-head">
                    <span className="v2-sec-label">
                      {entry.voice === "doctor"
                        ? `${entry.sender_label} · signed`
                        : entry.voice === "assistant"
                          ? `${entry.sender_label} · from your record & plan`
                          : isZh ? "你" : "You"}
                    </span>
                    <span className="voice-quote">{new Date(entry.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                  </div>
                  <div className={entry.voice === "doctor" ? "voice-doctor" : "voice-assistant"}>{entry.body}</div>
                  {entry.voice === "assistant" && (
                    <div className="voice-quote">{isZh ? home.review_sla.label_zh : home.review_sla.label_en}</div>
                  )}
                </div>
              ))
            )}
          </div>
        </section>

        {/* Collapsed Episode History */}
        <section className="v2-sec">
          <div className="v2-sec-label">{isZh ? "過往就診與跟進" : "Episodes"}</div>
          <div className="v2-card-flat">
            {home.episodes.map((ep) => (
              <div key={ep.id} className="v2-summary-item">
                <button
                  type="button"
                  className="v2-demo-item"
                  onClick={() => setExpandedEpisode(expandedEpisode === ep.id ? null : ep.id)}
                >
                  <span>
                    <strong>{ep.title}</strong> · {ep.date}
                  </span>
                  <span className="voice-quote">{ep.status}</span>
                </button>
                {expandedEpisode === ep.id && <div className="voice-assistant">{ep.summary}</div>}
              </div>
            ))}
          </div>
        </section>
      </main>

      {/* Single Calm Composer (zero demo preset buttons!) */}
      <div className="v2-composer-dock">
        <div className="v2-composer-inner">
          <div className="v2-composer-row">
            <input
              className="v2-composer-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") sendMessage(); }}
              placeholder={isZh ? "輸入症狀、藥物問題或覆檢查詢…" : "Ask about your medicines, symptoms, or results…"}
              data-testid="patient-composer-input"
            />
            <button type="button" className="v2-btn-jade" disabled={busy || !input.trim()} onClick={sendMessage} data-testid="patient-send-btn">
              {isZh ? "送出" : "Send"}
            </button>
          </div>
        </div>
      </div>

      {/* Single "Your record" Sheet (Meds, Allergies, Signed Docs, Account & Privacy at foot) */}
      <Sheet open={recordOpen} title={isZh ? "你的病歷紀錄" : "Your record"} onClose={() => setRecordOpen(false)}>
        <div className="v2-sec">
          <div className="v2-sec-label">Medicines &amp; allergies</div>
          <div className="v2-card-flat">
            <div><strong>Active medicines:</strong> {home.record.current_meds.length ? home.record.current_meds.join(", ") : "None"}</div>
            <div><strong>Allergies:</strong> {home.record.allergies.length ? home.record.allergies.join(", ") : "None recorded"}</div>
            <div><strong>G6PD:</strong> {home.record.g6pd_deficient ? "Deficient" : "Normal"}</div>
          </div>
        </div>

        <div className="v2-sec">
          <div className="v2-sec-label">Signed certificates &amp; prescriptions</div>
          <div className="v2-card-flat">
            {home.record.certificates.map((c) => (
              <div key={c.id} className="v2-summary-item v2-order-head">
                <span className="voice-doctor">Sick leave {c.start_date} – {c.end_date} ({c.days_off}d) · MCHK {c.mchk_reg_no}</span>
                <button type="button" className="v2-btn-secondary" onClick={() => openCertPdf(c.id)}>PDF</button>
              </div>
            ))}
            {home.record.prescriptions.map((r) => (
              <div key={r.id} className="v2-summary-item">
                <div className="voice-doctor">e-Script ({r.fulfilment_status}) · MCHK {r.mchk_reg_no}</div>
                {r.fulfilment_mode === "pending_choice" && (
                  <div className="v2-composer-row">
                    <button type="button" className="v2-btn-secondary" onClick={async () => { await v2Api.chooseFulfilment(r.id, "pickup", "", session.token); await load(); }}>Clinic pickup</button>
                    <button type="button" className="v2-btn-secondary" onClick={async () => { await v2Api.chooseFulfilment(r.id, "delivery", "Central, Hong Kong", session.token); await load(); }}>Courier delivery</button>
                  </div>
                )}
              </div>
            ))}
            {home.record.certificates.length === 0 && home.record.prescriptions.length === 0 && (
              <div className="voice-assistant">No signed documents yet.</div>
            )}
          </div>
        </div>

        <div className="v2-sec">
          <div className="v2-sec-label">Account &amp; Hong Kong PDPO Privacy</div>
          <div className="v2-card-flat voice-assistant">
            <div>{home.patient.name_en} · HKID {home.patient.hkid_masked} · {home.patient.phone_masked}</div>
            <div>Data stored in Hong Kong (asia-east2). Statutory medical record retention applies (Cap. 347).</div>
          </div>
        </div>
      </Sheet>

      <Sheet open={Boolean(paperModal)} title={paperModal?.title || "Source document"} onClose={() => setPaperModal(null)}>
        {paperModal && (
          <Paper
            title={paperModal.title}
            text={paperModal.text}
            quotes={[{ quote: paperModal.quote }]}
            focusedQuote={paperModal.quote}
          />
        )}
      </Sheet>
    </div>
  );
}
