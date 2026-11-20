"""Encrypted Document Vault: per-document AES-256-GCM DEK + KMS-wrapped KEK + short-lived signed tokens.

Security invariants (Section 17 & HK PDPO DPP4 / ISO 27799):
- Every document gets a fresh random 256-bit AES-GCM Data Encryption Key (DEK) and 96-bit nonce.
- The DEK is wrapped by a Key Encryption Key (KEK) behind a KMSProvider interface (local KEK in dev/demo,
  GCP Cloud KMS / AWS KMS drop-in for production).
- Associated authenticated data (AAD) binds the ciphertext to (tenant_id, doc_id, content_type) so
  ciphertext cannot be transplanted across tenants or documents.
- No public URLs: every download requires a short-lived HMAC-signed token bound to (tenant_id, doc_id,
  subject_id, expiry) and verified against an active step-up authenticated session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(RuntimeError):
    """Raised on encryption, decryption, integrity, or signed-token failure."""


class KMSProvider(Protocol):
    key_id: str
    def wrap_dek(self, dek: bytes, aad: bytes) -> bytes: ...
    def unwrap_dek(self, wrapped: bytes, aad: bytes) -> bytes: ...


class LocalKMSProvider:
    """AES-256-GCM KEK wrapper for local dev/CI and Cloud Run Secret Manager KEK."""

    def __init__(self, kek_bytes: bytes | None = None, key_id: str = "local-kek-v1"):
        self.key_id = key_id
        if kek_bytes is None:
            env_b64 = os.environ.get("VAULT_KEK_B64")
            if env_b64:
                kek_bytes = base64.b64decode(env_b64)
            else:
                # Deterministic dev/test KEK derived from env secret (override VAULT_KEK_B64 in staging/prod)
                seed = os.environ.get("VAULT_KEK_SEED", "tmrw-dev-vault-kek-hk-asia-east2").encode()
                kek_bytes = hashlib.sha256(seed).digest()
        if len(kek_bytes) != 32:
            raise VaultError("KEK must be exactly 32 bytes (256-bit)")
        self._aead = AESGCM(kek_bytes)

    def wrap_dek(self, dek: bytes, aad: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aead.encrypt(nonce, dek, aad)

    def unwrap_dek(self, wrapped: bytes, aad: bytes) -> bytes:
        if len(wrapped) < 28:
            raise VaultError("Corrupted wrapped DEK")
        nonce, ct = wrapped[:12], wrapped[12:]
        try:
            return self._aead.decrypt(nonce, ct, aad)
        except Exception as exc:
            raise VaultError("Failed to unwrap DEK (integrity/AAD mismatch)") from exc


class StorageBackend(Protocol):
    def put(self, key: str, payload: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class LocalFSBackend:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("..", "").lstrip("/")
        root_resolved = self.root.resolve()
        p = (root_resolved / safe).resolve()
        if not p.is_relative_to(root_resolved):
            raise VaultError("Path traversal rejected")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, key: str, payload: bytes) -> None:
        p = self._path(key)
        p.write_bytes(payload)
        os.chmod(p, 0o600)

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise VaultError(f"Vault object not found: {key}")
        return p.read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()


class GCSStorageBackend:
    """Google Cloud Storage REST backend using cached ADC credentials + pooled httpx.Client (asia-east2 bucket)."""

    def __init__(self, bucket: str, prefix: str = "vault"):
        import threading
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._lock = threading.Lock()
        self._creds = None
        self._client = None

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=20.0)
        return self._client

    def _headers(self) -> dict[str, str]:
        import google.auth
        from google.auth.transport.requests import Request
        with self._lock:
            if self._creds is None:
                self._creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
            if not self._creds.valid:
                self._creds.refresh(Request())
            return {"Authorization": f"Bearer {self._creds.token}"}

    def _obj(self, key: str) -> str:
        safe = key.replace("..", "").lstrip("/")
        return f"{self.prefix}/{safe}" if self.prefix else safe

    def put(self, key: str, payload: bytes) -> None:
        from urllib.parse import quote
        obj = quote(self._obj(key), safe="")
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o?uploadType=media&name={obj}"
        r = self._get_client().post(url, content=payload, headers={**self._headers(), "Content-Type": "application/octet-stream"})
        if r.status_code not in (200, 201):
            raise VaultError(f"GCS put failed ({r.status_code}): {r.text[:200]}")

    def get(self, key: str) -> bytes:
        from urllib.parse import quote
        obj = quote(self._obj(key), safe="")
        url = f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{obj}?alt=media"
        r = self._get_client().get(url, headers=self._headers())
        if r.status_code != 200:
            raise VaultError(f"GCS get failed ({r.status_code})")
        return r.content

    def delete(self, key: str) -> None:
        from urllib.parse import quote
        obj = quote(self._obj(key), safe="")
        url = f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{obj}"
        self._get_client().delete(url, headers=self._headers())


@dataclass(frozen=True)
class StoredDocument:
    doc_id: str
    tenant_id: str
    content_type: str
    filename: str
    sha256: str
    size_bytes: int
    kms_key_id: str


class DocumentVault:
    """High-level envelope-encrypted document vault with short-lived signed access tokens."""

    def __init__(
        self,
        backend: StorageBackend | None = None,
        kms: KMSProvider | None = None,
        signing_secret: str | None = None,
    ):
        if backend is None:
            gcs_bucket = os.environ.get("VAULT_GCS_BUCKET")
            if gcs_bucket:
                backend = GCSStorageBackend(gcs_bucket)
            else:
                backend = LocalFSBackend(os.environ.get("VAULT_LOCAL_DIR", "/tmp/tmrw-vault"))
        self.backend = backend
        self.kms = kms or LocalKMSProvider()
        self._signing_key = (signing_secret or os.environ.get("VAULT_SIGNING_SECRET", "tmrw-vault-sign-dev-hk")).encode()

    def health_check(self) -> dict[str, str | bool]:
        """Verify vault backend & KEK readiness for /api/v1/health/ready."""
        backend_type = type(self.backend).__name__
        if isinstance(self.backend, LocalFSBackend):
            ok = self.backend.root.exists() and os.access(self.backend.root, os.W_OK)
            return {"ok": ok, "backend": backend_type, "kms_key_id": self.kms.key_id}
        if isinstance(self.backend, GCSStorageBackend):
            try:
                self.backend._headers()
                return {"ok": True, "backend": backend_type, "bucket": self.backend.bucket, "kms_key_id": self.kms.key_id}
            except Exception:
                return {"ok": False, "backend": backend_type, "bucket": self.backend.bucket, "kms_key_id": self.kms.key_id}
        return {"ok": True, "backend": backend_type, "kms_key_id": self.kms.key_id}

    def _aad(self, tenant_id: str, doc_id: str, content_type: str) -> bytes:
        return f"{tenant_id}|{doc_id}|{content_type}".encode("utf-8")

    def store(self, tenant_id: str, content: bytes, content_type: str, filename: str) -> StoredDocument:
        doc_id = f"doc_{uuid.uuid4().hex[:16]}"
        dek = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        aad = self._aad(tenant_id, doc_id, content_type)
        ciphertext = AESGCM(dek).encrypt(nonce, content, aad)
        wrapped_dek = self.kms.wrap_dek(dek, aad)
        digest = hashlib.sha256(content).hexdigest()

        envelope = {
            "v": 1,
            "doc_id": doc_id,
            "tenant_id": tenant_id,
            "content_type": content_type,
            "filename": filename,
            "sha256": digest,
            "size_bytes": len(content),
            "kms_key_id": self.kms.key_id,
            "wrapped_dek": base64.b64encode(wrapped_dek).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
        self.backend.put(f"{tenant_id}/{doc_id}.vault.json", json.dumps(envelope).encode("utf-8"))
        return StoredDocument(
            doc_id=doc_id,
            tenant_id=tenant_id,
            content_type=content_type,
            filename=filename,
            sha256=digest,
            size_bytes=len(content),
            kms_key_id=self.kms.key_id,
        )

    def retrieve(self, tenant_id: str, doc_id: str) -> tuple[bytes, dict]:
        raw = self.backend.get(f"{tenant_id}/{doc_id}.vault.json")
        try:
            env = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise VaultError("Malformed vault envelope") from exc
        if env.get("tenant_id") != tenant_id or env.get("doc_id") != doc_id:
            raise VaultError("Tenant/document mismatch in vault envelope")
        aad = self._aad(tenant_id, doc_id, env["content_type"])
        dek = self.kms.unwrap_dek(base64.b64decode(env["wrapped_dek"]), aad)
        try:
            plaintext = AESGCM(dek).decrypt(
                base64.b64decode(env["nonce"]),
                base64.b64decode(env["ciphertext"]),
                aad,
            )
        except Exception as exc:
            raise VaultError("Document ciphertext failed authentication") from exc
        if hashlib.sha256(plaintext).hexdigest() != env["sha256"]:
            raise VaultError("Document SHA-256 integrity mismatch")
        meta = {
            "doc_id": doc_id,
            "tenant_id": tenant_id,
            "content_type": env["content_type"],
            "filename": env["filename"],
            "sha256": env["sha256"],
            "size_bytes": env["size_bytes"],
        }
        return plaintext, meta

    def mint_signed_token(self, tenant_id: str, doc_id: str, subject_id: str, ttl_seconds: int = 300) -> str:
        """Mint a short-lived HMAC-signed access token (default 5 min, zero public URLs)."""
        payload = {
            "t": tenant_id,
            "d": doc_id,
            "s": subject_id,
            "exp": int(time.time()) + ttl_seconds,
            "n": secrets.token_hex(8),
        }
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = hmac.new(self._signing_key, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify_signed_token(self, token: str, expected_tenant: str, expected_doc: str, expected_subject: str) -> None:
        if "." not in token:
            raise VaultError("Malformed download token")
        body, sig = token.rsplit(".", 1)
        want = hmac.new(self._signing_key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            raise VaultError("Invalid download token signature")
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad).decode())
        if int(time.time()) > int(payload["exp"]):
            raise VaultError("Download token expired")
        if payload["t"] != expected_tenant or payload["d"] != expected_doc or payload["s"] != expected_subject:
            raise VaultError("Download token scope mismatch")
