"""Shared pytest fixtures (role=test_support — under tests/, no test_* functions)."""

import pytest


@pytest.fixture
def sample_payload() -> dict:
    return {"name": "Ada", "email": "ada@example.com", "phone": "555", "country": "GB"}
