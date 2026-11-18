"""In-process care-flow contract. US-00 consent, fixed crisis and STOP acts, and the shared hard fails.

This is not the care-line service and it does not send WhatsApp. A non-template send is not a hard fail.
Consent, STOP, and crisis / 999 stay on fixed templates. For these two weeks any other reply is a draft
the doctor taps to send.
"""
from .flow import CareFlow

__all__ = ["CareFlow"]
