"""CLI entrypoint for the 24/7 Autonomous Cloud Infra SRE Agent.

Usage:
  python -m backend.sre --once                   # run one observe/diagnose/remediate tick
  python -m backend.sre --daemon --interval 60   # run 24/7 continuous on-call loop
  python -m backend.sre --serve --port 8081      # run FastAPI control plane + Mission Control UI
"""
from __future__ import annotations

import argparse
import json
import time

from .agent import AutonomousSREAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="tmrw 24/7 Autonomous Cloud Infra SRE Agent")
    parser.add_argument("--once", action="store_true", help="Run a single check/self-heal tick and exit")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in a 24/7 daemon loop")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between daemon ticks (default: 60)")
    parser.add_argument("--serve", action="store_true", help="Serve FastAPI control plane + Mission Control UI")
    parser.add_argument("--port", type=int, default=8081, help="HTTP port for --serve")
    args = parser.parse_args()

    if args.serve:
        import uvicorn

        uvicorn.run("backend.sre.app:app_from_env", factory=True, host="0.0.0.0", port=args.port)
        return

    agent = AutonomousSREAgent()
    if args.daemon:
        while True:
            res = agent.tick()
            print(json.dumps(res, ensure_ascii=False), flush=True)
            time.sleep(max(10, args.interval))
    else:
        res = agent.tick()
        print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
