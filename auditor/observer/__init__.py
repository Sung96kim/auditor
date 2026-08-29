"""Observer package: spec 8.6's change assessment, and the wire-compat constant.

`assess.py` holds the assessment itself, pure functions over frozen models. The constant stays
here because the hook client (``auditr_observer.py``) may not import ``auditor``, so the literal is
duplicated there and ``tests/observer/test_client.py`` asserts the two agree.
"""

OBSERVER_API_VERSION = 1

__all__ = ["OBSERVER_API_VERSION"]
