# EC Phase 0: sample report

What EC gets back from the Phase 0 look-back (`docs/EC_DATA_REQUEST.md`, "What EC gets back"), made by the real
look-back from a simulated export in EC's format. The simulated clinic is a group practice: 5 doctors and 2,750
synthetic patients over one simulated year (LoopBench `hk_group` on Apricot VITA, seed 1). Synthetic data only, and
the page says so at the top.

- `report.html`: the report, written by `backend/lookback/report.py`.
- `owed.csv`: the keys of the patients still owed a follow-up, the list EC would turn back into patients to call.

It's for the meeting with Julie on Wed 11 Nov (#23). The ask: Phase 0 agreed in writing, with its scope, fee and how
the data reaches us.

Rebuild:

    cd ../loopbench && PYTHONPATH=src python -m loopbench.cli lookback --clinic hk_group --system apricot_vita \
        --seeds 1 --out /tmp/ec_phase0
    cd ../harness && R=/tmp/ec_phase0/hk_group_apricot_vita_s1 && python -m backend.lookback.report \
        --export $R/export --lookback $R/lookback --validation $R/score.json --out demo/ec-phase0

On EC's own export, run the second command without `--validation`: there's no answer key, so the report has no
accuracy section.
