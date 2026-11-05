"""The doctor's follow-up list as a service (FastAPI). Agent endpoints open or raise; doctor endpoints close."""
from .app import create_app
from .store import Store

__all__ = ["Store", "create_app"]
