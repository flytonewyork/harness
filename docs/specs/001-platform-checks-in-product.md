# 001: The seven platform checks, inside the product

**Problem.** Every item the engine opens is meant to be reviewable like a pull request: the platform re-derives,
from only what the engine could see, that the item stands up — the seven checks `evidence`, `quotes`, `identity`,
`searches`, `duplicate`, `owner`, `reasoning` (and the two hard fails a made-up quote or an unseen citation trip).
Today those checks run **only inside LoopBench**: the test bed's simulator verifies each item as its host
(`loopbench/src/loopbench/verify.py`, called from `sim.open_item`). The product itself has no verifier. When the
engine runs against real data instead of the simulator — the EC Phase 0 look-back on a real export is next
(`NOW.md`, Next 3; `docs/OPERATING_SYSTEM.md`, "Two kinds of eval") — nothing checks the items it opens. The
observability that catches a made-up quote or a citation the engine could not have seen would not exist at the
moment real data first arrives, which is exactly when it is needed.

**Change.** The product gains its own copy of the seven checks and runs them on **every item the engine opens**,
using only what the engine could see through the port — no hidden simulator state, no import of the test bed. A
thin `VerifyingClinic` wraps any `ClinicPort`: it remembers every record the engine is shown (the port's stand-in
for the simulator's "visible" set), and on each `open_item` it computes the seven checks and records them, plus any
hard fail (a quote not found word for word in its record, or a citation the engine could not have seen). The engine
is unchanged; it still only opens and raises. The doctor-facing item already carries a `checks` array
(`backend/api/store.py`) — this fills it for runs the product drives itself, the same way LoopBench fills it today.

**Done when.** A test proves that for every item opened through `VerifyingClinic` the seven checks are computed and
recorded; that a well-formed item passes all of them; that a made-up quote and an unseen citation each surface as a
hard fail (`unsupported_claim`, `unsupported_evidence`); and that identity mismatch, duplicates and an away owner
are flagged as the simulator flags them. The eval is `tests/test_verify.py`, written before the wiring. Because the
layer only observes and is not inserted into the LoopBench scoring path, the scoreboard is unchanged: hard fails and
made-up quotes stay 0 and no number moves (`cd ../loopbench && PYTHONPATH=src python scripts/scoreboard.py --seeds 1`).

**Invariants touched.**
- *Authority line* — preserved. `VerifyingClinic` only observes; it delegates `open_item`, `raise_urgency`,
  `alarm` and `attest_all_clear` unchanged and adds no close, dismiss, downgrade or reassure.
- *Verbatim quotes* — enforced here: quotes are checked word for word against the cited record; a quote that is not
  in its record is the hard fail `unsupported_claim`.
- *Rules floor* — untouched. The wrapper changes no engine decision (same items, urgencies, owners, evidence,
  reasoning); it is observational and is not inserted when LoopBench hosts the engine, so v1.0 stays comparable.
- *No LoopBench imports* — the seven checks are re-implemented in `backend/engine/verify.py` from port-visible data;
  nothing is imported from `loopbench`. The existing guard (`tests/test_engine.py::test_engine_never_imports_loopbench`)
  scans all of `backend/` and must stay green.
- *Synthetic data* — the test uses opaque, fictional records only.

**Out of scope.** A live or replay EC adapter and the loop that drives the engine against it (this ships the
verifier the driver will use, not the driver). Persisting the computed checks into the look-back output
(`backend/adapters/lookback_output.py`) or the HTTP agent endpoint (`backend/api/store.py`). Any change to the
doctor model, to the look-back's retrospective flag logic, or to LoopBench's own `verify.py`.
