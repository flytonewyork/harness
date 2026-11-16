"""The care line's server: the WhatsApp webhook, the people's queue, the simulator and the evals dashboard.

Routes
- GET/POST /webhook/whatsapp        Meta's webhook (verify token; X-Hub-Signature-256 checked when WA_APP_SECRET is set)
- POST /api/careline/sim/message     a patient message from the dashboard's phone (sim and tester modes)
- POST /api/careline/sim/checkin     the clinic starts a day-3 check-in (AV-02)
- GET  /api/careline/state           conversations, hand-offs, drafts, record
- POST /api/careline/person/{claim,close,approve,reply}   a person's actions; X-Person: a person id from the settings
- GET  /api/careline/evals, POST /api/careline/evals/run  the eval set, scored
- GET  /careline                     the dashboard;  GET /join  the patient's join page with the QR code

Modes: `sim` (nothing leaves the machine), `tester` (live WhatsApp, but only numbers in CARELINE_TESTERS get the agent;
anyone else gets one fixed notice and nothing they write is kept), `live` (everyone; not to be used until the
gates in spec 003 are signed off and a decision on real patient data is recorded in docs/DECISIONS.md).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from . import evals as ev
from .agent import CareAgent, Inbound
from .store import AuthorityError
from .whatsapp import RecordingTransport, parse_webhook, to_graph, verify_signature

STATIC = Path(__file__).with_name("static")


class SimMsg(BaseModel):
    wa: str = "85290000001"
    text: str = ""
    reply_id: str | None = None
    kind: str = "text"
    at: str | None = None


class Act(BaseModel):
    id: str
    resolution: str = "done"
    text: str | None = None


def create_app(agent: CareAgent, transport, *, run_path: str, mode: str = "sim", verify_token: str = "", app_secret: str = "",
               data_dir: Path | None = None, wa_number: str = "") -> FastAPI:
    app = FastAPI(title="tmrw care line", version="0.1")
    store, settings = agent.store, agent.settings
    tape = (data_dir / "webhook-tape.jsonl") if data_dir else None
    evals_cache: dict = {}
    lock = threading.Lock()

    def deliver(outs, now=None):
        res = []
        for o in outs:
            r = transport.send(o)
            if not r.get("ok"):
                store.log(now or settings.now(), o.to, "send_failed", status=r.get("status"), error=r.get("error"))
            res.append({**o.as_dict(), "graph": to_graph(o), "delivery": r})
        store.save()
        return res

    # ---- WhatsApp webhook ------------------------------------------------------------------------------------------
    @app.get("/webhook/whatsapp")
    def wa_verify(mode_: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
                  challenge: str = Query("", alias="hub.challenge")):
        if mode_ == "subscribe" and verify_token and token == verify_token:
            return PlainTextResponse(challenge)
        raise HTTPException(403, "verify token mismatch")

    @app.post("/webhook/whatsapp")
    async def wa_inbound(request: Request, x_hub_signature_256: str | None = Header(None)):
        body = await request.body()
        if app_secret and not verify_signature(body, x_hub_signature_256, app_secret):
            raise HTTPException(401, "bad signature")
        if mode != "sim" and not app_secret:
            raise HTTPException(503, "WA_APP_SECRET is not set; refusing unsigned webhooks outside sim mode")
        payload = json.loads(body or b"{}")
        sent = []
        for m in parse_webhook(payload, settings.from_epoch):
            if hasattr(transport, "mark_read") and m.msg_id:
                try:
                    transport.mark_read(m.msg_id)
                except Exception:  # noqa: BLE001 — a read receipt is never worth a lost reply
                    pass
            outs = agent.handle(m)
            r = deliver(outs, m.at)
            sent += r
            if tape and (mode == "sim" or m.wa in agent.testers):          # strangers' messages are never kept
                with tape.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": settings.now().isoformat(timespec="seconds"), "in": m.__dict__ | {"at": m.at.isoformat() if m.at else None},
                                        "out": [x["graph"] for x in r]}, ensure_ascii=False) + "\n")
        return {"ok": True, "replies": len(sent)}

    # ---- simulator --------------------------------------------------------------------------------------------------
    @app.post("/api/careline/sim/message")
    def sim_message(m: SimMsg):
        if mode == "live":
            raise HTTPException(403, "the simulator is off in live mode")
        at = datetime.fromisoformat(m.at) if m.at else settings.now()
        outs = agent.handle(Inbound(wa=m.wa, text=m.text, reply_id=m.reply_id, kind=m.kind, at=at))
        sim_sink = RecordingTransport() if mode == "sim" or m.wa.startswith("sim-") else transport
        res = []
        for o in outs:
            r = sim_sink.send(o)
            res.append({**o.as_dict(), "delivery": r})
        store.save()
        return {"replies": res}

    @app.post("/api/careline/sim/checkin")
    def sim_checkin(m: SimMsg):
        at = datetime.fromisoformat(m.at) if m.at else settings.now()
        outs = agent.start_checkin(m.wa, at)
        sink = RecordingTransport() if mode == "sim" or m.wa.startswith("sim-") else transport
        return {"replies": [{**o.as_dict(), "delivery": sink.send(o)} for o in outs],
                "note": None if outs else "not sent: no consent, opted out, not linked, or outside daytime (HE-06)"}

    @app.post("/api/careline/tick")
    def tick():
        moved = store.tick(settings.now())
        store.save()
        return {"moved": [h["id"] for h in moved]}

    # ---- state ------------------------------------------------------------------------------------------------------
    @app.get("/api/careline/health")
    def health():
        return {"ok": True, "mode": mode, "transport": transport.name, "intent_model": getattr(agent.intents, "name", "rules"),
                "conversations": len(store.conversations), "open_handoffs": sum(h["status"] == "open" for h in store.handoffs.values())}

    @app.get("/api/careline/state")
    def state():
        now = settings.now()
        people = {k: {"id": k, **v} for k, v in settings["people"].items()}
        convs = []
        for c in store.conversations.values():
            p = agent.clinic.patient(c["patient_id"]) if c.get("patient_id") else None
            convs.append({**c, "patient": {"name_en": p["name_en"], "name_zh": p.get("name_zh"), "dob": p["dob"]} if p else None})
        return {"now": now.isoformat(timespec="minutes"), "open": settings.is_open(now), "next_open": settings.next_open(now).isoformat(timespec="minutes"),
                "mode": mode, "transport": transport.name, "clinic": {**agent.clinic.clinic(), "name": settings["name_en"]},
                "source": getattr(agent.clinic, "source", {}), "people": people, "named_doctor": settings["named_doctor"],
                "personas": agent.clinic.personas() if hasattr(agent.clinic, "personas") else [],
                "conversations": sorted(convs, key=lambda c: (c["messages"][-1]["at"] if c["messages"] else ""), reverse=True),
                "handoffs": sorted(store.handoffs.values(), key=lambda h: (h["status"] != "open", h["kind"] != "named doctor", h["reply_by"])),
                "drafts": list(store.drafts.values()), "events": store.events[-300:],
                "wa_link": wa_link(), "intent_model": getattr(agent.intents, "name", "rules")}

    # ---- people's actions (never the agent's) --------------------------------------------------------------------------
    def person(x_person: str | None) -> str:
        if not x_person or x_person not in settings["people"]:
            raise HTTPException(403, "X-Person must name a person in the clinic's settings")
        return x_person

    def guarded(fn):
        try:
            fn()
        except AuthorityError as e:
            raise HTTPException(403, str(e))
        except KeyError:
            raise HTTPException(404, "not found")
        store.save()
        return {"ok": True}

    @app.post("/api/careline/person/claim")
    def claim(a: Act, x_person: str | None = Header(None)):
        return guarded(lambda: store.claim(settings.now(), a.id, person(x_person)))

    @app.post("/api/careline/person/close")
    def close(a: Act, x_person: str | None = Header(None)):
        return guarded(lambda: store.close(settings.now(), a.id, person(x_person), a.resolution))

    @app.post("/api/careline/person/approve")
    def approve(a: Act, x_person: str | None = Header(None)):
        return guarded(lambda: store.approve(settings.now(), a.id, person(x_person), a.text))

    @app.post("/api/careline/person/reply")
    def reply(a: Act, x_person: str | None = Header(None)):
        """A person writes to the patient themselves. Labelled with their name, so it never reads as the agent."""
        by = person(x_person)
        h = store.handoffs.get(a.id)
        if not h or not a.text:
            raise HTTPException(404, "no such hand-off, or no text")
        c = store.conversations.get(h["wa"], {"lang": "en"})
        name = settings["people"][by]["name"]
        from .agent import Outbound
        o = Outbound(to=h["wa"], body=f"{settings.name(c.get('lang', 'en'))}: {name}\n{a.text}", feature="person")
        sink = RecordingTransport() if mode == "sim" or h["wa"].startswith("sim-") else transport
        r = sink.send(o)
        c.setdefault("messages", []).append({"dir": "out", "at": settings.now().isoformat(timespec="seconds"), "feature": "person", "by": by, "text": o.body})
        store.log(settings.now(), h["wa"], "person_reply", id=a.id, by=by)
        if not h["claimed_by"]:
            store.claim(settings.now(), a.id, by)
        store.save()
        return {"ok": r.get("ok"), "delivery": r}

    # ---- evals ------------------------------------------------------------------------------------------------------
    @app.get("/api/careline/evals")
    def get_evals():
        with lock:
            if "res" not in evals_cache:
                f = ev.ROOT / "evals" / "careline" / "results.json"
                evals_cache["res"] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else ev.run_all(run_path)
        return JSONResponse(evals_cache["res"])

    @app.post("/api/careline/evals/run")
    def run_evals(intent: str = "rules"):
        with lock:
            evals_cache["res"] = ev.run_all(run_path, intent)
        return JSONResponse(evals_cache["res"]["summary"])

    # ---- pages ------------------------------------------------------------------------------------------------------
    def wa_link() -> str:
        if not wa_number:
            return ""
        return f"https://wa.me/{wa_number}?text=" + quote("JOIN " + settings["name_en"])

    @app.get("/careline/qr.svg")
    def qr():
        link = wa_link() or "https://wa.me/"
        try:
            import io

            import segno
            buf = io.BytesIO()
            segno.make(link, error="m").save(buf, kind="svg", scale=8, border=2, dark="#0B1F33")
            return Response(buf.getvalue(), media_type="image/svg+xml")
        except ImportError:
            raise HTTPException(501, "pip install segno to draw the QR code")

    @app.get("/careline", response_class=HTMLResponse)
    def dashboard():
        return (STATIC / "dashboard.html").read_text(encoding="utf-8")

    @app.get("/join", response_class=HTMLResponse)
    def join():
        html = (STATIC / "join.html").read_text(encoding="utf-8")
        return html.replace("{{WA_LINK}}", wa_link() or "#").replace("{{CLINIC}}", settings["name_en"]).replace("{{CLINIC_ZH}}", settings["name_zh"]) \
                   .replace("{{MODE}}", mode)

    @app.get("/")
    def root():
        return HTMLResponse('<meta http-equiv="refresh" content="0; url=/careline">')

    return app
