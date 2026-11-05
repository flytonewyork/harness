"""The follow-up engine: finds open loops in what reaches a clinic and puts each one, with its reasoning,
in front of the doctor who owns it. See ports.py for what the engine may read and do."""
from .escalation import Escalation
from .ports import FAMILIES, ClinicPort
from .rules import RulesHarness

__all__ = ["ClinicPort", "Escalation", "FAMILIES", "RulesHarness"]
