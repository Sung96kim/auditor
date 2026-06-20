"""End-to-end audit of the realistic sample repo fixture: every category fires on the
right file, lookalikes stay quiet, roles relax test code, cross-file dedup works, and edge
cases (syntax error, generated, nested scopes) are handled."""

from pathlib import Path

import pytest

from auditor.config import load_config
from auditor.database import IndexStore
from auditor.engine import ScanEngine


async def _scan(repo: Path) -> dict[str, dict]:
    """Return {rel_path: {"rules": set, "findings": list}} for the whole repo."""
    settings = load_config(repo)
    async with await IndexStore.connect(repo / ".auditor" / "index.db") as index:
        results = await ScanEngine.for_target(
            repo, settings=settings, index=index
        ).scan_path(repo)
    return {
        r.file: {
            "rules": {f.rule_id for f in r.findings},
            "findings": r.findings,
            "result": r,
        }
        for r in results
    }


@pytest.fixture
async def scanned(sample_repo):
    return await _scan(sample_repo)


def _rules(scanned, name: str) -> set[str]:
    for path, data in scanned.items():
        if path.endswith(name):
            return data["rules"]
    raise AssertionError(f"{name} not scanned; have {sorted(scanned)}")


# --- security ---------------------------------------------------------------


async def test_security_cases(scanned):
    rules = _rules(scanned, "src/integrations.py")
    expected = {
        "PY-SEC-SHELL-INJECTION",
        "PY-SEC-DANGEROUS-EVAL",
        "PY-SEC-UNSAFE-DESERIALIZE",
        "PY-SEC-WEAK-HASH",
        "PY-SEC-INSECURE-TLS",
        "PY-SEC-REQUEST-NO-TIMEOUT",
        "PY-SEC-SSRF",
        "PY-SEC-INSECURE-TEMPFILE",
        "PY-SEC-BIND-ALL-INTERFACES",
    }
    assert expected <= rules


async def test_security_lookalikes_quiet(scanned):
    # exactly one eval finding (the dynamic one), not the constant eval
    findings = next(
        d["findings"] for p, d in scanned.items() if p.endswith("src/integrations.py")
    )
    eval_lines = [f.line for f in findings if f.rule_id == "PY-SEC-DANGEROUS-EVAL"]
    assert len(eval_lines) == 1  # parse_known_literal's eval("(1,2,3)") did NOT fire


# --- async ------------------------------------------------------------------


async def test_async_cases(scanned):
    rules = _rules(scanned, "src/async_service.py")
    assert {
        "PY-ASYNC-SYNC-IO",
        "PY-ASYNC-DANGLING-TASK",
        "PY-ASYNC-SEQUENTIAL-AWAITS",
        "PY-ASYNC-NO-AWAIT-BODY",
        "PY-STYLE-INLINE-IMPORT",
    } <= rules


async def test_async_nested_sync_not_flagged(scanned):
    findings = next(
        d["findings"] for p, d in scanned.items() if p.endswith("src/async_service.py")
    )
    sync_io = [f.line for f in findings if f.rule_id == "PY-ASYNC-SYNC-IO"]
    # open() inside the nested sync helper must NOT add a sync-io finding
    assert all(
        "with_nested_sync" not in f.evidence
        for f in findings
        if f.rule_id == "PY-ASYNC-SYNC-IO"
    )
    assert len(sync_io) == 2  # requests.get + time.sleep only


# --- oop / composition ------------------------------------------------------


async def test_oop_cases(scanned):
    proc = _rules(scanned, "src/processing.py")
    assert {
        "PY-OOP-DISPATCH-LADDER",
        "PY-OOP-BUILDER-CLASS",
        "PY-OOP-STATIC-METHOD-CLASS",
        "PY-OOP-THIN-WRAPPER",
        "PY-OOP-GOD-CLASS",
        "PY-OOP-FREE-FN-ORCHESTRATOR",
    } <= proc
    models = _rules(scanned, "src/models.py")
    assert {
        "PY-OOP-FLAT-FIELD-MODEL",
        "PY-OOP-CONSTRUCTOR-WALL",
        "PY-OOP-DATACLASS-IN-PYDANTIC",
    } <= models


