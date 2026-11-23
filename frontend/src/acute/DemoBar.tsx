import { useEffect, useState } from "react";
import {
  Building2,
  ClipboardList,
  Globe,
  LogOut,
  MessageCircle,
  RotateCcw,
  ShieldCheck,
  Smartphone,
  Stethoscope,
  UserCheck,
} from "lucide-react";
import { acuteApi, tr } from "./api";
import type { AuthSession, DemoPersona, Lang } from "./api";

export type SurfaceId = "wa" | "patient" | "clinician" | "admin" | "corporate" | "legacy";

interface DemoBarProps {
  demoMode: boolean;
  surface: SurfaceId;
  onSelectSurface: (surface: SurfaceId) => void;
  lang: Lang;
  onToggleLang: () => void;
  session: AuthSession | null;
  onSessionChange: (session: AuthSession | null, targetSurface?: SurfaceId) => void;
  onDemoResetDone: () => void;
  onNotify: (msg: string, isError?: boolean) => void;
}

export function DemoBar({
  demoMode,
  surface,
  onSelectSurface,
  lang,
  onToggleLang,
  session,
  onSessionChange,
  onDemoResetDone,
  onNotify,
}: DemoBarProps) {
  const [personas, setPersonas] = useState<DemoPersona[]>([]);
  const [selectedPersona, setSelectedPersona] = useState<string>("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!demoMode) return;
    acuteApi
      .demoPersonas()
      .then((list) => {
        setPersonas(list);
        if (list.length > 0 && !selectedPersona) {
          setSelectedPersona(list[0].key ?? list[0].id);
        }
      })
      .catch(() => {
        setPersonas([]);
      });
  }, [demoMode]);

  if (!demoMode) return null;

  const surfaceForRole = (role: AuthSession["role"]): SurfaceId => {
    switch (role) {
      case "patient":
        return "patient";
      case "clinician":
        return "clinician";
      case "clinic_admin":
        return "admin";
      case "corporate_admin":
        return "corporate";
    }
  };

  const handleLoginPersona = async (personaKey: string) => {
    if (!personaKey) return;
    setBusy(true);
    try {
      const auth = await acuteApi.demoLoginAs(personaKey);
      const token = auth.access_token || auth.token || "";
      const normalized: AuthSession = { ...auth, access_token: token };
      onSessionChange(normalized, surfaceForRole(normalized.role));
      onNotify(
        tr(
          lang,
          `Signed in as ${normalized.name_en || normalized.role} (${normalized.role})`,
          `已登入為 ${normalized.name_zh || normalized.name_en || normalized.role}`
        )
      );
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Persona login failed", true);
    } finally {
      setBusy(false);
    }
  };

  const handleReset = async () => {
    setBusy(true);
    try {
      await acuteApi.demoReset();
      onDemoResetDone();
      onNotify(tr(lang, "Demo database reset to clean Hong Kong flu-season seed.", "示範資料庫已重設至初始狀態。"));
    } catch (e) {
      onNotify(e instanceof Error ? e.message : "Reset failed", true);
    } finally {
      setBusy(false);
    }
  };

  const tabs: Array<{ id: SurfaceId; labelEn: string; labelZh: string; icon: React.ReactNode }> = [
    { id: "wa", labelEn: "WhatsApp Doorbell", labelZh: "WhatsApp 門鈴", icon: <MessageCircle size={14} /> },
    { id: "patient", labelEn: "Patient PWA", labelZh: "病人端 PWA", icon: <Smartphone size={14} /> },
    { id: "clinician", labelEn: "Clinician Console", labelZh: "醫生審核台", icon: <Stethoscope size={14} /> },
    { id: "admin", labelEn: "Clinic Ops Admin", labelZh: "診所營運管理", icon: <ShieldCheck size={14} /> },
    { id: "corporate", labelEn: "Corporate Admin", labelZh: "企業福利後台", icon: <Building2 size={14} /> },
    { id: "legacy", labelEn: "Legacy Follow-Up", labelZh: "舊版覆診清單", icon: <ClipboardList size={14} /> },
  ];

  return (
    <header className="ac-demobar" data-testid="demo-bar">
      <div className="ac-demobar-brand">
        <span>Clinic AI Harness</span>
        <span className="ac-demo-pill">HK Staging Demo</span>
      </div>

      <nav className="ac-demobar-tabs" aria-label="Surface Switcher">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`ac-demobar-tab${surface === t.id ? " active" : ""}`}
            data-testid={`nav-surface-${t.id}`}
            onClick={() => onSelectSurface(t.id)}
          >
            {t.icon}
            <span>{tr(lang, t.labelEn, t.labelZh)}</span>
          </button>
        ))}
      </nav>

      <div className="ac-demobar-right">
        {personas.length > 0 && (
          <>
            <select
              className="ac-demobar-select"
              data-testid="demo-persona-select"
              aria-label="Synthetic Demo Persona"
              value={selectedPersona}
              onChange={(e) => {
                setSelectedPersona(e.target.value);
                void handleLoginPersona(e.target.value);
              }}
              disabled={busy}
            >
              {personas.map((p) => {
                const k = p.key ?? p.id;
                return (
                  <option key={k} value={k}>
                    {tr(lang, p.label, p.label_zh || p.label)} [{p.role}]
                  </option>
                );
              })}
            </select>
            <button
              type="button"
              className="ac-demobar-btn"
              data-testid="demo-login-btn"
              disabled={busy || !selectedPersona}
              onClick={() => void handleLoginPersona(selectedPersona)}
              title={tr(lang, "1-click login as selected synthetic persona", "一鍵登入已選示範帳戶")}
            >
              <UserCheck size={13} />
              <span>{tr(lang, "Switch", "切換")}</span>
            </button>
          </>
        )}

        <button
          type="button"
          className="ac-demobar-btn"
          data-testid="lang-toggle"
          onClick={onToggleLang}
        >
          <Globe size={13} />
          <span>{lang === "en" ? "繁體中文" : "English"}</span>
        </button>

        <button
          type="button"
          className="ac-demobar-btn danger"
          data-testid="demo-reset-btn"
          disabled={busy}
          onClick={() => void handleReset()}
        >
          <RotateCcw size={13} />
          <span>{tr(lang, "Reset Demo Data", "重設示範數據")}</span>
        </button>

        {session && (
          <button
            type="button"
            className="ac-demobar-btn"
            data-testid="demo-signout-btn"
            onClick={() => onSessionChange(null)}
            title={tr(lang, "Sign out", "登出")}
          >
            <LogOut size={13} />
            <span>
              {session.name_en || session.role}
              {session.mchk_reg_no ? ` (${session.mchk_reg_no})` : ""}
            </span>
          </button>
        )}
      </div>
    </header>
  );
}
