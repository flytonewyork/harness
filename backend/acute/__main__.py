"""Run the Clinic AI Harness Flu-Season Acute Care server: `python -m backend.acute`."""
from __future__ import annotations

import os
import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("backend.acute.app:app_from_env", factory=True, host=host, port=port)


if __name__ == "__main__":
    main()
