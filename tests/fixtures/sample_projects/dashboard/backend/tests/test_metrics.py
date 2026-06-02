"""Tests for the metrics service. Classified as `test` role → security/typing relaxed."""

from app.core.auth import hash_password
from app.services.metrics import summarize


def test_hash_password():
    password = "hunter2-test-fixture"
    assert hash_password(password) != password


def test_summarize():
    rows = [{"key": "a", "value": 1}, {"key": "b", "value": 2}]
    assert summarize(rows) == {"a": 1, "b": 2}
