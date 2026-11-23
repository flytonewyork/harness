import { useEffect, useState } from "react";
import QRCode from "qrcode";
import {
  AlertTriangle,
  Calendar,
  CheckCircle2,
  Download,
  FileText,
  HeartPulse,
  Lock,
  MessageSquare,
  PhoneCall,
  Pill,
  QrCode,
  Send,
  Shield,
  ShieldAlert,
  Syringe,
  Truck,
  UserCheck,
} from "lucide-react";
import { acuteApi, CVX_VACCINE_OPTIONS, downloadVaultPdf, tr } from "./api";
import type {
  AuthSession,
  IntakeResponse,
  Lang,
  MagicPreviewResponse,
  PatientMeResponse,
  PicsDocument,
  TeleconsultSlot,
} from "./api";

type PatientTab = "intake" | "booking" | "documents" | "messages" | "privacy";

interface PatientPWAProps {
  lang: Lang;
  session: AuthSession | null;
  magicToken: string | null;
  onSessionEstablished: (session: AuthSession) => void;
  onGoToWhatsApp: (campaignCode?: string, referralCode?: string) => void;
  onNotify: (msg: string, isError?: boolean) => void;
}

const DEFAULT_CONSENT_SCOPES: PicsDocument["scopes"] = [
  {
    scope: "clinical_care",
    label_en: "Clinical Teleconsultation & Medical Record Storage (Required)",
    label_zh: "遙距診症及醫療記錄保存（必須）",
    description_en: "Enables HK registered doctors to assess your symptoms, issue prescriptions/certificates, and maintain statutory medical records.",
    description_zh: "授權香港註冊西醫評估您的症狀、處方藥物／簽發病假紙及依法保存醫療記錄。",
    required: true,
    default_checked: true,
  },
  {
    scope: "ai_assisted_intake",
    label_en: "AI-Assisted Structured Intake & Draft Preparation (Optional — Unbundled)",
    label_zh: "AI 輔助病歷整理及草稿預備（自選 — 獨立授權）",
    description_en: "AI structures your symptoms for the doctor. Declining routes directly to a human clinician without AI processing.",
    description_zh: "由 AI 協助整理症狀供醫生審閱。如不同意，您的個案將直接由醫生人手處理。",
    required: false,
    default_checked: true,
  },
  {
    scope: "ehealth_sharing",
    label_en: "Hong Kong eHealth (醫健通) Sharing Opt-In (Optional)",
    label_zh: "香港醫健通（eHealth）資料互通同意（自選）",
    description_en: "Permits syncing your acute flu consultation summary and vaccinations with HK eHealth.",
    description_zh: "容許將急性流感診症摘要及疫苗接種記錄同步至香港醫健通。",
    required: false,
    default_checked: false,
  },
  {
    scope: "invitations",
    label_en: "48h Recovery Check-In & Seasonal Flu Vaccination Invitations (Optional)",
    label_zh: "48 小時康復跟進及季節性流感疫苗通知（自選）",
    description_en: "Receive non-clinical reminder doorbells for 48-hour check-in and seasonal flu shots.",
    description_zh: "接收 48 小時康復跟進及季節性流感疫苗接種的非臨床提醒。",
    required: false,
    default_checked: true,
  },
  {
    scope: "deidentified_eval",
    label_en: "De-identified Clinical Safety Quality Evaluation (Optional)",
    label_zh: "去識別化臨床安全質素評估（自選）",
    description_en: "Allows stripped, non-identifiable records to audit clinical safety gates.",
    description_zh: "容許使用已去除個人身份資料的記錄作臨床安全系統審核。",
    required: false,
    default_checked: false,
  },
];

const PARTNER_CLINICS = [
  "Central GP Clinic (Queen's Road Central)",
  "Admiralty Medical Centre (Pacific Place)",
  "Causeway Bay Family Practice (Hysan Place)",
  "Tsim Sha Tsui Harbour Clinic (Canton Road)",
];

