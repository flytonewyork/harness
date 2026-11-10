"""Run and manage the line.

    python -m backend.line serve                        # reads LINE_* settings from the environment (sim by default)
    python -m backend.line sim                          # the two-phone simulator at http://localhost:8080/sim
    python -m backend.line add-doctor --id D1 --code TH01 --name-en "Dr Thomas Hu" --name-zh 胡醫生 \\
        --short-en "Dr Hu" --wa 85291234567 --clinic "Harbour Clinic|海港診所|1 Test Road|測試路1號|2000 0000" \\
        --repeat-fee 200 --result-fee 300
    python -m backend.line templates submit|status      # WhatsApp templates (needs WA_TOKEN and WA_WABA_ID)
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m backend.line")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    s = sub.add_parser("sim")
    s.add_argument("--port", type=int, default=8080)
    a = sub.add_parser("add-doctor")
    for k in ("id", "code", "name-en", "wa"):
        a.add_argument(f"--{k}", required=True)
    for k in ("name-zh", "short-en", "short-zh", "email"):
        a.add_argument(f"--{k}", default="")
    a.add_argument("--clinic", action="append", default=[], help="name_en|name_zh|address_en|address_zh|phone")
    a.add_argument("--repeat-fee", type=int, default=0)
    a.add_argument("--result-fee", type=int, default=0)
    a.add_argument("--hours", default="", help='JSON, e.g. {"mon":[["09:00","13:00"]],...}')
    t = sub.add_parser("templates")
    t.add_argument("action", choices=("submit", "status"))
    args = ap.parse_args(argv)

    if args.cmd in ("serve", "sim"):
        import uvicorn
        from .api import create_app
        from .config import Config
        cfg = Config.from_env(**({"mode": "sim", "payments": True} if args.cmd == "sim" else {}))
        if args.cmd == "sim":
            cfg.base_url = f"http://localhost:{args.port}"
        print(f"care line: mode={cfg.mode} store={cfg.store} model={cfg.model} on :{args.port}", file=sys.stderr)
        uvicorn.run(create_app(cfg), host="0.0.0.0", port=args.port, log_level="info")
        return

    if args.cmd == "add-doctor":
        from .config import Config
        from .hours import clock
        from .store import open_store
        cfg = Config.from_env()
        st = open_store(cfg.store, cfg.vertex_project, cfg.store_prefix)
        clinics = []
        for i, c in enumerate(args.clinic, 1):
            f = (c.split("|") + [""] * 5)[:5]
            clinics.append({"id": f"c{i}", "name_en": f[0], "name_zh": f[1], "address_en": f[2], "address_zh": f[3], "phone": f[4]})
        d = {**(st.get("doctor", args.id) or {}), "id": args.id, "code": args.code, "name_en": args.name_en, "wa": args.wa.lstrip("+"),
             "name_zh": args.name_zh, "short_en": args.short_en or args.name_en, "short_zh": args.short_zh or args.name_zh,
             "email": args.email, "fees": {"repeat": args.repeat_fee, "result": args.result_fee}}
        if clinics:
            d["clinics"] = clinics
        if args.hours:
            d["hours"] = json.loads(args.hours)
        clock(d)
        st.put("doctor", d["id"], d)
        print(f"saved {d['id']} ({d['name_en']}); QR card: {cfg.base_url}/j/{d['code']}")
        return

    if args.cmd == "templates":
        import httpx
        from .templates import TEMPLATES, payload_body
        token, waba = os.environ["WA_TOKEN"], os.environ["WA_WABA_ID"]
        url = f"https://graph.facebook.com/{os.environ.get('WA_API_VERSION', 'v21.0')}/{waba}/message_templates"
        h = {"Authorization": f"Bearer {token}"}
        if args.action == "submit":
            for name, t in TEMPLATES.items():
                for lang in [k for k in t if k != "category"]:
                    r = httpx.post(url, headers=h, json=payload_body(name, lang), timeout=20)
                    print(name, lang, r.status_code, r.text[:200])
        else:
            r = httpx.get(url, headers=h, params={"limit": 100, "fields": "name,language,status,category"}, timeout=20).json()
            for t in r.get("data", []):
                if t["name"] in TEMPLATES:
                    print(f"{t['name']:18} {t['language']:6} {t['status']:10} {t['category']}")


if __name__ == "__main__":
    main()
