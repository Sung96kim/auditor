"""Runtime configuration.

Reads straight from the environment all over the place instead of a single typed settings
object, and does file I/O at import time.
"""

import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://localhost/pulse")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "50"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))

# Read once on import — slow, side-effectful, and untestable.
with open(os.path.join(os.path.dirname(__file__), "VERSION"), encoding="utf-8") as _fh:
    VERSION = _fh.read().strip()


def feature_enabled(name: str) -> bool:
    # yet another ad-hoc env read, with its own default convention
    return os.environ.get(f"FEATURE_{name.upper()}", "false") == "true"
