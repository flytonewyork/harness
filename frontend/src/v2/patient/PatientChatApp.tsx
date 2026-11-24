import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, FileText, Menu, RotateCcw, Sparkles, X } from "lucide-react";
import { v2Api } from "../api";
import "./patientChat.css";

interface ChatMsg {
  actor: string;
  role?: "doctor" | "assistant" | "patient" | "system";
  time: string;
  text: string;
  lang?: string;
  is_crisis?: boolean;
  trace_id?: string;
  care_plan?: {
    signed_by: string;
    mchk_reg_no: string;
    ai_followup_note?: string;
    rx_lines?: string[];
    certificates?: Array<{
      id: string;
      days_off: number;
      start_date: string;
      end_date: string;
      omit_employer_diagnosis?: boolean;
    }>;
  };
}

interface JourneyMeta {
  key: string;
  category?: "acute" | "chronic" | "family";
  patient_id: string;
  patient_name: string;
  patient_name_zh: string;
  age_sex: string;
  default_lang?: "en" | "zh-HK";
  title: string;
  title_zh: string;
  subtitle: string;
  status: string;
  baton?: string | null;
  message_count: number;
}

const JOURNEY_GROUPS: Array<{ key: "acute" | "chronic" | "family"; labelEn: string; labelZh: string }> = [
  { key: "acute", labelEn: "Acute & Same-Day", labelZh: "即日急症及常見病" },
  { key: "chronic", labelEn: "Hospital Follow-Up & Chronic", labelZh: "出院覆診及慢性病" },
  { key: "family", labelEn: "Checkups & Family Care", labelZh: "驗身、家庭及常規" },
];

interface PatientChatAppProps {
  initialJourney?: string;
}

// Patient bubbles are plain text (WhatsApp style): strip any markdown emphasis markers.
function renderBoldText(text: string) {
  return text.replace(/\*\*([^*]+)\*\*/g, "$1");
}

function statusBadgeLabel(status: string, baton: string | null | undefined, isZh: boolean): { text: string; live: boolean } {
  if (status === "signed") {
    return { text: isZh ? "醫生已簽署 ✓" : "Signed ✓", live: true };
  }
  if (baton === "with_nurse") {
    return { text: isZh ? "護士核對中" : "Nurse Check", live: true };
  }
  switch (status) {
    case "needs_decision":
      return { text: isZh ? "醫生審閱中" : "With Doctor", live: true };
    case "running":
      return { text: isZh ? "對話進行中" : "Active", live: true };
    case "scheduled":
      return { text: isZh ? "已排期追蹤" : "Scheduled", live: true };
    default:
      return { text: isZh ? "全新案例" : "New", live: false };
  }
}

