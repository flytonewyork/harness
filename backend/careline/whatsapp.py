"""WhatsApp Business Platform (Cloud API) adapter: the webhook in, messages out.

Inbound: Meta calls GET /webhook/whatsapp once to verify (hub.verify_token), then POSTs each message signed with the
app secret (X-Hub-Signature-256). A POST with a bad signature is refused before anything reads it.
Outbound: POST https://graph.facebook.com/<version>/<phone number id>/messages with the system user token.

Transports are pluggable so the same agent runs in the simulator, in tests and live:
- GraphTransport: the real Cloud API (needs WA_TOKEN and WA_PHONE_NUMBER_ID).
- RecordingTransport: keeps what would have been sent; nothing leaves the machine (sim, tests, shadow mode).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime

from .agent import Inbound, Outbound

GRAPH = "https://graph.facebook.com"
API_VERSION = os.environ.get("WA_API_VERSION", "v21.0")


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    if not header or not header.startswith("sha256=") or not app_secret:
        return False
    want = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


def parse_webhook(payload: dict, clock=None) -> list[Inbound]:
    """Messages in a webhook POST. Status updates (sent, delivered, read) are not messages and are skipped."""
    out = []
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            v = ch.get("value", {})
            for m in v.get("messages", []) or []:
                t, text, rid = m.get("type"), "", None
                if t == "text":
                    text = m["text"]["body"]
                elif t == "interactive":
                    i = m["interactive"]
                    r = i.get("button_reply") or i.get("list_reply") or {}
                    rid, text = r.get("id"), r.get("title", "")
                elif t == "button":                      # quick-reply button on a template
                    rid, text = m["button"].get("payload"), m["button"].get("text", "")
                elif t in ("image", "document"):
                    text = (m.get(t) or {}).get("caption", "")
                ts = (clock(int(m["timestamp"])) if clock else datetime.fromtimestamp(int(m["timestamp"]))) if m.get("timestamp") else None
                out.append(Inbound(wa=m["from"], text=text, reply_id=rid,
                                   kind=t if t in ("text", "interactive", "image", "document") else ("interactive" if t == "button" else "other"),
                                   at=ts, msg_id=m.get("id")))
    return out


def to_graph(o: Outbound) -> dict:
    """An Outbound as a Cloud API message body (interactive buttons ≤ 3, list rows ≤ 10)."""
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": o.to}
    if o.type == "buttons":
        return {**base, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": o.body[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}} for b in o.buttons[:3]]}}}
    if o.type == "list":
        return {**base, "type": "interactive", "interactive": {
            "type": "list", "body": {"text": o.body[:1024]},
            "action": {"button": (o.button or "Menu")[:20], "sections": [{"title": "Options"[:24], "rows": [
                {"id": r["id"][:200], "title": r["title"][:24], **({"description": r["description"][:72]} if r.get("description") else {})}
                for r in o.rows[:10]]}]}}}
    return {**base, "type": "text", "text": {"preview_url": False, "body": o.body[:4096]}}


class RecordingTransport:
    name = "recording"

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, o: Outbound) -> dict:
        self.sent.append(to_graph(o))
        return {"ok": True, "transport": self.name}


class GraphTransport:
    name = "graph"

    def __init__(self, token: str | None = None, phone_number_id: str | None = None):
        import httpx
        self.token = token or os.environ["WA_TOKEN"]
        self.pnid = phone_number_id or os.environ["WA_PHONE_NUMBER_ID"]
        self.http = httpx.Client(timeout=15)

    def send(self, o: Outbound) -> dict:
        r = self.http.post(f"{GRAPH}/{API_VERSION}/{self.pnid}/messages", headers={"Authorization": f"Bearer {self.token}"},
                           json=to_graph(o))
        ok = r.status_code < 300
        return {"ok": ok, "status": r.status_code, "transport": self.name, **({} if ok else {"error": r.text[:500]})}

    def mark_read(self, msg_id: str):
        self.http.post(f"{GRAPH}/{API_VERSION}/{self.pnid}/messages", headers={"Authorization": f"Bearer {self.token}"},
                       json={"messaging_product": "whatsapp", "status": "read", "message_id": msg_id})


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False)
