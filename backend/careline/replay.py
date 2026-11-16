"""Play-back testing: re-run recorded WhatsApp conversations through the current agent and show what changed.

    python -m backend.careline.replay --tape ~/.tmrw-careline/webhook-tape.jsonl --run <harness-run json> [--as-of ...]

The tape is what the webhook recorded (inbound message + the replies sent). Each inbound goes through a fresh agent
at its original time against the simulated clinic; a reply that differs from the recorded one is printed, so a change
to texts, routing or a model shows up as a diff before it reaches a patient. Strangers' messages are never on the tape.
"""
from __future__ import annotations

import argparse
import difflib
import json
from datetime import datetime

from .agent import CareAgent, Inbound
from .clinic import SimClinic
from .intents import make
from .settings import Settings
from .store import CareStore
from .whatsapp import to_graph


def _text(g: dict) -> str:
    if g["type"] == "text":
        return g["text"]["body"]
    i = g["interactive"]
    opts = [b["reply"]["title"] for b in i["action"].get("buttons", [])] + [r["title"] for s in i["action"].get("sections", []) for r in s["rows"]]
    return i["body"]["text"] + "\n[" + " | ".join(opts) + "]"


def replay(tape: str, run: str, as_of: str | None = None, intent: str | None = None) -> dict:
    s = Settings.load()
    agent = CareAgent(SimClinic(run, s, **({"as_of": as_of} if as_of else {})), s, CareStore(s), intents=make(intent))
    same = changed = 0
    diffs = []
    for line in open(tape, encoding="utf-8"):
        rec = json.loads(line)
        m = rec["in"]
        inb = Inbound(wa=m["wa"], text=m["text"], reply_id=m.get("reply_id"), kind=m["kind"], msg_id=m.get("msg_id"),
                      at=datetime.fromisoformat(m["at"]) if m.get("at") else datetime.fromisoformat(rec["at"]))
        now_out = [_text(to_graph(o)) for o in agent.handle(inb)]
        was = [_text(g) for g in rec["out"]]
        if now_out == was:
            same += 1
        else:
            changed += 1
            diffs.append({"in": m["text"] or m.get("reply_id"), "diff": "\n".join(difflib.unified_diff("\n".join(was).splitlines(), "\n".join(now_out).splitlines(), "recorded", "now", lineterm=""))})
    return {"messages": same + changed, "same": same, "changed": changed, "diffs": diffs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tape", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--as-of")
    ap.add_argument("--intent")
    a = ap.parse_args()
    r = replay(a.tape, a.run, a.as_of, a.intent)
    for d in r["diffs"]:
        print(f"--- {d['in']!r}\n{d['diff']}\n")
    print(f"{r['messages']} messages replayed: {r['same']} same, {r['changed']} changed")


if __name__ == "__main__":
    main()
