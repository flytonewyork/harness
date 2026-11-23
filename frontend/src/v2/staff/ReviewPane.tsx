import { useEffect, useState } from "react";
import type { DoseConflictItem, ProposalDetail } from "../api";
import { Paper } from "../shared/Paper";
import { Sparkline } from "../shared/Sparkline";
import { VerifyLine } from "../shared/VerifyLine";
import { ConflictCard } from "./ConflictCard";
import { OrderList } from "./OrderList";

interface ReviewPaneProps {
  detail: ProposalDetail;
  busy: boolean;
  undoState: { proposalId: string; patientName: string; secondsLeft: number } | null;
  onResolveConflict: (conflictId: string, choice: "1" | "2" | "Q") => void;
  onSign: (opts: { reply: string; overrideReason: string; renalDose: "75" | "30"; issueCert: boolean }) => void;
  onUndo: () => void;
  onNurseReady: (prepNote: string) => void;
  onOpenCertPdf?: (certId: string) => void;
}

export function ReviewPane({
  detail,
  busy,
  undoState,
  onResolveConflict,
  onSign,
  onUndo,
  onNurseReady,
  onOpenCertPdf,
}: ReviewPaneProps) {
  const [focusedQuote, setFocusedQuote] = useState<string | null>(null);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [reply, setReply] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [renalDose, setRenalDose] = useState<"75" | "30">("75");
  const [issueCert, setIssueCert] = useState(false);
  const [nurseNote, setNurseNote] = useState("");
  const [showNote, setShowNote] = useState(false);

  useEffect(() => {
    setFocusedQuote(null);
    setVerifyOpen(false);
    setOverrideReason("");
    setRenalDose("75");
    setShowNote(false);
    setReply(detail.payload?.patient_reply_draft || "");
    setIssueCert(Boolean(detail.suggested_cert));
    setNurseNote(detail.payload?.nurse_prep_note || "");
  }, [detail.id]);

  const isClinician = detail.viewer_role === "clinician";
  const conflicts: DoseConflictItem[] = detail.payload?.conflicts || [];
  const unresolvedConflicts = conflicts.filter((c) => !c.resolved);
  const summaryPoints: Array<{ text: string; quote?: string }> = detail.payload?.summary_points || [];
  const bpSeries = detail.payload?.bp_series || [];
  const labsContext: string | undefined = detail.payload?.labs_context;
  const checks = detail.checks || [];
  const csum = detail.checks_summary;
  const hasRenalChoice = checks.some((c) => c.id === "renal_unknown_elderly");

  const submitPrimary = () => {
    if (!isClinician) {
      onNurseReady(nurseNote);
      return;
    }
    if (!csum.can_sign || busy) return;
    const effReason =
      overrideReason.trim() ||
      (hasRenalChoice ? (renalDose === "30" ? "Switched Oseltamivir to 30 mg BD renal dose" : "Kept Oseltamivir 75 mg BD (CrCl > 60 assessed)") : "");
    onSign({ reply, overrideReason: effReason, renalDose, issueCert });
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const inInput = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        submitPrimary();
        return;
      }
      if (inInput) return;
      if (e.key.toLowerCase() === "v") setVerifyOpen((o) => !o);
      if (e.key.toLowerCase() === "z" && undoState) onUndo();
      if (isClinician && unresolvedConflicts.length > 0 && !busy) {
        const first = unresolvedConflicts[0];
        if (e.key === "1") onResolveConflict(first.id, "1");
        if (e.key === "2") onResolveConflict(first.id, "2");
        if (e.key.toLowerCase() === "q") onResolveConflict(first.id, "Q");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isClinician, unresolvedConflicts, busy, undoState, reply, overrideReason, renalDose, issueCert, nurseNote, csum.can_sign]);

  const isSigned = detail.status !== "pending_review";

  return (
    <>
      <div className="v2-review-grid">
        <div className="v2-packet-col">
          <h1 className="v2-decision-title" data-testid="decision-title">
            {detail.title}
          </h1>

          {detail.payload?.nurse_ready && (
            <div className="v2-conflict-resolved">
              <span>
                Prepared by {detail.payload.nurse_ready_by || "Nurse"}
                {detail.payload.nurse_prep_note ? ` — ${detail.payload.nurse_prep_note}` : ""}
              </span>
              <span className="voice-quote">ready</span>
            </div>
          )}

          {/* Section 1: What happened */}
          <section className="v2-sec">
            <div className="v2-sec-label">What happened</div>
            <div className="v2-card-flat">
              {summaryPoints.length > 0 ? (
                summaryPoints.map((pt, i) => (
                  <div key={i} className="v2-summary-item">
                    <div>{pt.text}</div>
                    {pt.quote && (
                      <button type="button" className="v2-quote-link" onClick={() => setFocusedQuote(pt.quote || null)}>
                        “{pt.quote}”
                      </button>
                    )}
                  </div>
                ))
              ) : (
                detail.source_quotes.map((sq, i) => (
                  <div key={i} className="v2-summary-item">
                    <div className="voice-assistant">{sq.field || "Evidence"}</div>
                    <button type="button" className="v2-quote-link" onClick={() => setFocusedQuote(sq.quote)}>
                      “{sq.quote}”
                    </button>
                  </div>
                ))
              )}
              {labsContext && <div className="v2-summary-item voice-assistant">Labs: {labsContext}</div>}
            </div>
          </section>

          {bpSeries.length > 1 && (
            <section className="v2-sec">
              <Sparkline points={bpSeries} />
            </section>
          )}

          {conflicts.map((c) => (
            <ConflictCard
              key={c.id}
              conflict={c}
              isClinician={isClinician}
              busy={busy}
              onFocusQuote={setFocusedQuote}
              onResolve={onResolveConflict}
            />
          ))}

          {/* Section 2: What we'll do (Orders — never labeled "Safe") */}
          <OrderList
            orders={detail.suggested_rx || []}
            checks={checks}
            suggestedCert={detail.suggested_cert}
            isClinician={isClinician}
            renalDose={renalDose}
            issueCert={issueCert}
            overrideReason={overrideReason}
            onChangeRenalDose={setRenalDose}
            onToggleCert={setIssueCert}
            onChangeOverrideReason={setOverrideReason}
          />

          {/* Section 3: What we'll tell the patient (Serif = doctor's signed words) */}
          <section className="v2-sec">
            <div className="v2-order-head">
              <div className="v2-sec-label">What we&apos;ll tell the patient</div>
              <button type="button" className="v2-text-btn" onClick={() => setShowNote((v) => !v)}>
                {showNote ? "Hide compiled note" : "View compiled note"}
              </button>
            </div>
            <textarea
              className="v2-reply-box"
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              disabled={!isClinician}
              placeholder="Patient reply draft…"
              data-testid="patient-reply-textarea"
            />
            {showNote && (
              <div className="v2-card-flat voice-assistant">
                <div className="v2-sec-label">Compiled clinical record (derived from signed decisions)</div>
                <div>{detail.payload?.draft_clinical_note || detail.encounter?.clinical_note || "Compiled on sign-off."}</div>
              </div>
            )}
          </section>

          {!isClinician && (
            <section className="v2-sec">
              <div className="v2-sec-label">Nurse preparation note for Dr. Lam</div>
              <input
                className="v2-composer-input"
                value={nurseNote}
                onChange={(e) => setNurseNote(e.target.value)}
                placeholder="Vitals verified, document checked, ready for doctor review…"
                data-testid="nurse-prep-input"
              />
            </section>
          )}
        </div>

        {/* Right Pane: The Source as Paper (Revived from App.tsx) */}
        <div className="v2-paper-col">
          <Paper
            title={detail.kind.replace(/_/g, " ").toUpperCase()}
            subtitle={`${detail.patient?.name_en || ""} · ${detail.patient?.hkid_masked || ""}`}
            text={detail.encounter?.chief_complaint_text || ""}
            quotes={detail.source_quotes || []}
            focusedQuote={focusedQuote}
          />
        </div>
      </div>

      {/* Single Sticky Sign Bar: Verification + Action on ONE line */}
      <div className="v2-sign-bar" data-testid="sign-bar">
        <VerifyLine checks={checks} summary={csum} open={verifyOpen} onToggle={() => setVerifyOpen((o) => !o)} />

        <div className="v2-staff-top-right">
          {detail.issued_certificates && detail.issued_certificates.length > 0 && onOpenCertPdf && (
            <button
              type="button"
              className="v2-btn-secondary"
              onClick={() => onOpenCertPdf(detail.issued_certificates![0].id)}
              data-testid="clinician-cert-pdf-btn"
            >
              MCHK Sick Leave PDF ({detail.issued_certificates[0].days_off}d) ↗
            </button>
          )}
          {undoState ? (
            <button type="button" className="v2-btn-secondary" onClick={onUndo} data-testid="undo-send-btn">
              Sending to {undoState.patientName} in {undoState.secondsLeft}s · Undo Z
            </button>
          ) : isSigned ? (
            <span className="voice-quote">✓ Signed ({detail.status})</span>
          ) : isClinician ? (
            <button
              type="button"
              className="v2-btn-primary"
              disabled={!csum.can_sign || busy}
              onClick={submitPrimary}
              data-testid="sign-and-send-btn"
            >
              <span>{csum.sign_cta}</span>
              {csum.can_sign && <span className="voice-quote">⌘↵</span>}
            </button>
          ) : (
            <button
              type="button"
              className="v2-btn-primary"
              disabled={busy}
              onClick={submitPrimary}
              data-testid="nurse-ready-btn"
            >
              <span>Ready for Dr Lam</span>
              <span className="voice-quote">⌘↵</span>
            </button>
          )}
        </div>
      </div>
    </>
  );
}
