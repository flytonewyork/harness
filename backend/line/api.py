"""The line's server.

Routes
- GET/POST /webhook/whatsapp       Meta's webhook (verify token; X-Hub-Signature-256 checked outside sim). Answers at
                                   once and works through messages in order on one worker thread.
- POST /webhook/stripe             Stripe's webhook (Stripe-Signature checked): checkout.session.completed → paid.
- POST /cron/tick                  Cloud Scheduler, every minute (X-Cron-Secret): check-ins, reminders, nudges.
- GET  /j/{code}, /j/{code}.png    a doctor's QR card and the QR image (opens WhatsApp with the doctor's code typed).
- GET  /privacy, /paid             the privacy notice; the page Stripe returns to.
- GET  /d/{token} → /doctor        one-time sign-in link from the doctor's WhatsApp, then the doctor's web page.
- /api/doctor/...                  the web page's data and actions (signed-in doctor only, own patients only).
- /sim, /api/sim/...               the two-phone simulator (sim mode only): a patient and a doctor, fakes behind.
- POST /api/admin/doctor           add or update a doctor (Bearer LINE_ADMIN_TOKEN).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from . import hours
from .agent import Line
from .config import Config
from .media import open_media
from .models import FakeModel, open_model
from .payments import FakeStripe, StripeConnect, verify_stripe
from .store import open_store
from .transport import FakeTransport, GraphTransport, parse_line_webhook, verify_signature

STATIC = Path(__file__).with_name("static")
log = logging.getLogger("line")


def build_line(cfg: Config) -> Line:
    store = open_store(cfg.store, cfg.vertex_project, cfg.store_prefix)
    tx = FakeTransport() if cfg.mode == "sim" else GraphTransport(cfg.wa_token, cfg.wa_phone_number_id)
    model = open_model(cfg)
    stripe = (StripeConnect(cfg.stripe_secret) if cfg.stripe_secret else FakeStripe()) if cfg.payments else None
    return Line(cfg, store, tx, model, stripe, open_media(cfg))


def _sign(secret: str, value: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


def create_app(cfg: Config | None = None, line: Line | None = None) -> FastAPI:
    cfg = cfg or Config.from_env()
    line = line or build_line(cfg)
    app = FastAPI(title="tmrw care line", docs_url=None, redoc_url=None)
    app.state.line, app.state.cfg = line, cfg
    work: queue.Queue = queue.Queue()

    def worker():
        while True:
            m = work.get()
            try:
                line.handle(m)
            except Exception as e:  # noqa: BLE001  never let one message stop the line; ids only in the log
                log.exception("handle failed for message %s", m.get("msg_id"))
                try:
                    line.store.event(hours.now_hk(cfg.timezone), "handle_failed", why=type(e).__name__)
                except Exception:  # noqa: BLE001
                    pass
            finally:
                work.task_done()

    if cfg.mode != "sim":
        threading.Thread(target=worker, daemon=True).start()

    # ---- WhatsApp -------------------------------------------------------------------------------------------------
    @app.get("/webhook/whatsapp")
    def wa_verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and cfg.wa_verify_token and q.get("hub.verify_token") == cfg.wa_verify_token:
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403)

    @app.post("/webhook/whatsapp")
    async def wa_in(request: Request, x_hub_signature_256: str | None = Header(None)):
        body = await request.body()
        if cfg.mode != "sim" and not verify_signature(body, x_hub_signature_256, cfg.wa_app_secret):
            raise HTTPException(401, "bad signature")
        tz = ZoneInfo(cfg.timezone)                              # the line's clock is Hong Kong's, whatever the server's is
        for m in parse_line_webhook(json.loads(body or b"{}"), clock=lambda ts: datetime.fromtimestamp(ts, tz).replace(tzinfo=None)):
            m["at"] = m.get("at") or hours.now_hk(cfg.timezone)
            if cfg.mode == "sim":
                line.handle(m)
            else:
                work.put(m)
        return {"ok": True}

    # ---- Stripe ---------------------------------------------------------------------------------------------------
    @app.post("/webhook/stripe")
    async def stripe_in(request: Request, stripe_signature: str | None = Header(None)):
        body = await request.body()
        if cfg.mode != "sim" and not verify_stripe(body, stripe_signature, cfg.stripe_webhook_secret):
            raise HTTPException(400, "bad signature")
        ev = json.loads(body or b"{}")
        obj = ev.get("data", {}).get("object", {})
        if ev.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded") and obj.get("payment_status") in ("paid", None):
            pid = (obj.get("metadata") or {}).get("payment_id") or obj.get("client_reference_id")
            if pid:
                line.paid(pid, subscription=obj.get("subscription"))
        if ev.get("type") == "customer.subscription.deleted" and obj.get("id"):
            line.membership_ended(obj["id"])
        if ev.get("type") == "account.updated":
            d = line.store.one("doctor", stripe_account=obj.get("id"))
            if d:
                d["stripe_ready"] = bool(obj.get("charges_enabled"))
                line.store.put("doctor", d["id"], d)
        return {"ok": True}

    # ---- scheduler -------------------------------------------------------------------------------------------------
    @app.post("/cron/tick")
    def tick(x_cron_secret: str | None = Header(None)):
        if cfg.mode != "sim" and (not cfg.cron_secret or x_cron_secret != cfg.cron_secret):
            raise HTTPException(403)
        return {"sent": len(line.tick())}

    @app.get("/healthz")
    def health():
        return {"ok": True, "mode": cfg.mode, "model": getattr(line.model, "name", "?"), "store": cfg.store}

    # ---- public pages ---------------------------------------------------------------------------------------------
    def wa_link(d: dict, c: int = 1) -> str:
        from urllib.parse import quote
        tag = d["code"] if c <= 1 else f"{d['code']}.{c}"         # .2, .3: the doctor's second and third clinic
        return f"https://wa.me/{cfg.wa_number or '85200000000'}?text=" + quote(f"Hi {d.get('short_en', d['name_en'])} [{tag}]")

    def clinic_no(d: dict, c: int) -> int:
        return c if 1 <= c <= max(1, len(d.get("clinics") or [])) and c <= 9 else 1

    @app.get("/j/{code}.png")
    def qr_png(code: str, c: int = 1):
        d = line.store.one("doctor", code=code)
        if not d:
            raise HTTPException(404)
        c = clinic_no(d, c)
        import io
        import segno
        buf = io.BytesIO()
        segno.make(wa_link(d, c), error="m").save(buf, kind="png", scale=10, border=2)
        return Response(buf.getvalue(), media_type="image/png")

    @app.get("/j/{code}", response_class=HTMLResponse)
    def qr_card(code: str, c: int = 1):
        d = line.store.one("doctor", code=code)
        if not d:
            raise HTTPException(404)
        c = clinic_no(d, c)
        cl = (d.get("clinics") or [{}])[c - 1]
        page = (STATIC / "card.html").read_text(encoding="utf-8")
        rep = {"{{NAME_EN}}": d["name_en"], "{{NAME_ZH}}": d.get("name_zh", ""), "{{CODE}}": code, "{{LINK}}": wa_link(d, c), "{{C}}": str(c),
               "{{CLINIC}}": " · ".join(x for x in (cl.get("name_en"), cl.get("name_zh")) if x) if len(d.get("clinics") or []) > 1 else "",
               "{{HOURS_EN}}": hours.hours_text(d, "en"), "{{HOURS_ZH}}": hours.hours_text(d, "zh")}
        for k, v in rep.items():
            page = page.replace(k, _esc(v))
        return page

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return (STATIC / "privacy.html").read_text(encoding="utf-8")

    @app.get("/paid", response_class=HTMLResponse)
    def paid_page(p: str = "", cancel: int = 0):
        msg = ("No payment was taken. You can go back to WhatsApp." if cancel else
               "Thank you. Your payment is received. Go back to WhatsApp for the next step.")
        zh = "冇收取任何費用，你可以返去WhatsApp。" if cancel else "多謝，已收到你嘅付款。請返去WhatsApp睇下一步。"
        return f"<!doctype html><meta name=viewport content='width=device-width'><title>tmrw care line</title><body style='font:18px system-ui;padding:24px;max-width:520px;margin:auto'><p>{msg}</p><p>{zh}</p><p><a href='https://wa.me/{cfg.wa_number}'>Open WhatsApp</a></p>"

    # ---- the doctor's web page -------------------------------------------------------------------------------------
    def session_doctor(request: Request) -> dict:
        c = request.cookies.get("line_session", "")
        try:
            did, exp, sig = c.split(".")
        except ValueError:
            raise HTTPException(401)
        if not hmac.compare_digest(sig, _sign(cfg.session_secret, f"{did}.{exp}")) or int(exp) < time.time():
            raise HTTPException(401)
        d = line.store.get("doctor", did)
        if not d:
            raise HTTPException(401)
        return d

    @app.get("/d/{token}", response_class=HTMLResponse)
    def sign_in_page(token: str):
        # Opening the link doesn't use it up (link previews and scanners open links too); pressing the button does.
        return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'><title>Sign in</title>"
                f"<body style='font:18px system-ui;padding:32px 16px;max-width:420px;margin:auto;text-align:center'><p>Your care line</p>"
                f"<form method=post action='/d/{_esc(token)}'><button style='font:inherit;padding:12px 28px;border-radius:999px;border:0;"
                f"background:#0f7b5f;color:#fff'>Sign in</button></form>")

    @app.post("/d/{token}")
    def sign_in(token: str):
        did = line.redeem(token, hours.now_hk(cfg.timezone))
        if not did:
            return HTMLResponse("<p style='font:18px system-ui;padding:24px'>This link has expired or was used. Send WEB to the line for a new one.</p>", 401)
        exp = str(int(time.time()) + 12 * 3600)
        r = RedirectResponse("/doctor", 303)
        r.set_cookie("line_session", f"{did}.{exp}.{_sign(cfg.session_secret, f'{did}.{exp}')}", httponly=True,
                     secure=cfg.mode != "sim", samesite="lax", max_age=12 * 3600)
        return r

    @app.get("/doctor", response_class=HTMLResponse)
    def doctor_page():
        return (STATIC / "doctor.html").read_text(encoding="utf-8")

    def own(d, p):
        if not p or p["doctor_id"] != d["id"]:
            raise HTTPException(404)
        return p

    def thread(p) -> list[dict]:
        """What passed between the line and this patient's number, for this doctor's record: not the doctor's console."""
        return sorted([m for m in line.store.find("msg", patient_id=p["id"]) if m.get("wa") == p["wa"]], key=lambda m: m["at"])

    @app.get("/api/doctor/state")
    def doctor_state(request: Request):
        d = session_doctor(request)
        return JSONResponse(state_for(line, cfg, d))

    @app.get("/api/doctor/patient/{pid}")
    def doctor_patient(pid: str, request: Request):
        d = session_doctor(request)
        p = own(d, line.store.get("patient", pid))
        msgs = thread(p)
        return {"patient": p, "messages": msgs, "plans": line.store.find("plan", patient_id=pid),
                "handoffs": line.store.find("handoff", patient_id=pid), "payments": line.store.find("payment", patient_id=pid)}

    @app.get("/api/doctor/media/{ref}")
    def doctor_media(ref: str, request: Request):
        d = session_doctor(request)
        mine = any(ref == r.get("ref") for p in line.store.find("patient", doctor_id=d["id"]) for r in p.get("reports", [])) or \
            any(ref == h.get("media_ref") for h in line.store.find("handoff", doctor_id=d["id"]))
        if not mine:
            raise HTTPException(404)
        data, mime = line.media.get(ref)
        return Response(data, media_type=mime, headers={"Cache-Control": "no-store"})

    @app.post("/api/doctor/plan/{plan_id}/{what}")
    def doctor_plan(plan_id: str, what: str, request: Request):
        d = session_doctor(request)
        if what not in ("send", "skip"):
            raise HTTPException(400)
        now = hours.now_hk(cfg.timezone)
        with line.store.lock:
            return {"sent": line.deliver(line.plan_action(d, plan_id, what, now), now)}

    @app.post("/api/doctor/handoff/{hid}/{what}")
    async def doctor_handoff(hid: str, what: str, request: Request):
        d = session_doctor(request)
        body = await request.json() if (await request.body()) else {}
        now = hours.now_hk(cfg.timezone)
        with line.store.lock:
            h = line.store.get("handoff", hid)
            if not h or h["doctor_id"] != d["id"]:
                raise HTTPException(404)
            if what == "done":
                line.close_handoff(now, hid, d["id"], body.get("resolution") or "done (web)")
                return {"ok": True}
            if what == "reply" and (body.get("text") or "").strip():
                sess = line._session(d)
                sess.update(mode="write", patient_id=h["patient_id"], handoff_id=hid)
                return {"sent": line.deliver(line._doctor_writes(d, sess, body["text"].strip(), now), now)}
            if what == "send_draft" and h.get("draft"):
                return {"sent": line.deliver(line.question_action(d, hid, "send", now), now)}
            if what in ("ok", "visit", "no") and h["kind"] == "repeat":
                return {"sent": line.deliver(line.repeat_decision(d, hid, what, now), now)}
        raise HTTPException(400)

    @app.post("/api/doctor/patient/{pid}/offer")
    def doctor_offer(pid: str, request: Request):
        d = session_doctor(request)
        own(d, line.store.get("patient", pid))
        now = hours.now_hk(cfg.timezone)
        with line.store.lock:
            outs = line.offer_membership(d, pid, now)
            note = outs[-1].body if outs else ""
            return {"sent": line.deliver(outs, now), "note": note}

    @app.post("/api/doctor/settings")
    async def doctor_settings(request: Request):
        d = session_doctor(request)
        body = await request.json()
        for k in ("fees", "hours", "daytime"):
            if k in body:
                d[k] = body[k]
        if "fees" in body:
            d["fees"] = {k: max(0, int(v)) for k, v in body["fees"].items() if k in ("repeat", "result")}
        if "membership_hkd" in body:
            d["membership_hkd"] = max(0, int(body["membership_hkd"]))
        hours.clock(d)                                   # refuses hours it can't read
        line.store.put("doctor", d["id"], d)
        return {"ok": True}

    @app.get("/api/doctor/payouts")
    def doctor_payouts(request: Request):
        d = session_doctor(request)
        if not (line.stripe and cfg.payments):
            return {"url": None, "why": "payments are off"}
        if not d.get("stripe_account"):
            d["stripe_account"] = line.stripe.create_account(d.get("email"))
            line.store.put("doctor", d["id"], d)
        return {"url": line.stripe.onboarding_link(d["stripe_account"], f"{cfg.base_url}/doctor", f"{cfg.base_url}/doctor")}

    @app.get("/api/doctor/export/{pid}", response_class=HTMLResponse)
    def doctor_export(pid: str, request: Request):
        d = session_doctor(request)
        p = own(d, line.store.get("patient", pid))
        msgs = thread(p)
        rows = "".join(f"<tr><td>{_esc(m['at'])}</td><td>{'Patient' if m['dir'] == 'in' else _esc(m.get('kind', ''))}</td>"
                       f"<td style='white-space:pre-wrap'>{_esc(m.get('text') or '[' + m.get('kind', '') + ']')}</td></tr>" for m in msgs)
        return (f"<!doctype html><meta charset=utf-8><title>Care line record</title><body style='font:13px system-ui;margin:24px'>"
                f"<h2>Care line record: {_esc(p.get('name', ''))} (b. {_esc(p.get('dob', ''))})</h2>"
                f"<p>Doctor: {_esc(d['name_en'])}. Joined {_esc(p.get('joined_at', ''))}. Consent: {_esc((p.get('consent') or {}).get('at', ''))}, "
                f"wording version {_esc((p.get('consent') or {}).get('text_version', ''))}. Exported {_esc(hours.now_hk().isoformat())}.</p>"
                f"<table border=1 cellpadding=4 style='border-collapse:collapse'><tr><th>When</th><th>Who</th><th>Message</th></tr>{rows}</table>"
                f"<p>Print to PDF for your clinical notes.</p>")

    # ---- admin -----------------------------------------------------------------------------------------------------
    @app.post("/api/admin/doctor")
    async def admin_doctor(request: Request, authorization: str | None = Header(None)):
        if not cfg.admin_token or authorization != f"Bearer {cfg.admin_token}":
            raise HTTPException(403)
        body = await request.json()
        need = {"id", "code", "name_en", "wa"}
        if not need <= set(body):
            raise HTTPException(400, f"needs {sorted(need)}")
        d = {**(line.store.get("doctor", body["id"]) or {}), **body}
        hours.clock(d)
        line.store.put("doctor", d["id"], d)
        return {"ok": True, "qr": f"{cfg.base_url}/j/{d['code']}"}

    # ---- the simulator (sim mode only) -----------------------------------------------------------------------------
    if cfg.mode == "sim":
        add_sim(app, line, cfg)
    return app


