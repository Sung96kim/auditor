"""Observer package. Only the wire-compat constant lives here until the daemon slice lands: the
hook client (``auditr_observer.py``) may not import ``auditor``, so the literal is duplicated
there and ``tests/observer/test_client.py`` asserts the two agree."""

OBSERVER_API_VERSION = 1

__all__ = ["OBSERVER_API_VERSION"]
