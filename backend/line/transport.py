"""WhatsApp in and out for the line, on top of spec 004's adapter.

Adds what a live line needs: the id of every message we send (so a doctor's swipe-reply finds its patient), template
messages (WhatsApp only lets a business start a conversation outside the 24-hour window with an approved template),
and media download (voice notes, reports).
"""
from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field

from backend.careline.whatsapp import GRAPH, parse_webhook, to_graph, verify_signature  # noqa: F401  (re-exported)

API_VERSION = os.environ.get("WA_API_VERSION", "v21.0")


@dataclass
class Out:
    to: str
    body: str = ""
    type: str = "text"                 # text | buttons | list | template
    buttons: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    button: str = ""
    feature: str = ""
    kind: str = "fixed"                # fixed | approved | doctor | console
    template: dict | None = None       # {"name", "lang", "params": [...], "payloads": [...]}: used outside 24 h
    ctx: dict | None = None            # what it is about: patient_id, handoff_id, plan_id
    fails: list = field(default_factory=list)


def template_body(o: Out) -> dict:
    t = o.template or {}
    comps = [{"type": "body", "parameters": [{"type": "text", "text": str(p)[:1000]} for p in t.get("params", [])]}]
    for i, p in enumerate(t.get("payloads", [])):
        comps.append({"type": "button", "sub_type": "quick_reply", "index": str(i), "parameters": [{"type": "payload", "payload": p}]})
    return {"messaging_product": "whatsapp", "to": o.to, "type": "template",
            "template": {"name": t["name"], "language": {"code": t.get("lang", "en")}, "components": comps}}


def graph_body(o: Out) -> dict:
    return template_body(o) if o.type == "template" else to_graph(o)


class FakeTransport:
    """Nothing leaves the machine. Keeps what would have been sent, with made-up message ids."""
    name = "fake"

    def __init__(self):
        self.sent: list[dict] = []
        self._n = itertools.count(1)
        self.media: dict[str, tuple[bytes, str]] = {}

    def send(self, o: Out) -> str:
        mid = f"wamid.FAKE{next(self._n)}"
        self.sent.append({"id": mid, "to": o.to, "type": o.type, "body": o.body, "feature": o.feature, "kind": o.kind,
                          "buttons": o.buttons, "rows": o.rows, "template": o.template, "graph": graph_body(o)})
        return mid

    def download(self, media_id: str) -> tuple[bytes, str]:
        return self.media.get(media_id, (b"", "application/octet-stream"))

    def to(self, wa: str) -> list[dict]:
        return [s for s in self.sent if s["to"] == wa]


class GraphTransport:
    name = "graph"

    def __init__(self, token: str, phone_number_id: str):
        import httpx
        self.token, self.pnid = token, phone_number_id
        self.http = httpx.Client(timeout=20)

    def _h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def send(self, o: Out) -> str:
        r = self.http.post(f"{GRAPH}/{API_VERSION}/{self.pnid}/messages", headers=self._h(), json=graph_body(o))
        if r.status_code >= 300:
            raise RuntimeError(f"WhatsApp send failed: {r.status_code} {r.text[:300]}")
        return r.json()["messages"][0]["id"]

    def download(self, media_id: str) -> tuple[bytes, str]:
        meta = self.http.get(f"{GRAPH}/{API_VERSION}/{media_id}", headers=self._h()).json()
        r = self.http.get(meta["url"], headers=self._h())
        r.raise_for_status()
        return r.content, meta.get("mime_type", "application/octet-stream")

    def mark_read(self, msg_id: str):
        self.http.post(f"{GRAPH}/{API_VERSION}/{self.pnid}/messages", headers=self._h(),
                       json={"messaging_product": "whatsapp", "status": "read", "message_id": msg_id})


def parse_line_webhook(payload: dict, clock=None) -> list[dict]:
    """Inbound messages with what spec 004's parser leaves out: media ids, mime types and the reply context."""
    out = []
    ins = parse_webhook(payload, clock)
    raw = [m for e in payload.get("entry", []) for ch in e.get("changes", []) for m in (ch.get("value", {}).get("messages") or [])]
    for m, r in zip(ins, raw):
        t = r.get("type")
        media = r.get(t) if t in ("audio", "image", "document", "voice") else None
        out.append({"wa": m.wa, "text": m.text, "reply_id": m.reply_id, "msg_id": m.msg_id, "at": m.at,
                    "kind": "audio" if t in ("audio", "voice") else m.kind,
                    "media_id": (media or {}).get("id"), "mime": (media or {}).get("mime_type"),
                    "filename": (media or {}).get("filename"),
                    "context_id": (r.get("context") or {}).get("id")})
    return out
