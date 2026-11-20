import { useCallback, useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Globe, ShieldCheck } from "lucide-react";
import LegacyApp from "../App";
import { acuteApi, setAuthToken, tr } from "./api";
import type { AuthSession, HealthResponse, Lang } from "./api";
import { DemoBar, type SurfaceId } from "./DemoBar";
import { WhatsAppDoorbell } from "./WhatsAppDoorbell";
import { PatientPWA } from "./PatientPWA";
import { ClinicianConsole } from "./ClinicianConsole";
import { AdminPortal } from "./AdminPortal";
import "./acute.css";

interface ParsedRoute {
  surface: SurfaceId;
  magicToken: string | null;
  campaignCode?: string;
  referralCode?: string;
}

function parseLocation(): ParsedRoute {
  const path = window.location.pathname.replace(/\/+$/, "") || "/";
  const qs = new URLSearchParams(window.location.search);

  if (qs.get("mode") === "legacy") {
    return { surface: "legacy", magicToken: null };
  }

  const magicMatch = /^\/p\/magic\/([^/]+)$/.exec(path);
  if (magicMatch) {
    return { surface: "patient", magicToken: decodeURIComponent(magicMatch[1]) };
  }

  if (path === "/p" || path.startsWith("/p/")) {
    return { surface: "patient", magicToken: qs.get("token") };
  }
  if (path === "/c" || path.startsWith("/c/")) {
    return { surface: "clinician", magicToken: null };
  }
  if (path === "/a" || path.startsWith("/a/")) {
    return { surface: "admin", magicToken: null };
  }
  if (path === "/corp" || path.startsWith("/corp/")) {
    return { surface: "corporate", magicToken: null };
  }

  // Default `/` and `/wa` open Surface 1: WhatsApp Doorbell
  return {
    surface: "wa",
    magicToken: null,
    campaignCode: qs.get("campaign") || undefined,
    referralCode: qs.get("ref") || undefined,
  };
}

function urlForSurface(surface: SurfaceId, opts?: { magicToken?: string | null; campaign?: string; ref?: string }): string {
  switch (surface) {
    case "legacy":
      return "/?mode=legacy";
    case "wa": {
      const qs = new URLSearchParams();
      if (opts?.campaign) qs.set("campaign", opts.campaign);
      if (opts?.ref) qs.set("ref", opts.ref);
      const s = qs.toString();
      return s ? `/wa?${s}` : "/wa";
    }
    case "patient":
      return opts?.magicToken ? `/p/magic/${encodeURIComponent(opts.magicToken)}` : "/p";
    case "clinician":
      return "/c";
    case "admin":
      return "/a";
    case "corporate":
      return "/corp";
  }
}

const SESSION_STORAGE_KEY = "tmrw_acute_session_v1";
const LANG_STORAGE_KEY = "tmrw_acute_lang_v1";

function loadStoredSession(): AuthSession | null {
  try {
    const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthSession;
    if (parsed?.access_token) {
      setAuthToken(parsed.access_token);
      return parsed;
    }
  } catch {
    // ignore storage errors
  }
  return null;
}

