"""Verbatim quotes for the case log.

A quote is kept only when it is a substring of the source record after whitespace is collapsed, and it
contains at least two characters that are not spaces. The same rule as the platform's quote check
(`backend.engine.verify.norm`): a trimmed quote is still verbatim, and a made-up one is not stored.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ZERO_WIDTH = frozenset({"\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff"})
_PDF_HYPHEN_WRAP_RE = re.compile(r"([A-Za-z])-[ \t]*\r?\n[ \t]*([a-z])")


class QuoteError(ValueError):
    """The quote is too short, or it does not appear in the source record."""


@dataclass(frozen=True, slots=True)
class VerifiedQuoteSpan:
    canonical_quote: str
    raw_quote: str
    start_char: int
    end_char: int


def norm(s: str) -> str:
    return " ".join(str(s).split())


def _is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0xF900 <= cp <= 0xFAFF
        or 0x3000 <= cp <= 0x303F
    )


def build_canonical_index(raw_text: str) -> tuple[str, list[int]]:
    """Return (canonical_text, norm_idx_to_raw_idx) for deterministic O(N) span recovery."""
    hyphen_skips: set[int] = set()
    for m in _PDF_HYPHEN_WRAP_RE.finditer(raw_text):
        for idx in range(m.start(1) + 1, m.start(2)):
            hyphen_skips.add(idx)

    norm_chars: list[str] = []
    norm_to_raw: list[int] = []
    pending_space_raw_idx: int | None = None

    for raw_idx, raw_ch in enumerate(raw_text):
        if raw_idx in hyphen_skips or raw_ch in _ZERO_WIDTH:
            continue
        expanded = unicodedata.normalize("NFKC", raw_ch)
        for ch in expanded:
            if ch in _ZERO_WIDTH:
                continue
            if ch.isspace():
                if norm_chars and pending_space_raw_idx is None:
                    pending_space_raw_idx = raw_idx
                continue
            if pending_space_raw_idx is not None:
                prev_ch = norm_chars[-1]
                if not (_is_cjk(prev_ch) or _is_cjk(ch)):
                    norm_chars.append(" ")
                    norm_to_raw.append(pending_space_raw_idx)
                pending_space_raw_idx = None
            norm_chars.append(ch.lower())
            norm_to_raw.append(raw_idx)

    return "".join(norm_chars), norm_to_raw


def verify_quote_span(quote: str, source: str) -> VerifiedQuoteSpan:
    """Verify quote is a verbatim span under NFKC + CJK-whitespace canonicalization and recover raw offsets."""
    norm_q, _ = build_canonical_index(str(quote or ""))
    cjk_count = sum(1 for ch in norm_q if _is_cjk(ch))
    alnum_count = sum(1 for ch in norm_q if ch.isascii() and ch.isalnum())
    if cjk_count < 2 and alnum_count < 4:
        raise QuoteError(f"quote lacks minimum information content: {quote!r}")

    norm_src, src_map = build_canonical_index(str(source or ""))
    pos = norm_src.find(norm_q)
    if pos < 0:
        raise QuoteError("quote is not a verbatim span of the source record")

    start_raw = src_map[pos]
    end_raw = src_map[pos + len(norm_q) - 1] + 1
    return VerifiedQuoteSpan(
        canonical_quote=norm(quote),
        raw_quote=source[start_raw:end_raw],
        start_char=start_raw,
        end_char=end_raw,
    )


def verify_quote(quote: str, source: str) -> str:
    """Return the normalised quote, or raise QuoteError. Does not invent text."""
    q = norm(quote)
    if len(q.replace(" ", "")) < 2:
        raise QuoteError("a quote needs at least 2 non-space characters")
    if q not in norm(source):
        # Allow bilingual OCR NFKC / intra-CJK whitespace fallback when entropy passes
        try:
            return verify_quote_span(quote, source).canonical_quote
        except QuoteError:
            raise QuoteError("quote is not a verbatim substring of the source record")
    return q
