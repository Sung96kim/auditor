"""Event processing pipeline. Exercises the OOP/composition candidate detectors with
realistic-looking code: a dispatch ladder, a builder, a god class, a static-only util,
a thin wrapper, and a free-function orchestrator chain."""

from dataclasses import dataclass


def route_event(kind: str, payload: dict) -> str:
    # PY-OOP-DISPATCH-LADDER (>=5 branches on one variable)
    if kind == "created":
        return _on_created(payload)
    elif kind == "updated":
        return _on_updated(payload)
    elif kind == "deleted":
        return _on_deleted(payload)
    elif kind == "archived":
        return _on_archived(payload)
    elif kind == "restored":
        return _on_restored(payload)
    elif kind == "purged":
        return _on_purged(payload)
    return "ignored"


def _on_created(p: dict) -> str:
    return "created"


def _on_updated(p: dict) -> str:
    return "updated"


def _on_deleted(p: dict) -> str:
    return "deleted"


def _on_archived(p: dict) -> str:
    return "archived"


def _on_restored(p: dict) -> str:
    return "restored"


def _on_purged(p: dict) -> str:
    return "purged"


@dataclass
class ReportBuilder:
    """PY-OOP-BUILDER-CLASS: holds inputs and produces one output via build()."""

    rows: list
    title: str

    def build(self) -> dict:
        return {"title": self.title, "rows": list(self.rows)}


class StringUtils:
    """PY-OOP-STATIC-METHOD-CLASS: every method is static — a namespace, not a class."""

    @staticmethod
    def slug(value: str) -> str:
        return value.strip().lower().replace(" ", "-")

    @staticmethod
    def truncate(value: str, limit: int) -> str:
        return value if len(value) <= limit else value[:limit] + "…"


def to_summary(events: list) -> dict:
    # PY-OOP-THIN-WRAPPER: single return delegating to one call
    return ReportBuilder(events, "summary").build()


class PipelineManager:
    """PY-OOP-GOD-CLASS: too many methods + instance attributes for one class."""

    def __init__(self) -> None:
        self.a = 1
        self.b = 2
        self.c = 3
        self.d = 4
        self.e = 5
        self.f = 6
        self.g = 7
        self.h = 8
        self.i = 9
        self.j = 10
        self.k = 11
        self.m = 12
        self.n = 13
        self.o = 14
        self.p = 15
        self.q = 16

    def m1(self): return 1
    def m2(self): return 2
    def m3(self): return 3
    def m4(self): return 4
    def m5(self): return 5
    def m6(self): return 6
    def m7(self): return 7
    def m8(self): return 8
    def m9(self): return 9
    def m10(self): return 10
    def m11(self): return 11
    def m12(self): return 12
    def m13(self): return 13
    def m14(self): return 14
    def m15(self): return 15
    def m16(self): return 16
    def m17(self): return 17
    def m18(self): return 18
    def m19(self): return 19
    def m20(self): return 20
    def m21(self): return 21


# PY-OOP-FREE-FN-ORCHESTRATOR: a chain of free functions threading the same state.
def build_signals(raw: list) -> list:
    return [r for r in raw if r]


def build_contexts(signals: list, registry: dict) -> list:
    return [registry.get(s, s) for s in build_signals(signals)]


def build_rows(signals: list, registry: dict) -> list:
    return list(build_contexts(signals, registry))
