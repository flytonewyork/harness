"""In-process care-flow contract. US-00 consent, fixed crisis and STOP acts, and the shared hard fails.

This is not the care-line service and it does not send WhatsApp. A non-template send is not a hard fail.
Consent, STOP, and crisis / 999 stay on fixed templates. For these two weeks any other reply is a draft
the doctor taps to send. `CareLine` is that path. A `DraftComposer` may write the draft, Grok first.
"""
from .flow import CareFlow
from .line import CareLine
from .models import GROK_MODEL, SONNET_MODEL, DraftComposer

__all__ = ["CareFlow", "CareLine", "DraftComposer", "GROK_MODEL", "SONNET_MODEL"]
