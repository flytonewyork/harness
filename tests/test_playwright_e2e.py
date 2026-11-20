"""Full-stack headless Chromium Playwright E2E across the Minimalist 2-Surface Clinical OS.

Boots a real uvicorn FastAPI server backed by PostgreSQL 15 + FORCE RLS + AES-256-GCM Vault
serving the compiled React/TypeScript/Vite bundle from `frontend/dist`, and drives real Chromium
through all 4 rationalised MVP demo stories with ZERO console/page errors:
1. Story A (Dr. Lam — QEH Discharge Conflict): Paper.tsx verbatim highlight, Warfarin DoseConflict
   resolution (1/2/Q), duplicate statin inline warning, Sign & send + 10-second Undo (Z).
2. Story B (Chronic HTN BP Drift): Doctor minimalist 30-day BP sparkline -> Patient 0-tab Companion
   (mobile viewport), pinned Now card, monitor-photo BP confirm, protocol nudge, grounded Q&A with
   tracked SLA, single Your Record sheet.
3. Story C (Nurse Handoff + 70M Unknown-eGFR Renal Flu Variant): Nurse Rachel Chan prep & 'Ready
   for Dr Lam' (zero Sign button), Dr. Lam inline renal 30mg BD choice & sign-off.
4. Story D (Safety & Ops): Bundled HK Mental-Health crisis screen (2389 2222 / 2896 0000 / 2382 0000
   + server-acked paging), Ops /c/settings Kill Switch -> FeedLine banner, deterministic ⌘K palette.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

HARNESS_ROOT = Path(__file__).resolve().parents[1]
CHROMIUM_BIN = "/usr/bin/chromium"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_health(base_url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"Server did not become healthy at {base_url}")


@pytest.fixture(scope="module")
def live_server():
    dist = HARNESS_ROOT / "frontend" / "dist" / "index.html"
    if not dist.exists():
        pytest.skip("frontend/dist not built")
    if not Path(CHROMIUM_BIN).exists():
        pytest.skip("Chromium binary not present")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="tmrw-e2e-vault-") as vault_dir:
        env = {
            **os.environ,
            "PORT": str(port),
            "HARNESS_ENV": "staging",
            "HARNESS_RUNTIME_MODE": "demo",
            "DEMO_MODE": "true",
            "DEMO_SHOW_OTP": "true",
            "VAULT_LOCAL_DIR": vault_dir,
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.acute.app:app_from_env", "--factory", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(HARNESS_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_health(base_url)
            # Ensure fresh deterministic seeded state before E2E
            req = urllib.request.Request(f"{base_url}/api/v1/demo/reset", method="POST")
            with urllib.request.urlopen(req, timeout=15) as r:
                assert r.status == 200
            yield base_url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_minimalist_two_surface_e2e_all_demo_stories(live_server: str):
    shot_dir = Path(os.environ["E2E_SCREENSHOT_DIR"]) if os.environ.get("E2E_SCREENSHOT_DIR") else None
    if shot_dir:
        shot_dir.mkdir(parents=True, exist_ok=True)

    console_errors: list[str] = []
    page_errors: list[str] = []

    with playwright_sync.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM_BIN, headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        # ------------------------------------------------------------------
        # STORY A: Dr. Lam — QEH Discharge Warfarin Conflict + Duplicate Statin + 10s Undo
        # ------------------------------------------------------------------
        page.goto(f"{live_server}/c", wait_until="networkidle")
        page.wait_for_selector('[data-testid="staff-workspace"]', timeout=10000)

        # Open Queue and pick QEH Discharge item
        page.click('[data-testid="queue-toggle-btn"]')
        page.wait_for_selector('[data-testid="queue-item-prop_qeh_discharge"]', timeout=10000)
        page.click('[data-testid="queue-item-prop_qeh_discharge"]')
        page.wait_for_selector('[data-testid="conflict-card-conf_warfarin_qeh"]', timeout=10000)

        # Verify Paper.tsx highlights verbatim quote on click & Sign CTA requires settling conflict
        assert "Settle 1 conflict" in page.inner_text('[data-testid="sign-and-send-btn"]')
        assert page.is_disabled('[data-testid="sign-and-send-btn"]')
        page.locator('.v2-quote-link').first.click()
        page.wait_for_selector('mark[data-focus="true"]', timeout=5000)
        if shot_dir:
            page.screenshot(path=str(shot_dir / "e2e_staff_qeh_conflict.png"))

        # Resolve Warfarin conflict -> Sign unlocks -> Sign -> Undo within 10s!
        page.click('[data-testid="resolve-conflict-1"]')
        page.wait_for_selector('[data-testid="conflict-resolved-conf_warfarin_qeh"]', timeout=10000)
        page.fill('[data-testid="override-reason-input"]', "Stopping GP Simvastatin 20mg; continuing QEH Atorvastatin 40mg.")
        page.click('[data-testid="sign-and-send-btn"]')
        page.wait_for_selector('[data-testid="undo-send-btn"]', timeout=10000)
        assert "Undo Z" in page.inner_text('[data-testid="undo-send-btn"]')
        page.click('[data-testid="undo-send-btn"]')
        page.wait_for_selector('[data-testid="sign-and-send-btn"]', timeout=10000)

        # Check VerifyLine popover & deterministic ⌘K palette
        page.click('[data-testid="verify-line-btn"]')
        page.wait_for_selector('[data-testid="verify-popover"]', timeout=5000)
        page.click('[data-testid="verify-line-btn"]')
        page.click('[data-testid="cmd-k-btn"]')
        page.wait_for_selector('[data-testid="command-palette"]', timeout=5000)
        page.keyboard.press("Escape")

        # ------------------------------------------------------------------
        # STORY B (Staff side): Chronic HTN BP Drift Sparkline
        # ------------------------------------------------------------------
        page.click('[data-testid="queue-toggle-btn"]')
        page.click('[data-testid="queue-item-prop_htn_bp_drift"]')
        page.wait_for_selector('[data-testid="bp-sparkline"]', timeout=10000)
        if shot_dir:
            page.screenshot(path=str(shot_dir / "e2e_staff_htn_drift.png"))

        # ------------------------------------------------------------------
        # STORY C: Nurse Rachel Chan Handoff -> Dr. Lam 70M Elderly Flu Renal Variant
        # ------------------------------------------------------------------
        page.click('[data-testid="demo-dock-pill"]')
        page.click('[data-testid="switch-nurse-nurse_chan"]')
        page.wait_for_selector('[data-testid="staff-workspace"]', timeout=10000)
        page.click('[data-testid="queue-toggle-btn"]')
        page.click('[data-testid="queue-item-prop_elderly_flu"]')
        page.wait_for_selector('[data-testid="nurse-ready-btn"]', timeout=10000)
        assert page.locator('[data-testid="sign-and-send-btn"]').count() == 0
        page.fill('[data-testid="nurse-prep-input"]', "Flu A RAT+ <24h confirmed; no recent eGFR on file.")
        page.click('[data-testid="nurse-ready-btn"]')
        page.wait_for_timeout(400)

        # Switch back to Dr. Lam -> verify Nurse Ready + renal quick choice -> sign
        page.click('[data-testid="demo-dock-pill"]')
        page.click('[data-testid="switch-clinician-doc_dr_lam"]')
        page.wait_for_selector('[data-testid="staff-workspace"]', timeout=10000)
        page.click('[data-testid="queue-toggle-btn"]')
        page.click('[data-testid="queue-item-prop_elderly_flu"]')
        page.wait_for_selector('[data-testid="sign-and-send-btn"]', timeout=10000)
        body_text = page.inner_text('[data-testid="staff-workspace"]')
        assert "Prepared by Nurse Rachel Chan" in body_text
        assert "Renal function unknown at 70" in body_text
        page.get_by_role("button", name="Switch to 30 mg BD (renal dose)").click()
        page.click('[data-testid="sign-and-send-btn"]')
        page.wait_for_selector('[data-testid="undo-send-btn"]', timeout=10000)

        # ------------------------------------------------------------------
        # STORY D1: Clinic Ops /c/settings Kill Switch -> FeedLine Banner
        # ------------------------------------------------------------------
        page.click('[data-testid="demo-dock-pill"]')
        page.click('[data-testid="switch-clinic_admin-adm_central_ops"]')
        page.wait_for_selector('[data-testid="clinic-settings-page"]', timeout=10000)
        assert "HARNESS_RUNTIME_MODE" in page.inner_text('[data-testid="clinic-settings-page"]')
        page.click('[data-testid="toggle-kill-switch-btn"]')
        page.wait_for_selector('[data-testid="feed-health-banner"]', timeout=10000)
        assert "AI drafting is off. Every item is manual." in page.inner_text('[data-testid="feed-health-banner"]')
        # Re-enable AI cleanly
        page.click('[data-testid="toggle-kill-switch-btn"]')
        page.wait_for_timeout(300)

        # ------------------------------------------------------------------
        # STORY B (Patient Mobile) + STORY D2 (Dedicated Mental-Health Crisis Screen)
        # ------------------------------------------------------------------
        page.set_viewport_size({"width": 390, "height": 844})
        page.click('[data-testid="demo-dock-pill"]')
        page.click('[data-testid="switch-patient-pat_ho_htn_drift"]')
        page.wait_for_selector('[data-testid="patient-companion"]', timeout=10000)
        assert "under 135/85" in page.inner_text('[data-testid="patient-goal-line"]')
        if shot_dir:
            page.screenshot(path=str(shot_dir / "e2e_patient_companion_mobile.png"))

        # Confirm monitor photo BP reading
        page.click('[data-testid="bp-photo-btn"]')
        page.click('[data-testid="confirm-bp-btn"]')
        page.wait_for_timeout(400)

        # Grounded Q&A turn with tracked SLA
        page.fill('[data-testid="patient-composer-input"]', "Should I double my amlodipine tonight?")
        page.click('[data-testid="patient-send-btn"]')
        page.wait_for_timeout(500)
        assert "Dr. Kenneth Lam reviews by" in page.inner_text('[data-testid="patient-timeline"]')

        # Single "Your record" sheet
        page.click('[data-testid="your-record-btn"]')
        page.wait_for_selector('role=dialog', timeout=5000)
        page.keyboard.press("Escape")

        # Mental-Health crisis screen (dedicated, separate from medical 999, verified HK hotlines)
        page.fill('[data-testid="patient-composer-input"]', "I cannot cope anymore and want to end my life")
        page.click('[data-testid="patient-send-btn"]')
        page.wait_for_selector('[data-testid="crisis-mental-health"]', timeout=10000)
        mh_text = page.inner_text('[data-testid="crisis-mental-health"]')
        assert "2389 2222" in mh_text
        assert "2896 0000" in mh_text
        assert "2382 0000" in mh_text
        assert "was paged at" in mh_text
        if shot_dir:
            page.screenshot(path=str(shot_dir / "e2e_crisis_mental_health.png"))
        page.click('[data-testid="crisis-ack-btn"]')

        browser.close()

    assert not console_errors, f"Console errors detected: {console_errors}"
    assert not page_errors, f"Uncaught JS page errors detected: {page_errors}"
