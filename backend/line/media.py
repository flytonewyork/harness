"""Voice notes and reports: kept in a private bucket in the live deployment (deleted by lifecycle rule after 30 days),
in a local folder otherwise. Only the doctor's web page reads them back, after sign-in."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class LocalMedia:
    def __init__(self, folder: str):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, mime: str) -> str:
        ref = hashlib.sha256(data).hexdigest()[:24]
        (self.dir / ref).write_bytes(data)
        (self.dir / f"{ref}.json").write_text(json.dumps({"mime": mime}))
        return ref

    def get(self, ref: str) -> tuple[bytes, str]:
        p = self.dir / ref
        if not ref.isalnum() or not p.exists():
            raise KeyError(ref)
        return p.read_bytes(), json.loads((self.dir / f"{ref}.json").read_text())["mime"]


class GcsMedia:
    def __init__(self, bucket: str):
        from google.cloud import storage  # only in the live image
        self.bucket = storage.Client().bucket(bucket.removeprefix("gs://"))

    def put(self, data: bytes, mime: str) -> str:
        ref = hashlib.sha256(data).hexdigest()[:24]
        self.bucket.blob(ref).upload_from_string(data, content_type=mime)
        return ref

    def get(self, ref: str) -> tuple[bytes, str]:
        if not ref.isalnum():
            raise KeyError(ref)
        b = self.bucket.blob(ref)
        data = b.download_as_bytes()
        return data, b.content_type or "application/octet-stream"


def open_media(cfg):
    return GcsMedia(cfg.media_bucket) if cfg.media_bucket else LocalMedia(cfg.media_dir)
