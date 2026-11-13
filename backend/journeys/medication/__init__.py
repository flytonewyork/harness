"""Medication changes and repeat prescriptions: see agent.py."""
from .agent import FAMILIES, MedicationJourney
from .reader import ModelReader, RulesReader

NAME = "medication"
SUMMARY = ("After a medicine is started, stopped or changed, side effects, confusion and dose questions reach the "
           "prescriber with the patient's words; repeat requests reach the doctor checked, never approved by the agent.")
JOURNEY = MedicationJourney

__all__ = ["FAMILIES", "JOURNEY", "NAME", "SUMMARY", "MedicationJourney", "ModelReader", "RulesReader"]
