import { useCallback, useEffect, useState } from "react";
import { v2Api } from "../api";
import type { CompanionHome, CrisisPayload, StoredSession } from "../api";
import { Paper } from "../shared/Paper";
import { Sheet } from "../shared/Sheet";
import { CrisisScreen } from "./CrisisScreen";
import { RecordSheet } from "./RecordSheet";

interface PatientAppProps {
  session: StoredSession;
}

const SYMPTOM_OPTIONS = [
  "Sore throat",
  "Blocked / runny nose",
  "Dry cough",
  "Body aches",
  "Headache",
  "Fatigue",
];

const ONSET_OPTIONS = [
  "Yesterday evening (<24h)",
  "1–2 days ago",
  "3+ days ago",
];

const RAT_OPTIONS = [
  "Negative (Viral URTI)",
  "Flu A Positive",
  "Not tested yet",
];

export function PatientApp({ session }: PatientAppProps) {
  const [home, setHome] = useState<CompanionHome | null>(null);
  const [lang, setLang] = useState<"en" | "zh-HK">("en");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [agentStepMsg, setAgentStepMsg] = useState<string | null>(null);
  const [recordOpen, setRecordOpen] = useState(false);
  const [paperModal, setPaperModal] = useState<{ title: string; text: string; quote: string } | null>(null);
  const [infoPackModal, setInfoPackModal] = useState<Record<string, any> | null>(null);
  const [slotModal, setSlotModal] = useState<Array<Record<string, any>> | null>(null);
  const [urtiModalOpen, setUrtiModalOpen] = useState(false);
  const [urtiSymptoms, setUrtiSymptoms] = useState<string[]>(["Sore throat", "Blocked / runny nose", "Dry cough"]);
  const [urtiOnset, setUrtiOnset] = useState<string>("Yesterday evening (<24h)");
  const [urtiTemp, setUrtiTemp] = useState<string>("38.2");
  const [urtiRat, setUrtiRat] = useState<string>("Negative (Viral URTI)");
  const [urtiDays, setUrtiDays] = useState<number>(2);
  const [urtiNote, setUrtiNote] = useState<string>("");
  const [bpDraft, setBpDraft] = useState<{ sys: string; dia: string } | null>(null);
  const [crisis, setCrisis] = useState<CrisisPayload | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const [expandedEpisode, setExpandedEpisode] = useState<string | null>(null);
  const [liveConnected, setLiveConnected] = useState(false);

  const isAgentHero = session.userId === "pat_lam_urti_demo";

  const load = useCallback(async () => {
    const data = await v2Api.companionHome(session.token);
    setHome(data);
    if (data.patient.preferred_lang?.startsWith("zh")) setLang("zh-HK");
    else setLang("en");
  }, [session.token]);

  useEffect(() => {
    void load();
  }, [load]);

  // Real-time SSE sync so signed care plans, MCHK sick-leave PDFs & nurse updates arrive live on the phone!
  useEffect(() => {
    let es: EventSource | null = null;
    let closed = false;
    let timer: number | null = null;

    const connect = () => {
      if (closed) return;
      es = new EventSource(`/api/v1/live/stream?patient_id=${encodeURIComponent(session.userId)}`);
      es.addEventListener("stream.connected", () => setLiveConnected(true));
      es.onopen = () => setLiveConnected(true);
      const onRefresh = (evtName: string) => {
        es?.addEventListener(evtName, () => {
          if (evtName === "patient.care_plan_delivered") {
            setStatusNote("Dr. Kenneth Lam signed your care plan & MCHK sick-leave certificate — ready below.");
          }
          void load();
        });
      };
      ["staff.queue_updated", "patient.care_plan_delivered", "emr.mutated", "demo.reset"].forEach(onRefresh);
      es.onerror = () => {
        setLiveConnected(false);
        es?.close();
        if (!closed) timer = window.setTimeout(connect, 2500);
      };
    };

    connect();
    const pollTimer = window.setInterval(() => {
      if (!closed) void load();
    }, 4000);
    return () => {
      closed = true;
      es?.close();
      if (timer) window.clearTimeout(timer);
      window.clearInterval(pollTimer);
    };
  }, [session.userId, load]);

  const sendMessage = async (overrideText?: string) => {
    const txt = (overrideText ?? input).trim();
    if (!txt || busy) return;
    setBusy(true);
    setInput("");
    setAgentStepMsg(isAgentHero ? "Checking your EMR chart, G6PD & allergy safety rules…" : null);
    try {
      if (isAgentHero) {
        const res = await v2Api.patientAgentTurn({ action: "message", message: txt, lang }, session.token);
        if (res.crisis) {
          setCrisis(res.crisis);
        } else if (res.open_urti_intake) {
          if (res.rich_cards) {
            const promptCard = res.rich_cards.find((c: any) => c.kind === "urti_intake_prompt");
            if (promptCard?.defaults?.temp_c) setUrtiTemp(String(promptCard.defaults.temp_c));
            if (promptCard?.defaults?.sick_leave_days) setUrtiDays(Number(promptCard.defaults.sick_leave_days));
          }
        }
      } else {
        const res = await v2Api.companionTurn(txt, lang, session.token);
        if (res.crisis) setCrisis(res.crisis);
      }
      await load();
    } finally {
      setBusy(false);
      setAgentStepMsg(null);
    }
  };

  const handleBookFluSlot = async (slotId: string) => {
    setBusy(true);
    setAgentStepMsg("Booking your Harbour FinTech flu shot into clinic schedule…");
    try {
      await v2Api.patientAgentTurn({ action: "book_flu_slot", payload: { slot_id: slotId }, lang }, session.token);
      setSlotModal(null);
      setStatusNote(lang === "zh-HK" ? "已預約 2026/27 流感疫苗時段並同步至診所病歷。" : "Flu vaccination slot booked and synced to your clinic EMR.");
      await load();
    } finally {
      setBusy(false);
      setAgentStepMsg(null);
    }
  };

  const handleSubmitUrtiIntake = async () => {
    setBusy(true);
    setAgentStepMsg("Verifying quotes, running G6PD/allergy CDS & sending to Nurse Rachel…");
    try {
      const res = await v2Api.patientAgentTurn(
        {
          action: "submit_urti_intake",
          payload: {
            symptoms: urtiSymptoms,
            onset: urtiOnset,
            temp_c: Number(urtiTemp) || 38.2,
            rat_result: urtiRat,
            sick_leave_days: urtiDays,
            note: urtiNote,
          },
          lang,
        },
        session.token,
      );
      setUrtiModalOpen(false);
      if (res.crisis) setCrisis(res.crisis);
      else setStatusNote(lang === "zh-HK" ? "症狀及病假資料已送交陳護士及林醫生，請留意即時更新。" : "URTI intake & sick-leave request sent live to Nurse Rachel & Dr. Kenneth Lam.");
      await load();
    } finally {
      setBusy(false);
      setAgentStepMsg(null);
    }
  };

  const handleOpenCertPdf = async (certId: string) => {
    try {
      const tok = await v2Api.certDownloadToken(certId, session.token);
      await v2Api.downloadVaultPdf(tok.download_url, session.token, `HK_MCHK_Sick_Leave_${certId}.pdf`);
    } catch (e: any) {
      setStatusNote(e?.message || "Could not open MCHK Sick Leave PDF.");
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

  if (crisis) {
    return <CrisisScreen crisis={crisis} lang={lang} onAcknowledge={() => setCrisis(null)} />;
  }

  if (!home) {
    return <div className="v2-patient-shell"><div className="v2-patient-main voice-assistant">Loading your clinic record…</div></div>;
  }

  const isZh = lang === "zh-HK";
  const richCards = home.rich_cards || [];

  const toggleSymptom = (sym: string) => {
    setUrtiSymptoms((prev) => (prev.includes(sym) ? prev.filter((x) => x !== sym) : [...prev, sym]));
  };

  return (
    <div className="v2-patient-shell" data-testid="patient-companion">
      <header className="v2-patient-top">
        <div>
          <strong style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {home.clinic_name}
            {liveConnected && (
              <span
                title="Real-time clinic sync active"
                style={{ width: 7, height: 7, borderRadius: "50%", background: "#16A34A", display: "inline-block" }}
              />
            )}
          </strong>
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
        {/* Rich AI-Native Proactive & Live Sync Cards */}
        {richCards.map((card) => {
          if (card.kind === "signed_care_plan") {
            return (
              <section
                key={card.id}
                className="v2-now-card"
                style={{ borderColor: "#16695D", background: "#F2FBF8" }}
                data-testid="patient-signed-care-plan"
              >
                <div className="v2-sec-label" style={{ color: "#16695D" }}>
                  {card.doctor} · {isZh ? "已簽署" : "SIGNED CARE PLAN"}
                </div>
                <strong className="voice-doctor" style={{ fontSize: 17 }}>
                  {isZh ? card.title_zh : card.title_en}
                </strong>
                {card.rx && card.rx.items?.length > 0 && (
                  <div className="v2-summary-item">
                    <div className="v2-sec-label" style={{ marginBottom: 4 }}>{isZh ? "G6PD 安全處方" : "G6PD-Safe Prescription"}</div>
                    {card.rx.items.map((it: any, idx: number) => (
                      <div key={idx} className="voice-assistant" style={{ fontSize: 13 }}>
                        • <strong>{it.display || `${it.drug} ${it.dose_mg || ""}mg ${it.frequency || ""}`}</strong>
                      </div>
                    ))}
                  </div>
                )}
                {card.certificate && (
                  <div className="v2-summary-item" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    <div className="voice-quote" style={{ fontSize: 12 }}>
                      MCHK Sick Leave: {card.certificate.days_off} day{card.certificate.days_off > 1 ? "s" : ""} ({card.certificate.start_date} → {card.certificate.end_date})
                    </div>
                    <button
                      type="button"
                      className="v2-btn-jade"
                      onClick={() => handleOpenCertPdf(card.certificate.id)}
                      data-testid="patient-download-cert-pdf-btn"
                    >
                      {isZh ? "下載雙語醫生病假證明書 (PDF)" : "Download MCHK Sick Leave Certificate (PDF) ↗"}
                    </button>
                  </div>
                )}
                <div className="voice-assistant" style={{ fontSize: 12, marginTop: 6 }}>
                  {isZh ? card.followup_note_zh : card.followup_note_en}
                </div>
              </section>
            );
          }

          if (card.kind === "proposal_status") {
            return (
              <section
                key={card.id}
                className="v2-now-card"
                style={{ borderLeft: "3px solid #16695D" }}
                data-testid="patient-proposal-status-card"
              >
                <div className="v2-sec-label">
                  {card.stage === "nurse_ready"
                    ? (isZh ? "護士已核對 · 等候醫生簽署" : "NURSE VERIFIED · WITH DR. LAM")
                    : (isZh ? "診所團隊跟進中" : "LIVE WITH CLINIC TEAM")}
                </div>
                <strong>{isZh ? card.title_zh : card.title_en}</strong>
                <div className="voice-assistant" style={{ marginTop: 4 }}>{isZh ? card.detail_zh : card.detail_en}</div>
              </section>
            );
          }

          if (card.kind === "medication_safety") {
            return (
              <section
                key={card.id}
                className="v2-card-flat v2-sec"
                style={{ borderColor: "var(--red-line)", background: "var(--red-bg)" }}
                data-testid="patient-cds-safety-card"
              >
                <div className="v2-sec-label" style={{ color: "var(--red)" }}>
                  {isZh ? card.title_zh : card.title_en}
                </div>
                {(card.findings || []).map((f: any, idx: number) => (
                  <div key={idx} className="v2-summary-item" style={{ fontSize: 13 }}>
                    <strong style={{ color: "var(--red)" }}>{String(f.drug || "").toUpperCase()}</strong>: {f.message}
                  </div>
                ))}
              </section>
            );
          }

          if (card.kind === "urti_intake_prompt") {
            return (
              <section
                key={card.id}
                className="v2-now-card"
                style={{ borderColor: "#16695D", background: "#FFFFFF" }}
                data-testid="patient-urti-intake-card"
              >
                <div className="v2-sec-label" style={{ color: "#16695D" }}>
                  {isZh ? "互動問診卡" : "STRUCTURED INTAKE"}
                </div>
                <strong>{isZh ? card.title_zh : card.title_en}</strong>
                <div className="voice-assistant" style={{ margin: "4px 0 10px" }}>
                  {isZh ? card.subtitle_zh : card.subtitle_en}
                </div>
                <button
                  type="button"
                  className="v2-btn-jade"
                  onClick={() => setUrtiModalOpen(true)}
                  data-testid="open-urti-popup-btn"
                >
                  {isZh ? "開啟症狀及病假資料彈窗" : "Complete 15-sec URTI & Sick Leave Check →"}
                </button>
              </section>
            );
          }

          if (card.kind === "lab_result") {
            return (
              <div key={card.id} className="v2-card-flat v2-sec" data-testid="patient-lab-card">
                <div className="v2-sec-label">{isZh ? card.title_zh : card.title_en}</div>
                <div className="voice-assistant" style={{ marginBottom: 8 }}>
                  {isZh ? card.summary_zh : card.summary_en}
                </div>
                <button
                  type="button"
                  className="v2-quote-link"
                  onClick={() => setPaperModal({ title: card.doc_title, text: card.doc_text, quote: card.quote })}
                  data-testid="open-lab-paper-btn"
                >
                  {isZh ? "查看化驗報告原文" : "View Original KingMed Report"} →
                </button>
              </div>
            );
          }

          if (card.kind === "flu_vaccine") {
            return (
              <div
                key={card.id}
                className="v2-card-flat v2-sec"
                style={card.status === "deferred" ? { background: "var(--amber-bg)", borderColor: "var(--amber-line)" } : undefined}
                data-testid="patient-flu-card"
              >
                <div className="v2-sec-label">
                  {card.status === "booked"
                    ? (isZh ? "流感疫苗已預約" : "FLU SEASON · BOOKED")
                    : card.status === "deferred"
                      ? (isZh ? "診所指引自動暫緩" : "FLU SEASON · AUTO-DEFERRED")
                      : (isZh ? "冬季流感預防計劃" : "FLU SEASON 2026/27")}
                </div>
                <strong>{isZh ? card.title_zh : card.title_en}</strong>
                <div className="voice-assistant" style={{ margin: "4px 0 10px" }}>
                  {isZh ? card.detail_zh : card.detail_en}
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="v2-btn-secondary"
                    onClick={() => setInfoPackModal(card.info_pack)}
                    data-testid="open-flu-info-pack-btn"
                  >
                    {isZh ? "個人化疫苗資訊包" : "Personalised Info Pack"}
                  </button>
                  {card.status === "eligible" && (
                    <button
                      type="button"
                      className="v2-btn-jade"
                      onClick={() => setSlotModal(card.slots || [])}
                      data-testid="open-flu-slots-btn"
                    >
                      {isZh ? "選擇預約時段" : "Pick a Slot ($0)"}
                    </button>
                  )}
                </div>
              </div>
            );
          }

          return null;
        })}

        {/* Pinned "Now" line (at most 1 pending item — preserved for BP / QEH / Elderly Flu stories) */}
        {home.now && !isAgentHero && (
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
        {agentStepMsg && (
          <div className="v2-goal-strip" style={{ borderColor: "#16695D", color: "#16695D" }}>
            <span>{agentStepMsg}</span>
          </div>
        )}

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

      {/* Single Calm Composer (with one-thumb contextual suggestions for the working-age URTI phone demo) */}
      <div className="v2-composer-dock">
        <div className="v2-composer-inner">
          {isAgentHero && (
            <div style={{ display: "flex", gap: 6, overflowX: "auto", paddingBottom: 7 }}>
              <button
                type="button"
                className="v2-kbd-chip"
                style={{ whiteSpace: "nowrap" }}
                disabled={busy}
                onClick={() =>
                  void sendMessage(
                    "I woke up with a sore throat, blocked nose, dry cough and 38.2°C fever since yesterday evening. Can I take aspirin or Panadol, and can I get a 2-day sick leave certificate for work?",
                  )
                }
                data-testid="quick-urti-msg-btn"
              >
                “Sore throat &amp; 38.2°C — Aspirin? 2d sick leave?”
              </button>
              <button
                type="button"
                className="v2-kbd-chip"
                style={{ whiteSpace: "nowrap", borderColor: "#16695D", color: "#16695D" }}
                disabled={busy}
                onClick={() => setUrtiModalOpen(true)}
                data-testid="quick-open-intake-btn"
              >
                + URTI &amp; Sick Leave Form
              </button>
            </div>
          )}
          <div className="v2-composer-row">
            <input
              className="v2-composer-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void sendMessage(); }}
              placeholder={isZh ? "輸入症狀、藥物問題或覆檢查詢…" : "Ask about your medicines, symptoms, or results…"}
              data-testid="patient-composer-input"
            />
            <button type="button" className="v2-btn-jade" disabled={busy || !input.trim()} onClick={() => void sendMessage()} data-testid="patient-send-btn">
              {isZh ? "送出" : "Send"}
            </button>
          </div>
        </div>
      </div>

      <RecordSheet
        open={recordOpen}
        isZh={isZh}
        home={home}
        token={session.token}
        onClose={() => setRecordOpen(false)}
        onUpdated={load}
      />

      {/* Verbatim Source Lab Report Pop-up */}
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

      {/* Personalised Flu Season Info Pack Pop-up */}
      <Sheet
        open={Boolean(infoPackModal)}
        title={infoPackModal ? (isZh ? infoPackModal.headline_zh : infoPackModal.headline_en) : "Info Pack"}
        onClose={() => setInfoPackModal(null)}
      >
        {infoPackModal && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="v2-card-flat" style={{ background: "var(--jade-soft)", borderColor: "#99D5C9" }}>
              <div className="v2-sec-label" style={{ color: "var(--jade)" }}>
                {isZh ? "根據你的病歷核對" : "Personalised to your clinic chart"}
              </div>
              {(infoPackModal.chart_checks || []).map((line: string, i: number) => (
                <div key={i} className="v2-summary-item" style={{ fontSize: 13 }}>
                  ✓ {line}
                </div>
              ))}
            </div>
            <div className="v2-card-flat">
              <div className="v2-sec-label">{isZh ? "接種須知" : "What to expect"}</div>
              {(infoPackModal.what_to_expect || []).map((line: string, i: number) => (
                <div key={i} className="v2-summary-item voice-assistant" style={{ fontSize: 13 }}>
                  • {line}
                </div>
              ))}
            </div>
          </div>
        )}
      </Sheet>

      {/* Interactive Flu Vaccination Slot Picker Pop-up */}
      <Sheet
        open={Boolean(slotModal)}
        title={isZh ? "預約 2026/27 流感疫苗（免費）" : "Pick a Flu Vaccination Slot ($0 Copay)"}
        onClose={() => setSlotModal(null)}
      >
        {slotModal && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="voice-assistant" style={{ fontSize: 13 }}>
              {isZh
                ? "選擇合適時段後將即時同步至中環診所預約系統："
                : "Tap any available 10-minute nurse slot to confirm directly into the clinic EMR:"}
            </div>
            {slotModal.map((sl) => (
              <button
                key={sl.id}
                type="button"
                className="v2-conflict-side"
                style={{ width: "100%", textAlign: "left" }}
                disabled={busy}
                onClick={() => void handleBookFluSlot(sl.id)}
                data-testid={`book-slot-${sl.id}`}
              >
                <div>
                  <strong>{isZh ? sl.label_zh : sl.label_en}</strong>
                  <div className="voice-assistant" style={{ fontSize: 12 }}>
                    {sl.provider} · {sl.location}
                  </div>
                </div>
                <span className="v2-kbd-chip" style={{ color: "#16695D", borderColor: "#99D5C9" }}>
                  {sl.badge || "$0"}
                </span>
              </button>
            ))}
          </div>
        )}
      </Sheet>

      {/* Interactive URTI Symptom, Home RAT & MCHK Sick-Leave Certificate Pop-up */}
      <Sheet
        open={urtiModalOpen}
        title={isZh ? "上呼吸道症狀及病假證明書資料" : "URTI Symptom & Sick Leave Check"}
        onClose={() => setUrtiModalOpen(false)}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }} data-testid="urti-intake-sheet">
          <div>
            <div className="v2-sec-label">{isZh ? "1. 症狀（可多選）" : "1. Current symptoms"}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {SYMPTOM_OPTIONS.map((sym) => {
                const active = urtiSymptoms.includes(sym);
                return (
                  <button
                    key={sym}
                    type="button"
                    className="v2-kbd-chip"
                    style={
                      active
                        ? { background: "#16695D", color: "#FFFFFF", borderColor: "#16695D" }
                        : undefined
                    }
                    onClick={() => toggleSymptom(sym)}
                  >
                    {active ? "✓ " : ""}{sym}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="v2-sec-label">{isZh ? "2. 發病時間與最高體溫" : "2. Onset & peak temperature"}</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
              {ONSET_OPTIONS.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  className="v2-kbd-chip"
                  style={urtiOnset === opt ? { background: "#18181B", color: "#FFFFFF", borderColor: "#18181B" } : undefined}
                  onClick={() => setUrtiOnset(opt)}
                >
                  {opt}
                </button>
              ))}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="voice-assistant">Temp (°C):</span>
              <input
                className="v2-composer-input"
                style={{ maxWidth: 96 }}
                value={urtiTemp}
                onChange={(e) => setUrtiTemp(e.target.value)}
                aria-label="Peak temperature"
              />
            </div>
          </div>

          <div>
            <div className="v2-sec-label">{isZh ? "3. 家居流感 / 新冠快測結果" : "3. Home Flu / COVID RAT"}</div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {RAT_OPTIONS.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  className="v2-kbd-chip"
                  style={urtiRat === opt ? { background: "#18181B", color: "#FFFFFF", borderColor: "#18181B" } : undefined}
                  onClick={() => setUrtiRat(opt)}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="v2-sec-label">{isZh ? "4. 僱主病假證明書（香港註冊醫生雙語 PDF）" : "4. MCHK Bilingual Sick Leave Certificate"}</div>
            <div style={{ display: "flex", gap: 6 }}>
              {[1, 2, 3].map((d) => (
                <button
                  key={d}
                  type="button"
                  className="v2-kbd-chip"
                  style={urtiDays === d ? { background: "#16695D", color: "#FFFFFF", borderColor: "#16695D" } : undefined}
                  onClick={() => setUrtiDays(d)}
                  data-testid={`urti-days-${d}`}
                >
                  {d} {d === 1 ? "day" : "days"}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="v2-sec-label">{isZh ? "5. 補充說明（可選）" : "5. Additional note for Dr. Lam (optional)"}</div>
            <input
              className="v2-composer-input"
              value={urtiNote}
              onChange={(e) => setUrtiNote(e.target.value)}
              placeholder="e.g., Need PDF for HR portal today; confirmed no chest pain or shortness of breath."
            />
          </div>

          <div className="v2-card-flat" style={{ background: "var(--subtle)", fontSize: 12 }}>
            <strong>Chart safety verified automatically:</strong> G6PD Deficient · Penicillin Allergy · Red-flag check clear.
            Final prescription &amp; MCHK sick-leave certificate are signed by Dr. Kenneth Lam (M14829).
          </div>

          <button
            type="button"
            className="v2-btn-jade"
            disabled={busy || urtiSymptoms.length === 0}
            onClick={() => void handleSubmitUrtiIntake()}
            data-testid="submit-urti-intake-btn"
          >
            {busy
              ? (isZh ? "正在傳送至診所…" : "Sending to Nurse Rachel & Dr. Lam…")
              : (isZh ? "確認送交診所團隊覆核" : "Send to Nurse Rachel & Dr. Lam →")}
          </button>
        </div>
      </Sheet>
    </div>
  );
}
