import { useEffect, useState } from "react";
import { v2Api } from "../api";
import type { ProposalDetail, StaffQueueItem, StoredSession } from "../api";
import { FeedLine } from "../shared/FeedLine";
import { Sheet } from "../shared/Sheet";
import { ClinicSettings } from "./ClinicSettings";
import { CommandPalette } from "./CommandPalette";
import { ReviewPane } from "./ReviewPane";

interface StaffAppProps {
  session: StoredSession;
  initialView?: "review" | "queue" | "settings";
  onSwitchToOps?: () => void;
}

export function StaffApp({ session, initialView = "review" }: StaffAppProps) {
  const [view, setView] = useState<"review" | "queue" | "settings">(
    session.role === "clinic_admin" ? "settings" : initialView,
  );
  const [items, setItems] = useState<StaffQueueItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProposalDetail | null>(null);
  const [killSwitch, setKillSwitch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [patientSheetOpen, setPatientSheetOpen] = useState(false);
  const [undoState, setUndoState] = useState<{ proposalId: string; patientName: string; secondsLeft: number } | null>(null);
  const [bannerMsg, setBannerMsg] = useState<string | null>(null);

  const loadQueue = async (preferredId?: string) => {
    if (session.role === "clinic_admin") return;
    const res = await v2Api.staffQueue(session.token);
    setItems(res.items || []);
    setKillSwitch(Boolean(res.kill_switch_active));
    const pending = (res.items || []).filter((i) => i.status === "pending_review");
    const target = preferredId || activeId || pending[0]?.id || res.items[0]?.id || null;
    if (target) {
      setActiveId(target);
      const det = await v2Api.proposalDetail(target, session.token);
      setDetail(det);
      setKillSwitch(Boolean(det.kill_switch_active));
    }
  };

  useEffect(() => {
    setView(session.role === "clinic_admin" ? "settings" : initialView);
    loadQueue();
  }, [session.token, session.role]);

  useEffect(() => {
    if (!undoState) return;
    if (undoState.secondsLeft <= 0) {
      setUndoState(null);
      return;
    }
    const timer = setTimeout(
      () => setUndoState((u) => (u ? { ...u, secondsLeft: u.secondsLeft - 1 } : null)),
      1000,
    );
    return () => clearTimeout(timer);
  }, [undoState]);

  const openItem = async (id: string) => {
    setActiveId(id);
    setView("review");
    const det = await v2Api.proposalDetail(id, session.token);
    setDetail(det);
    setKillSwitch(Boolean(det.kill_switch_active));
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
        return;
      }
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "Escape" && !paletteOpen && !patientSheetOpen) setView("queue");
      if (e.key.toLowerCase() === "p" && detail) setPatientSheetOpen((o) => !o);
      if ((e.key.toLowerCase() === "j" || e.key.toLowerCase() === "k") && items.length > 0 && activeId) {
        const idx = items.findIndex((i) => i.id === activeId);
        const nextIdx = e.key.toLowerCase() === "j" ? (idx + 1) % items.length : (idx - 1 + items.length) % items.length;
        openItem(items[nextIdx].id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, activeId, detail, paletteOpen, patientSheetOpen]);

  if (view === "settings") {
    return (
      <ClinicSettings
        token={session.token}
        onBack={() => setView(session.role === "clinic_admin" ? "settings" : "review")}
        onKillSwitchChanged={(enabled) => setKillSwitch(!enabled)}
      />
    );
  }

  const pendingCount = items.filter((i) => i.status === "pending_review").length;
  const siteCount = (detail?.payload?.case_sites || ["Central GP"]).length;
  const unresolvedCount = (detail?.payload?.conflicts || []).filter((c: any) => !c.resolved).length;

  const handleResolveConflict = async (conflictId: string, choice: "1" | "2" | "Q") => {
    if (!detail) return;
    setBusy(true);
    try {
      await v2Api.resolveConflict(detail.id, conflictId, choice, session.token);
      await loadQueue(detail.id);
    } finally {
      setBusy(false);
    }
  };

  const handleSign = async (opts: { reply: string; overrideReason: string; renalDose: "75" | "30"; issueCert: boolean }) => {
    if (!detail) return;
    setBusy(true);
    setBannerMsg(null);
    try {
      const acuteRx = (detail.suggested_rx || [])
        .filter((m) => !m.is_non_acute_order && !m.stopped)
        .map((m) => (String(m.drug || "").toLowerCase() === "oseltamivir" ? { ...m, dose_mg: opts.renalDose === "30" ? 30 : 75 } : m));
      const res = await v2Api.signProposal(
        detail.id,
        {
          decision: "approved",
          review_seconds: 12.0,
          rubber_stamp_confirmed: true,
          patient_reply: opts.reply,
          override_reason: opts.overrideReason || undefined,
          issue_certificate: opts.issueCert,
          cert_days_off: detail.suggested_cert?.days_off || 2,
          rx_items: acuteRx.length > 0 ? acuteRx : undefined,
        },
        session.token,
      );
      setUndoState({
        proposalId: detail.id,
        patientName: detail.patient_name_en,
        secondsLeft: res.dispatch_undo_seconds ?? 10,
      });
      await loadQueue(detail.id);
    } catch (e: any) {
      setBannerMsg(e?.message || "Sign-off blocked.");
    } finally {
      setBusy(false);
    }
  };

  const handleUndo = async () => {
    if (!undoState) return;
    setBusy(true);
    try {
      await v2Api.undoDispatch(undoState.proposalId, session.token);
      const id = undoState.proposalId;
      setUndoState(null);
      await loadQueue(id);
    } finally {
      setBusy(false);
    }
  };

  const handleNurseReady = async (prepNote: string) => {
    if (!detail) return;
    setBusy(true);
    try {
      await v2Api.nurseReady(detail.id, prepNote, session.token);
      await loadQueue(detail.id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="v2-staff-shell" data-testid="staff-workspace">
      <header className="v2-staff-top">
        <div className="v2-staff-top-left">
          <button type="button" className="v2-back-btn" onClick={() => setView(view === "queue" ? "review" : "queue")} data-testid="queue-toggle-btn">
            ‹ {pendingCount} left
          </button>
          {detail && view === "review" && (
            <button type="button" className="v2-text-btn v2-identity-strip" onClick={() => setPatientSheetOpen(true)}>
              <strong>{detail.patient?.name_en}</strong>
              {detail.patient?.name_zh ? ` ${detail.patient.name_zh}` : ""}
              <span className="v2-identity-meta">
                {detail.patient?.sex} · {detail.patient?.allergies?.length ? `Allergy: ${detail.patient.allergies.join(", ")}` : "NKDA"}
                {detail.patient?.g6pd_deficient ? " · G6PD deficient" : ""}
                {` · ${siteCount} site${siteCount > 1 ? "s" : ""}${unresolvedCount ? ` · ${unresolvedCount} conflict` : ""}`}
              </span>
            </button>
          )}
        </div>
        <div className="v2-staff-top-right">
          <span className="voice-assistant">{session.nameEn}</span>
          <button type="button" className="v2-kbd-chip" onClick={() => setPaletteOpen(true)} data-testid="cmd-k-btn">
            ⌘K
          </button>
        </div>
      </header>

      <FeedLine killSwitchActive={killSwitch} />
      {bannerMsg && <div className="v2-inline-fail">{bannerMsg}</div>}

      {view === "queue" ? (
        <div className="v2-queue-wrap" data-testid="staff-queue-list">
          <h1 className="v2-decision-title">Clinic review queue ({pendingCount} open)</h1>
          {items.map((it) => (
            <button key={it.id} type="button" className="v2-queue-row" onClick={() => openItem(it.id)} data-testid={`queue-item-${it.id}`}>
              <div>
                <div className="v2-staff-top-left">
                  <span className={`v2-dot ${it.urgency >= 4 || it.unresolved_conflicts > 0 ? "v2-dot-red" : it.urgency === 3 ? "v2-dot-amber" : "v2-dot-ink"}`} />
                  <strong>{it.patient_name_en}</strong>
                  <span className="voice-assistant">{it.patient_name_zh}</span>
                  {it.nurse_ready && <span className="voice-quote">· Nurse ready</span>}
                </div>
                <div className="voice-assistant">{it.title}</div>
              </div>
              <div className="voice-quote">
                {it.status === "pending_review"
                  ? it.unresolved_conflicts > 0
                    ? `${it.unresolved_conflicts} conflict`
                    : `✓ ${it.checks_summary.passed}/${it.checks_summary.applicable ?? it.checks_summary.total}`
                  : it.status}
              </div>
            </button>
          ))}
        </div>
      ) : detail ? (
        <ReviewPane
          detail={detail}
          busy={busy}
          undoState={undoState}
          onResolveConflict={handleResolveConflict}
          onSign={handleSign}
          onUndo={handleUndo}
          onNurseReady={handleNurseReady}
        />
      ) : (
        <div className="v2-queue-wrap voice-assistant">Loading review workspace…</div>
      )}

      <Sheet open={patientSheetOpen} title={`${detail?.patient?.name_en || "Patient"} · Longitudinal record`} onClose={() => setPatientSheetOpen(false)}>
        {detail?.patient && (
          <div>
            <div className="v2-sec">
              <div className="v2-sec-label">Active medications</div>
              <div className="v2-card-flat">
                {(detail.patient.current_meds || []).length === 0
                  ? "None recorded"
                  : detail.patient.current_meds.map((m, i) => <div key={i} className="v2-summary-item">{m}</div>)}
              </div>
            </div>
            <div className="v2-sec">
              <div className="v2-sec-label">Allergies &amp; safety profile</div>
              <div className="v2-card-flat">
                <div>Allergies: {detail.patient.allergies.length ? detail.patient.allergies.join(", ") : "NKDA"}</div>
                <div>G6PD: {detail.patient.g6pd_deficient ? "Deficient" : "Normal"}</div>
                <div>eGFR: {detail.patient.egfr ?? "Unknown (not on file)"}</div>
              </div>
            </div>
            <div className="v2-sec">
              <div className="v2-sec-label">Recent messages ({detail.messages.length})</div>
              <div className="v2-card-flat">
                {detail.messages.map((m) => (
                  <div key={m.id} className="v2-summary-item">
                    <div className="voice-quote">{m.sender_role}</div>
                    <div className={m.sender_role === "clinician" ? "voice-doctor" : "voice-assistant"}>{m.body_text}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </Sheet>

      <CommandPalette
        open={paletteOpen}
        items={items}
        onClose={() => setPaletteOpen(false)}
        onSelectItem={openItem}
        onGoQueue={() => setView("queue")}
        onOpenPatientSheet={() => setPatientSheetOpen(true)}
      />
    </div>
  );
}
