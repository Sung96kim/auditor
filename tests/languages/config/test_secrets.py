"""Config/data-file secret rules: CFG-SECRET-DETECTED (sweep) and CFG-ENV-FILE-COMMITTED."""

import pytest
from _support import BENIGN_SECRET_LOOKALIKES, SECRET_SAMPLES, rule_ids

from auditor.config import AuditorSettings, ResolvedConfig
from auditor.languages.config.auditor import ConfigAuditor
from auditor.models import FileRole

SECRET_RULE = "CFG-SECRET-DETECTED"
ENV_RULE = "CFG-ENV-FILE-COMMITTED"


def run_config_audit(source: str, *, rel_path: str = "settings.yaml") -> set[str]:
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.PRODUCTION, rel_path=rel_path)
    result = ConfigAuditor().audit(
        file_path=rel_path, source=source, role=FileRole.PRODUCTION, config=rc
    )
    return rule_ids(result)


@pytest.mark.parametrize(
    "label, value", SECRET_SAMPLES, ids=[s[0] for s in SECRET_SAMPLES]
)
def test_secret_in_config_value_is_flagged(label, value):
    # the credential lives in a data-file value, not a code literal
    assert SECRET_RULE in run_config_audit(f"api_key: {value}\n"), f"missed {label}"


@pytest.mark.parametrize("value", BENIGN_SECRET_LOOKALIKES)
def test_benign_config_value_is_clean(value):
    assert SECRET_RULE not in run_config_audit(f"id: {value}\n")


def test_clean_config_has_no_findings():
    assert run_config_audit("name: myapp\nport: 8080\nreplicas: 3\n") == set()


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production"])
def test_committed_dotenv_is_flagged(name):
    assert ENV_RULE in run_config_audit("DB_PASSWORD=hunter2\n", rel_path=name)


@pytest.mark.parametrize(
    "name", [".env.example", ".env.sample", ".env.template", ".env.dist"]
)
def test_dotenv_example_templates_are_exempt(name):
    assert ENV_RULE not in run_config_audit("DB_PASSWORD=changeme\n", rel_path=name)


def test_env_evidence_never_echoes_the_first_line():
    """The committed-dotenv finding must not surface the file's content (it may be a live secret)."""
    rc = ResolvedConfig(AuditorSettings(), role=FileRole.PRODUCTION, rel_path=".env")
    result = ConfigAuditor().audit(
        file_path=".env",
        source="AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE\n",
        role=FileRole.PRODUCTION,
        config=rc,
    )
    env = next(f for f in result.findings if f.rule_id == ENV_RULE)
    assert env.evidence == ""


def test_committed_dotenv_also_flags_its_secrets():
    found = run_config_audit(
        "AWS_SECRET_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE\n", rel_path=".env"
    )
    assert {ENV_RULE, SECRET_RULE} <= found
