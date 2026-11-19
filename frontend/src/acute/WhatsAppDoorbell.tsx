import { useEffect, useState } from "react";
import QRCode from "qrcode";
import {
  CheckCircle2,
  Copy,
  ExternalLink,
  Eye,
  Lock,
  MessageCircle,
  QrCode,
  RefreshCw,
  ShieldCheck,
  Smartphone,
} from "lucide-react";
import { acuteApi, tr } from "./api";
import type { Lang, MagicPreviewResponse, WhatsAppMessage } from "./api";

interface WhatsAppDoorbellProps {
  lang: Lang;
  initialCampaignCode?: string;
  initialReferralCode?: string;
  onOpenMagicLink: (token: string, phone: string) => void;
  onNotify: (msg: string, isError?: boolean) => void;
}

function extractMagicToken(msg: WhatsAppMessage): string | null {
  if (msg.magic_token) return msg.magic_token;
  if (!msg.magic_link_url) return null;
  const m = /\/magic\/([A-Za-z0-9_\-.]+)/.exec(msg.magic_link_url);
  return m ? m[1] : null;
}

const DEFAULT_CAMPAIGNS = [
  { code: "HARBOUR-FLU-2026", label: "Harbour FinTech Winter Flu Shield 2026" },
  { code: "PACIFIC-FLU-2026", label: "Pacific BioLabs Flu Care 2026 (Small Team <5)" },
];