export default function AcuteApp() {
  const initialRoute = parseLocation();
  const [surface, setSurface] = useState<SurfaceId>(initialRoute.surface);
  const [magicToken, setMagicToken] = useState<string | null>(initialRoute.magicToken);
  const [campaignCode, setCampaignCode] = useState<string | undefined>(initialRoute.campaignCode);
  const [referralCode, setReferralCode] = useState<string | undefined>(initialRoute.referralCode);

  const [lang, setLang] = useState<Lang>(() => {
    const saved = window.sessionStorage.getItem(LANG_STORAGE_KEY);
    return saved === "zh" ? "zh" : "en";
  });

  const [session, setSession] = useState<AuthSession | null>(() => loadStoredSession());
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [resetEpoch, setResetEpoch] = useState(0);
  const [toast, setToast] = useState<{ text: string; isError: boolean } | null>(null);

  // Sync auth token immediately on any session change
  const updateSession = useCallback((next: AuthSession | null, targetSurface?: SurfaceId) => {
    setAuthToken(next?.access_token || null);
    setSession(next);
    try {
      if (next) {
        window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(next));
      } else {
        window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
      }
    } catch {
      // ignore
    }
    if (targetSurface) {
      setSurface(targetSurface);
      try {
        window.history.pushState(null, "", urlForSurface(targetSurface));
      } catch {
        // ignore
      }
    }
  }, []);

  const notify = useCallback((text: string, isError = false) => {
    setToast({ text, isError });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(id);
  }, [toast]);

  // Fetch /api/v1/health to check demo_mode & HK residency status
  useEffect(() => {
    acuteApi
      .health()
      .then(setHealth)
      .catch(() => {
        // In static preview or offline mode, keep graceful fallback
        setHealth({ status: "offline", demo_mode: false });
      });
  }, []);

  // Listen for browser back/forward navigation
  useEffect(() => {
    const onPopState = () => {
      const r = parseLocation();
      setSurface(r.surface);
      setMagicToken(r.magicToken);
      setCampaignCode(r.campaignCode);
      setReferralCode(r.referralCode);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigateSurface = useCallback(
    (next: SurfaceId, opts?: { magicToken?: string | null; campaign?: string; ref?: string }) => {
      setSurface(next);
      if (opts?.magicToken !== undefined) setMagicToken(opts.magicToken);
      if (opts?.campaign !== undefined) setCampaignCode(opts.campaign);
      if (opts?.ref !== undefined) setReferralCode(opts.ref);
      try {
        window.history.pushState(null, "", urlForSurface(next, opts));
      } catch {
        // ignore
      }
    },
    []
  );

  const handleToggleLang = () => {
    const next: Lang = lang === "en" ? "zh" : "en";
    setLang(next);
    try {
      window.sessionStorage.setItem(LANG_STORAGE_KEY, next);
    } catch {
      // ignore
    }
  };

  const handleDemoResetDone = () => {
    updateSession(null);
    setMagicToken(null);
    setResetEpoch((n) => n + 1);
  };

  const demoMode = Boolean(health?.demo_mode);

  // Legacy Follow-Up List Route (?mode=legacy) — keeps existing App.tsx 100% intact
  if (surface === "legacy") {
    return (
      <>
        {demoMode && (
          <DemoBar
            demoMode={demoMode}
            surface={surface}
            onSelectSurface={(s) => navigateSurface(s)}
            lang={lang}
            onToggleLang={handleToggleLang}
            session={session}
            onSessionChange={updateSession}
            onDemoResetDone={handleDemoResetDone}
            onNotify={notify}
          />
        )}
        <LegacyApp />
      </>
    );
  }

  return (
    <div className="ac-root" lang={lang === "zh" ? "zh-Hant" : "en"}>
      {/* Staging-only Demo Control Bar (hidden when demo_mode=false) */}
      <DemoBar
        demoMode={demoMode}
        surface={surface}
        onSelectSurface={(s) => navigateSurface(s)}
        lang={lang}
        onToggleLang={handleToggleLang}
        session={session}
        onSessionChange={updateSession}
        onDemoResetDone={handleDemoResetDone}
        onNotify={notify}
      />

      {/* Minimal Production Header when DEMO_MODE=false */}
      {!demoMode && (
        <header
          style={{
            background: "var(--ac-surface)",
            borderBottom: "1px solid var(--ac-border)",
            padding: "0.55rem 1.25rem",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "0.75rem",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", fontWeight: 700, color: "var(--ac-teal)" }}>
            <ShieldCheck size={18} />
            <span>{tr(lang, "Central GP Clinic — Acute Flu Care", "中環普通科診所 — 急性流感遙距診症")}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
            <span className="ac-badge ok ac-mono">{health?.region || "asia-east2"}</span>
            <button type="button" className="ac-btn sm" data-testid="lang-toggle" onClick={handleToggleLang}>
              <Globe size={13} /> {lang === "en" ? "繁體中文" : "English"}
            </button>
          </div>
        </header>
      )}

      {/* Surface 1: WhatsApp Doorbell */}
      {surface === "wa" && (
        <WhatsAppDoorbell
          key={`wa-${resetEpoch}`}
          lang={lang}
          initialCampaignCode={campaignCode}
          initialReferralCode={referralCode}
          onOpenMagicLink={(tok) => {
            // Clear any non-patient session so magic-link step-up gate renders cleanly
            if (session && session.role !== "patient") {
              updateSession(null);
            }
            navigateSurface("patient", { magicToken: tok });
          }}
          onNotify={notify}
        />
      )}

      {/* Surface 2: Patient PWA */}
      {surface === "patient" && (
        <PatientPWA
          key={`pt-${resetEpoch}`}
          lang={lang}
          session={session}
          magicToken={magicToken}
          onSessionEstablished={(s) => updateSession(s)}
          onGoToWhatsApp={(camp, ref) => navigateSurface("wa", { campaign: camp, ref })}
          onNotify={notify}
        />
      )}

      {/* Surface 3: Clinician Console */}
      {surface === "clinician" && (
        <ClinicianConsole
          key={`cl-${resetEpoch}`}
          lang={lang}
          session={session}
          onSessionChange={(s) => updateSession(s)}
          onNotify={notify}
        />
      )}

      {/* Surface 4A: Clinic Ops Admin */}
      {surface === "admin" && (
        <AdminPortal
          key={`adm-${resetEpoch}`}
          mode="clinic_admin"
          lang={lang}
          session={session}
          onSessionChange={(s) => updateSession(s)}
          onOpenCampaignQr={(code) => navigateSurface("wa", { campaign: code })}
          onNotify={notify}
        />
      )}

      {/* Surface 4B: Corporate Benefits Admin */}
      {surface === "corporate" && (
        <AdminPortal
          key={`corp-${resetEpoch}`}
          mode="corporate_admin"
          lang={lang}
          session={session}
          onSessionChange={(s) => updateSession(s)}
          onOpenCampaignQr={(code) => navigateSurface("wa", { campaign: code })}
          onNotify={notify}
        />
      )}

      {/* Global Toast Notification */}
      {toast && (
        <div
          className={`ac-toast${toast.isError ? " error" : ""}`}
          role={toast.isError ? "alert" : "status"}
          data-testid="ac-toast"
          onClick={() => setToast(null)}
        >
          {toast.isError ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
          <span>{toast.text}</span>
        </div>
      )}
    </div>
  );
}
