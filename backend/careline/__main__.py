"""Run the care line.

    python -m backend.careline --run <harness-run json> [--mode sim|tester|live] [--port 8787] [--data ~/.tmrw-careline]

Settings come from the environment (put them in ~/.tmrw-careline/env on the Mac mini; never in git):
  WA_TOKEN, WA_PHONE_NUMBER_ID   Cloud API system-user token and the business number's id (tester/live)
  WA_APP_SECRET                  the Meta app secret, to check webhook signatures (tester/live)
  WA_VERIFY_TOKEN                any string; the same one goes in Meta's webhook settings
  WA_DISPLAY_NUMBER              the business number in international form, digits only (for the QR code)
  CARELINE_TESTERS               comma-separated WhatsApp numbers allowed to use the agent in tester mode
  CARELINE_INTENT                rules (default) | claude-api | claude-cli
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .agent import CareAgent
from .api import create_app
from .clinic import SimClinic
from .intents import make
from .settings import Settings
from .store import CareStore
from .whatsapp import GraphTransport, RecordingTransport


def build(run: str, mode: str, data: Path, as_of: str | None = None, intent: str | None = None):
    settings = Settings.load(os.environ.get("CARELINE_SETTINGS"))
    clinic = SimClinic(run, settings, **({"as_of": as_of} if as_of else {}))
    store = CareStore(settings, data / f"store-{mode}.json")
    testers = {n.strip().lstrip("+") for n in os.environ.get("CARELINE_TESTERS", "").split(",") if n.strip()}
    agent = CareAgent(clinic, settings, store, intents=make(intent), mode=mode, testers=testers)
    transport = RecordingTransport() if mode == "sim" else GraphTransport()
    app = create_app(agent, transport, run_path=run, mode=mode, verify_token=os.environ.get("WA_VERIFY_TOKEN", ""),
                     app_secret=os.environ.get("WA_APP_SECRET", ""), data_dir=data,
                     wa_number=os.environ.get("WA_DISPLAY_NUMBER", "").lstrip("+").replace(" ", ""))
    return app, agent


def main():
    ap = argparse.ArgumentParser(description="tmrw WhatsApp care line")
    ap.add_argument("--run", required=True)
    ap.add_argument("--mode", choices=("sim", "tester", "live"), default="sim")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--data", default=os.environ.get("CARELINE_DATA", str(Path.home() / ".tmrw-careline")))
    ap.add_argument("--as-of", help="the simulated clinic's moment (default: Sun 13 Jun 2027 09:30)")
    ap.add_argument("--intent", help="rules | claude-api | claude-cli")
    a = ap.parse_args()
    if a.mode == "live" and os.environ.get("CARELINE_I_HAVE_SIGN_OFF") != "yes":
        raise SystemExit("live mode lets any patient reach the agent. It needs the gates in spec 003 signed off and the real-data "
                         "decision in docs/DECISIONS.md; then set CARELINE_I_HAVE_SIGN_OFF=yes. Use --mode tester until then.")
    if a.mode == "tester" and not os.environ.get("CARELINE_TESTERS"):
        print("warning: CARELINE_TESTERS is empty, so every number gets only the test notice")
    data = Path(a.data).expanduser()
    data.mkdir(parents=True, exist_ok=True)
    app, _ = build(a.run, a.mode, data, a.as_of, a.intent)
    print(f"care line ({a.mode}) on http://{a.host}:{a.port}/careline · webhook /webhook/whatsapp · join page /join")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
