"""Typed shapes for documents in and commitments out."""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from .prompt import KINDS, OWNERS, PROMPT_VERSION

Kind = Literal[tuple(KINDS)]          # type: ignore[valid-type]
Owner = Literal[tuple(OWNERS)]        # type: ignore[valid-type]


class Document(BaseModel):
    doc_id: str
    kind: str
    date: str                          # ISO date the clinic received or wrote it; due dates count from here
    text: str
    group: str | None = None           # batching only (never shown to a model): keeps one patient's documents apart


class Commitment(BaseModel):
    kind: Kind
    what: str = Field(max_length=200)
    due_in_days: int | None = Field(default=None, ge=0, le=3650)
    owner: Owner = "clinic"
    conditional: bool = False
    quote: str = Field(min_length=1, max_length=400)


class Extraction(BaseModel):
    doc_id: str
    model: str
    prompt_version: str = PROMPT_VERSION
    commitments: list[Commitment] = []
    repaired: int = 0                  # quotes fixed to the document's exact casing or spacing
    dropped: list[dict] = []           # commitments removed because their quote is not in the document
    error: str | None = None


def content_id(kind: str, text: str) -> str:
    """Documents are addressed by content, so identical texts are extracted once."""
    return "D" + hashlib.sha256(f"{kind}\n{text}".encode()).hexdigest()[:16]