def state_for(line: Line, cfg: Config, d: dict) -> dict:
    now = hours.now_hk(cfg.timezone)
    pats = line.store.find("patient", doctor_id=d["id"])
    pays = [x for x in line.store.find("payment", doctor_id=d["id"]) if x["status"] == "paid" and x["kind"] != "membership"]
    month = now.strftime("%Y-%m")
    by_id = {p["id"]: p for p in pats}
    open_h = sorted([h for h in line.store.find("handoff", doctor_id=d["id"]) if h["status"] == "open"], key=lambda h: h["reply_by"])
    drafts = [x for x in line.store.find("plan", doctor_id=d["id"]) if x["status"] == "draft"]
    for h in open_h:
        h["patient_name"] = (by_id.get(h["patient_id"]) or {}).get("name", "?")
        h["patient_wa"] = (by_id.get(h["patient_id"]) or {}).get("wa", "")
    for x in drafts:
        x["patient_name"] = (by_id.get(x["patient_id"]) or {}).get("name", "?")
    earned = sum(x["doctor_fee"] for x in pays if x.get("paid_at", "").startswith(month))
    return {
        "doctor": {k: d.get(k) for k in ("id", "code", "name_en", "name_zh", "hours", "fees", "membership_hkd", "paused_until", "stripe_account", "stripe_ready")},
        "hours_text": hours.hours_text(d, "en"),
        "qr": f"{cfg.base_url}/j/{d['code']}",
        "qrs": [{"name": c.get("name_en", ""), "url": f"{cfg.base_url}/j/{d['code']}" + (f"?c={i}" if i > 1 else "")}
                for i, c in enumerate(d.get("clinics") or [], 1)],
        "queue": open_h, "drafts": drafts,
        "patients": sorted([{**{k: p.get(k) for k in ("id", "name", "dob", "joined_at", "state", "plan_id", "lang", "membership_offered")},
                             "member": Line.is_member(p)} for p in pats],
                           key=lambda p: p.get("joined_at") or "", reverse=True),
        "money": {"month": month, "earned_hkd": earned, "paid_count": sum(1 for x in pays if x.get("paid_at", "").startswith(month)),
                  "service_fees_hkd": sum(x["service_fee"] for x in pays if x.get("paid_at", "").startswith(month)),
                  "subscription_hkd": cfg.subscription_hkd, "shadow_subscription_hkd": cfg.shadow_subscription_hkd},
        "membership": {"members": sum(1 for p in pats if Line.is_member(p)), "offered": sum(1 for p in pats if p.get("membership_offered")),
                       "monthly_hkd": sum((p.get("member") or {}).get("doctor_fee", 0) for p in pats if Line.is_member(p)),
                       "fee_pct": cfg.membership_fee_pct},
        "stats": {"joined": len(pats), "with_plan": sum(1 for p in pats if p.get("plan_id")),
                  "stopped": sum(1 for p in pats if p.get("state") == "stopped"), "open": len(open_h)},
        "payments_on": bool(cfg.payments),
    }