# --- typing / correctness / config -----------------------------------------


async def test_web_cases(scanned):
    rules = _rules(scanned, "src/web.py")
    assert {
        "PY-TYPING-UNTYPED-DICT",
        "PY-TYPING-MISSING-HINTS",
        "PY-CORRECT-BROAD-EXCEPT",
        "PY-CORRECT-SWALLOWED-EXCEPTION",
        "PY-CONFIG-ADHOC-ENV",
        "PY-CONFIG-IMPORT-TIME-IO",
        "PY-SEC-JINJA-AUTOESCAPE-OFF",
        "PY-SEC-FLASK-DEBUG",
    } <= rules


async def test_route_handler_dict_exempt(scanned):
    findings = next(
        d["findings"] for p, d in scanned.items() if p.endswith("src/web.py")
    )
    untyped = [f for f in findings if f.rule_id == "PY-TYPING-UNTYPED-DICT"]
    # serialize_widget fires; the @app.get("/health") handler does not
    assert untyped and all("health" not in f.message for f in untyped)


# --- clean baseline ---------------------------------------------------------


async def test_clean_file_has_no_findings(scanned):
    assert _rules(scanned, "src/clean.py") == set()


async def test_settings_has_no_env_or_security_findings(scanned):
    # the canonical BaseSettings module: no ad-hoc env reads, no security issues
    rules = _rules(scanned, "src/settings.py")
    assert "PY-CONFIG-ADHOC-ENV" not in rules
    assert not any(r.startswith("PY-SEC-") for r in rules)


# --- roles ------------------------------------------------------------------


async def test_test_role_relaxed(scanned):
    rules = _rules(scanned, "tests/test_app.py")
    # relaxed away in test code:
    assert "PY-SEC-HARDCODED-SECRET" not in rules
    assert "PY-SEC-ASSERT-FOR-SECURITY" not in rules
    # genuinely dangerous in any context: kept
    assert "PY-SEC-SQL-STRING-BUILD" in rules


async def test_strict_tests_reenables(sample_repo):
    # flip tests to production strength
    (sample_repo / ".auditor").mkdir(exist_ok=True)
    (sample_repo / ".auditor" / "config.toml").write_text(
        'extends = "strict"\ntest_mode = "strict"\n'
    )
    scanned = await _scan(sample_repo)
    rules = _rules(scanned, "tests/test_app.py")
    assert "PY-SEC-HARDCODED-SECRET" in rules
    assert "PY-SEC-ASSERT-FOR-SECURITY" in rules


# --- cross-file -------------------------------------------------------------


async def test_cross_file_duplicates(scanned):
    assert "PY-XFILE-DUP-MODEL" in _rules(scanned, "src/account.py")
    assert "PY-XFILE-DUP-MODEL" in _rules(scanned, "src/customer.py")
    assert "PY-XFILE-DUP-FUNCTION" in _rules(scanned, "src/account.py")


# --- edge cases -------------------------------------------------------------


async def test_syntax_error_does_not_crash(scanned):
    data = next(d for p, d in scanned.items() if p.endswith("edge/broken.py"))
    assert data["rules"] == set()
    assert any(s.rule_id == "*" for s in data["result"].skipped_rules)


async def test_empty_file_is_clean(scanned):
    assert _rules(scanned, "edge/empty.py") == set()


async def test_tricky_precision(scanned):
    rules = _rules(scanned, "edge/tricky.py")
    findings = next(
        d["findings"] for p, d in scanned.items() if p.endswith("edge/tricky.py")
    )
    # exactly one unlocked lazy init (racy), not the lock-guarded one
    lazy = [f for f in findings if f.rule_id == "PY-ASYNC-UNLOCKED-LAZY-INIT"]
    assert len(lazy) == 1
    # TYPE_CHECKING import is not flagged inline; constant evals don't fire
    assert "PY-STYLE-INLINE-IMPORT" not in rules
    assert "PY-SEC-DANGEROUS-EVAL" not in rules


async def test_generated_excluded(scanned):
    # the _pb2 file is excluded from discovery entirely
    assert not any(p.endswith("client_pb2.py") for p in scanned)
