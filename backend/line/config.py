"""Settings from the environment. Secrets come from Secret Manager in the live deployment, never from git."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

MODES = ("sim", "staging", "live")

# Hong Kong general holidays the line treats as closed unless a doctor's hours say otherwise. Doctors can add their own.
HK_HOLIDAYS = ["2026-12-25", "2026-12-26", "2027-01-01", "2027-02-06", "2027-02-08", "2027-02-09", "2027-03-26",
               "2027-03-27", "2027-03-29", "2027-04-05", "2027-05-01", "2027-05-13", "2027-06-09", "2027-07-01"]


@dataclass
class Config:
    mode: str = "sim"
    base_url: str = "http://localhost:8080"          # public https URL in staging and live
    timezone: str = "Asia/Hong_Kong"
    service_fee_hkd: int = 30                         # tmrw's fee to the patient on a paid follow-up (10 Nov decision)
    subscription_hkd: int = 0                         # the doctor subscription: a setting, off (0) during the pilot
    shadow_subscription_hkd: int = 480                # what the dashboard shows a subscription would have cost
    payments: bool = False                            # Stripe on; off in sim unless a fake is wired
    store: str = "memory"                             # memory | sqlite:<path> | firestore
    store_prefix: str = "line_"                       # Firestore collection prefix: line_ (live), stg_ (staging)
    model: str = "fake"                               # fake | vertex
    vertex_project: str = ""
    vertex_region: str = "asia-east2"
    vertex_model: str = "gemini-2.5-flash"
    wa_number: str = ""                               # the shared number, digits only, e.g. 85291234567
    wa_token: str = ""
    wa_phone_number_id: str = ""
    wa_app_secret: str = ""
    wa_verify_token: str = ""
    stripe_secret: str = ""
    stripe_webhook_secret: str = ""
    cron_secret: str = ""
    session_secret: str = "dev-only-not-secret"
    admin_token: str = ""
    media_bucket: str = ""                            # gs://… in live; a local folder otherwise
    media_dir: str = ".line-media"
    testers: set = field(default_factory=set)         # staging: only these numbers (and doctors) reach the agent
    signed_off: bool = False                          # live needs CARELINE_I_HAVE_SIGN_OFF=yes

    @classmethod
    def from_env(cls, **over) -> "Config":
        e = os.environ.get
        c = cls(
            mode=e("LINE_MODE", "sim"), base_url=e("LINE_BASE_URL", "http://localhost:8080").rstrip("/"),
            service_fee_hkd=int(e("LINE_SERVICE_FEE_HKD", "30")), subscription_hkd=int(e("LINE_SUBSCRIPTION_HKD", "0")),
            shadow_subscription_hkd=int(e("LINE_SHADOW_SUBSCRIPTION_HKD", "480")),
            payments=e("LINE_PAYMENTS", "off") == "on", store=e("LINE_STORE", "memory"), store_prefix=e("LINE_STORE_PREFIX", "line_"), model=e("LINE_MODEL", "fake"),
            vertex_project=e("VERTEX_PROJECT", e("GOOGLE_CLOUD_PROJECT", "")), vertex_region=e("VERTEX_REGION", "asia-east2"),
            vertex_model=e("VERTEX_MODEL", "gemini-2.5-flash"),
            wa_number=e("WA_NUMBER", ""), wa_token=e("WA_TOKEN", ""), wa_phone_number_id=e("WA_PHONE_NUMBER_ID", ""),
            wa_app_secret=e("WA_APP_SECRET", ""), wa_verify_token=e("WA_VERIFY_TOKEN", ""),
            stripe_secret=e("STRIPE_SECRET_KEY", ""), stripe_webhook_secret=e("STRIPE_WEBHOOK_SECRET", ""),
            cron_secret=e("LINE_CRON_SECRET", ""), session_secret=e("LINE_SESSION_SECRET", "dev-only-not-secret"),
            admin_token=e("LINE_ADMIN_TOKEN", ""), media_bucket=e("LINE_MEDIA_BUCKET", ""),
            media_dir=e("LINE_MEDIA_DIR", ".line-media"),
            testers={x.strip() for x in e("LINE_TESTERS", "").split(",") if x.strip()},
            signed_off=e("CARELINE_I_HAVE_SIGN_OFF", "") == "yes",
        )
        for k, v in over.items():
            setattr(c, k, v)
        c.check()
        return c

    def check(self):
        if self.mode not in MODES:
            raise ValueError(f"LINE_MODE must be one of {MODES}")
        if self.mode == "live" and not self.signed_off:
            raise SystemExit("live mode needs CARELINE_I_HAVE_SIGN_OFF=yes (spec 005: the doctor signs off)")
        if self.mode in ("staging", "live"):
            missing = [k for k in ("wa_token", "wa_phone_number_id", "wa_app_secret", "wa_verify_token", "wa_number",
                                   "cron_secret", "session_secret") if not getattr(self, k)]
            if self.session_secret == "dev-only-not-secret":
                missing.append("session_secret")
            if self.payments:
                missing += [k for k in ("stripe_secret", "stripe_webhook_secret") if not getattr(self, k)]
            if missing:
                raise SystemExit(f"{self.mode} mode is missing: {', '.join(sorted(set(missing)))}")
        if self.mode == "live" and (self.store == "memory" or self.model == "fake"):
            raise SystemExit("live mode needs a lasting store (firestore) and a real model (vertex)")
