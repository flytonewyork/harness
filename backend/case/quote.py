"""Verbatim quotes for the case log.

A quote is kept only when it is a substring of the source record after whitespace is collapsed, and it
contains at least two characters that are not spaces. The same rule as the platform's quote check
(`backend.engine.verify.norm`): a trimmed quote is still verbatim, and a made-up one is not stored.
"""
from __future__ import annotations


class QuoteError(ValueError):
    """The quote is too short, or it does not appear in the source record."""


def norm(s: str) -> str:
    return " ".join(str(s).split())


def verify_quote(quote: str, source: str) -> str:
    """Return the normalised quote, or raise QuoteError. Does not invent text."""
    q = norm(quote)
    if len(q.replace(" ", "")) < 2:
        raise QuoteError("a quote needs at least 2 non-space characters")
    if q not in norm(source):
        raise QuoteError("quote is not a verbatim substring of the source record")
    return q
