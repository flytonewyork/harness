"""scripts/check.sh: a missing sibling is a failed gate, and a failed restore keeps the firstrun backup."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "check.sh"


def test_missing_loopbench_exits_nonzero_before_tests(tmp_path):
    harness = tmp_path / "harness"
    scripts = harness / "scripts"
    scripts.mkdir(parents=True)
    dest = scripts / "check.sh"
    shutil.copy(CHECK, dest)
    dest.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "CHECK_LIB_ONLY"}
    proc = subprocess.run(["bash", str(dest)], cwd=harness, env=env, capture_output=True, text=True, timeout=20)
    assert proc.returncode != 0
    assert "no ../loopbench" in proc.stderr
    assert "harness tests" not in proc.stdout


def _bash(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CHECK_LIB_ONLY"] = "1"
    return subprocess.run(["bash", "-c", body], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20)


def test_failed_restore_keeps_the_backup_and_does_not_drop_firstrun(tmp_path):
    live = tmp_path / "live"
    keep = tmp_path / "keep"
    (keep / "journeys").mkdir(parents=True)
    (live / "results" / "journeys").mkdir(parents=True)
    (keep / "journeys" / "med_blind_v1_firstrun.json").write_text("{kept}\n", encoding="utf-8")
    (live / "results" / "journeys" / "all.json").write_text("{eval}\n", encoding="utf-8")
    script = f'''
source "{CHECK}"
cp() {{ return 1; }}
if restore_saved_journeys "{keep}" "{live}"; then echo RESTORED; else echo KEPT; fi
'''
    proc = _bash(tmp_path, script)
    assert proc.returncode == 0, proc.stderr
    assert "KEPT" in proc.stdout
    assert "backup kept" in proc.stderr
    assert (keep / "journeys" / "med_blind_v1_firstrun.json").read_text(encoding="utf-8") == "{kept}\n"
    assert (live / "results" / "journeys" / "all.json").read_text(encoding="utf-8") == "{eval}\n"
    assert not (live / "results" / "journeys" / "med_blind_v1_firstrun.json").exists()


def test_restore_puts_firstrun_back_and_drops_the_eval_write(tmp_path):
    live = tmp_path / "live"
    keep = tmp_path / "keep"
    (keep / "journeys").mkdir(parents=True)
    (live / "results" / "journeys").mkdir(parents=True)
    (keep / "journeys" / "precare_blind_blind3_firstrun.json").write_text("{first}\n", encoding="utf-8")
    (live / "results" / "journeys" / "all.json").write_text("{eval}\n", encoding="utf-8")
    script = f'''
source "{CHECK}"
if restore_saved_journeys "{keep}" "{live}"; then echo RESTORED; else echo KEPT; fi
'''
    proc = _bash(tmp_path, script)
    assert proc.returncode == 0, proc.stderr
    assert "RESTORED" in proc.stdout
    assert (live / "results" / "journeys" / "precare_blind_blind3_firstrun.json").read_text(encoding="utf-8") == "{first}\n"
    assert not (live / "results" / "journeys" / "all.json").exists()
    assert not keep.exists()
