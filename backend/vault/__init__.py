"""Encrypted Document Vault with per-document AES-256-GCM envelope encryption and short-lived signed access."""
from .vault import DocumentVault, GCSStorageBackend, KMSProvider, LocalFSBackend, LocalKMSProvider, VaultError

__all__ = [
    "DocumentVault",
    "GCSStorageBackend",
    "KMSProvider",
    "LocalFSBackend",
    "LocalKMSProvider",
    "VaultError",
]
