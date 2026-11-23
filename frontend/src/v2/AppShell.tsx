import { useEffect, useState } from "react";
import App from "../App";
import AcuteApp from "../acute/AcuteApp";
import { loadSession, saveSession, v2Api } from "./api";
import type { StoredSession } from "./api";
import { DemoDock } from "./DemoDock";
import { EmrHarnessApp } from "./emr/EmrHarnessApp";
import { PatientApp } from "./patient/PatientApp";
import { StaffApp } from "./staff/StaffApp";
import "./tokens.css";

function isAcutePortalPath(path: string): boolean {
  return (
    path === "/wa" ||
    path.startsWith("/wa/") ||
    path === "/corp" ||
    path.startsWith("/corp/") ||
    path === "/a" ||
    path.startsWith("/a/") ||
    path.startsWith("/p/magic/")
  );
}

function extractQrPatientId(path: string): string | null {
  const m = /^\/p\/qr\/([A-Za-z0-9_-]+)/.exec(path);
  return m ? m[1] : null;
}

function getRoute(): { path: string; legacy: boolean; acutePortal: boolean; emrApp: boolean; qrPatientId: string | null } {
  const params = new URLSearchParams(window.location.search);
  const path = window.location.pathname || "/";
  return {
    path,
    legacy: params.get("mode") === "legacy",
    acutePortal: params.get("mode") === "acute" || isAcutePortalPath(path),
    emrApp: path === "/emr" || path.startsWith("/emr/"),
    qrPatientId: extractQrPatientId(path),
  };
}

export default function AppShell() {
  const [route, setRoute] = useState(getRoute);
  const [session, setSession] = useState<StoredSession | null>(() => loadSession());
  const [demoEnabled, setDemoEnabled] = useState(true);
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);

  const navigate = (path: string) => {
    window.history.pushState({}, "", path);
    setRoute(getRoute());
  };

  const autoLoginForPath = async (path: string) => {
    const qrPid = extractQrPatientId(path);
    if (qrPid) {
      const res = await v2Api.qrCheckin(qrPid);
      const next: StoredSession = {
        token: res.token,
        role: "patient",
        userId: res.subject_id,
        nameEn: res.name_en,
        nameZh: res.name_zh,
      };
      saveSession(next);
      setSession(next);
      return;
    }

    const persona =
      path.startsWith("/p")
        ? "patient:pat_ho_htn_drift"
        : path.startsWith("/c/settings")
          ? "clinic_admin:adm_central_ops"
          : "clinician:doc_dr_lam";
    const res = await v2Api.demoLoginAs(persona);
    const next: StoredSession = {
      token: res.token,
      role: res.role,
      userId: res.subject_id,
      nameEn: res.name_en,
      nameZh: res.name_zh,
      mchkRegNo: res.mchk_reg_no,
    };
    saveSession(next);
    setSession(next);
  };

  useEffect(() => {
    const onPop = () => setRoute(getRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const h = await v2Api.health();
        const allowDemo = Boolean(h.runtime_toggles?.demo_controls_enabled ?? h.demo_mode);
        if (!active) return;
        setDemoEnabled(allowDemo);

        const path = window.location.pathname || "/";
        if (path === "/emr" || path.startsWith("/emr/")) {
          // Separate Simulated Clinic EMR + Agent Harness UI App boots directly
          return;
        }

        const qrPid = extractQrPatientId(path);
        if (qrPid && allowDemo) {
          await autoLoginForPath(path);
          return;
        }

        const cur = loadSession();
        const needsPatient = path.startsWith("/p");
        const needsSettings = path.startsWith("/c/settings");
        const roleMismatch =
          !cur ||
          (needsPatient && cur.role !== "patient") ||
          (needsSettings && cur.role !== "clinic_admin") ||
          (!needsPatient && !needsSettings && !["clinician", "nurse"].includes(cur.role));

        if (allowDemo && roleMismatch) {
          await autoLoginForPath(path);
        }
      } catch (e: any) {
        if (active) setBootError(e?.message || "Clinic backend unreachable.");
      } finally {
        if (active) setBooting(false);
      }
    })();
    return () => { active = false; };
  }, []);

  if (route.legacy) {
    return <App />;
  }

  if (route.acutePortal) {
    return <AcuteApp />;
  }

  if (route.emrApp) {
    return <EmrHarnessApp />;
  }

  if (booting) {
    return <div className="v2-staff-shell"><div className="v2-queue-wrap voice-assistant">Connecting to clinic harness…</div></div>;
  }

  if (bootError) {
    return <div className="v2-staff-shell"><div className="v2-queue-wrap v2-inline-fail">{bootError}</div></div>;
  }

  const isPatientRoute = route.path.startsWith("/p") || session?.role === "patient";
  const initialStaffView = route.path.startsWith("/c/settings") ? "settings" : "review";

  return (
    <>
      {session ? (
        isPatientRoute && session.role === "patient" ? (
          <PatientApp key={session.userId} session={session} />
        ) : (
          <StaffApp key={`${session.userId}:${initialStaffView}`} session={session} initialView={initialStaffView} />
        )
      ) : (
        <div className="v2-staff-shell">
          <div className="v2-queue-wrap">
            <h1 className="v2-decision-title">Central &amp; Mid-Levels Family Medical</h1>
            <p className="voice-assistant">Please sign in via your clinic link or staff account.</p>
          </div>
        </div>
      )}

      {/* Hide Staging Dock on QR-scanned mobile views so real phone screens stay pure & native */}
      {!route.qrPatientId && (
        <DemoDock
          enabled={demoEnabled}
          session={session}
          onSwitched={(next, targetPath) => {
            saveSession(next);
            setSession(next);
            navigate(targetPath);
          }}
          onResetComplete={async () => {
            await autoLoginForPath(window.location.pathname || "/c");
            window.location.reload();
          }}
        />
      )}
    </>
  );
}
