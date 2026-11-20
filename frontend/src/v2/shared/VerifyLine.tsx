import type { ChecksSummary, UnifiedCheck } from "../api";

interface VerifyLineProps {
  checks: UnifiedCheck[];
  summary: ChecksSummary;
  open: boolean;
  onToggle: () => void;
}

const GLYPH: Record<UnifiedCheck["status"], string> = {
  pass: "✓",
  warn: "!",
  fail: "✕",
  unknown: "?",
  na: "–",
};

export function VerifyLine({ checks, summary, open, onToggle }: VerifyLineProps) {
  const applicable = checks.filter((c) => c.status !== "na");
  const naChecks = checks.filter((c) => c.status === "na");
  const passed = applicable.filter((c) => c.status === "pass").length;
  const fails = applicable.filter((c) => c.status === "fail");
  const unknowns = applicable.filter((c) => c.status === "unknown");
  const warns = applicable.filter((c) => c.status === "warn");

  let tone = "";
  let headline = `✓ ${passed} checks passed`;
  if (fails.length > 0) {
    tone = "is-fail";
    headline = `✕ ${fails[0].label}`;
  } else if (unknowns.length > 0) {
    tone = "is-unknown";
    headline = `? ${unknowns[0].label}`;
  } else if (warns.length > 0) {
    tone = "is-warn";
    headline = `! ${warns.length} check${warns.length > 1 ? "s" : ""} need attention (${passed}/${applicable.length} passed)`;
  }

  return (
    <div>
      <button
        type="button"
        className={`v2-verify-trigger ${tone}`}
        onClick={onToggle}
        data-testid="verify-line-btn"
        aria-expanded={open}
      >
        <span>{headline}</span>
        <span className="v2-kbd-chip">V</span>
      </button>

      {open && (
        <div className="v2-verify-popover" role="dialog" aria-label="Verification checks" data-testid="verify-popover">
          <div className="v2-sec-label">
            Verification ({passed} of {applicable.length} applicable passed{summary.unknown ? ` · ${summary.unknown} unknown` : ""})
          </div>
          {applicable.map((c) => (
            <div key={c.id} className="v2-summary-item">
              <div className="v2-order-head">
                <span>
                  <strong className="voice-quote">{GLYPH[c.status]}</strong> {c.label}
                </span>
                <span className="voice-quote">{c.status}</span>
              </div>
              <div className="voice-assistant">{c.detail}</div>
            </div>
          ))}
          {naChecks.length > 0 && (
            <div className="v2-summary-item">
              <div className="v2-sec-label">Not applicable</div>
              {naChecks.map((c) => (
                <div key={c.id} className="voice-assistant">
                  – {c.label}: {c.detail}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
