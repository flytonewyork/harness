"""python -m backend.api --run results/product/<run>.json [--port 8000]
python -m backend.api --lookback <look-back out dir>[,<export dir>]"""
import argparse
import os

import uvicorn


def main():
    ap = argparse.ArgumentParser(prog="python -m backend.api")
    ap.add_argument("--run", help="harness-run/1 file written by LoopBench (loopbench product-feed)")
    ap.add_argument("--lookback", help="look-back out dir[,export dir]")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    if a.lookback:
        os.environ["HARNESS_LOOKBACK"] = a.lookback
    elif a.run:
        os.environ["HARNESS_RUN"] = a.run
    else:
        ap.error("give --run or --lookback")
    uvicorn.run("backend.api.app:app_from_env", factory=True, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
