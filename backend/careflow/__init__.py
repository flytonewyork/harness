"""In-process care-flow contract. US-00 consent, fixed crisis and STOP acts, and the shared hard fails.

This is not the care-line service. It does not send WhatsApp and it does not compose patient-facing text.
A non-template send is not a hard fail. Consent, STOP, and crisis / 999 stay on fixed templates.
"""
from .flow import CareFlow

__all__ = ["CareFlow"]
