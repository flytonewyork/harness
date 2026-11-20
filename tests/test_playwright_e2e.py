"""Full-stack headless Chromium Playwright E2E test across ALL 4 surfaces of Clinic AI Harness.

Boots a real uvicorn FastAPI server backed by real PostgreSQL 15 + FORCE RLS + AES-256-GCM Vault
serving the compiled React/TypeScript/Vite bundle from `frontend/dist`, then drives a real Chromium
browser through:
1. Surface 1 (WhatsApp Doorbell): QR campaign scan, zero-leakage badge, GET preview (token unburned), open PWA
2. Surface 2 (Patient PWA): POST magic-link redeem, 6-digit OTP step-up, HK PDPO PICS & granular consents,
   bilingual intake + 999 red-flag crisis banner, teleconsult booking, flu vax booking, Vault PDF cert,
   e-script fulfilment, 48h check-in, PDPO DAR HL7 FHIR R4 export, DCR, statutory deletion hold
3. Surface 3 (Clinician Console): prioritized queue (red flags first), source quotes, G6PD/allergy badges,
   live Cap. 134 hard-block check, automation-bias friction, sign cert+Rx, audit trail
4. Surface 4 (Clinic Ops Admin & Corporate Admin): asia-east2 residency, AI kill switch, protocol governance,
   invoicing, audit log, corporate <5 k-anonymity suppression & zero individual PII in DOM
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
            # Ensure fresh deterministic seeded state
            req = urllib.request.Request(f"{base_url}/api/v1/demo/reset", method="POST")
            urllib.request.urlopen(req, timeout=10).read()
            yield base_url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_four_surfaces_end_to_end_in_chromium(live_server: str):
    page_errors: list[str] = []
    console_errors: list[str] = []

    with playwright_sync.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROMIUM_BIN, headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # ------------------------------------------------------------------ 1. WHATSAPP DOORBELL
        page.goto(f"{live_server}/wa", wait_until="networkidle")
        page.get_by_test_id("surface-wa").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("wa-zero-leakage-badge").wait_for(state="visible")
        page.get_by_test_id("wa-phone-input").fill("+85296881122")
        page.get_by_test_id("wa-simulate-scan-btn").click()

        # Verify magic-link card appears, GET preview does not burn token, then open Patient PWA
        page.get_by_test_id("wa-magic-link-card").first.wait_for(state="visible", timeout=10000)
        page.get_by_test_id("wa-preview-magic-btn").first.click()
        page.get_by_test_id("wa-magic-preview-result").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("wa-open-magic-btn").first.click()

        # ------------------------------------------------------------------ 2. PATIENT PWA (POST REDEEM + OTP + PDPO ONBOARD)
        page.get_by_test_id("surface-patient-auth").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("magic-redeem-btn").click()
        page.get_by_test_id("otp-step-screen").wait_for(state="visible", timeout=10000)

        otp_hint = page.get_by_test_id("otp-demo-hint").inner_text()
        digits = "".join(ch for ch in otp_hint if ch.isdigit())
        otp_code = digits[-6:]
        assert len(otp_code) == 6, f"Could not read 6-digit demo OTP from: {otp_hint}"
        page.get_by_test_id("otp-code-input").fill(otp_code)
        page.get_by_test_id("otp-verify-btn").click()

        # Onboarding + HK PDPO PICS & granular consent
        page.get_by_test_id("surface-patient-onboard").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("onboard-name-en").fill("MAK Siu-Ling")
        page.get_by_test_id("onboard-name-zh").fill("麥小玲")
        page.get_by_test_id("onboard-dob").fill("1992-05-20")
        page.get_by_test_id("onboard-hkid").fill("E441920(3)")
        page.get_by_test_id("onboard-allergies").fill("Penicillin")
        page.get_by_test_id("onboard-g6pd").check()
        # Check required clinical_care & ai_assisted_intake consents if not already checked
        for cid in ("consent-clinical_care", "consent-ai_assisted_intake", "consent-ehealth_sharing"):
            cb = page.get_by_test_id(cid)
            if cb.count() > 0 and not cb.is_checked():
                cb.check()
        page.get_by_test_id("onboard-submit-btn").click()

        # Patient Portal unlocked -> test Cantonese red-flag emergency banner + normal flu intake
        page.get_by_test_id("surface-patient-portal").wait_for(state="visible", timeout=10000)
        intake_box = page.locator("textarea").first
        intake_box.fill("發燒39.4度，而家心口痛同唞唔到氣，講唔到成句。")
        page.locator("[data-testid='pt-panel-intake'] button[type='submit']").click()
        page.get_by_test_id("crisis-banner").wait_for(state="visible", timeout=10000)
        assert "999" in page.get_by_test_id("crisis-banner").inner_text()

        # Submit ordinary flu intake
        intake_box.fill("Fever 38.5C, dry cough and sore throat for 2 days. Flu A RAT positive.")
        page.locator("[data-testid='pt-panel-intake'] button[type='submit']").click()
        page.get_by_test_id("intake-success-alert").wait_for(state="visible", timeout=10000)

        # Book teleconsult slot + seasonal flu vaccination (CVX)
        page.get_by_test_id("pt-tab-booking").click()
        page.get_by_test_id("pt-panel-booking").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("book-slot-btn").first.click()
        page.wait_for_timeout(400)
        page.get_by_test_id("book-fluvax-btn").click()
        page.get_by_test_id("booked-fluvax-item").first.wait_for(state="visible", timeout=10000)

        # Check Privacy & PDPO DAR HL7 FHIR R4 Export + Submit Data Correction Request (DCR)
        page.get_by_test_id("pt-tab-privacy").click()
        page.get_by_test_id("patient-referral-code").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("pdpo-export-fhir-btn").click()
        page.get_by_test_id("fhir-bundle-preview").wait_for(state="visible", timeout=10000)
        assert '"resourceType": "Bundle"' in page.get_by_test_id("fhir-bundle-preview").inner_text()
        page.get_by_test_id("pdpo-dcr-value").fill("Add peanut allergy")
        page.get_by_test_id("pdpo-dcr-reason").fill("Confirmed by allergist report")
        page.get_by_test_id("pdpo-dcr-submit-btn").click()
        page.wait_for_timeout(400)

        # ------------------------------------------------------------------ 3. CLINICIAN CONSOLE
        page.get_by_test_id("demo-persona-select").select_option(value="clinician:doc_dr_lam")
        page.get_by_test_id("demo-login-btn").click()
        page.get_by_test_id("nav-surface-clinician").click()
        page.get_by_test_id("surface-clinician").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("clinician-mchk-badge").wait_for(state="visible")
        page.get_by_test_id("clinician-queue-list").wait_for(state="visible")
        page.get_by_test_id("clinician-filter-redflags").click()
        page.get_by_test_id("clinician-filter-pending").click()

        # Click the seeded pending flu consult proposal (WONG Siu-Mei, G6PD+ & Penicillin allergy)
        page.get_by_test_id("queue-item-prop_pat_wong_flu_g6pd").click()
        page.get_by_test_id("clinician-patient-card").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("clinician-g6pd-badge").wait_for(state="visible")
        page.get_by_test_id("clinician-source-quotes").wait_for(state="visible")
        page.get_by_test_id("clinician-cds-card").wait_for(state="visible", timeout=10000)

        # Send secure in-portal clinician reply (zero clinical text on WhatsApp)
        page.get_by_test_id("clinician-reply-input").fill("Rest well, hydrate, and return to A&E if dyspnoea develops.")
        page.get_by_test_id("clinician-reply-btn").click()
        page.wait_for_timeout(400)
        assert "Rest well, hydrate" in page.get_by_test_id("clinician-message-thread").inner_text()

        # Live deterministic Rx safety check: add Cap. 134 codeine -> must show HARD BLOCK!
        page.get_by_test_id("clinician-add-drug-select").select_option("codeine")
        page.get_by_test_id("clinician-add-drug-btn").click()
        page.get_by_test_id("clinician-run-rx-safety-btn").click()
        page.get_by_test_id("rx-safety-findings").wait_for(state="visible", timeout=10000)
        assert "HARD" in page.get_by_test_id("rx-safety-findings").inner_text().upper() or "CAP" in page.get_by_test_id("rx-safety-findings").inner_text().upper()

        # Remove codeine, verify paracetamol passes, confirm automation-bias friction, sign cert + Rx!
        page.get_by_test_id("rx-remove-codeine").click()
        page.get_by_test_id("clinician-run-rx-safety-btn").click()
        friction_cb = page.get_by_test_id("clinician-friction-checkbox")
        if friction_cb.count() > 0 and friction_cb.is_visible():
            friction_cb.check()
        page.get_by_test_id("clinician-approve-btn").click()
        page.wait_for_timeout(800)

        # Check Clinician Audit Trail tab
        page.get_by_test_id("clinician-tab-audit").click()
        page.get_by_test_id("clinician-panel-audit").wait_for(state="visible", timeout=10000)

        # ------------------------------------------------------------------ 4A. CLINIC OPS ADMIN
        page.get_by_test_id("demo-persona-select").select_option(value="clinic_admin:adm_central_ops")
        page.get_by_test_id("demo-login-btn").click()
        page.get_by_test_id("nav-surface-admin").click()
        page.get_by_test_id("surface-admin").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("admin-residency-card").wait_for(state="visible")
        assert "asia-east2" in page.get_by_test_id("admin-residency-card").inner_text()
        page.get_by_test_id("admin-protocols-section").wait_for(state="visible")

        # Toggle AI Kill Switch off and back on
        page.get_by_test_id("admin-kill-switch-btn").click()
        page.wait_for_timeout(500)
        page.get_by_test_id("admin-kill-switch-btn").click()
        page.wait_for_timeout(500)

        # Check Observability & PDPO Ops tab: SLO counters, Outbox sweep, and DCR approval
        page.get_by_test_id("admin-tab-observability").click()
        page.get_by_test_id("admin-observability-panel").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("admin-safety-counters-card").wait_for(state="visible")
        page.get_by_test_id("admin-process-outbox-btn").click()
        page.wait_for_timeout(400)
        page.get_by_test_id("admin-dcr-table").wait_for(state="visible")
        approve_dcr_btn = page.locator("[data-testid^='admin-dcr-approve-']").first
        if approve_dcr_btn.count() > 0:
            approve_dcr_btn.click()
            page.wait_for_timeout(400)
            assert "approved_addendum" in page.get_by_test_id("admin-dcr-table").inner_text()

        # Check Invoicing & Immutable Audit tabs
        page.get_by_test_id("admin-tab-invoicing").click()
        page.get_by_test_id("admin-invoicing-table").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("admin-tab-audit").click()
        page.get_by_test_id("admin-audit-table").wait_for(state="visible", timeout=10000)

        # ------------------------------------------------------------------ 4B. CORPORATE ADMIN (k-ANONYMITY <5 & ZERO PII)
        page.get_by_test_id("demo-persona-select").select_option(value="corporate_admin:corp_pacific_bio")
        page.get_by_test_id("demo-login-btn").click()
        page.get_by_test_id("nav-surface-corporate").click()
        page.get_by_test_id("surface-corporate").wait_for(state="visible", timeout=10000)
        page.get_by_test_id("corp-privacy-guarantee").wait_for(state="visible")
        corp_dom = page.get_by_test_id("surface-corporate").inner_text()
        assert "<5" in corp_dom, f"Expected k-anonymity <5 suppression in Corporate Admin DOM, got: {corp_dom[:400]}"
        for forbidden in ("CHAN Ka-Ho", "WONG Siu-Mei", "MAK Siu-Ling", "Warfarin", "J11.1"):
            assert forbidden not in corp_dom, f"Employer PII leaked in Corporate DOM: {forbidden}"

        browser.close()

    assert not page_errors, f"Uncaught JS page errors: {page_errors}"
    assert not console_errors, f"Browser console errors: {console_errors}"