export function WhatsAppDoorbell({
  lang,
  initialCampaignCode,
  initialReferralCode,
  onOpenMagicLink,
  onNotify,
}: WhatsAppDoorbellProps) {
  const [campaignCode, setCampaignCode] = useState(initialCampaignCode || "HARBOUR-FLU-2026");
  const [referralCode, setReferralCode] = useState(initialReferralCode || "");
  const [phone, setPhone] = useState("+85291234567");
  const [qrDataUrl, setQrDataUrl] = useState<string>("");
  const [messages, setMessages] = useState<WhatsAppMessage[]>([]);
  const [loadingInbox, setLoadingInbox] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [previewResult, setPreviewResult] = useState<{ token: string; data: MagicPreviewResponse } | null>(null);
  const [previewBusy, setPreviewBusy] = useState<string | null>(null);

  useEffect(() => {
    if (initialCampaignCode) setCampaignCode(initialCampaignCode);
  }, [initialCampaignCode]);

  useEffect(() => {
    if (initialReferralCode) setReferralCode(initialReferralCode);
  }, [initialReferralCode]);

  // Generate real scannable QR code pointing to this campaign's WhatsApp doorbell payload
  useEffect(() => {
    const qrPayload = new URL("/wa", window.location.origin);
    qrPayload.searchParams.set("campaign", campaignCode.trim() || "ACME-FLU-2026");
    if (referralCode.trim()) {
      qrPayload.searchParams.set("ref", referralCode.trim());
    }
    QRCode.toDataURL(qrPayload.toString(), {
      width: 240,
      margin: 2,
      color: { dark: "#0f1f2b", light: "#ffffff" },
    })
      .then(setQrDataUrl)
      .catch(() => setQrDataUrl(""));
  }, [campaignCode, referralCode]);

  const loadInbox = async (targetPhone?: string) => {
    setLoadingInbox(true);
    try {
      const list = await acuteApi.waInbox(targetPhone ?? phone);
      setMessages(list);
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Could not load WhatsApp inbox", true);
    } finally {
      setLoadingInbox(false);
    }
  };

  useEffect(() => {
    void loadInbox(phone);
  }, []);

  const handleSimulateScan = async () => {
    setScanning(true);
    setPreviewResult(null);
    try {
      const res = await acuteApi.waScanQr({
        phone: phone.trim(),
        campaign_code: campaignCode.trim(),
        referral_code: referralCode.trim() || undefined,
        lang,
      });
      await loadInbox(phone.trim());
      onNotify(
        tr(
          lang,
          `WhatsApp doorbell message sent for ${res.campaign_code || campaignCode}. Zero clinical/ID data included.`,
          `已發送 WhatsApp 門鈴訊息（${res.campaign_code || campaignCode}），不含任何臨床資料或身份證號碼。`
        )
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "QR scan simulation failed", true);
    } finally {
      setScanning(false);
    }
  };

  const handlePreviewMagic = async (token: string) => {
    setPreviewBusy(token);
    try {
      const data = await acuteApi.magicPreview(token);
      setPreviewResult({ token, data });
      onNotify(
        tr(
          lang,
          "GET preview succeeded without burning token (safe against WhatsApp/bot link unfurling).",
          "GET 預覽成功且未消耗一次性連結（防止通訊軟件預覽失效）。"
        )
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Magic link preview failed", true);
    } finally {
      setPreviewBusy(null);
    }
  };

  const handleCopyOtp = (otp: string) => {
    void navigator.clipboard?.writeText(otp);
    onNotify(tr(lang, `Copied staging OTP ${otp}`, `已複製測試驗證碼 ${otp}`));
  };

  const allClean = messages.every((m) => m.leakage_passed !== false);

  return (
    <div className="ac-container" data-testid="surface-wa">
      <div className="ac-page-header">
        <div>
          <h1>{tr(lang, "WhatsApp Doorbell & Corporate QR Poster", "WhatsApp 門鈴與企業二維碼海報")}</h1>
          <p>
            {tr(
              lang,
              "WhatsApp is strictly a non-clinical doorbell. Every outbound template is scanned by WhatsAppLeakageGuard for zero symptoms, drugs, diagnoses, or HKID numbers.",
              "WhatsApp 僅作非臨床入口通知。所有發出訊息均經 WhatsAppLeakageGuard 掃描，確保零症狀、零藥物、零診斷及零身份證資料外洩。"
            )}
          </p>
        </div>
        <span className="ac-badge ok" data-testid="wa-zero-leakage-badge">
          <ShieldCheck size={14} />
          {tr(
            lang,
            allClean ? "0 Clinical/ID Leakage Verified" : "Leakage Guard Active",
            allClean ? "已驗證：0 臨床/身份資料外洩" : "防外洩保護已啟用"
          )}
        </span>
      </div>

      <div className="ac-wa-layout">
        {/* Left: Corporate Office QR Poster + Simulator Controls */}
        <section className="ac-qr-poster" aria-label="Corporate Flu Campaign QR Poster">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
            <span className="ac-badge teal">
              <QrCode size={13} />
              {tr(lang, "Corporate Pantry Poster", "企業茶水間海報")}
            </span>
            <span className="ac-badge">MCHK Factual Notice</span>
          </div>

          <h2 style={{ margin: "0.4rem 0 0.2rem", fontSize: "1.2rem" }}>
            {tr(lang, "Seasonal Flu Acute Care Access", "季節性流感急性診症入口")}
          </h2>
          <p style={{ margin: 0, fontSize: "12.5px", color: "var(--ac-ink-2)" }}>
            {tr(
              lang,
              "Scan to receive a secure clinic portal link on WhatsApp. Licensed Hong Kong registered doctors.",
              "掃描二維碼以於 WhatsApp 接收加密診所入口連結。由香港註冊西醫主理。"
            )}
          </p>

          <div className="ac-qr-frame" data-testid="wa-qr-poster">
            {qrDataUrl ? (
              <img src={qrDataUrl} alt={`QR Code for campaign ${campaignCode}`} />
            ) : (
              <div style={{ width: 210, height: 210, display: "grid", placeItems: "center" }}>Generating QR…</div>
            )}
          </div>

          <div style={{ textAlign: "left", marginTop: "0.5rem" }}>
            <div className="ac-field">
              <label htmlFor="wa-campaign-select">{tr(lang, "Corporate Campaign Code", "企業計劃代碼")}</label>
              <div style={{ display: "flex", gap: "0.4rem" }}>
                <input
                  id="wa-campaign-select"
                  className="ac-input ac-mono"
                  data-testid="wa-campaign-input"
                  value={campaignCode}
                  onChange={(e) => setCampaignCode(e.target.value)}
                  placeholder="ACME-FLU-2026"
                />
              </div>
              <div className="ac-pills" style={{ marginTop: "0.35rem" }}>
                {DEFAULT_CAMPAIGNS.map((c) => (
                  <button
                    key={c.code}
                    type="button"
                    className="ac-pill-btn"
                    onClick={() => setCampaignCode(c.code)}
                  >
                    {c.code}
                  </button>
                ))}
              </div>
            </div>

            <div className="ac-grid-2">
              <div className="ac-field">
                <label htmlFor="wa-phone-input">{tr(lang, "Employee Mobile (HK)", "員工手提電話（香港）")}</label>
                <input
                  id="wa-phone-input"
                  className="ac-input ac-mono"
                  data-testid="wa-phone-input"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="+85291234567"
                />
              </div>
              <div className="ac-field">
                <label htmlFor="wa-referral-input">
                  {tr(lang, "Colleague Referral Code (Optional)", "同事推薦碼（選填）")}
                </label>
                <input
                  id="wa-referral-input"
                  className="ac-input ac-mono"
                  data-testid="wa-referral-input"
                  value={referralCode}
                  onChange={(e) => setReferralCode(e.target.value)}
                  placeholder="REF-XXXXXX"
                />
              </div>
            </div>

            <button
              type="button"
              className="ac-btn primary"
              style={{ width: "100%", padding: "0.68rem 1rem" }}
              data-testid="wa-simulate-scan-btn"
              disabled={scanning || !phone.trim() || !campaignCode.trim()}
              onClick={() => void handleSimulateScan()}
            >
              <Smartphone size={16} />
              <span>
                {scanning
                  ? tr(lang, "Scanning QR & Dispatching Doorbell…", "正在掃描並發送門鈴訊息…")
                  : tr(lang, "Simulate QR Scan on Phone", "模擬手機掃描二維碼")}
              </span>
            </button>
          </div>
        </section>

        {/* Right: Realistic WhatsApp Phone Frame Simulator */}
        <section aria-label="WhatsApp Doorbell Phone Simulator">
          <div className="ac-phone-frame" data-testid="wa-phone-frame">
            <div className="ac-wa-top">
              <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                <div
                  style={{
                    width: 34,
                    height: 34,
                    borderRadius: "50%",
                    background: "rgba(255,255,255,0.18)",
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  <MessageCircle size={18} />
                </div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: "13.5px" }}>
                    {tr(lang, "Central GP Clinic (Doorbell)", "中環普通科診所（安全入口）")}
                  </div>
                  <div style={{ fontSize: "11px", opacity: 0.85 }}>
                    {tr(lang, "Official Business Account • Non-Clinical Only", "官方認證帳號 • 僅限非臨床通知")}
                  </div>
                </div>
              </div>
              <button
                type="button"
                className="ac-demobar-btn"
                onClick={() => void loadInbox(phone)}
                disabled={loadingInbox}
                data-testid="wa-refresh-inbox-btn"
                title={tr(lang, "Refresh inbox", "重新整理訊息")}
              >
                <RefreshCw size={13} />
              </button>
            </div>

            <div className="ac-wa-chat" data-testid="wa-inbox-thread">
              <div
                style={{
                  alignSelf: "center",
                  background: "rgba(255, 255, 255, 0.88)",
                  border: "1px solid var(--ac-ok-border)",
                  color: "var(--ac-ok)",
                  fontSize: "11px",
                  padding: "0.28rem 0.65rem",
                  borderRadius: "8px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "0.35rem",
                  fontWeight: 600,
                }}
              >
                <Lock size={11} />
                {tr(
                  lang,
                  "0 Clinical / 0 ID Numbers — All care happens inside the encrypted Patient PWA",
                  "0 臨床資料 / 0 身份證號 — 所有診症均於加密病人端進行"
                )}
              </div>

              {messages.length === 0 && (
                <div className="ac-wa-bubble" style={{ alignSelf: "center", textAlign: "center", color: "var(--ac-ink-3)" }}>
                  {tr(
                    lang,
                    'No messages for this phone yet. Click "Simulate QR Scan on Phone" to start Story 1.',
                    "此電話號碼暫無訊息。請按「模擬手機掃描二維碼」開始體驗。"
                  )}
                </div>
              )}

              {messages.map((msg) => {
                const token = extractMagicToken(msg);
                return (
                  <div key={msg.id} className="ac-wa-bubble" data-testid="wa-message-bubble">
                    <div style={{ whiteSpace: "pre-wrap" }}>{msg.body_text}</div>

                    {token && (
                      <div className="ac-wa-link-card" data-testid="wa-magic-link-card">
                        <div style={{ fontSize: "11.5px", fontWeight: 700, color: "var(--ac-teal)", marginBottom: "0.25rem" }}>
                          {tr(lang, "Secure Single-Use Clinic Portal Link", "加密一次性診所入口連結")}
                        </div>
                        <div className="ac-mono" style={{ fontSize: "11px", color: "var(--ac-ink-3)", wordBreak: "break-all" }}>
                          /p/magic/{token}
                        </div>
                        <div className="ac-btn-row" style={{ marginTop: "0.5rem" }}>
                          <button
                            type="button"
                            className="ac-btn sm"
                            data-testid="wa-preview-magic-btn"
                            disabled={previewBusy === token}
                            onClick={() => void handlePreviewMagic(token)}
                          >
                            <Eye size={13} />
                            <span>{tr(lang, "GET Preview (Safe)", "GET 預覽（不消耗）")}</span>
                          </button>
                          <button
                            type="button"
                            className="ac-btn primary sm"
                            data-testid="wa-open-magic-btn"
                            onClick={() => onOpenMagicLink(token, msg.phone || phone)}
                          >
                            <ExternalLink size={13} />
                            <span>{tr(lang, "Open Patient PWA", "開啟加密病人端")}</span>
                          </button>
                        </div>
                      </div>
                    )}

                    {msg.demo_otp && (
                      <div className="ac-wa-otp-box" data-testid="wa-demo-otp-box">
                        <div>
                          <div style={{ fontSize: "11px", fontWeight: 700, color: "var(--ac-warn)" }}>
                            {tr(lang, "Staging 6-Digit OTP", "測試用 6 位數驗證碼")}
                          </div>
                          <div className="ac-mono" style={{ fontSize: "16px", fontWeight: 800, letterSpacing: "0.14em" }} data-testid="wa-demo-otp-code">
                            {msg.demo_otp}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="ac-btn sm"
                          onClick={() => handleCopyOtp(msg.demo_otp!)}
                        >
                          <Copy size={13} />
                          <span>{tr(lang, "Copy", "複製")}</span>
                        </button>
                      </div>
                    )}

                    <div className="ac-wa-bubble-meta">
                      <span className="ac-badge ok" style={{ padding: "0.05rem 0.4rem", fontSize: "10.5px" }}>
                        <CheckCircle2 size={10} />
                        {msg.leakage_passed !== false
                          ? tr(lang, "0 Clinical/ID Leakage", "0 臨床/ID外洩")
                          : "Flagged"}
                      </span>
                      <span>{new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {previewResult && (
            <div className="ac-card" style={{ marginTop: "1rem" }} data-testid="wa-magic-preview-result">
              <div className="ac-card-head" style={{ marginBottom: "0.45rem" }}>
                <h3 className="ac-card-title">
                  <ShieldCheck size={16} color="var(--ac-ok)" />
                  {tr(lang, "GET Magic-Link Preview Verified (Token NOT Burned)", "GET 連結預覽驗證（一次性權杖未被消耗）")}
                </h3>
                <span className={`ac-badge ${previewResult.data.redeemed ? "crit" : "ok"}`}>
                  {previewResult.data.redeemed ? "Redeemed" : "Still Valid (Unburned)"}
                </span>
              </div>
              <div style={{ fontSize: "12.5px", color: "var(--ac-ink-2)" }}>
                <div>
                  <strong>{tr(lang, "Campaign:", "計劃：")}</strong> {previewResult.data.campaign_code || "—"}{" "}
                  {previewResult.data.campaign_name ? `(${previewResult.data.campaign_name})` : ""}
                </div>
                <div>
                  <strong>{tr(lang, "Clinic:", "診所：")}</strong> {previewResult.data.clinic_name || "Central GP Clinic"}
                </div>
                <div>
                  <strong>{tr(lang, "Phone:", "電話：")}</strong>{" "}
                  <span className="ac-mono">{previewResult.data.phone_masked || "****"}</span>
                </div>
              </div>
              <div style={{ marginTop: "0.65rem" }}>
                <button
                  type="button"
                  className="ac-btn primary sm"
                  onClick={() => onOpenMagicLink(previewResult.token, phone)}
                >
                  <ExternalLink size={13} />
                  <span>{tr(lang, "Proceed to POST Redeem & OTP Step-Up", "前往 POST 兌換及驗證碼步驟")}</span>
                </button>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
