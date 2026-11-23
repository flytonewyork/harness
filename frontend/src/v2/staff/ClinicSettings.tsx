import { useEffect, useState } from "react";
import { v2Api } from "../api";
import { FeedLine } from "../shared/FeedLine";

interface ClinicSettingsProps {
  token: string;
  onBack: () => void;
  onKillSwitchChanged: (aiEnabled: boolean) => void;
}

export function ClinicSettings({ token, onBack, onKillSwitchChanged }: ClinicSettingsProps) {
  const [overview, setOverview] = useState<Record<string, any> | null>(null);
  const [requests, setRequests] = useState<Array<Record<string, any>>>([]);
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = async () => {
    try {
      setErr(null);
      const [ov, h] = await Promise.all([v2Api.adminOverview(token), v2Api.health()]);
      setOverview(ov);
      setHealth(h);
      const dr = await v2Api.adminDataRequests(token).catch(() => ({ requests: [] }));
      setRequests(dr.requests || []);
    } catch (e: any) {
      setErr(e?.message || "Clinic ops role required to modify clinic settings.");
    }
  };

  useEffect(() => {
    load();
  }, [token]);

  const aiEnabled = overview?.tenant?.ai_enabled ?? health?.ai_enabled ?? true;
  const toggles = health?.runtime_toggles || {};

  const toggleKillSwitch = async () => {
    setBusy(true);
    try {
      const next = !aiEnabled;
      await v2Api.adminKillSwitch(next, next ? "Re-enabled by clinic ops" : "Engaged kill switch from /c/settings", token);
      onKillSwitchChanged(next);
      await load();
    } catch (e: any) {
      setErr(e?.message || "Could not toggle AI kill switch.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="v2-staff-shell" data-testid="clinic-settings-page">
      <header className="v2-staff-top">
        <div className="v2-staff-top-left">
          <button type="button" className="v2-back-btn" onClick={onBack}>
            ‹ Back to workspace
          </button>
          <span className="v2-identity-strip">Clinic settings &amp; governance</span>
        </div>
        <span className="voice-quote">{health?.region || "asia-east2"}</span>
      </header>

      <FeedLine killSwitchActive={!aiEnabled} />

      <div className="v2-queue-wrap">
        {err && <div className="v2-inline-fail">{err}</div>}

        <section className="v2-sec">
          <div className="v2-sec-label">AI Drafting Kill Switch</div>
          <div className="v2-card-flat v2-order-head">
            <div>
              <strong>{aiEnabled ? "AI drafting is active (L1 clinician-gated)" : "AI drafting is OFF — manual mode"}</strong>
              <div className="voice-assistant">
                Deterministic 999 red-flag screening, Rx safety rules, and verbatim quote checks stay permanently on.
              </div>
            </div>
            <button
              type="button"
              className={aiEnabled ? "v2-btn-primary" : "v2-btn-secondary"}
              disabled={busy}
              onClick={toggleKillSwitch}
              data-testid="toggle-kill-switch-btn"
            >
              {aiEnabled ? "Turn AI drafting off" : "Re-enable AI drafting"}
            </button>
          </div>
        </section>

        <section className="v2-sec">
          <div className="v2-sec-label">Provisioned HK Infrastructure Toggles (AI Engineering Walkthrough)</div>
          <div className="v2-card-flat">
            <div className="v2-summary-item v2-order-head">
              <span>HARNESS_RUNTIME_MODE</span>
              <span className="voice-quote">{toggles.harness_runtime_mode || "demo"}</span>
            </div>
            <div className="v2-summary-item v2-order-head">
              <span>INFRA_MODE (PG15 FORCE RLS + GCS Vault)</span>
              <span className="voice-quote">{toggles.infra_mode || "lite"}</span>
            </div>
            <div className="v2-summary-item v2-order-head">
              <span>AI_INFERENCE_MODE (HK asia-east2)</span>
              <span className="voice-quote">{toggles.ai_inference_mode || "deterministic_rich"}</span>
            </div>
            <div className="v2-summary-item v2-order-head">
              <span>DISPATCH_UNDO_SECONDS (server-held window)</span>
              <span className="voice-quote">{toggles.dispatch_undo_seconds ?? 10}s</span>
            </div>
          </div>
        </section>

        <section className="v2-sec">
          <div className="v2-sec-label">Clinic Protocols (Versioned YAML)</div>
          <div className="v2-card-flat">
            {(overview?.protocols || []).map((pr: any) => (
              <div key={pr.id} className="v2-summary-item v2-order-head">
                <span>
                  <strong>{pr.protocol_id}</strong> v{pr.version} ({pr.autonomy_level})
                </span>
                <span className="voice-quote">
                  {pr.status} · {(pr.content_hash || "").slice(0, 10)}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="v2-sec">
          <div className="v2-sec-label">HK PDPO Data Correction &amp; Access Requests ({requests.length})</div>
          <div className="v2-card-flat">
            {requests.length === 0 ? (
              <div className="voice-assistant">No open PDPO requests.</div>
            ) : (
              requests.map((r) => (
                <div key={r.id} className="v2-summary-item v2-order-head">
                  <span>
                    <strong>{r.request_type}</strong> · {r.status}
                  </span>
                  {r.status !== "completed" && (
                    <button
                      type="button"
                      className="v2-btn-secondary"
                      onClick={async () => {
                        await v2Api.adminResolveDataReq(r.id, token);
                        await load();
                      }}
                    >
                      Mark resolved
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
