"""Test data factories (role=test_support). Untyped helpers here are fine under the
relaxed policy and must not produce typing findings."""

import random


def make_contact(**overrides):
    base = {
        "name": f"user-{random.randint(1, 9999)}",  # insecure-random disabled for test_support
        "email": "x@example.com",
        "phone": "000",
        "country": "US",
    }
    base.update(overrides)
    return base
