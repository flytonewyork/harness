"""In-process care-flow contract. US-00 consent, fixed crisis and STOP acts, and the shared hard fails.

This is not the care-line service and it does not send WhatsApp. A non-template send is not a hard fail.
Consent, STOP, and crisis / 999 stay on fixed templates. For these two weeks any other reply is a draft
the doctor taps to send. `CareLine` is that path: a synthetic message goes in, and the draft stays held.
"""
from .flow import CareFlow
from .line import CareLine

__all__ = ["CareFlow", "CareLine"]
