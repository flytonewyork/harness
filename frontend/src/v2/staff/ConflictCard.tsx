import type { DoseConflictItem } from "../api";

interface ConflictCardProps {
  conflict: DoseConflictItem;
  isClinician: boolean;
  busy: boolean;
  onFocusQuote: (quote: string) => void;
  onResolve: (conflictId: string, choice: "1" | "2" | "Q") => void;
}

export function ConflictCard({ conflict, isClinician, busy, onFocusQuote, onResolve }: ConflictCardProps) {
  if (conflict.resolved) {
    return (
      <div className="v2-conflict-resolved" data-testid={`conflict-resolved-${conflict.id}`}>
        <span>
          <strong>{conflict.drug}:</strong> {conflict.resolution_summary || `Choice ${conflict.chosen_side}`}
        </span>
        <span className="voice-quote">✓ settled</span>
      </div>
    );
  }

  return (
    <div className="v2-conflict-card" data-testid={`conflict-card-${conflict.id}`}>
      <div className="v2-order-head">
        <strong>{conflict.drug.toUpperCase()}: two doses on file</strong>
        <span className="voice-quote">agent never picks</span>
      </div>

      {conflict.sides.map((side) => (
        <div key={side.key} className="v2-conflict-side">
          <div>
            <div>
              <strong>{side.regimen}</strong>{" "}
              <span className="voice-assistant">
                ({side.site}, {side.date})
              </span>
            </div>
            <button
              type="button"
              className="v2-quote-link"
              onClick={() => onFocusQuote(side.quote)}
            >
              “{side.quote}”
            </button>
          </div>
          {isClinician && (
            <button
              type="button"
              className="v2-btn-secondary"
              disabled={busy}
              onClick={() => onResolve(conflict.id, side.key)}
              data-testid={`resolve-conflict-${side.key}`}
            >
              Use ({side.key})
            </button>
          )}
        </div>
      ))}

      {isClinician && (
        <div className="v2-summary-item">
          <button
            type="button"
            className="v2-text-btn"
            disabled={busy}
            onClick={() => onResolve(conflict.id, "Q")}
            data-testid="resolve-conflict-Q"
          >
            Hold &amp; ask patient which tablets they take (Q)
          </button>
        </div>
      )}
    </div>
  );
}
