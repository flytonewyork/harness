import { useEffect, useMemo, useRef } from "react";
import type { QuoteAnchor } from "../api";

interface PaperProps {
  title: string;
  subtitle?: string;
  text: string;
  quotes: QuoteAnchor[];
  focusedQuote?: string | null;
}

interface Segment {
  text: string;
  highlighted: boolean;
  focused: boolean;
}

export function Paper({ title, subtitle, text, quotes, focusedQuote }: PaperProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  const segments = useMemo<Segment[]>(() => {
    if (!text) return [];
    const spans: Array<{ start: number; end: number; focused: boolean }> = [];
    const seenQuotes = new Set<string>();

    const candidates = [...quotes];
    if (focusedQuote && !candidates.some((q) => q.quote === focusedQuote)) {
      candidates.push({ quote: focusedQuote });
    }

    for (const q of candidates) {
      const needle = (q.quote || "").trim();
      if (!needle || seenQuotes.has(needle)) continue;
      seenQuotes.add(needle);
      const isFocused = Boolean(focusedQuote && needle === focusedQuote.trim());
      if (typeof q.start_char === "number" && typeof q.end_char === "number" && q.end_char > q.start_char) {
        spans.push({ start: q.start_char, end: Math.min(q.end_char, text.length), focused: isFocused });
      } else {
        const idx = text.indexOf(needle);
        if (idx >= 0) spans.push({ start: idx, end: idx + needle.length, focused: isFocused });
      }
    }

    spans.sort((a, b) => a.start - b.start || (b.focused ? 1 : -1));
    const merged: typeof spans = [];
    for (const sp of spans) {
      const last = merged[merged.length - 1];
      if (!last || sp.start >= last.end) merged.push(sp);
      else if (sp.focused) {
        last.end = Math.max(last.end, sp.end);
        last.focused = true;
      }
    }

    const out: Segment[] = [];
    let cursor = 0;
    for (const sp of merged) {
      if (sp.start > cursor) out.push({ text: text.slice(cursor, sp.start), highlighted: false, focused: false });
      out.push({ text: text.slice(sp.start, sp.end), highlighted: true, focused: sp.focused });
      cursor = sp.end;
    }
    if (cursor < text.length) out.push({ text: text.slice(cursor), highlighted: false, focused: false });
    return out;
  }, [text, quotes, focusedQuote]);

  useEffect(() => {
    if (!focusedQuote || !containerRef.current) return;
    const el = containerRef.current.querySelector('mark[data-focus="true"]');
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [focusedQuote]);

  return (
    <div className="v2-paper-sheet" ref={containerRef} data-testid="paper-sheet">
      <div className="v2-paper-meta">
        <strong>{title}</strong>
        {subtitle && <span>{subtitle}</span>}
      </div>
      <div className="v2-paper-body">
        {segments.length === 0 ? (
          <span className="voice-assistant">No source document attached.</span>
        ) : (
          segments.map((seg, idx) =>
            seg.highlighted ? (
              <mark key={idx} data-focus={seg.focused ? "true" : "false"}>
                {seg.text}
              </mark>
            ) : (
              <span key={idx}>{seg.text}</span>
            ),
          )
        )}
      </div>
    </div>
  );
}
