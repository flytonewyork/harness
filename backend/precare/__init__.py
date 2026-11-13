"""Precare: what happens before the visit (spec 006).

An automated care-team line contacts the patient before a booked appointment. It confirms who they are, asks for
consent, collects the reason for the visit and history in the patient's own words, sends the clinic's approved
preparation instructions, and opens a pre-visit brief for the doctor with every claim quoted word for word.

Same safety model as the rest of the product: the agent can only open an item, raise urgency, raise an alarm or
attest. It never closes, dismisses, downgrades, reassures, diagnoses or advises on medicines. Urgent symptoms get a
fixed emergency reply and an alarm, never reassurance. Only a doctor closes the brief.
"""
from .agent import PrecareAgent

__all__ = ["PrecareAgent"]
