"""External integrations: command execution, cache (de)serialization, hashing, HTTP.

Realistic module with a mix of genuine security issues and lookalikes that must NOT fire
(constant eval, argv-list subprocess, safe yaml, sha256, verify left default, timeouts set).
"""

import hashlib
import pickle  # noqa: F401 - used by the cache helper below
import subprocess
import tempfile

import requests
import yaml

DEFAULT_REGISTRY = "0.0.0.0"  # PY-SEC-BIND-ALL-INTERFACES (a real bind address constant)


class CommandRunner:
    """Runs maintenance commands. Mixes a safe argv call with an unsafe shell call."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    def safe(self, args: list[str]) -> str:
        # argv list, no shell -> must NOT trip PY-SEC-SHELL-INJECTION
        completed = subprocess.run(args, capture_output=True, text=True, check=True)
        return completed.stdout

    def unsafe(self, user_pattern: str) -> str:
        # PY-SEC-SHELL-INJECTION
        return subprocess.run(f"grep {user_pattern} /var/log/app.log", shell=True, check=False).stdout


def evaluate_rule(expression: str, context: dict) -> object:
    # PY-SEC-DANGEROUS-EVAL (dynamic expression)
    return eval(expression, {}, context)


def parse_known_literal() -> object:
    # constant argument -> must NOT trip PY-SEC-DANGEROUS-EVAL
    return eval("(1, 2, 3)")


def load_cache(blob: bytes) -> object:
    # PY-SEC-UNSAFE-DESERIALIZE
    return pickle.loads(blob)


def load_config_yaml(raw: str) -> dict:
    # PY-SEC-UNSAFE-DESERIALIZE (yaml.load without SafeLoader)
    return yaml.load(raw)


def load_config_yaml_safe(raw: str) -> dict:
    # negative: safe_load must NOT fire
    return yaml.safe_load(raw)


def content_fingerprint(data: bytes) -> str:
    # PY-SEC-WEAK-HASH (md5 for integrity)
    return hashlib.md5(data).hexdigest()


def secure_fingerprint(data: bytes) -> str:
    # negative: sha256 is fine
    return hashlib.sha256(data).hexdigest()


def fetch_insecure(url: str) -> bytes:
    # PY-SEC-INSECURE-TLS + PY-SEC-REQUEST-NO-TIMEOUT + PY-SEC-SSRF (variable url)
    return requests.get(url, verify=False).content


def fetch_pinned() -> bytes:
    # negative: constant url + timeout + default verify
    return requests.get("https://example.com/health", timeout=5).content


def scratch_path() -> str:
    # PY-SEC-INSECURE-TEMPFILE
    return tempfile.mktemp(suffix=".tmp")
