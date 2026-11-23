import { useEffect, useState } from "react";
import type { StaffQueueItem } from "../api";

interface CommandPaletteProps {
  open: boolean;
  items: StaffQueueItem[];
  onClose: () => void;
  onSelectItem: (id: string) => void;
  onGoQueue: () => void;
  onOpenPatientSheet: () => void;
  onOpenSettings?: () => void;
}

export function CommandPalette({
  open,
  items,
  onClose,
  onSelectItem,
  onGoQueue,
  onOpenPatientSheet,
  onOpenSettings,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  if (!open) return null;

  const q = query.trim().toLowerCase();
  const filteredItems = items.filter(
    (it) =>
      !q ||
      it.patient_name_en.toLowerCase().includes(q) ||
      (it.patient_name_zh || "").includes(q) ||
      it.title.toLowerCase().includes(q),
  );

  return (
    <div className="v2-palette-wrap" onClick={onClose} role="dialog" aria-modal="true" aria-label="Command palette">
      <div className="v2-palette" onClick={(e) => e.stopPropagation()} data-testid="command-palette">
        <div className="v2-card-flat">
          <input
            autoFocus
            className="v2-composer-input"
            placeholder="Jump to patient, item, or action… (deterministic)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "Enter" && filteredItems[0]) {
                onSelectItem(filteredItems[0].id);
                onClose();
              }
            }}
          />
          <div className="v2-summary-item">
            <div className="v2-sec-label">Navigation &amp; shortcuts</div>
            <button type="button" className="v2-demo-item" onClick={() => { onGoQueue(); onClose(); }}>
              <span>All queue items</span>
              <span className="voice-quote">Esc</span>
            </button>
            <button type="button" className="v2-demo-item" onClick={() => { onOpenPatientSheet(); onClose(); }}>
              <span>Patient longitudinal record</span>
              <span className="voice-quote">P</span>
            </button>
            {onOpenSettings && (
              <button type="button" className="v2-demo-item" onClick={() => { onOpenSettings(); onClose(); }}>
                <span>Clinic settings &amp; AI kill switch</span>
                <span className="voice-quote">/c/settings</span>
              </button>
            )}
          </div>

          <div className="v2-summary-item">
            <div className="v2-sec-label">Queue ({filteredItems.length})</div>
            {filteredItems.slice(0, 6).map((it) => (
              <button
                key={it.id}
                type="button"
                className="v2-demo-item"
                onClick={() => { onSelectItem(it.id); onClose(); }}
              >
                <span>
                  {it.patient_name_en} · {it.title.slice(0, 42)}
                </span>
                <span className="voice-quote">{it.status === "pending_review" ? `U${it.urgency}` : it.status}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
