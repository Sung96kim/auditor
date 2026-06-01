"""Edge cases that exercise detector precision in realistic shapes:

- a TYPE_CHECKING import (must NOT be flagged as an inline import)
- eval on a constant nested deep in a comprehension inside a method (must NOT fire)
- a lock-guarded lazy init (must NOT trip unlocked-lazy-init)
- an unguarded lazy init in a sibling method (MUST trip it)
- a property with branching (complexity stays under threshold)
"""

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # negative: not an inline import
    from collections.abc import Iterable


class Registry:
    def __init__(self) -> None:
        self._cache: dict | None = None
        self._lock = threading.Lock()

    def cached(self) -> dict:
        # negative: double-checked locking under a lock -> must NOT trip unlocked-lazy-init
        if self._cache is None:
            with self._lock:
                if self._cache is None:
                    self._cache = self._build()
        return self._cache

    def racy(self) -> dict:
        # PY-ASYNC-UNLOCKED-LAZY-INIT: check-then-set with no lock
        if self._cache is None:
            self._cache = self._build()
        return self._cache

    def _build(self) -> dict:
        # constant evals nested in a comprehension -> must NOT trip dangerous-eval
        return {i: eval("i * 2", {"i": i}) for i in range(3) if eval("True")}


def labels(items: "Iterable[str]") -> list[str]:
    return [item.upper() for item in items]
