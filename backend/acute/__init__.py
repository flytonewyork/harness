"""Clinic AI Harness — Flu-Season Acute Care MVP platform package.

Implements the four-surface, Hong Kong PDPO (Cap. 486) & MCHK-compliant clinical harness:
- FHIR R4-aligned PostgreSQL 15 schema with forced Row-Level Security (RLS) and immutable audit/consent triggers
- Encrypted document vault (per-document AES-256-GCM DEK, short-lived signed URLs)
- Model gateway with Hong Kong residency gate (asia-east2), PHI redaction hook, and deterministic replay
- Deterministic clinical rules (red-flag floor, HK G6PD / Cap. 134 / Cap. 138 / allergy / interaction / dose checks)
- Durable transactional outbox workflow engine
"""