export function PatientPWA({
  lang,
  session,
  magicToken,
  onSessionEstablished,
  onGoToWhatsApp,
  onNotify,
}: PatientPWAProps) {
  // Step 1: Magic Link & OTP Step-Up state
  const [manualToken, setManualToken] = useState(magicToken || "");
  const [preview, setPreview] = useState<MagicPreviewResponse | null>(null);
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [demoOtpHint, setDemoOtpHint] = useState<string | null>(null);
  const [otpCode, setOtpCode] = useState("");
  const [authBusy, setAuthBusy] = useState(false);

  // Step 2: PICS & Onboarding state
  const [pics, setPics] = useState<PicsDocument | null>(null);
  const [nameEn, setNameEn] = useState("CHAN Siu Ming");
  const [nameZh, setNameZh] = useState("陳小明");
  const [dob, setDob] = useState("1991-06-15");
  const [sex, setSex] = useState("M");
  const [hkid, setHkid] = useState("C123456(7)");
  const [g6pdDeficient, setG6pdDeficient] = useState(false);
  const [weightKg, setWeightKg] = useState("68");
  const [allergiesText, setAllergiesText] = useState("");
  const [currentMedsText, setCurrentMedsText] = useState("");
  const [consents, setConsents] = useState<Record<string, boolean>>({
    clinical_care: true,
    ai_assisted_intake: true,
    ehealth_sharing: false,
    invitations: true,
    deidentified_eval: false,
  });
  const [onboardBusy, setOnboardBusy] = useState(false);

  // Step 3: Authenticated Patient Portal state
  const [me, setMe] = useState<PatientMeResponse | null>(null);
  const [loadingMe, setLoadingMe] = useState(false);
  const [tab, setTab] = useState<PatientTab>("intake");

  // Intake tab
  const [symptomsText, setSymptomsText] = useState("");
  const [tempC, setTempC] = useState("38.4");
  const [spo2Pct, setSpo2Pct] = useState("98");
  const [fluRatA, setFluRatA] = useState("positive");
  const [fluRatB, setFluRatB] = useState("negative");
  const [covidRat, setCovidRat] = useState("negative");
  const [intakeBusy, setIntakeBusy] = useState(false);
  const [intakeResult, setIntakeResult] = useState<IntakeResponse | null>(null);

  // Booking & Flu Vax tab
  const [slots, setSlots] = useState<TeleconsultSlot[]>([]);
  const [bookingBusy, setBookingBusy] = useState(false);
  const [vaxCvx, setVaxCvx] = useState("150");
  const [vaxClinic, setVaxClinic] = useState(PARTNER_CLINICS[0]);
  const [vaxSlot, setVaxSlot] = useState(() => {
    const dt = new Date(Date.now() + 2 * 86400000);
    dt.setHours(10, 30, 0, 0);
    return dt.toISOString().slice(0, 16);
  });

  // Documents & e-Scripts tab
  const [deliveryAddress, setDeliveryAddress] = useState("Flat 12B, Harbour View Tower, Central, Hong Kong");
  const [docBusy, setDocBusy] = useState<string | null>(null);

  // 48h Check-In & Messages tab
  const [followupStatus, setFollowupStatus] = useState<"improving" | "same" | "worse">("improving");
  const [feverResolved, setFeverResolved] = useState(true);
  const [newRedFlags, setNewRedFlags] = useState("");
  const [followupNotes, setFollowupNotes] = useState("");
  const [msgText, setMsgText] = useState("");
  const [msgBusy, setMsgBusy] = useState(false);

  // Privacy & Referrals tab
  const [referralQr, setReferralQr] = useState("");
  const [fhirBundle, setFhirBundle] = useState<Record<string, unknown> | null>(null);
  const [dcrField, setDcrField] = useState("allergies");
  const [dcrValue, setDcrValue] = useState("");
  const [dcrReason, setDcrReason] = useState("");
  const [deletionNotice, setDeletionNotice] = useState<{ status: string; retention_until?: string; notice: string } | null>(null);
  const [privacyBusy, setPrivacyBusy] = useState(false);

  const isPatientStepUp = Boolean(session && session.role === "patient" && session.step_up_verified !== false);

  useEffect(() => {
    if (magicToken) {
      setManualToken(magicToken);
      setChallengeId(null);
      setDemoOtpHint(null);
      setOtpCode("");
      acuteApi
        .magicPreview(magicToken)
        .then(setPreview)
        .catch(() => setPreview(null));
    }
  }, [magicToken]);

  const loadPatientState = async () => {
    if (!isPatientStepUp) return;
    setLoadingMe(true);
    try {
      const data = await acuteApi.patientMe();
      setMe(data);
      if (!data.patient || data.onboarded === false) {
        const picsDoc = await acuteApi.patientPics(lang).catch(() => null);
        if (picsDoc) setPics(picsDoc);
      } else {
        acuteApi
          .patientSlots()
          .then(setSlots)
          .catch(() => setSlots([]));
      }
    } catch (e) {
      // If patient hasn't onboarded yet, load PICS
      const picsDoc = await acuteApi.patientPics(lang).catch(() => null);
      if (picsDoc) setPics(picsDoc);
      if (e instanceof Error && !/onboard/i.test(e.message)) {
        onNotify(e.message, true);
      }
    } finally {
      setLoadingMe(false);
    }
  };

  useEffect(() => {
    if (isPatientStepUp) {
      void loadPatientState();
    } else {
      setMe(null);
    }
  }, [session?.access_token, isPatientStepUp]);

  // Generate colleague referral QR when patient profile loads
  useEffect(() => {
    const code = me?.patient?.referral_code;
    if (!code) return;
    const url = new URL("/wa", window.location.origin);
    url.searchParams.set("campaign", me?.patient?.campaign_code || "ACME-FLU-2026");
    url.searchParams.set("ref", code);
    QRCode.toDataURL(url.toString(), { width: 190, margin: 2, color: { dark: "#0f1f2b", light: "#ffffff" } })
      .then(setReferralQr)
      .catch(() => setReferralQr(""));
  }, [me?.patient?.referral_code, me?.patient?.campaign_code]);

  // ---------------------------------------------------------------------- Auth Handlers
  const handleRedeemMagic = async () => {
    const tok = (magicToken || manualToken).trim();
    if (!tok) return;
    setAuthBusy(true);
    try {
      const res = await acuteApi.magicRedeem(tok);
      const cid = res.challenge_id || res.otp_challenge_id || "";
      setChallengeId(cid);
      setDemoOtpHint(res.demo_otp || null);
      if (res.demo_otp) setOtpCode(res.demo_otp);
      onNotify(
        tr(
          lang,
          "Magic link redeemed via POST. 6-digit OTP sent for step-up verification.",
          "一次性連結已透過 POST 兌換，6 位數驗證碼已發送。"
        )
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Magic link redemption failed", true);
    } finally {
      setAuthBusy(false);
    }
  };

  const handleVerifyOtp = async () => {
    if (!challengeId || !otpCode.trim()) return;
    setAuthBusy(true);
    try {
      const res = await acuteApi.magicVerifyOtp({
        challenge_id: challengeId,
        code: otpCode.trim(),
        magic_token: (magicToken || manualToken).trim() || undefined,
      });
      const token = res.access_token || res.token || "";
      onSessionEstablished({
        ...res,
        access_token: token,
        role: "patient",
        step_up_verified: true,
      });
      onNotify(tr(lang, "OTP verified! Welcome to your encrypted clinic portal.", "驗證碼確認成功！歡迎進入加密病人端。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Invalid OTP", true);
    } finally {
      setAuthBusy(false);
    }
  };

  // ---------------------------------------------------------------------- Onboarding Handler
  const handleOnboard = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!consents.clinical_care) {
      onNotify(tr(lang, "Clinical care consent is required to continue.", "必須同意臨床診症條款方可繼續。"), true);
      return;
    }
    setOnboardBusy(true);
    try {
      const res = await acuteApi.patientOnboard({
        name_en: nameEn.trim(),
        name_zh: nameZh.trim() || undefined,
        dob,
        sex,
        hkid: hkid.trim(),
        preferred_lang: lang,
        g6pd_deficient: g6pdDeficient,
        weight_kg: weightKg ? Number(weightKg) : null,
        allergies: allergiesText
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        current_meds: currentMedsText
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        consents,
        pics_version: pics?.version || "HK-PICS-v1.0",
      });
      if (res.access_token && session) {
        onSessionEstablished({ ...session, access_token: res.access_token, onboarded: true, name_en: res.patient.name_en });
      }
      await loadPatientState();
      onNotify(tr(lang, "Onboarding complete & PDPO consents recorded in immutable ledger.", "完成登記並已將個人資料同意書寫入不可篡改記錄。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Onboarding failed", true);
    } finally {
      setOnboardBusy(false);
    }
  };

  // ---------------------------------------------------------------------- Clinical Flow Handlers
  const handleSubmitIntake = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!symptomsText.trim()) return;
    setIntakeBusy(true);
    try {
      const res = await acuteApi.patientIntake({
        symptoms_text: symptomsText.trim(),
        temperature_c: tempC ? Number(tempC) : null,
        spo2_pct: spo2Pct ? Number(spo2Pct) : null,
        flu_rat_a: fluRatA === "not_done" ? null : fluRatA,
        flu_rat_b: fluRatB === "not_done" ? null : fluRatB,
        covid_rat: covidRat === "not_done" ? null : covidRat,
        lang,
      });
      setIntakeResult(res);
      await loadPatientState();
      onNotify(
        res.red_flag
          ? tr(lang, "RED-FLAG DETECTED: Emergency instructions shown & urgent doctor alert escalated.", "偵測到危急警號：已顯示緊急求醫指引並即時升級通知值班醫生。")
          : tr(lang, "Symptoms submitted for clinician review. You can now book a teleconsult slot.", "症狀已提交供醫生審閱，現可預約遙距診症時段。")
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Intake submission failed", true);
    } finally {
      setIntakeBusy(false);
    }
  };

  const handleBookSlot = async (slot: TeleconsultSlot) => {
    setBookingBusy(true);
    try {
      const latestEncounterId = me?.encounters?.[0]?.id;
      await acuteApi.patientBookTeleconsult({
        encounter_id: latestEncounterId,
        practitioner_id: slot.practitioner_id,
        slot_at: slot.slot_at,
      });
      await loadPatientState();
      onNotify(
        tr(
          lang,
          `Teleconsult booked with ${slot.practitioner_name} (${slot.mchk_reg_no}).`,
          `已預約 ${slot.practitioner_name_zh || slot.practitioner_name}（${slot.mchk_reg_no}）遙距診症。`
        )
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Teleconsult booking failed", true);
    } finally {
      setBookingBusy(false);
    }
  };

  const handleBookFluVax = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setBookingBusy(true);
    try {
      await acuteApi.patientFluVaxBook({
        cvx_code: vaxCvx,
        partner_clinic: vaxClinic,
        slot_at: new Date(vaxSlot).toISOString(),
      });
      await loadPatientState();
      onNotify(tr(lang, "Seasonal flu vaccination booked at partner clinic.", "已成功預約合作診所季節性流感疫苗接種。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Flu vaccination booking failed", true);
    } finally {
      setBookingBusy(false);
    }
  };

  const handleDownloadCert = async (certId: string) => {
    setDocBusy(certId);
    try {
      const tokRes = await acuteApi.patientCertDownloadToken(certId);
      const url = tokRes.download_url || tokRes.url || `/api/v1/patient/certificates/${certId}/download?token=${encodeURIComponent(tokRes.token)}`;
      await downloadVaultPdf(url, `HK_Medical_Certificate_${certId}.pdf`);
      onNotify(tr(lang, "Signed sick-leave certificate decrypted from Vault and downloaded.", "已從加密保險庫解密並下載醫生簽署病假證明書 PDF。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Certificate download failed", true);
    } finally {
      setDocBusy(null);
    }
  };

  const handleChooseFulfilment = async (rxId: string, mode: "delivery" | "pickup") => {
    setDocBusy(rxId);
    try {
      await acuteApi.patientPrescriptionFulfilment(rxId, {
        mode,
        address: mode === "delivery" ? deliveryAddress : undefined,
        pharmacy: mode === "pickup" ? "Central GP Dispensary (Queen's Road Central)" : undefined,
      });
      await loadPatientState();
      onNotify(
        tr(
          lang,
          mode === "delivery" ? "Courier delivery dispatched to partner pharmacy." : "Clinic pharmacy pickup selected.",
          mode === "delivery" ? "已安排合作藥房速遞送藥。" : "已選擇合作藥房自取藥物。"
        )
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Fulfilment update failed", true);
    } finally {
      setDocBusy(null);
    }
  };

  const handleFollowup48h = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setMsgBusy(true);
    try {
      const res = await acuteApi.patientFollowup48h({
        encounter_id: me?.encounters?.[0]?.id,
        status: followupStatus,
        fever_resolved: feverResolved,
        new_red_flags: newRedFlags.trim() || undefined,
        notes: followupNotes.trim() || undefined,
      });
      await loadPatientState();
      onNotify(
        res.escalated
          ? tr(lang, "48h check-in flagged deterioration — escalated to clinician queue.", "48 小時跟進顯示症狀惡化，已即時轉交醫生審核隊列。")
          : tr(lang, "48-hour check-in recorded. Wishing you a smooth recovery!", "48 小時康復跟進已記錄，祝您早日康復！")
      );
      setFollowupNotes("");
      setNewRedFlags("");
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "48h check-in failed", true);
    } finally {
      setMsgBusy(false);
    }
  };

  const handleSendMessage = async (requestHuman = false) => {
    const body = requestHuman
      ? tr(lang, "I would like to speak directly with a human clinician please.", "我希望直接與真人醫生聯絡跟進。")
      : msgText.trim();
    if (!body) return;
    setMsgBusy(true);
    try {
      await acuteApi.patientSendMessage({
        body_text: body,
        encounter_id: me?.encounters?.[0]?.id,
        request_human: requestHuman,
      });
      setMsgText("");
      await loadPatientState();
      onNotify(
        requestHuman
          ? tr(lang, "Human clinician request sent in secure in-app thread.", "已透過加密院內對話要求真人醫生跟進。")
          : tr(lang, "Secure message sent.", "已發送加密訊息。")
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Message failed", true);
    } finally {
      setMsgBusy(false);
    }
  };

  const handleExportFhir = async () => {
    setPrivacyBusy(true);
    try {
      const bundle = await acuteApi.patientExportFhir();
      setFhirBundle(bundle);
      onNotify(tr(lang, "HL7 FHIR R4 Bundle generated (HK PDPO DPP6 DAR).", "已生成 HL7 FHIR R4 個人醫療資料包（香港 PDPO 查閱資料要求）。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "FHIR export failed", true);
    } finally {
      setPrivacyBusy(false);
    }
  };

  const handleDownloadFhirJson = () => {
    if (!fhirBundle) return;
    const blob = new Blob([JSON.stringify(fhirBundle, null, 2)], { type: "application/fhir+json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `patient-fhir-r4-bundle.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };

  const handleDcrSubmit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!dcrValue.trim() || !dcrReason.trim()) return;
    setPrivacyBusy(true);
    try {
      await acuteApi.patientDataCorrection({
        field: dcrField,
        requested_value: dcrValue.trim(),
        reason: dcrReason.trim(),
      });
      setDcrValue("");
      setDcrReason("");
      await loadPatientState();
      onNotify(tr(lang, "Data Correction Request (PDPO DPP6) recorded.", "資料更正要求（PDPO 第 6 原則）已提交並記錄。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Data correction failed", true);
    } finally {
      setPrivacyBusy(false);
    }
  };

  const handleWithdrawConsent = async (scope: string) => {
    setPrivacyBusy(true);
    try {
      await acuteApi.patientWithdrawConsent({ scope, reason: "Patient withdrew consent via PWA" });
      await loadPatientState();
      onNotify(tr(lang, `Consent withdrawn for ${scope} (append-only ledger updated).`, `已撤回 ${scope} 同意授權（已寫入不可篡改記錄）。`));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Consent withdrawal failed", true);
    } finally {
      setPrivacyBusy(false);
    }
  };

  const handleDeleteData = async () => {
    setPrivacyBusy(true);
    try {
      const res = await acuteApi.patientDeleteData({ reason: "Patient requested account/data deletion via PWA" });
      setDeletionNotice(res);
      await loadPatientState();
      onNotify(tr(lang, "Deletion request processed with statutory medical retention notice.", "已處理刪除要求並顯示法定醫療記錄保存聲明。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Deletion request failed", true);
    } finally {
      setPrivacyBusy(false);
    }
  };

  // ========================================================================= RENDER STEP 1: STEP-UP GATE
  if (!isPatientStepUp) {
    return (
      <div className="ac-container-narrow" data-testid="surface-patient-auth">
        <div className="ac-pwa-header">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" }}>
            <span className="ac-badge" style={{ background: "rgba(255,255,255,0.16)", color: "#fff", borderColor: "rgba(255,255,255,0.3)" }}>
              <Lock size={12} /> {tr(lang, "HK PDPO Step-Up Verification", "香港 PDPO 雙重身分驗證")}
            </span>
            <span style={{ fontSize: "11.5px", opacity: 0.9 }}>asia-east2 (HK)</span>
          </div>
          <h2 style={{ marginTop: "0.6rem" }}>
            {tr(lang, "Secure Patient Clinic Portal", "加密病人遙距診症入口")}
          </h2>
          <p>
            {tr(
              lang,
              "Clinical intake, doctor certificates, and e-scripts are locked behind single-use POST magic-link redemption and 6-digit OTP verification.",
              "所有臨床問診、病假證明及電子處方均受一次性 POST 連結兌換及 6 位數 OTP 驗證保護。"
            )}
          </p>
        </div>

        <div className="ac-card">
          {!challengeId ? (
            <>
              <div className="ac-card-head">
                <div>
                  <h3 className="ac-card-title">
                    <Shield size={17} color="var(--ac-teal)" />
                    {tr(lang, "Step 1: Redeem Single-Use Magic Link", "步驟一：兌換一次性安全連結")}
                  </h3>
                  <p className="ac-card-sub">
                    {tr(
                      lang,
                      "Viewing this page (GET) never burns your token. Click below (POST) to issue your 6-digit OTP.",
                      "瀏覽本頁（GET）不會消耗連結。請按下按鈕（POST）以領取 6 位數驗證碼。"
                    )}
                  </p>
                </div>
              </div>

              {preview && (
                <div className="ac-alert info" style={{ marginBottom: "0.85rem" }} data-testid="magic-preview-card">
                  <CheckCircle2 size={16} />
                  <div>
                    <strong>{preview.clinic_name || "Central GP Clinic"}</strong>
                    {preview.campaign_code ? ` • ${preview.campaign_code}` : ""}
                    <div style={{ fontSize: "12px", marginTop: "0.15rem" }}>
                      {tr(lang, "Phone:", "電話：")} <span className="ac-mono">{preview.phone_masked || "****"}</span> •{" "}
                      {preview.redeemed
                        ? tr(lang, "Already redeemed", "已兌換")
                        : tr(lang, "Valid & ready to redeem", "有效，準備兌換")}
                    </div>
                  </div>
                </div>
              )}

              <div className="ac-field">
                <label htmlFor="magic-token-input">{tr(lang, "Magic Link Token", "一次性安全連結權杖")}</label>
                <input
                  id="magic-token-input"
                  className="ac-input ac-mono"
                  data-testid="magic-token-input"
                  value={manualToken}
                  onChange={(e) => setManualToken(e.target.value)}
                  placeholder={tr(lang, "Paste token or scan QR in WhatsApp Doorbell", "貼上權杖或從 WhatsApp 門鈴掃描二維碼")}
                />
              </div>

              <div className="ac-btn-row">
                <button
                  type="button"
                  className="ac-btn primary"
                  data-testid="magic-redeem-btn"
                  disabled={authBusy || !manualToken.trim()}
                  onClick={() => void handleRedeemMagic()}
                >
                  <Lock size={15} />
                  <span>
                    {authBusy
                      ? tr(lang, "Redeeming…", "兌換中…")
                      : tr(lang, "Confirm & Send 6-Digit OTP", "確認並發送 6 位數驗證碼")}
                  </span>
                </button>
                <button
                  type="button"
                  className="ac-btn"
                  onClick={() => onGoToWhatsApp()}
                >
                  {tr(lang, "← Back to WhatsApp Doorbell", "← 返回 WhatsApp 門鈴")}
                </button>
              </div>
            </>
          ) : (
            <div data-testid="otp-step-screen">
              <div className="ac-card-head">
                <div>
                  <h3 className="ac-card-title">
                    <UserCheck size={17} color="var(--ac-teal)" />
                    {tr(lang, "Step 2: Enter 6-Digit Verification Code", "步驟二：輸入 6 位數安全驗證碼")}
                  </h3>
                  <p className="ac-card-sub">
                    {tr(
                      lang,
                      "Your 6-digit OTP expires in 5 minutes (max 3 attempts).",
                      "您的 6 位數驗證碼將於 5 分鐘內失效（最多嘗試 3 次）。"
                    )}
                  </p>
                </div>
              </div>

              {demoOtpHint && (
                <div className="ac-alert warn" style={{ marginBottom: "0.85rem" }} data-testid="otp-demo-hint">
                  <ShieldAlert size={16} />
                  <div>
                    <strong>{tr(lang, "Staging Demo OTP:", "測試環境驗證碼：")}</strong>{" "}
                    <span className="ac-mono" style={{ fontSize: "15px", fontWeight: 800 }}>
                      {demoOtpHint}
                    </span>
                    <div style={{ fontSize: "11.5px" }}>
                      {tr(lang, "(Delivered via WhatsApp/SMS in production; hidden when DEMO_MODE=false)", "（正式環境經 SMS/WhatsApp 發送，非示範模式不會顯示）")}
                    </div>
                  </div>
                </div>
              )}

              <div className="ac-field">
                <label htmlFor="otp-code-input">{tr(lang, "6-Digit OTP Code", "6 位數驗證碼")}</label>
                <input
                  id="otp-code-input"
                  className="ac-input ac-mono"
                  data-testid="otp-code-input"
                  style={{ fontSize: "1.25rem", letterSpacing: "0.25em", textAlign: "center" }}
                  maxLength={6}
                  value={otpCode}
                  onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ""))}
                  placeholder="123456"
                />
              </div>

              <div className="ac-btn-row">
                <button
                  type="button"
                  className="ac-btn primary"
                  data-testid="otp-verify-btn"
                  disabled={authBusy || otpCode.trim().length < 6}
                  onClick={() => void handleVerifyOtp()}
                >
                  <CheckCircle2 size={15} />
                  <span>
                    {authBusy
                      ? tr(lang, "Verifying…", "驗證中…")
                      : tr(lang, "Verify OTP & Open Clinical Portal", "驗證並進入病人端")}
                  </span>
                </button>
                <button type="button" className="ac-btn" onClick={() => setChallengeId(null)}>
                  {tr(lang, "Back", "返回")}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ========================================================================= RENDER STEP 2: ONBOARDING & PDPO PICS
  if (loadingMe && !me) {
    return (
      <div className="ac-container-narrow">
        <div className="ac-card">{tr(lang, "Loading encrypted patient portal…", "正在載入加密病人端…")}</div>
      </div>
    );
  }

  if (!me?.patient || me.onboarded === false) {
    const scopes = pics?.scopes?.length ? pics.scopes : DEFAULT_CONSENT_SCOPES;
    return (
      <div className="ac-container-narrow" data-testid="surface-patient-onboard">
        <div className="ac-pwa-header">
          <span className="ac-badge" style={{ background: "rgba(255,255,255,0.16)", color: "#fff", borderColor: "rgba(255,255,255,0.3)" }}>
            <CheckCircle2 size={12} /> {tr(lang, "OTP Verified • HK PDPO Onboarding", "已完成 OTP 驗證 • 病人登記")}
          </span>
          <h2 style={{ marginTop: "0.5rem" }}>
            {tr(lang, "Patient Identity & HK PDPO Consent", "病人身分核實及個人資料收集聲明")}
          </h2>
          <p>
            {tr(
              lang,
              "Two-identifier clinical verification and granular unbundled consent under Hong Kong PDPO (Cap. 486).",
              "根據香港《個人資料（私隱）條例》（第 486 章）提供雙重身分核實及分項同意授權。"
            )}
          </p>
        </div>

        <form className="ac-card" onSubmit={(e) => void handleOnboard(e)}>
          <h3 className="ac-card-title" style={{ marginBottom: "0.75rem" }}>
            {tr(lang, "1. Patient 2-ID Verification", "1. 病人雙重身分資料")}
          </h3>
          <div className="ac-grid-2">
            <div className="ac-field">
              <label htmlFor="ob-name-en">{tr(lang, "Full Name (English, as on HKID)", "英文全名（與香港身份證相同）")}</label>
              <input id="ob-name-en" className="ac-input" data-testid="onboard-name-en" required value={nameEn} onChange={(e) => setNameEn(e.target.value)} />
            </div>
            <div className="ac-field">
              <label htmlFor="ob-name-zh">{tr(lang, "Chinese Name (Optional)", "中文姓名（選填）")}</label>
              <input id="ob-name-zh" className="ac-input" data-testid="onboard-name-zh" value={nameZh} onChange={(e) => setNameZh(e.target.value)} />
            </div>
          </div>

          <div className="ac-grid-3">
            <div className="ac-field">
              <label htmlFor="ob-dob">{tr(lang, "Date of Birth", "出生日期")}</label>
              <input id="ob-dob" type="date" className="ac-input" data-testid="onboard-dob" required value={dob} onChange={(e) => setDob(e.target.value)} />
            </div>
            <div className="ac-field">
              <label htmlFor="ob-sex">{tr(lang, "Sex", "性別")}</label>
              <select id="ob-sex" className="ac-select" data-testid="onboard-sex" value={sex} onChange={(e) => setSex(e.target.value)}>
                <option value="M">{tr(lang, "Male", "男")}</option>
                <option value="F">{tr(lang, "Female", "女")}</option>
                <option value="U">{tr(lang, "Unspecified", "不詳")}</option>
              </select>
            </div>
            <div className="ac-field">
              <label htmlFor="ob-hkid">{tr(lang, "HKID (Stored Masked)", "香港身份證（加密遮蔽保存）")}</label>
              <input id="ob-hkid" className="ac-input ac-mono" data-testid="onboard-hkid" required value={hkid} onChange={(e) => setHkid(e.target.value)} placeholder="A123456(7)" />
            </div>
          </div>

          <div className="ac-grid-2">
            <div className="ac-field">
              <label htmlFor="ob-allergies">{tr(lang, "Known Drug Allergies (comma-separated)", "已知藥物過敏（以逗號分隔）")}</label>
              <input id="ob-allergies" className="ac-input" data-testid="onboard-allergies" value={allergiesText} onChange={(e) => setAllergiesText(e.target.value)} placeholder="e.g. Penicillin, NSAIDs" />
            </div>
            <div className="ac-field">
              <label htmlFor="ob-meds">{tr(lang, "Current Medications (comma-separated)", "現正服用藥物（以逗號分隔）")}</label>
              <input id="ob-meds" className="ac-input" data-testid="onboard-meds" value={currentMedsText} onChange={(e) => setCurrentMedsText(e.target.value)} placeholder="e.g. Warfarin 3mg" />
            </div>
          </div>

          <div className="ac-grid-2" style={{ alignItems: "center" }}>
            <div className="ac-field">
              <label htmlFor="ob-weight">{tr(lang, "Weight (kg, optional)", "體重（公斤，選填）")}</label>
              <input id="ob-weight" type="number" className="ac-input" value={weightKg} onChange={(e) => setWeightKg(e.target.value)} />
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: 600, fontSize: "13px", cursor: "pointer" }}>
              <input
                type="checkbox"
                data-testid="onboard-g6pd"
                checked={g6pdDeficient}
                onChange={(e) => setG6pdDeficient(e.target.checked)}
              />
              <span>{tr(lang, "I have G6PD Deficiency (蠶豆症)", "本人患有蠶豆症（G6PD 缺乏症）")}</span>
            </label>
          </div>

          <hr className="ac-divider" />

          <h3 className="ac-card-title" style={{ marginBottom: "0.45rem" }}>
            {tr(lang, "2. HK PDPO PICS & AI Disclosure", "2. 個人資料收集聲明（PICS）及 AI 使用披露")}
          </h3>
          <div className="ac-alert info" style={{ marginBottom: "0.85rem" }} data-testid="pics-ai-disclosure">
            <Shield size={16} />
            <div style={{ fontSize: "12.5px" }}>
              <div>
                <strong>{tr(lang, pics?.title_en || "Personal Information Collection Statement (HK-PICS-v1.0)", pics?.title_zh || "個人資料收集聲明（HK-PICS-v1.0）")}</strong>
              </div>
              <div style={{ marginTop: "0.2rem" }}>
                {tr(
                  lang,
                  pics?.ai_disclosure_en ||
                    "AI assists with bilingual intake structuring and draft clinical notes only. A Hong Kong registered medical practitioner (MCHK) independently reviews and signs every diagnosis, prescription, and sick-leave certificate. Employers NEVER see individual clinical records.",
                  pics?.ai_disclosure_zh ||
                    "AI 僅協助整理雙語症狀及預備病歷草稿。所有診斷、藥物處方及病假證明均由香港醫務委員會註冊醫生獨立審核及簽署。僱主絕不會獲得個別員工的醫療資料。"
                )}
              </div>
              <div style={{ marginTop: "0.25rem", fontSize: "11.5px", opacity: 0.85 }}>
                DPO: <span className="ac-mono">{pics?.dpo_contact || "dpo@tmrw-clinic.hk"}</span>
              </div>
            </div>
          </div>

          {scopes.map((sc) => (
            <label key={sc.scope} className="ac-consent-item">
              <input
                type="checkbox"
                data-testid={`consent-${sc.scope}`}
                checked={Boolean(consents[sc.scope])}
                onChange={(e) => setConsents((prev) => ({ ...prev, [sc.scope]: e.target.checked }))}
              />
              <div>
                <div style={{ fontWeight: 600, fontSize: "13px" }}>
                  {tr(lang, sc.label_en, sc.label_zh)}
                </div>
                <div style={{ fontSize: "12px", color: "var(--ac-ink-3)" }}>
                  {tr(lang, sc.description_en, sc.description_zh)}
                </div>
              </div>
            </label>
          ))}

          <button
            type="submit"
            className="ac-btn primary"
            style={{ width: "100%", marginTop: "0.5rem", padding: "0.7rem" }}
            data-testid="onboard-submit-btn"
            disabled={onboardBusy}
          >
            <CheckCircle2 size={16} />
            <span>
              {onboardBusy
                ? tr(lang, "Saving Profile & Consents…", "正在儲存資料與同意書…")
                : tr(lang, "Complete Verification & Enter Clinic Portal", "完成核實並進入診所服務")}
            </span>
          </button>
        </form>
      </div>
    );
  }

  // ========================================================================= RENDER STEP 3: TABBED PATIENT PORTAL
  const patient = me.patient;
  const activeConsents = new Map<string, boolean>();
  for (const c of me.consents || []) {
    activeConsents.set(c.scope, c.granted);
  }

  return (
    <div className="ac-container-narrow" style={{ maxWidth: 720 }} data-testid="surface-patient-portal">
      {/* Patient Header Banner */}
      <div className="ac-pwa-header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.6rem", flexWrap: "wrap" }}>
          <div>
            <h2>
              {patient.name_en} {patient.name_zh ? <span lang="zh-Hant">{patient.name_zh}</span> : null}
            </h2>
            <p className="ac-mono" style={{ fontSize: "12px" }}>
              HKID {patient.hkid_masked} • DOB {patient.dob} • {patient.phone_masked}
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap" }}>
            {patient.g6pd_deficient && (
              <span className="ac-badge warn">G6PD Deficient (蠶豆症)</span>
            )}
            {patient.campaign_code && (
              <span className="ac-badge" style={{ background: "rgba(255,255,255,0.18)", color: "#fff", borderColor: "rgba(255,255,255,0.3)" }}>
                {patient.corporate_name || patient.campaign_code}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Navigation Tabs */}
      <nav className="ac-tabs" aria-label="Patient Portal Sections">
        <button type="button" className={`ac-tab${tab === "intake" ? " active" : ""}`} data-testid="pt-tab-intake" onClick={() => setTab("intake")}>
          <HeartPulse size={14} /> {tr(lang, "Symptom Intake", "症狀問診")}
        </button>
        <button type="button" className={`ac-tab${tab === "booking" ? " active" : ""}`} data-testid="pt-tab-booking" onClick={() => setTab("booking")}>
          <Calendar size={14} /> {tr(lang, "Teleconsult & Flu Vax", "預約診症與疫苗")}
        </button>
        <button type="button" className={`ac-tab${tab === "documents" ? " active" : ""}`} data-testid="pt-tab-documents" onClick={() => setTab("documents")}>
          <FileText size={14} /> {tr(lang, "Docs & e-Scripts", "病假紙與處方")} ({(me.certificates?.length || 0) + (me.prescriptions?.length || 0)})
        </button>
        <button type="button" className={`ac-tab${tab === "messages" ? " active" : ""}`} data-testid="pt-tab-messages" onClick={() => setTab("messages")}>
          <MessageSquare size={14} /> {tr(lang, "48h Check-In & Chat", "48小時跟進與訊息")}
        </button>
        <button type="button" className={`ac-tab${tab === "privacy" ? " active" : ""}`} data-testid="pt-tab-privacy" onClick={() => setTab("privacy")}>
          <Shield size={14} /> {tr(lang, "Privacy & Referrals", "私隱與推薦")}
        </button>
      </nav>

      {/* TAB 1: SYMPTOM INTAKE & TRIAGE */}
      {tab === "intake" && (
        <section data-testid="pt-panel-intake">
          {/* Fixed Non-LLM Red-Flag Crisis Banner */}
          {intakeResult?.red_flag && (
            <div className="ac-crisis-banner" role="alert" data-testid="crisis-banner">
              <h3>
                <AlertTriangle size={20} />
                {tr(
                  lang,
                  intakeResult.crisis_banner?.title_en || "URGENT MEDICAL / SAFETY ALERT — SEEK IMMEDIATE HELP",
                  intakeResult.crisis_banner?.title_zh || "緊急醫療／安全警號 — 請立即尋求協助"
                )}
              </h3>
              <p style={{ margin: 0, fontSize: "13.5px", lineHeight: 1.55 }}>
                {tr(
                  lang,
                  intakeResult.crisis_banner?.body_en ||
                    "Your symptoms include a high-risk red flag that CANNOT wait for routine teleconsultation. Please call 999 or attend the nearest Hong Kong Hospital Authority A&E immediately. Our duty clinician has also been alerted urgently.",
                  intakeResult.crisis_banner?.body_zh ||
                    "您的症狀包含高風險危急警號，不宜等候一般遙距診症。請立即致電 999 或前往最近的香港醫院管理局急症室求醫。本診所值班醫生亦已收到緊急通知。"
                )}
              </p>
              <div className="ac-crisis-numbers">
                <a className="ac-crisis-chip" href="tel:999">
                  <PhoneCall size={14} /> {tr(lang, "Emergency: Call 999", "緊急求助：致電 999")}
                </a>
                <span className="ac-crisis-chip">
                  {tr(lang, "Nearest HA A&E Department", "就近醫管局急症室 (A&E)")}
                </span>
                <a className="ac-crisis-chip" href="tel:23892222">
                  <PhoneCall size={14} /> {tr(lang, "Samaritans HK (24h): 2389 2222", "香港撒瑪利亞防止自殺會：2389 2222")}
                </a>
              </div>
            </div>
          )}

          {intakeResult && !intakeResult.red_flag && (
            <div className="ac-alert ok" style={{ marginBottom: "0.85rem" }} data-testid="intake-success-alert">
              <CheckCircle2 size={16} />
              <div>
                <strong>{tr(lang, "Intake received & queued for licensed clinician review.", "已收到症狀資料並排入醫生審核隊列。")}</strong>
                <div style={{ fontSize: "12px", marginTop: "0.15rem" }}>
                  {intakeResult.ai_disabled_notice
                    ? intakeResult.ai_disabled_notice
                    : tr(
                        lang,
                        "No diagnosis or prescription is issued autonomously. Please book a teleconsult slot next.",
                        "系統不會自動作出診斷或處方。請前往「預約診症與疫苗」選擇診症時段。"
                      )}
                </div>
              </div>
            </div>
          )}

          <form className="ac-card" onSubmit={(e) => void handleSubmitIntake(e)}>
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">{tr(lang, "Flu-Season Symptom Intake (EN / 廣東話)", "流感症狀問診（英文／廣東話）")}</h3>
                <p className="ac-card-sub">
                  {tr(
                    lang,
                    "Describe your symptoms in English or Cantonese. Deterministic safety screens run before any AI structuring.",
                    "請以英文或廣東話描述症狀。系統會先執行硬性危急篩查，再交由醫生審閱。"
                  )}
                </p>
              </div>
            </div>

            <div style={{ marginBottom: "0.75rem" }}>
              <div style={{ fontSize: "11.5px", fontWeight: 600, color: "var(--ac-ink-3)", marginBottom: "0.35rem" }}>
                {tr(lang, "Quick Demo Symptom Presets:", "快速示範症狀範例：")}
              </div>
              <div className="ac-pills">
                <button
                  type="button"
                  className="ac-pill-btn"
                  data-testid="pill-normal-flu-en"
                  onClick={() => {
                    setSymptomsText("Fever 38.4C since yesterday evening, dry cough, sore throat, and body aches. Flu A rapid antigen test positive this morning.");
                    setTempC("38.4");
                    setSpo2Pct("98");
                    setFluRatA("positive");
                  }}
                >
                  {tr(lang, "Normal Flu (EN)", "一般流感（英文）")}
                </button>
                <button
                  type="button"
                  className="ac-pill-btn"
                  data-testid="pill-normal-flu-zh"
                  onClick={() => {
                    setSymptomsText("尋晚開始發燒38.5度，喉嚨痛、乾咳、流鼻水同全身肌肉痠痛，今朝甲型流感快測陽性。");
                    setTempC("38.5");
                    setSpo2Pct("98");
                    setFluRatA("positive");
                  }}
                >
                  {tr(lang, "Normal Flu (廣東話)", "一般流感（廣東話）")}
                </button>
                <button
                  type="button"
                  className="ac-pill-btn crit"
                  data-testid="pill-redflag-emergency"
                  onClick={() => {
                    setSymptomsText("Severe chest pain and shortness of breath since 2 hours ago, feeling dizzy with bluish lips.");
                    setTempC("39.2");
                    setSpo2Pct("89");
                    setFluRatA("positive");
                  }}
                >
                  {tr(lang, "Red-Flag Emergency (Chest Pain/SpO2)", "危急警號（胸痛／氣促）")}
                </button>
                <button
                  type="button"
                  className="ac-pill-btn crit"
                  data-testid="pill-redflag-crisis"
                  onClick={() => {
                    setSymptomsText("I cannot cope anymore and have suicidal thoughts tonight.");
                    setTempC("36.8");
                    setSpo2Pct("99");
                  }}
                >
                  {tr(lang, "Crisis / Samaritans Alert", "情緒危機警號")}
                </button>
              </div>
            </div>

            <div className="ac-field">
              <label htmlFor="intake-symptoms">{tr(lang, "Symptoms & Onset", "主要症狀及發病時間")}</label>
              <textarea
                id="intake-symptoms"
                className="ac-textarea"
                data-testid="intake-symptoms-input"
                required
                value={symptomsText}
                onChange={(e) => setSymptomsText(e.target.value)}
                placeholder={tr(lang, "e.g. Fever since yesterday, cough, sore throat, myalgia…", "例如：尋日開始發燒、咳、喉嚨痛、周身骨痛…")}
              />
            </div>

            <div className="ac-grid-3">
              <div className="ac-field">
                <label htmlFor="intake-temp">{tr(lang, "Temp (°C, LOINC 8310-5)", "體溫 (°C)")}</label>
                <input id="intake-temp" type="number" step="0.1" className="ac-input" data-testid="intake-temp-input" value={tempC} onChange={(e) => setTempC(e.target.value)} />
              </div>
              <div className="ac-field">
                <label htmlFor="intake-spo2">{tr(lang, "SpO2 (%, LOINC 2708-6)", "血氧飽和度 (%)")}</label>
                <input id="intake-spo2" type="number" className="ac-input" data-testid="intake-spo2-input" value={spo2Pct} onChange={(e) => setSpo2Pct(e.target.value)} />
              </div>
              <div className="ac-field">
                <label htmlFor="intake-flua">{tr(lang, "Flu A RAT", "甲型流感快測")}</label>
                <select id="intake-flua" className="ac-select" data-testid="intake-flua-select" value={fluRatA} onChange={(e) => setFluRatA(e.target.value)}>
                  <option value="not_done">{tr(lang, "Not done", "未測試")}</option>
                  <option value="positive">{tr(lang, "Positive (+)", "陽性 (+)")}</option>
                  <option value="negative">{tr(lang, "Negative (-)", "陰性 (-)")}</option>
                </select>
              </div>
            </div>

            <div className="ac-grid-2">
              <div className="ac-field">
                <label htmlFor="intake-flub">{tr(lang, "Flu B RAT", "乙型流感快測")}</label>
                <select id="intake-flub" className="ac-select" value={fluRatB} onChange={(e) => setFluRatB(e.target.value)}>
                  <option value="not_done">{tr(lang, "Not done", "未測試")}</option>
                  <option value="positive">{tr(lang, "Positive (+)", "陽性 (+)")}</option>
                  <option value="negative">{tr(lang, "Negative (-)", "陰性 (-)")}</option>
                </select>
              </div>
              <div className="ac-field">
                <label htmlFor="intake-covid">{tr(lang, "COVID-19 RAT", "新冠快速測試")}</label>
                <select id="intake-covid" className="ac-select" value={covidRat} onChange={(e) => setCovidRat(e.target.value)}>
                  <option value="not_done">{tr(lang, "Not done", "未測試")}</option>
                  <option value="positive">{tr(lang, "Positive (+)", "陽性 (+)")}</option>
                  <option value="negative">{tr(lang, "Negative (-)", "陰性 (-)")}</option>
                </select>
              </div>
            </div>

            <button
              type="submit"
              className="ac-btn primary"
              style={{ width: "100%", padding: "0.65rem" }}
              data-testid="intake-submit-btn"
              disabled={intakeBusy || !symptomsText.trim()}
            >
              <Send size={15} />
              <span>
                {intakeBusy
                  ? tr(lang, "Running Safety Triage & Submitting…", "正在執行安全分流及提交…")
                  : tr(lang, "Submit Symptoms for Doctor Review", "提交症狀予醫生審閱")}
              </span>
            </button>
          </form>

          {me.encounters?.length > 0 && (
            <div className="ac-card">
              <h3 className="ac-card-title" style={{ marginBottom: "0.6rem" }}>
                {tr(lang, "Your Recent Encounters", "近期診症記錄")}
              </h3>
              {me.encounters.map((enc) => (
                <div key={enc.id} style={{ padding: "0.55rem 0", borderBottom: "1px solid #edf2f5" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem", alignItems: "center" }}>
                    <strong style={{ fontSize: "13px" }}>{enc.chief_complaint_text.slice(0, 90) || enc.encounter_type}</strong>
                    <span className={`ac-badge ${enc.status === "red_flag_escalated" ? "crit" : enc.status === "completed" ? "ok" : "teal"}`}>
                      {enc.status}
                    </span>
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--ac-ink-3)", marginTop: "0.2rem" }}>
                    {enc.icd10_code ? `Diagnosis: ${enc.icd10_code} / ${enc.icpc2_code || ""} • ` : ""}
                    {enc.scheduled_at ? `Slot: ${new Date(enc.scheduled_at).toLocaleString()} • ` : ""}
                    {new Date(enc.created_at).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* TAB 2: TELECONSULT & FLU VACCINATION */}
      {tab === "booking" && (
        <section data-testid="pt-panel-booking">
          <div className="ac-card">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <Calendar size={16} color="var(--ac-teal)" />
                  {tr(lang, "Book Teleconsult with HK Registered Doctor", "預約香港註冊西醫遙距診症")}
                </h3>
                <p className="ac-card-sub">
                  {tr(lang, "Real-time doctor roster availability. Covered by your corporate flu benefit if eligible.", "即時顯示註冊醫生應診時段。合資格員工可享企業流感計劃保障。")}
                </p>
              </div>
            </div>

            {slots.length === 0 ? (
              <p style={{ color: "var(--ac-ink-3)", fontSize: "13px" }}>
                {tr(lang, "No slots returned yet. Ask a clinician to publish availability or refresh.", "暫無可預約時段。")}
              </p>
            ) : (
              <div style={{ display: "grid", gap: "0.55rem" }}>
                {slots.map((s, idx) => (
                  <div
                    key={`${s.practitioner_id}-${s.slot_at}-${idx}`}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: "0.75rem",
                      padding: "0.7rem 0.85rem",
                      border: "1px solid var(--ac-border)",
                      borderRadius: "8px",
                      background: "var(--ac-surface-muted)",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 700, fontSize: "13.5px" }}>
                        {s.practitioner_name} {s.practitioner_name_zh ? <span lang="zh-Hant">{s.practitioner_name_zh}</span> : null}
                      </div>
                      <div style={{ fontSize: "12px", color: "var(--ac-ink-3)" }}>
                        <span className="ac-mono">MCHK {s.mchk_reg_no}</span> • {s.clinic_name || "Central GP Clinic"} •{" "}
                        <strong>{new Date(s.slot_at).toLocaleString()}</strong>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="ac-btn primary sm"
                      data-testid="book-slot-btn"
                      disabled={bookingBusy}
                      onClick={() => void handleBookSlot(s)}
                    >
                      {tr(lang, "Book Slot", "立即預約")}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <form className="ac-card" onSubmit={(e) => void handleBookFluVax(e)}>
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <Syringe size={16} color="var(--ac-teal)" />
                  {tr(lang, "Seasonal Flu Vaccination (Partner Clinics, CVX-Coded)", "預約季節性流感疫苗接種（合作診所）")}
                </h3>
                <p className="ac-card-sub">
                  {tr(lang, "FHIR Immunization resource coded with official CVX seasonal influenza vaccine codes.", "採用國際標準 CVX 疫苗編碼並支援醫健通記錄。")}
                </p>
              </div>
            </div>

            <div className="ac-field">
              <label htmlFor="vax-cvx">{tr(lang, "Vaccine Formulation (CVX)", "疫苗種類（CVX 編碼）")}</label>
              <select id="vax-cvx" className="ac-select" data-testid="vax-cvx-select" value={vaxCvx} onChange={(e) => setVaxCvx(e.target.value)}>
                {CVX_VACCINE_OPTIONS.map((v) => (
                  <option key={v.code} value={v.code}>
                    CVX {v.code} — {tr(lang, v.display, v.display_zh)}
                  </option>
                ))}
              </select>
            </div>

            <div className="ac-grid-2">
              <div className="ac-field">
                <label htmlFor="vax-clinic">{tr(lang, "Partner Clinic Location", "合作診所地點")}</label>
                <select id="vax-clinic" className="ac-select" data-testid="vax-clinic-select" value={vaxClinic} onChange={(e) => setVaxClinic(e.target.value)}>
                  {PARTNER_CLINICS.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
              <div className="ac-field">
                <label htmlFor="vax-slot">{tr(lang, "Preferred Date & Time", "預約日期及時間")}</label>
                <input id="vax-slot" type="datetime-local" className="ac-input" data-testid="vax-slot-input" value={vaxSlot} onChange={(e) => setVaxSlot(e.target.value)} />
              </div>
            </div>

            <button type="submit" className="ac-btn primary" data-testid="book-fluvax-btn" disabled={bookingBusy}>
              <Syringe size={15} />
              <span>{tr(lang, "Book Flu Vaccination", "預約季節性流感疫苗")}</span>
            </button>

            {me.immunizations?.length > 0 && (
              <div style={{ marginTop: "1rem" }}>
                <h4 style={{ margin: "0 0 0.45rem", fontSize: "13px" }}>{tr(lang, "Booked Flu Vaccinations", "已預約之流感疫苗")}</h4>
                {me.immunizations.map((imm) => (
                  <div key={imm.id} className="ac-alert ok" style={{ marginBottom: "0.45rem" }} data-testid="booked-fluvax-item">
                    <CheckCircle2 size={15} />
                    <div>
                      <strong>{imm.vaccine_display}</strong> (CVX {imm.cvx_code})
                      <div style={{ fontSize: "12px" }}>
                        {imm.partner_clinic} • {new Date(imm.slot_at).toLocaleString()} • Ref: <span className="ac-mono">{imm.booking_ref}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </form>
        </section>
      )}

      {/* TAB 3: DOCUMENTS & E-SCRIPTS */}
      {tab === "documents" && (
        <section data-testid="pt-panel-documents">
          <div className="ac-card">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <FileText size={16} color="var(--ac-teal)" />
                  {tr(lang, "Doctor-Signed Sick-Leave Certificates (AES-256-GCM Vault)", "醫生簽署病假證明書（加密保險庫）")}
                </h3>
                <p className="ac-card-sub">
                  {tr(lang, "Zero public URLs. Clicking Download mints a short-lived signed Vault token.", "不設公開網址。點擊下載將即時簽發限時加密保險庫權杖。")}
                </p>
              </div>
            </div>

            {(!me.certificates || me.certificates.length === 0) ? (
              <p style={{ color: "var(--ac-ink-3)", fontSize: "13px" }}>
                {tr(lang, "No signed medical certificates yet. Certificates appear here immediately after a doctor signs in Clinician Console.", "暫未有醫生簽署病假證明書。醫生審核簽發後將即時顯示於此。")}
              </p>
            ) : (
              me.certificates.map((cert) => (
                <div
                  key={cert.id}
                  style={{
                    padding: "0.85rem",
                    border: "1px solid var(--ac-border)",
                    borderRadius: "8px",
                    marginBottom: "0.65rem",
                    background: "var(--ac-surface-ivory)",
                  }}
                  data-testid="patient-cert-card"
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.6rem", flexWrap: "wrap" }}>
                    <div>
                      <div style={{ fontWeight: 700 }}>
                        {tr(lang, `Medical Sick-Leave Certificate (${cert.days_off} day${cert.days_off > 1 ? "s" : ""})`, `醫生病假證明書（${cert.days_off} 天）`)}
                      </div>
                      <div style={{ fontSize: "12.5px", color: "var(--ac-ink-2)", marginTop: "0.2rem" }}>
                        {cert.start_date} → {cert.end_date} • <span className="ac-mono">ICD-10 {cert.diagnosis_icd10} / ICPC-2 {cert.diagnosis_icpc2}</span>
                      </div>
                      <div style={{ fontSize: "11.5px", color: "var(--ac-ink-3)", marginTop: "0.2rem" }}>
                        Signed by MCHK <span className="ac-mono">{cert.mchk_reg_no}</span> • SHA-256{" "}
                        <span className="ac-mono">{cert.verification_hash.slice(0, 16)}…</span>
                      </div>
                    </div>
                    <button
                      type="button"
                      className="ac-btn primary sm"
                      data-testid="cert-download-btn"
                      disabled={docBusy === cert.id}
                      onClick={() => void handleDownloadCert(cert.id)}
                    >
                      <Download size={14} />
                      <span>{docBusy === cert.id ? tr(lang, "Decrypting…", "解密中…") : tr(lang, "Download PDF", "下載加密 PDF")}</span>
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="ac-card">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <Pill size={16} color="var(--ac-teal)" />
                  {tr(lang, "Doctor-Signed e-Scripts & Pharmacy Fulfilment", "醫生簽署電子處方及配藥追蹤")}
                </h3>
                <p className="ac-card-sub">
                  {tr(lang, "Choose same-day Hong Kong courier delivery or clinic dispensary pickup.", "選擇即日速遞送藥或親臨合作藥房自取。")}
                </p>
              </div>
            </div>

            <div className="ac-field">
              <label htmlFor="rx-address">{tr(lang, "Courier Delivery Address (Hong Kong)", "香港速遞送藥地址")}</label>
              <input id="rx-address" className="ac-input" value={deliveryAddress} onChange={(e) => setDeliveryAddress(e.target.value)} />
            </div>

            {(!me.prescriptions || me.prescriptions.length === 0) ? (
              <p style={{ color: "var(--ac-ink-3)", fontSize: "13px" }}>
                {tr(lang, "No signed prescriptions yet.", "暫未有醫生簽署電子處方。")}
              </p>
            ) : (
              me.prescriptions.map((rx) => (
                <div
                  key={rx.id}
                  style={{
                    padding: "0.85rem",
                    border: "1px solid var(--ac-border)",
                    borderRadius: "8px",
                    marginBottom: "0.65rem",
                  }}
                  data-testid="patient-rx-card"
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
                    <div>
                      <strong>e-Script #{rx.id}</strong>{" "}
                      <span className="ac-mono" style={{ fontSize: "12px", color: "var(--ac-ink-3)" }}>MCHK {rx.mchk_reg_no}</span>
                    </div>
                    <span className="ac-badge teal" data-testid="rx-fulfilment-status">
                      <Truck size={12} /> {rx.fulfilment_mode} • {rx.fulfilment_status}
                    </span>
                  </div>

                  <ul style={{ margin: "0.5rem 0", paddingLeft: "1.15rem", fontSize: "13px" }}>
                    {(rx.items || []).map((it, i) => (
                      <li key={i}>
                        <strong>{it.display || it.drug}</strong> — {it.dose_mg}mg {it.frequency} × {it.days}d{" "}
                        {it.rxnorm ? <span className="ac-mono" style={{ fontSize: "11px", color: "var(--ac-ink-3)" }}>(RxNorm {it.rxnorm})</span> : null}
                      </li>
                    ))}
                  </ul>

                  <div className="ac-btn-row" style={{ marginTop: "0.55rem" }}>
                    <button
                      type="button"
                      className="ac-btn primary sm"
                      data-testid="rx-choose-delivery-btn"
                      disabled={docBusy === rx.id}
                      onClick={() => void handleChooseFulfilment(rx.id, "delivery")}
                    >
                      <Truck size={13} />
                      <span>{tr(lang, "Request Courier Delivery", "安排速遞送藥")}</span>
                    </button>
                    <button
                      type="button"
                      className="ac-btn sm"
                      data-testid="rx-choose-pickup-btn"
                      disabled={docBusy === rx.id}
                      onClick={() => void handleChooseFulfilment(rx.id, "pickup")}
                    >
                      <span>{tr(lang, "Clinic Pharmacy Pickup", "藥房自取")}</span>
                    </button>
                  </div>

                  {rx.tracking_events && rx.tracking_events.length > 0 && (
                    <div style={{ marginTop: "0.6rem", fontSize: "12px", color: "var(--ac-ink-2)" }}>
                      <strong>{tr(lang, "Live Fulfilment Tracker:", "配藥狀態追蹤：")}</strong>
                      <ul style={{ margin: "0.25rem 0 0", paddingLeft: "1.1rem" }}>
                        {rx.tracking_events.map((ev, idx) => (
                          <li key={idx}>
                            <span className="ac-mono">{ev.status}</span> — {ev.detail || ev.note || ""} ({new Date(ev.at).toLocaleTimeString()})
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </section>
      )}

      {/* TAB 4: 48H CHECK-IN & SECURE MESSAGES */}
      {tab === "messages" && (
        <section data-testid="pt-panel-messages">
          <form className="ac-card" onSubmit={(e) => void handleFollowup48h(e)}>
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">{tr(lang, "48-Hour Recovery Check-In", "48 小時康復進度跟進")}</h3>
                <p className="ac-card-sub">
                  {tr(lang, "Reporting worsening symptoms or new red flags automatically escalates to a duty doctor.", "如匯報症狀惡化或出現新危急警號，系統會自動通知值班醫生。")}
                </p>
              </div>
            </div>

            <div className="ac-grid-2">
              <div className="ac-field">
                <label htmlFor="f48-status">{tr(lang, "How are you feeling?", "目前康復情況")}</label>
                <select
                  id="f48-status"
                  className="ac-select"
                  data-testid="followup-status-select"
                  value={followupStatus}
                  onChange={(e) => setFollowupStatus(e.target.value as "improving" | "same" | "worse")}
                >
                  <option value="improving">{tr(lang, "Improving", "正在好轉")}</option>
                  <option value="same">{tr(lang, "About the same", "沒有明顯變化")}</option>
                  <option value="worse">{tr(lang, "Worse / Not improving", "惡化／未見好轉")}</option>
                </select>
              </div>
              <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: 600, fontSize: "13px" }}>
                <input type="checkbox" checked={feverResolved} onChange={(e) => setFeverResolved(e.target.checked)} />
                <span>{tr(lang, "Fever has resolved (< 37.5°C)", "已退燒（低於 37.5°C）")}</span>
              </label>
            </div>

            <div className="ac-field">
              <label htmlFor="f48-redflags">{tr(lang, "Any new worrying symptoms? (optional)", "有否出現任何新不適或危急症狀？（選填）")}</label>
              <input id="f48-redflags" className="ac-input" data-testid="followup-redflags-input" value={newRedFlags} onChange={(e) => setNewRedFlags(e.target.value)} placeholder="e.g. chest tightness, shortness of breath…" />
            </div>

            <div className="ac-field">
              <label htmlFor="f48-notes">{tr(lang, "Additional Notes for Doctor", "給醫生的補充備註")}</label>
              <input id="f48-notes" className="ac-input" value={followupNotes} onChange={(e) => setFollowupNotes(e.target.value)} />
            </div>

            <button type="submit" className="ac-btn primary" data-testid="followup-48h-submit-btn" disabled={msgBusy}>
              <CheckCircle2 size={15} />
              <span>{tr(lang, "Submit 48h Check-In", "提交 48 小時跟進")}</span>
            </button>
          </form>

          <div className="ac-card">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <Lock size={15} color="var(--ac-teal)" />
                  {tr(lang, "Encrypted In-App Clinical Thread (Never WhatsApp)", "院內加密臨床對話（非 WhatsApp）")}
                </h3>
              </div>
              <button
                type="button"
                className="ac-btn warn sm"
                data-testid="reach-human-btn"
                disabled={msgBusy}
                onClick={() => void handleSendMessage(true)}
              >
                <PhoneCall size={13} />
                <span>{tr(lang, "Reach a Human Clinician", "聯絡真人醫生")}</span>
              </button>
            </div>

            <div className="ac-msg-thread" data-testid="secure-msg-thread">
              {(!me.messages || me.messages.length === 0) ? (
                <div style={{ color: "var(--ac-ink-3)", fontSize: "12.5px", textAlign: "center", padding: "1rem" }}>
                  {tr(lang, "No messages yet. Send a question or tap 'Reach a Human Clinician'.", "暫無訊息。可發送查詢或按「聯絡真人醫生」。")}
                </div>
              ) : (
                me.messages.map((m) => (
                  <div key={m.id} className={`ac-msg ${m.direction}`}>
                    <div>{m.body_text}</div>
                    <div className="ac-msg-meta">
                      {m.sender_role} • {new Date(m.created_at).toLocaleTimeString()}
                    </div>
                  </div>
                ))
              )}
            </div>

            <div style={{ display: "flex", gap: "0.5rem" }}>
              <input
                className="ac-input"
                data-testid="secure-msg-input"
                value={msgText}
                onChange={(e) => setMsgText(e.target.value)}
                placeholder={tr(lang, "Type a secure message to the clinic team…", "輸入加密訊息予診所醫療團隊…")}
              />
              <button
                type="button"
                className="ac-btn primary"
                data-testid="secure-msg-send-btn"
                disabled={msgBusy || !msgText.trim()}
                onClick={() => void handleSendMessage(false)}
              >
                <Send size={14} />
              </button>
            </div>
          </div>
        </section>
      )}

      {/* TAB 5: PRIVACY (PDPO DPP6) & COLLEAGUE REFERRAL QR */}
      {tab === "privacy" && (
        <section data-testid="pt-panel-privacy">
          {/* Colleague Referral QR (Story 10 — MCHK Factual) */}
          <div className="ac-card">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <QrCode size={16} color="var(--ac-teal)" />
                  {tr(lang, "Share Clinic Access with a Colleague (MCHK Compliant)", "與同事分享診所資訊（符合醫委會守則）")}
                </h3>
                <p className="ac-card-sub">
                  {tr(lang, "Factual clinic access QR only — no discounts, vouchers, or inducements.", "僅提供事實性診所入口二維碼，絕無折扣或招攬優惠。")}
                </p>
              </div>
              <span className="ac-badge teal ac-mono" data-testid="patient-referral-code">{patient.referral_code}</span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "1.1rem", flexWrap: "wrap" }}>
              {referralQr && (
                <img src={referralQr} alt="Colleague referral QR" style={{ width: 136, height: 136, border: "1px solid var(--ac-border)", borderRadius: "8px", padding: "4px", background: "#fff" }} />
              )}
              <div style={{ flex: 1, minWidth: 220 }}>
                <p style={{ fontSize: "12.5px", color: "var(--ac-ink-2)", marginTop: 0 }}>
                  {tr(
                    lang,
                    "Colleagues under your company flu benefit can scan this QR code to open the WhatsApp Doorbell.",
                    "同公司合資格同事可掃描此二維碼進入 WhatsApp 安全入口。"
                  )}
                </p>
                <button
                  type="button"
                  className="ac-btn sm"
                  data-testid="test-referral-wa-btn"
                  onClick={() => onGoToWhatsApp(patient.campaign_code || "ACME-FLU-2026", patient.referral_code)}
                >
                  {tr(lang, "Test Colleague QR in WhatsApp Doorbell →", "於 WhatsApp 門鈴測試推薦碼 →")}
                </button>
              </div>
            </div>
          </div>

          {/* PDPO DPP6: DAR HL7 FHIR R4 Export */}
          <div className="ac-card">
            <div className="ac-card-head">
              <div>
                <h3 className="ac-card-title">
                  <Download size={16} color="var(--ac-teal)" />
                  {tr(lang, "PDPO DPP6 — Data Access Request (HL7 FHIR R4 Bundle)", "PDPO 第 6 原則 — 查閱資料要求（HL7 FHIR R4）")}
                </h3>
                <p className="ac-card-sub">
                  {tr(lang, "Export your complete structured clinical record, consents, observations, certificates, and audit log.", "匯出完整的結構化醫療記錄、同意書、化驗結果、病假紙及存取審計日誌。")}
                </p>
              </div>
            </div>
            <div className="ac-btn-row">
              <button type="button" className="ac-btn primary" data-testid="pdpo-export-fhir-btn" disabled={privacyBusy} onClick={() => void handleExportFhir()}>
                <FileText size={15} />
                <span>{tr(lang, "Generate HL7 FHIR R4 Export", "生成 HL7 FHIR R4 資料包")}</span>
              </button>
              {fhirBundle && (
                <button type="button" className="ac-btn" data-testid="pdpo-download-fhir-btn" onClick={handleDownloadFhirJson}>
                  <Download size={15} />
                  <span>{tr(lang, "Download .json", "下載 .json")}</span>
                </button>
              )}
            </div>
            {fhirBundle && (
              <pre className="ac-code-pre" style={{ marginTop: "0.75rem" }} data-testid="fhir-bundle-preview">
                {JSON.stringify(fhirBundle, null, 2).slice(0, 3500)}
                {JSON.stringify(fhirBundle, null, 2).length > 3500 ? "\n… (truncated in preview; click Download .json for full bundle)" : ""}
              </pre>
            )}
          </div>

          {/* PDPO DPP6: Data Correction Request (DCR) */}
          <form className="ac-card" onSubmit={(e) => void handleDcrSubmit(e)}>
            <h3 className="ac-card-title" style={{ marginBottom: "0.6rem" }}>
              {tr(lang, "PDPO DPP6 — Data Correction Request (DCR)", "PDPO 第 6 原則 — 資料更正要求（DCR）")}
            </h3>
            <div className="ac-grid-2">
              <div className="ac-field">
                <label htmlFor="dcr-field">{tr(lang, "Field to Correct", "需更正欄位")}</label>
                <select id="dcr-field" className="ac-select" value={dcrField} onChange={(e) => setDcrField(e.target.value)}>
                  <option value="allergies">{tr(lang, "Drug Allergies", "藥物過敏記錄")}</option>
                  <option value="name_en">{tr(lang, "English Name", "英文姓名")}</option>
                  <option value="dob">{tr(lang, "Date of Birth", "出生日期")}</option>
                  <option value="current_meds">{tr(lang, "Current Medications", "現用藥物")}</option>
                </select>
              </div>
              <div className="ac-field">
                <label htmlFor="dcr-value">{tr(lang, "Requested Correction", "更正內容")}</label>
                <input id="dcr-value" className="ac-input" data-testid="pdpo-dcr-value" required value={dcrValue} onChange={(e) => setDcrValue(e.target.value)} placeholder="e.g. Add Sulfa drug allergy" />
              </div>
            </div>
            <div className="ac-field">
              <label htmlFor="dcr-reason">{tr(lang, "Reason / Supporting Detail", "更正原因")}</label>
              <input id="dcr-reason" className="ac-input" data-testid="pdpo-dcr-reason" required value={dcrReason} onChange={(e) => setDcrReason(e.target.value)} placeholder="e.g. Confirmed by allergist report" />
            </div>
            <button type="submit" className="ac-btn" data-testid="pdpo-dcr-submit-btn" disabled={privacyBusy}>
              {tr(lang, "Submit Data Correction Request", "提交資料更正要求")}
            </button>
          </form>

          {/* Granular Consent Ledger & Withdrawal + Statutory Deletion Hold */}
          <div className="ac-card">
            <h3 className="ac-card-title" style={{ marginBottom: "0.6rem" }}>
              {tr(lang, "Granular Consent Ledger & Deletion Rights", "分項同意授權記錄及刪除權利")}
            </h3>
            {DEFAULT_CONSENT_SCOPES.map((sc) => {
              const granted = activeConsents.get(sc.scope) ?? false;
              return (
                <div
                  key={sc.scope}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: "0.75rem",
                    padding: "0.5rem 0",
                    borderBottom: "1px solid #edf2f5",
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 600, fontSize: "13px" }}>{tr(lang, sc.label_en, sc.label_zh)}</div>
                    <span className={`ac-badge ${granted ? "ok" : "warn"}`} style={{ marginTop: "0.2rem" }}>
                      {granted ? tr(lang, "Granted", "已授權") : tr(lang, "Withdrawn / Not Granted", "已撤回／未授權")}
                    </span>
                  </div>
                  {granted && sc.scope !== "clinical_care" && (
                    <button
                      type="button"
                      className="ac-btn sm"
                      data-testid={`pdpo-withdraw-btn-${sc.scope}`}
                      disabled={privacyBusy}
                      onClick={() => void handleWithdrawConsent(sc.scope)}
                    >
                      {tr(lang, "Withdraw", "撤回同意")}
                    </button>
                  )}
                </div>
              );
            })}

            <hr className="ac-divider" />

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "0.75rem", flexWrap: "wrap" }}>
              <div>
                <strong style={{ color: "var(--ac-crit)" }}>{tr(lang, "Request Account & Data Deletion", "要求刪除帳戶及個人資料")}</strong>
                <div style={{ fontSize: "12px", color: "var(--ac-ink-3)" }}>
                  {tr(
                    lang,
                    "Non-clinical preferences are purged immediately; statutory HK clinical records are placed under restricted retention hold.",
                    "非臨床偏好將即時清除；香港法定醫療記錄將轉入受限保存狀態。"
                  )}
                </div>
              </div>
              <button type="button" className="ac-btn danger sm" data-testid="pdpo-delete-btn" disabled={privacyBusy} onClick={() => void handleDeleteData()}>
                {tr(lang, "Request Data Deletion", "提交刪除資料要求")}
              </button>
            </div>

            {(deletionNotice || patient.status === "deleted_statutory_hold") && (
              <div className="ac-alert warn" style={{ marginTop: "0.75rem" }} data-testid="statutory-hold-notice">
                <ShieldAlert size={16} />
                <div>
                  <strong>{tr(lang, "Hong Kong Statutory Clinical Record Retention Hold", "香港法定醫療記錄保存鎖定通知")}</strong>
                  <div style={{ fontSize: "12.5px", marginTop: "0.2rem" }}>
                    {deletionNotice?.notice ||
                      tr(
                        lang,
                        "Your deletion request has been honoured for marketing and non-clinical data. Clinical consultation records, prescriptions, and medical certificates are locked in statutory retention hold under Hong Kong medical record regulations.",
                        "已刪除您的推廣及非臨床資料。根據香港醫療記錄保存指引，您的臨床病歷、處方及病假證明已鎖定於法定保存期內，不可作其他用途。"
                      )}
                  </div>
                  {(deletionNotice?.retention_until || patient.retention_until) && (
                    <div className="ac-mono" style={{ fontSize: "11.5px", marginTop: "0.25rem" }}>
                      Retention until: {deletionNotice?.retention_until || patient.retention_until}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}
