import { useEffect } from "react";
import type { ReactNode } from "react";

interface SheetProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function Sheet({ open, title, onClose, children }: SheetProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="v2-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-label={title}>
      <div className="v2-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="v2-order-head">
          <h2 className="v2-decision-title">{title}</h2>
          <button type="button" className="v2-text-btn" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
