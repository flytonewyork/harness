import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, FileText, Menu, RotateCcw, Sparkles, X } from "lucide-react";
import { v2Api } from "../api";
import "./patientChat.css";

interface ChatMsg {
  actor: string;
  role?: "doctor" | "assistant" | "patient";
  time: string;
  text: string;
  lang?: string;
  is_crisis?: boolean;
  trace_id?: string;
  care_plan?: {
    signed_by: string;
    mchk_reg_no: string;
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

function renderBoldText(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={idx}>{part.slice(2, -2)}</strong>;
    }
    return <span key={idx}>{part}</span>;
  });
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
        setMessages(Array.isArray(res.messages) ? res.messages : []);
        setSuggestedReplies(Array.isArray(res.suggested_replies) ? res.suggested_replies : []);
        setCrisis(res.crisis || null);
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
          const isDoctor = m.role === "doctor" || Boolean(m.care_plan);
          const isClinic = m.actor === "Sam" || m.role === "assistant";
          const isPatient = !isDoctor && !isClinic;

          if (isDoctor && m.care_plan) {
            return (
              <div key={idx} className="pc-row is-doctor" data-testid="chat-doctor-signed-card">
                <div className="pc-doctor-card">
                  <div className="pc-doctor-kicker">
                    {m.care_plan.signed_by} · MCHK {m.care_plan.mchk_reg_no} · {isZh ? "已簽署" : "Signed Plan"}
                  </div>
                  <div className="pc-doctor-title">
                    {isZh ? "你的個人化診所護理計劃已就緒" : "Your Doctor-Signed Care Plan"}
                  </div>
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

      {/* Optional Subtle Suggested Replies (for smooth live stage demoing — full free-text always works!) */}
      {(() => {
        const sentSet = new Set(
          messages
            .filter((m) => m.actor !== "Sam" && m.role !== "assistant" && m.role !== "doctor")
            .map((m) => m.text.trim().toLowerCase()),
        );
        const remainingSuggestions = suggestedReplies.filter((p) => !sentSet.has(p.trim().toLowerCase()));
        if (!showPrompts || remainingSuggestions.length === 0 || messages.length === 0 || typing || crisis?.active) {
          return null;
        }
        return (
          <div className="pc-suggestions" aria-label="Suggested demo replies">
            {remainingSuggestions.map((prompt, i) => (
              <button
                key={prompt}
                type="button"
                className="pc-chip"
                onClick={() => void handleSend(prompt)}
                title={prompt}
                data-testid={`suggested-reply-${i}`}
              >
                {prompt}
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
          style={{ width: 34, height: 34, borderRadius: "50%", color: showPrompts ? "var(--pc-jade)" : "var(--pc-ink-3)" }}
          title={showPrompts ? "Hide demo reply chips" : "Show demo reply chips"}
          onClick={() => setShowPrompts((v) => !v)}
        >
          <Sparkles size={15} />
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