def add_sim(app: FastAPI, line: Line, cfg: Config):
    """Two phones on one page: a patient and the doctor, with the fake model, WhatsApp and Stripe behind them."""
    clock = {"now": None}

    def now():
        return clock["now"] or hours.now_hk(cfg.timezone)

    if not line.store.one("doctor", code="TH01"):
        line.store.put("doctor", "D1", {"id": "D1", "code": "TH01", "name_en": "Dr Thomas Hu", "name_zh": "胡醫生", "short_en": "Dr Hu",
                                        "short_zh": "胡醫生", "wa": "85290000001", "fees": {"repeat": 200, "result": 300},
                                        "stripe_account": "acct_FAKE0" if cfg.payments else None,
                                        "clinics": [{"id": "c1", "name_en": "Harbour Clinic (synthetic)", "name_zh": "海港診所（模擬）",
                                                     "address_en": "1 Test Road", "address_zh": "測試路1號", "phone": "2000 0000"}]})

    @app.get("/sim", response_class=HTMLResponse)
    def sim_page():
        return (STATIC / "sim.html").read_text(encoding="utf-8")

    @app.post("/api/sim/send")
    async def sim_send(request: Request):
        b = await request.json()
        m = {"wa": b["wa"], "text": b.get("text", ""), "reply_id": b.get("reply_id"), "kind": b.get("kind", "text"),
             "msg_id": f"sim-{time.time_ns()}", "at": now(), "context_id": b.get("context_id")}
        if b.get("audio_text"):                          # 'dictate' in the simulator: the fake model reads it as the voice note
            mid = f"media-{time.time_ns()}"
            line.tx.media[mid] = (b["audio_text"].encode(), "audio/ogg")
            m.update(kind="audio", media_id=mid, mime="audio/ogg")
        if b.get("file_text"):
            mid = f"media-{time.time_ns()}"
            line.tx.media[mid] = (b["file_text"].encode(), "application/pdf")
            m.update(kind="document", media_id=mid, mime="application/pdf")
        shown = b.get("text") or ("🎤 " + b["audio_text"] if b.get("audio_text") else "📄 report.pdf" if b.get("file_text") else "")
        typed.append({"wa": b["wa"], "pos": len(line.tx.sent), "body": shown, "mine": True})
        line.handle(m)
        return {"ok": True}

    typed: list[dict] = []

    @app.get("/api/sim/log")
    def sim_log(wa: str):
        w = line.store.get("wa", wa) or {}
        out = []
        for i, s in enumerate(line.tx.sent + [None]):
            out += [t for t in typed if t["wa"] == wa and t["pos"] == i]
            if s and s["to"] == wa:
                out.append(s | {"graph": None})
        return {"sent": out, "outbox": len(w.get("outbox", []))}

    @app.post("/api/sim/clock")
    async def sim_clock(request: Request):
        b = await request.json()
        clock["now"] = datetime.fromisoformat(b["at"]) if b.get("at") else None
        return {"now": now().isoformat()}

    @app.post("/api/sim/tick")
    def sim_tick():
        return {"sent": len(line.tick(now()))}

    @app.post("/api/sim/pay")
    def sim_pay():
        pend = [p for p in line.store.all("payment") if p["status"] == "pending"]
        for p in pend:
            line.paid(p["id"], now())
        return {"paid": len(pend)}

    @app.get("/api/sim/login")
    def sim_login():
        d = line.store.one("doctor", code="TH01")
        return {"url": line.login_link(d, now()).replace(cfg.base_url, "")}


def _esc(s) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