export function PatientChatApp({ initialJourney = "wingyan" }: PatientChatAppProps) {
  const [journeyKey, setJourneyKey] = useState<string>(initialJourney);
  const [lang, setLang] = useState<"en" | "zh-HK">("en");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [showPrompts, setShowPrompts] = useState(true);

  const [journeys, setJourneys] = useState<JourneyMeta[]>([]);
  const [activeJourneyInfo, setActiveJourneyInfo] = useState<JourneyMeta | null>(null);
  const [patientToken, setPatientToken] = useState<string>("");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [suggestedReplies, setSuggestedReplies] = useState<string[]>([]);
  const [crisis, setCrisis] = useState<Record<string, any> | null>(null);

  const [typing, setTyping] = useState(false);
  const [sending, setSending] = useState(false);
  const [input, setInput] = useState("");
  const [errorNote, setErrorNote] = useState<string | null>(null);

  // Two-mode Identity & PDPO Consent Gate (New Case minimal details vs Existing Patient verified chart link)
  const [consentDone, setConsentDone] = useState(false);
  const [idMode, setIdMode] = useState<"new_case" | "existing">("new_case");
  const [idName, setIdName] = useState("");
  const [idHkid4, setIdHkid4] = useState("4821");
  const [day3Busy, setDay3Busy] = useState(false);

  const sessionEpochRef = useRef<string | null>(null);
  const bootGenRef = useRef<number>(0);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  const isZh = lang === "zh-HK";

  const scrollToBottom = useCallback(() => {
    window.requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages.length, typing, scrollToBottom]);

  const refreshJourneys = useCallback(async () => {
    try {
      const res = await v2Api.liveDemoJourneys();
      if (Array.isArray(res.journeys)) {
        setJourneys(res.journeys);
      }
      if (res.session_epoch && !sessionEpochRef.current) {
        sessionEpochRef.current = res.session_epoch;
      }
    } catch {
      // ignore transient network errors
    }
  }, []);

  /**
   * Core QR / Journey Start Sequence:
   * 1. Clear chat completely empty
   * 2. Pause ~420ms -> show clinic typing bubble
   * 3. Call backend /api/v1/demo/live/chat/start (spawns live thread on laptop /workspace)
   * 4. After natural ~900ms typing cadence -> reveal welcome message & clinic introduction
   */
  const startJourneyFlow = useCallback(
    async (targetKey: string, targetLang: "en" | "zh-HK", forceRestart = false) => {
      const gen = ++bootGenRef.current;
      setMessages([]);
      setSuggestedReplies([]);
      setCrisis(null);
      setErrorNote(null);
      setTyping(false);
      setConsentDone(false);
      setIdMode(targetKey === "wingyan" ? "new_case" : "existing");

      // Step 1: Empty chat interface pause before clinic typing bubble appears
      await new Promise((r) => window.setTimeout(r, 420));
      if (bootGenRef.current !== gen) return;

      // Step 2: Clinic typing bubble appears while backend creates live thread in /workspace
      setTyping(true);
      const tStart = Date.now();
      try {
        const res = await v2Api.liveChatStart(targetKey, targetLang, forceRestart);
        if (bootGenRef.current !== gen) return;

        // Ensure realistic typing cadence on brand-new start
        const minTypingMs = res.already_started && !forceRestart ? 260 : 920;
        const elapsed = Date.now() - tStart;
        if (elapsed < minTypingMs) {
          await new Promise((r) => window.setTimeout(r, minTypingMs - elapsed));
        }
        if (bootGenRef.current !== gen) return;

        sessionEpochRef.current = res.session_epoch || sessionEpochRef.current;
        setPatientToken(res.token || "");
        setActiveJourneyInfo(res.journey || null);
        const nextMsgs = Array.isArray(res.messages) ? res.messages : [];
        setMessages(nextMsgs);
        setSuggestedReplies(Array.isArray(res.suggested_replies) ? res.suggested_replies : []);
        setCrisis(res.crisis || null);
        setIdName(res.journey?.patient_name || "");
        // If conversation already has patient turns, keep gate unlocked
        setConsentDone(nextMsgs.length > 1);
        setTyping(false);
        void refreshJourneys();
      } catch (e: any) {
        if (bootGenRef.current !== gen) return;
        setTyping(false);
        setErrorNote(e?.message || "Could not connect to clinic careline.");
      }
    },
    [refreshJourneys],
  );

  // Initial mount: QR scan lands here -> starts fresh empty -> typing bubble -> welcome!
  useEffect(() => {
    void startJourneyFlow(journeyKey, lang, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [journeyKey]);

  // Live SSE + 2.5s poll fallback (reliable even over Cloudflare quick tunnels)
  useEffect(() => {
    let closed = false;
    let es: EventSource | null = null;
    let reconnectTimer: number | null = null;

    const syncState = async () => {
      if (closed || sending) return;
      try {
        const st = await v2Api.liveChatState(journeyKey, lang);
        if (closed) return;

        // Detect global "Reset Demo" triggered on the laptop!
        if (st.session_epoch && sessionEpochRef.current && st.session_epoch !== sessionEpochRef.current) {
          sessionEpochRef.current = st.session_epoch;
          await startJourneyFlow(journeyKey, lang, false);
          return;
        }

        if (st.started && Array.isArray(st.messages)) {
          setMessages((prev) => {
            if (JSON.stringify(prev) === JSON.stringify(st.messages)) return prev;
            return st.messages;
          });
          setCrisis(st.crisis || null);
          if (Array.isArray(st.suggested_replies)) {
            setSuggestedReplies((prev) =>
              JSON.stringify(prev) === JSON.stringify(st.suggested_replies) ? prev : st.suggested_replies,
            );
          }
        }
        void refreshJourneys();
      } catch {
        // ignore transient poll failures
      }
    };

    const connectSse = () => {
      if (closed || typeof EventSource === "undefined") return;
      try {
        es = new EventSource("/api/v1/live/stream");
        const onEvt = () => {
          void syncState();
        };
        [
          "patient.care_plan_delivered",
          "patient.care_plan_retracted",
          "workspace.committed",
          "workspace.undone",
          "demo.reset",
        ].forEach((ev) => es?.addEventListener(ev, onEvt));
        es.onerror = () => {
          es?.close();
          if (!closed) {
            reconnectTimer = window.setTimeout(connectSse, 3000);
          }
        };
      } catch {
        // poll fallback handles sync
      }
    };

    connectSse();
    const poll = window.setInterval(() => {
      void syncState();
    }, 2500);

    return () => {
      closed = true;
      es?.close();
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      window.clearInterval(poll);
    };
  }, [journeyKey, lang, sending, startJourneyFlow, refreshJourneys]);

  const handleSend = async (overrideText?: string) => {
    const txt = (overrideText ?? input).trim();
    if (!txt || sending || typing) return;
    setInput("");
    setErrorNote(null);
    setSending(true);

    // Optimistic patient bubble on right
    const nowTime = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
    const patName = activeJourneyInfo?.patient_name || "You";
    setMessages((prev) => [...prev, { actor: patName, role: "patient", time: nowTime, text: txt }]);

    // Brief natural pause before clinic typing bubble
    await new Promise((r) => window.setTimeout(r, 260));
    setTyping(true);
    const t0 = Date.now();

    try {
      const res = await v2Api.liveChatTurn(journeyKey, txt, lang);
      const elapsed = Date.now() - t0;
      if (elapsed < 720) {
        await new Promise((r) => window.setTimeout(r, 720 - elapsed));
      }
      setMessages(Array.isArray(res.messages) ? res.messages : []);
      setCrisis(res.crisis || null);
      if (Array.isArray(res.suggested_replies)) {
        setSuggestedReplies(res.suggested_replies);
      }
      void refreshJourneys();
    } catch (e: any) {
      setErrorNote(e?.message || "Unable to send message right now.");
    } finally {
      setTyping(false);
      setSending(false);
    }
  };

  const handleSelectJourney = (nextKey: string, restart = false) => {
    setDrawerOpen(false);
    if (nextKey === journeyKey) {
      if (restart) {
        void startJourneyFlow(nextKey, lang, true);
      }
      return;
    }
    // Auto-switch the phone language to the persona's preferred language (e.g. Cantonese-speaking patients).
    const nextMeta = journeys.find((j) => j.key === nextKey);
    const preferred = nextMeta?.default_lang === "zh-HK" ? "zh-HK" : "en";
    if (preferred !== lang) setLang(preferred);
    window.history.replaceState({}, "", `/p/chat/${nextKey}`);
    setJourneyKey(nextKey);
  };

  const handleGlobalReset = async () => {
    setDrawerOpen(false);
    try {
      const res = await v2Api.liveDemoReset();
      sessionEpochRef.current = res.session_epoch || null;
      await startJourneyFlow(journeyKey, lang, false);
    } catch (e: any) {
      setErrorNote(e?.message || "Reset failed.");
    }
  };

  const handleDownloadPdf = async (certId: string) => {
    if (!patientToken) return;
    try {
      const tok = await v2Api.certDownloadToken(certId, patientToken);
      await v2Api.downloadVaultPdf(tok.download_url, patientToken, `HK_MCHK_Sick_Leave_${certId}.pdf`);
    } catch (e: any) {
      setErrorNote(e?.message || "Unable to download certificate PDF.");
    }
  };

  const toggleLang = () => {
    const nextLang = isZh ? "en" : "zh-HK";
    setLang(nextLang);
    // Refresh suggested replies in new language
    void v2Api.liveChatState(journeyKey, nextLang).then((st) => {
      if (Array.isArray(st.suggested_replies)) setSuggestedReplies(st.suggested_replies);
    });
  };

  const currentPatientLabel = activeJourneyInfo
    ? `${isZh ? activeJourneyInfo.patient_name_zh : activeJourneyInfo.patient_name} (${activeJourneyInfo.age_sex})`
    : "";

  return (
    <div className="pc-shell" data-testid="patient-chat-app">
      {/* Top Native Mobile Header */}
      <header className="pc-header">
        <div className="pc-header-left">
          <button
            type="button"
            className="pc-icon-btn"
            aria-label="Open patient journeys"
            onClick={() => {
              void refreshJourneys();
              setDrawerOpen(true);
            }}
            data-testid="open-journeys-sidebar-btn"
          >
            <Menu size={18} />
          </button>
          <div className="pc-avatar" aria-hidden="true">CM</div>
          <div className="pc-header-title-wrap">
            <div className="pc-header-title">
              {isZh ? "中環及半山家庭醫務中心" : "Central Medical"}
            </div>
            <div className="pc-header-sub">
              <span className="pc-online-dot" />
              <span>
                {typing
                  ? isZh
                    ? "Sam 正在輸入…"
                    : "Sam is typing…"
                  : isZh
                    ? "Sam · 診所護理助手"
                    : "Sam · Clinic Care Assistant"}
              </span>
            </div>
          </div>
        </div>
        <button type="button" className="pc-lang-btn" onClick={toggleLang}>
          {isZh ? "EN" : "繁中"}
        </button>
      </header>

      {/* Subtle Persona Context Bar */}
      {currentPatientLabel && (
        <div className="pc-persona-strip">
          <span>
            {isZh ? "病人視角：" : "Patient: "}
            <strong>{currentPatientLabel}</strong>
          </span>
          <button type="button" onClick={() => { void refreshJourneys(); setDrawerOpen(true); }}>
            {isZh ? "切換故事 ▾" : "Switch journey ▾"}
          </button>
        </div>
      )}

      {/* Pure Conversational Thread */}
      <main className="pc-messages" data-testid="patient-chat-messages">
        <div className="pc-date-pill">{isZh ? "今天" : "Today"}</div>

        {messages.map((m, idx) => {
          if (m.role === "system") {
            return (
              <div
                key={idx}
                className="pc-system-pill"
                data-testid="chat-system-divider"
                style={{
                  alignSelf: "center",
                  margin: "8px 0 4px",
                  padding: "6px 12px",
                  borderRadius: 999,
                  background: "#eef6f3",
                  border: "1px solid #cce3db",
                  color: "#0f6b58",
                  fontSize: 12,
                  fontWeight: 600,
                  textAlign: "center",
                  maxWidth: "92%",
                }}
              >
                {m.text} · {m.time}
              </div>
            );
          }

          const isDoctorCard = m.role === "doctor" || Boolean(m.care_plan);
          const isClinic = m.actor === "Sam" || m.role === "assistant";
          const isPatient = !isDoctorCard && !isClinic;

          if (isDoctorCard && m.care_plan) {
            return (
              <div key={idx} className="pc-row is-doctor" data-testid="chat-doctor-signed-card">
                <span className="pc-sender-label">Sam · {isZh ? "診所助手" : "Clinic AI"}</span>
                <div className="pc-doctor-card">
                  <div style={{ fontSize: 14, lineHeight: 1.48 }}>{m.text}</div>
                  {m.care_plan.rx_lines && m.care_plan.rx_lines.length > 0 && (
                    <ul className="pc-doctor-list">
                      {m.care_plan.rx_lines.map((line, i) => (
                        <li key={i}>{line}</li>
                      ))}
                    </ul>
                  )}
                  {m.care_plan.certificates && m.care_plan.certificates.map((cert) => (
                    <button
                      key={cert.id}
                      type="button"
                      className="pc-pdf-btn"
                      onClick={() => handleDownloadPdf(cert.id)}
                      data-testid="chat-download-mchk-pdf"
                    >
                      <FileText size={15} />
                      <span>
                        {isZh
                          ? `下載雙語醫生病假證明書 (${cert.days_off}日 · PDF)`
                          : `Download MCHK Sick Leave PDF (${cert.days_off}d)`}
                      </span>
                    </button>
                  ))}
                </div>
                <span className="pc-bubble-time">{m.time}</span>
              </div>
            );
          }

          if (m.is_crisis) {
            return (
              <div key={idx} className="pc-row is-doctor" data-testid="chat-crisis-card">
                <div className="pc-crisis-card">
                  <div className="pc-crisis-title">
                    {isZh ? "偵測到緊急症狀 — 請立即致電 999" : "Emergency Symptoms — Call 999 Now"}
                  </div>
                  <div style={{ fontSize: 14, lineHeight: 1.48 }}>{m.text}</div>
                  <a href="tel:999" className="pc-crisis-call">
                    {isZh ? "立即致電 999 緊急熱線" : "Call 999 Emergency Services"}
                  </a>
                </div>
                <span className="pc-bubble-time">{m.time}</span>
              </div>
            );
          }

          return (
            <div key={idx} className={`pc-row ${isPatient ? "is-patient" : "is-clinic"}`}>
              {!isPatient && <span className="pc-sender-label">Sam · {isZh ? "診所助手" : "Clinic AI"}</span>}
              <div className="pc-bubble">{renderBoldText(m.text)}</div>
              <span className="pc-bubble-time">{m.time}</span>
            </div>
          );
        })}

        {/* First-Step Identity & HK PDPO Consent Gate (New Case vs Existing Patient HKID Link) */}
        {!consentDone && messages.length > 0 && messages.length <= 1 && !typing && (
          <div
            data-testid="chat-identity-consent-gate"
            style={{
              background: "#ffffff",
              border: "1px solid #e2ddd4",
              borderRadius: 14,
              padding: "13px 14px",
              marginTop: 4,
              boxShadow: "0 1px 3px rgba(28,25,23,0.05)",
              display: "flex",
              flexDirection: "column",
              gap: 10,
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 700, color: "#0f6b58", letterSpacing: "0.03em", textTransform: "uppercase" }}>
              {isZh ? "身份確認及私隱同意 (HK PDPO)" : "Quick Check-In & Consent (HK PDPO)"}
            </div>
            <div style={{ fontSize: 13, color: "#44403c", lineHeight: 1.42 }}>
              {isZh
                ? "為保障私隱，我們只會收集協助今次診症所需嘅基本資料。未經核實身份，系統唔會查閱任何過往病歷。"
                : "To protect your privacy, we only collect what's needed for today's inquiry. Existing clinic charts are never accessed until verified."}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
              <button
                type="button"
                onClick={() => setIdMode("new_case")}
                style={{
                  padding: "8px 10px",
                  borderRadius: 9,
                  fontSize: 12.5,
                  fontWeight: 600,
                  border: idMode === "new_case" ? "1.5px solid #0f766e" : "1px solid #d6d3d1",
                  background: idMode === "new_case" ? "#eef7f5" : "#fafaf9",
                  color: idMode === "new_case" ? "#0f6b58" : "#57534e",
                  cursor: "pointer",
                }}
              >
                {isZh ? "新個案（只需姓名）" : "New Case (Name only)"}
              </button>
              <button
                type="button"
                onClick={() => setIdMode("existing")}
                style={{
                  padding: "8px 10px",
                  borderRadius: 9,
                  fontSize: 12.5,
                  fontWeight: 600,
                  border: idMode === "existing" ? "1.5px solid #0f766e" : "1px solid #d6d3d1",
                  background: idMode === "existing" ? "#eef7f5" : "#fafaf9",
                  color: idMode === "existing" ? "#0f6b58" : "#57534e",
                  cursor: "pointer",
                }}
              >
                {isZh ? "舊症病人（核對病歷）" : "Existing Patient"}
              </button>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <input
                type="text"
                value={idName}
                onChange={(e) => setIdName(e.target.value)}
                placeholder={isZh ? "你的姓名" : "Your name"}
                style={{
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: "1px solid #d6d3d1",
                  fontSize: 13.5,
                  background: "#fafaf9",
                }}
              />
              {idMode === "existing" && (
                <input
                  type="text"
                  value={idHkid4}
                  onChange={(e) => setIdHkid4(e.target.value)}
                  placeholder={isZh ? "香港身份證最後 4 位數字（核實病歷用）" : "HKID last 4 digits (to link existing chart)"}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 8,
                    border: "1px solid #d6d3d1",
                    fontSize: 13.5,
                    background: "#fafaf9",
                  }}
                />
              )}
            </div>
            <button
              type="button"
              onClick={() => setConsentDone(true)}
              data-testid="consent-continue-btn"
              style={{
                padding: "9px 12px",
                borderRadius: 9,
                border: "none",
                background: "#0f766e",
                color: "#ffffff",
                fontWeight: 600,
                fontSize: 13.5,
                cursor: "pointer",
              }}
            >
              {isZh
                ? idMode === "existing"
                  ? "同意並核實身份開始對話 →"
                  : "同意並開始新對話 →"
                : idMode === "existing"
                  ? "Consent & Verify Chart →"
                  : "Consent & Start Chat →"}
            </button>
          </div>
        )}

        {typing && (
          <div className="pc-row is-clinic" data-testid="clinic-typing-bubble">
            <span className="pc-sender-label">Sam · {isZh ? "診所助手" : "Clinic AI"}</span>
            <div className="pc-typing-bubble" aria-label="Clinic is typing">
              <span className="pc-typing-dot" />
              <span className="pc-typing-dot" />
              <span className="pc-typing-dot" />
            </div>
          </div>
        )}

        {errorNote && (
          <div style={{ alignSelf: "center", fontSize: 12.5, color: "var(--pc-red)", padding: "4px 10px" }}>
            {errorNote}
          </div>
        )}

        <div ref={bottomRef} />
      </main>

      {/* Demo Fast-Forward: Day-3 Autonomous AI Check-In (shown once doctor has signed) */}
      {(() => {
        const hasSigned = messages.some((m) => Boolean(m.care_plan));
        const hasDay3 = messages.some((m) => m.role === "system" && m.time === "Day +3");
        if (!hasSigned || hasDay3 || typing || crisis?.active) return null;
        return (
          <div style={{ padding: "6px 12px 2px", display: "flex", justifyContent: "center" }}>
            <button
              type="button"
              disabled={day3Busy}
              data-testid="fast-forward-day3-btn"
              onClick={async () => {
                setDay3Busy(true);
                setTyping(true);
                try {
                  const res = await v2Api.liveChatCheckin(journeyKey, lang);
                  if (Array.isArray(res.messages)) setMessages(res.messages);
                  if (Array.isArray(res.suggested_replies)) setSuggestedReplies(res.suggested_replies);
                  void refreshJourneys();
                } catch (e: any) {
                  setErrorNote(e?.message || "Could not trigger Day-3 check-in.");
                } finally {
                  setTyping(false);
                  setDay3Busy(false);
                }
              }}
              style={{
                padding: "7px 14px",
                borderRadius: 999,
                border: "1px solid #0f766e",
                background: "#eef7f5",
                color: "#0f6b58",
                fontSize: 12.5,
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {day3Busy
                ? isZh ? "模擬 3 日後追蹤中…" : "Simulating Day +3 check-in…"
                : isZh ? "快轉示範：3 日後 AI 自動問候 →" : "Fast-forward: Day +3 AI Check-In →"}
            </button>
          </div>
        );
      })()}

      {/* Bottom Suggested Demo Prompts & Context-Aware Replies (always anchored above composer — full free-text always works!) */}
      {(() => {
        const sentSet = new Set(
          messages
            .filter((m) => m.actor !== "Sam" && m.role !== "assistant" && m.role !== "doctor" && m.role !== "system")
            .map((m) => m.text.trim().toLowerCase()),
        );
        const remainingSuggestions = suggestedReplies.filter((p) => !sentSet.has(p.trim().toLowerCase()));
        if (!consentDone || !showPrompts || remainingSuggestions.length === 0 || messages.length === 0 || typing || crisis?.active) {
          return null;
        }
        const isOpeningTurn = messages.length === 1;
        return (
          <div className="pc-suggestions" aria-label="Suggested demo replies">
            {remainingSuggestions.map((prompt, i) => (
              <button
                key={prompt}
                type="button"
                className={`pc-chip${isOpeningTurn && i === 0 ? " is-demo-first" : ""}`}
                onClick={() => void handleSend(prompt)}
                title={prompt}
                data-testid={isOpeningTurn && i === 0 ? "suggested-first-demo-prompt" : `suggested-reply-${i}`}
              >
                {isOpeningTurn && i === 0 ? `✦ ${prompt}` : prompt}
              </button>
            ))}
          </div>
        );
      })()}

      {/* Natural Language Message Composer */}
      <form
        className="pc-composer"
        onSubmit={(e) => {
          e.preventDefault();
          void handleSend();
        }}
      >
        <button
          type="button"
          className="pc-icon-btn"
          style={{ width: 34, height: 34, borderRadius: "50%", color: "var(--pc-jade)", background: "#eef7f5" }}
          title={isZh ? "填入下一個示範對話" : "Fill next demo prompt"}
          data-testid="next-demo-prompt-btn"
          onClick={() => {
            if (!consentDone) setConsentDone(true);
            setShowPrompts(true);
            const sentSet = new Set(
              messages
                .filter((m) => m.actor !== "Sam" && m.role !== "assistant" && m.role !== "doctor" && m.role !== "system")
                .map((m) => m.text.trim().toLowerCase()),
            );
            const nextPrompt = suggestedReplies.find((p) => !sentSet.has(p.trim().toLowerCase()));
            if (nextPrompt) {
              setInput(nextPrompt);
            }
          }}
        >
          <Sparkles size={15} />
        </button>
        <button
          type="button"
          className="pc-icon-btn"
          style={{ width: 32, height: 32, borderRadius: "50%", color: "var(--pc-ink-2)", fontWeight: 600, fontSize: 16 }}
          title={isZh ? "附上化驗報告或相片" : "Attach photo or lab report"}
          onClick={() => {
            if (!consentDone) setConsentDone(true);
            setInput(
              isZh
                ? "我附上了最近嘅化驗報告／家居血壓讀數相片，請幫我交畀醫生參考。"
                : "I've attached a photo of my recent lab report / home reading for the doctor to review.",
            );
          }}
        >
          +
        </button>
        <input
          className="pc-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={isZh ? "輸入訊息予診所…" : "Message Central Medical…"}
          disabled={sending}
          data-testid="patient-chat-input"
        />
        <button
          type="submit"
          className="pc-send-btn"
          disabled={sending || typing || !input.trim()}
          aria-label="Send message"
          data-testid="patient-chat-send-btn"
        >
          <ArrowUp size={18} />
        </button>
      </form>

      {/* Slide-Over Patient Journeys Sidebar */}
      {drawerOpen && (
        <>
          <div className="pc-drawer-backdrop" onClick={() => setDrawerOpen(false)} />
          <aside className="pc-drawer" data-testid="patient-journeys-drawer">
            <div className="pc-drawer-head">
              <div>
                <div className="pc-drawer-title">
                  {isZh ? `診所病人個案（${journeys.length} 位）` : `Patient Journeys (${journeys.length})`}
                </div>
                <div className="pc-drawer-sub">
                  {isZh ? "選擇病人視角，從空白對話即時互動" : "Pick any patient to start a live conversation"}
                </div>
              </div>
              <button type="button" className="pc-icon-btn" onClick={() => setDrawerOpen(false)} aria-label="Close sidebar">
                <X size={16} />
              </button>
            </div>

            <div className="pc-drawer-list">
              {JOURNEY_GROUPS.map((grp) => {
                const items = journeys.filter((j) => (j.category || "acute") === grp.key);
                if (items.length === 0) return null;
                return (
                  <div key={grp.key} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <div className="pc-drawer-section">
                      <span>{isZh ? grp.labelZh : grp.labelEn}</span>
                      <span>{items.length}</span>
                    </div>
                    {items.map((j) => {
                      const isActive = j.key === journeyKey;
                      const badge = statusBadgeLabel(j.status, j.baton, isZh);
                      return (
                        <div
                          key={j.key}
                          className={`pc-journey-item ${isActive ? "is-active" : ""}`}
                          onClick={() => handleSelectJourney(j.key, false)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(e) => { if (e.key === "Enter") handleSelectJourney(j.key, false); }}
                          data-testid={`journey-option-${j.key}`}
                        >
                          <div className="pc-journey-top">
                            <span className="pc-journey-name">
                              {isZh ? j.patient_name_zh : j.patient_name} ({j.age_sex})
                            </span>
                            <span className={`pc-journey-badge ${badge.live ? "is-live" : ""}`}>
                              {badge.text}
                            </span>
                          </div>
                          <div className="pc-journey-title">{isZh ? j.title_zh : j.title}</div>
                          <div className="pc-journey-sub">{j.subtitle}</div>
                          {j.status !== "not_started" && (
                            <div style={{ marginTop: 4 }}>
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleSelectJourney(j.key, true);
                                }}
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 4,
                                  fontSize: 11.5,
                                  color: "var(--pc-jade)",
                                  background: "none",
                                  border: "none",
                                  padding: 0,
                                  cursor: "pointer",
                                  fontWeight: 600,
                                }}
                              >
                                <RotateCcw size={11} />
                                <span>{isZh ? "重新開始此對話" : "Start fresh from empty chat"}</span>
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>

            <div className="pc-drawer-foot">
              <button type="button" className="pc-reset-btn" onClick={handleGlobalReset} data-testid="phone-reset-demo-btn">
                {isZh ? "重設整個示範環境（清空工作台）" : "Reset Entire Demo (Clear All Cases)"}
              </button>
            </div>
          </aside>
        </>
      )}
    </div>
  );
}
