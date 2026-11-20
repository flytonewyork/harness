import { useEffect, useState } from "react";
import { v2Api } from "./api";
import type { StoredSession } from "./api";

interface DemoDockProps {
  enabled: boolean;
  session: StoredSession | null;
  onSwitched: (next: StoredSession, targetPath: string) => void;
  onResetComplete: () => void;
}

const DEMO_SHORTCUTS = [
  { key: "clinician:doc_dr_lam", label: "Dr. Kenneth Lam (M14829)", sub: "Staff · QEH / HTN / Flu", path: "/c" },
  { key: "nurse:nurse_chan", label: "Nurse Rachel Chan (RN)", sub: "Staff · Triage & Prep", path: "/c" },
  { key: "clinic_admin:adm_central_ops", label: "Grace Ho (Clinic Ops)", sub: "Settings · Kill Switch", path: "/c/settings" },
  { key: "patient:pat_ho_htn_drift", label: "HO Ka-Yan (HTN Drift)", sub: "Patient · 365d Companion", path: "/p" },
  { key: "patient:pat_chan_qeh_discharge", label: "CHAN Tai Man (QEH)", sub: "Patient · Discharge", path: "/p" },
  { key: "patient:pat_cheung_elderly_flu", label: "CHEUNG Wing-Kit (70M Flu)", sub: "Patient · Renal Flu", path: "/p" },
  { key: "patient:pat_wong_flu_g6pd", label: "WONG Siu-Mei (Flu+G6PD)", sub: "Patient · Acute Flu", path: "/p" },
];

export function DemoDock({ enabled, session, onSwitched, onResetComplete }: DemoDockProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ".") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);

  if (!enabled) return null;

  const switchTo = async (personaKey: string, path: string) => {
    setBusy(true);
    try {
      const res = await v2Api.demoLoginAs(personaKey);
      const next: StoredSession = {
        token: res.token,
        role: res.role,
        userId: res.subject_id,
        nameEn: res.name_en,
        nameZh: res.name_zh,
        mchkRegNo: res.mchk_reg_no,
      };
      setOpen(false);
      onSwitched(next, path);
    } finally {
      setBusy(false);
    }
  };

  const resetDemo = async () => {
    setBusy(true);
    try {
      await v2Api.demoReset();
      setOpen(false);
      onResetComplete();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="v2-demo-dock" data-testid="demo-dock">
      {open && (
        <div className="v2-demo-menu" data-testid="demo-dock-menu">
          <div className="v2-sec-label">Staging Switcher (⌘.) · Synthetic Only</div>
          {DEMO_SHORTCUTS.map((s) => (
            <button
              key={s.key}
              type="button"
              className="v2-demo-item"
              disabled={busy}
              onClick={() => switchTo(s.key, s.path)}
              data-testid={`switch-${s.key.replace(":", "-")}`}
            >
              <span>{s.label}</span>
              <span className="voice-quote">{s.sub.split(" · ")[0]}</span>
            </button>
          ))}
          <div className="v2-summary-item">
            <button type="button" className="v2-btn-secondary" disabled={busy} onClick={resetDemo} data-testid="demo-reset-btn">
              Reset synthetic clinic data
            </button>
          </div>
        </div>
      )}
      <button type="button" className="v2-demo-pill" onClick={() => setOpen((o) => !o)} data-testid="demo-dock-pill">
        Staging · {session?.nameEn?.split(" ").slice(0, 2).join(" ") || "Demo"} ▾
      </button>
    </div>
  );
}
