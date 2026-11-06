"""The follow-up engine: finds open loops in what reaches a clinic and puts each one, with its reasoning,
in front of the doctor who owns it. See ports.py for what the engine may read and do."""
from .escalation import Escalation
from .ports import FAMILIES, ClinicPort
from .rules import RulesHarness
from .verify import CHECKS, VerifyingClinic, check_item

__all__ = ["CHECKS", "ClinicPort", "Escalation", "FAMILIES", "RulesHarness", "VerifyingClinic", "check_item"]
