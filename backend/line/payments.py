"""Payments (10 Nov decision): the patient pays the doctor's own fee plus a HK$30 tmrw service fee, through Stripe
Connect. The doctor's fee goes to the doctor's own Stripe account; tmrw's fee is the Connect application fee, kept
apart. Nothing is charged unless the doctor has approved (repeats) or the patient has asked for a review (results).

`FakeStripe` in tests and sim; `StripeConnect` in staging (test keys) and live.
"""
from __future__ import annotations

import hashlib
import hmac
import itertools
import time


def fees(doctor_fee: int, service_fee: int) -> dict:
    return {"doctor_fee": doctor_fee, "service_fee": service_fee, "total": doctor_fee + service_fee}


def verify_stripe(payload: bytes, header: str | None, secret: str, tolerance: int = 300, now: float | None = None) -> bool:
    """Stripe-Signature: t=<ts>,v1=<hmac>. HMAC-SHA256 of '<t>.<payload>' with the endpoint secret."""
    if not header or not secret:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    t, sig = parts.get("t"), [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    if not t or not sig:
        return False
    if abs((now or time.time()) - int(t)) > tolerance:
        return False
    want = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, s) for s in sig)


class FakeStripe:
    name = "fake"

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self._n = itertools.count(1)

    def create_account(self, email: str | None = None) -> str:
        return f"acct_FAKE{next(self._n)}"

    def onboarding_link(self, account: str, refresh_url: str, return_url: str) -> str:
        return f"https://connect.stripe.test/onboard/{account}"

    def account_ready(self, account: str) -> bool:
        return True

    def checkout(self, *, account: str, doctor_fee: int, service_fee: int, title: str, payment_id: str,
                 success_url: str, cancel_url: str) -> dict:
        sid = f"cs_test_FAKE{next(self._n)}"
        self.sessions[sid] = {"account": account, "doctor_fee": doctor_fee, "service_fee": service_fee, "payment_id": payment_id}
        return {"id": sid, "url": f"https://checkout.stripe.test/{sid}"}

    def refund(self, session_id: str) -> bool:
        return True

    def subscription_checkout(self, *, account: str, doctor_fee: int, service_fee: int, title: str, payment_id: str,
                              success_url: str, cancel_url: str) -> dict:
        sid = f"cs_test_FAKESUB{next(self._n)}"
        self.sessions[sid] = {"account": account, "doctor_fee": doctor_fee, "service_fee": service_fee, "payment_id": payment_id,
                              "mode": "subscription"}
        return {"id": sid, "url": f"https://checkout.stripe.test/{sid}"}

    def cancel_subscription(self, subscription_id: str) -> bool:
        self.cancelled = getattr(self, "cancelled", []) + [subscription_id]
        return True


class StripeConnect:
    API = "https://api.stripe.com/v1"

    def __init__(self, secret: str):
        import httpx
        self.http = httpx.Client(timeout=20, auth=(secret, ""))

    def _post(self, path: str, data: dict) -> dict:
        r = self.http.post(self.API + path, data=data)
        if r.status_code >= 300:
            raise RuntimeError(f"Stripe {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def create_account(self, email: str | None = None) -> str:
        data = {"type": "express", "country": "HK", "capabilities[card_payments][requested]": "true",
                "capabilities[transfers][requested]": "true", "business_type": "individual",
                "business_profile[mcc]": "8011", "business_profile[product_description]": "Medical follow-up care between visits"}
        if email:
            data["email"] = email
        return self._post("/accounts", data)["id"]

    def onboarding_link(self, account: str, refresh_url: str, return_url: str) -> str:
        return self._post("/account_links", {"account": account, "refresh_url": refresh_url, "return_url": return_url,
                                             "type": "account_onboarding"})["url"]

    def account_ready(self, account: str) -> bool:
        r = self.http.get(f"{self.API}/accounts/{account}").json()
        return bool(r.get("charges_enabled"))

    def checkout(self, *, account, doctor_fee, service_fee, title, payment_id, success_url, cancel_url) -> dict:
        data = {
            "mode": "payment", "success_url": success_url, "cancel_url": cancel_url, "client_reference_id": payment_id,
            "metadata[payment_id]": payment_id, "payment_intent_data[metadata][payment_id]": payment_id,
            "line_items[0][quantity]": "1", "line_items[0][price_data][currency]": "hkd",
            "line_items[0][price_data][unit_amount]": str(doctor_fee * 100),
            "line_items[0][price_data][product_data][name]": title,
            "line_items[1][quantity]": "1", "line_items[1][price_data][currency]": "hkd",
            "line_items[1][price_data][unit_amount]": str(service_fee * 100),
            "line_items[1][price_data][product_data][name]": "tmrw service fee",
            "payment_intent_data[application_fee_amount]": str(service_fee * 100),
            "payment_intent_data[transfer_data][destination]": account,
            "expires_at": str(int(time.time()) + 23 * 3600),
        }
        r = self._post("/checkout/sessions", data)
        return {"id": r["id"], "url": r["url"]}

    def subscription_checkout(self, *, account, doctor_fee, service_fee, title, payment_id, success_url, cancel_url) -> dict:
        """A monthly membership: one recurring price (doctor's fee + tmrw's fee); tmrw's share is the application fee
        percentage, so the doctor's fee reaches the doctor's account in full."""
        total = doctor_fee + service_fee
        data = {
            "mode": "subscription", "success_url": success_url, "cancel_url": cancel_url, "client_reference_id": payment_id,
            "metadata[payment_id]": payment_id, "subscription_data[metadata][payment_id]": payment_id,
            "line_items[0][quantity]": "1", "line_items[0][price_data][currency]": "hkd",
            "line_items[0][price_data][unit_amount]": str(total * 100),
            "line_items[0][price_data][recurring][interval]": "month",
            "line_items[0][price_data][product_data][name]": title,
            "subscription_data[application_fee_percent]": f"{100 * service_fee / total:.2f}",
            "subscription_data[transfer_data][destination]": account,
        }
        r = self._post("/checkout/sessions", data)
        return {"id": r["id"], "url": r["url"]}

    def cancel_subscription(self, subscription_id: str) -> bool:
        r = self.http.delete(f"{self.API}/subscriptions/{subscription_id}")
        return r.status_code < 300

    def refund(self, session_id: str) -> bool:
        s = self.http.get(f"{self.API}/checkout/sessions/{session_id}").json()
        pi = s.get("payment_intent")
        if not pi:
            return False
        self._post("/refunds", {"payment_intent": pi, "refund_application_fee": "true", "reverse_transfer": "true"})
        return True
