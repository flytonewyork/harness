"""Data in. Each adapter turns a source into the harness-run shape the API serves: LoopBench runs, the Phase 0
look-back output, and later EC's live system. The engine and API never import a source directly."""
from .lookback_output import load_lookback
from .loopbench_run import load_run

__all__ = ["load_lookback", "load_run"]
