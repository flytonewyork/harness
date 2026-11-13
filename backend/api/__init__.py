"""The doctor's follow-up list as a service (FastAPI).

Agent endpoints open or raise. Doctor endpoints close an item, or confirm, reject or close a case fact.
`create_app` is loaded lazily so a caller can read `Store` without importing the web stack.
"""
from .store import AuthorityError, Store


def create_app(store: Store):
    from .app import create_app as build
    return build(store)


__all__ = ["AuthorityError", "Store", "create_app"]
