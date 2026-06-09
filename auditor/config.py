"""Configuration: typed Pydantic models, layered TOML loading, per-file resolution.

Layering (later wins): built-in ``extends`` profile chain -> ``pyproject [tool.auditor]``
-> ``.auditor/config.toml`` -> environment. A repo tailors rules/severities/thresholds and
per-role/per-glob policy. ``load_config`` performs the two-phase plugin/config load so a
config may reference plugin-contributed rules.
"""

import tomllib
from fnmatch import fnmatch
from importlib import resources
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import auditor.builtins  # noqa: F401  (registers built-in detectors before validation)
from auditor.models import FileRole, RuleId, Severity, VerdictKind, severity_rank
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY

RoleMode = Literal["relaxed", "strict", "excluded"]


class OopThreshold(BaseModel):
    """Floors for the OOP/composition-shape detectors."""

    model_config = ConfigDict(extra="forbid")

    wall_kwarg_min: int = Field(
        12, ge=1, description="kwargs in a constructor call before it's a 'wall'"
    )
    flat_field_min: int = Field(
        12, ge=1, description="fields in a flat model before it should be grouped"
    )
    field_copy_min: int = Field(
        5,
        ge=1,
        description="`self.x = src.x` copies before it's field-by-field copying",
    )
    module_const_min: int = Field(
        2,
        ge=1,
        description="module consts prefixed with a subclass name before flagging",
    )
    dispatch_min_branches: int = Field(
        5,
        ge=1,
        description="if/elif or guard-clause branches before it's a dispatch ladder",
    )


class SizeThreshold(BaseModel):
    """Floors for the size/complexity detectors."""

    model_config = ConfigDict(extra="forbid")

    file_max_lines: int = Field(
        800, ge=1, description="split a module past this many lines"
    )
    max_params: int = Field(
        6, ge=1, description="parameters before a signature is too long"
    )
    max_methods: int = Field(
        20, ge=1, description="methods before a class is a god class"
    )
    max_attrs: int = Field(
        15, ge=1, description="instance attributes before a class is a god class"
    )
    max_complexity: int = Field(
        10, ge=1, description="cyclomatic complexity ceiling per function"
    )


class DryThreshold(BaseModel):
    """Floors for the duplication / parameterize-me detectors."""

    model_config = ConfigDict(extra="forbid")

    dup_block_min_statements: int = Field(
        3, ge=1, description="statements in a repeated block before flagging"
    )
    dup_block_min_tokens: int = Field(
        12,
        ge=1,
        description="tokens in a repeated block before flagging (filters trivial)",
    )
    parallel_sibling_min_tokens: int = Field(
        4, ge=1, description="skeleton size before two defs can be parallel siblings"
    )
    parallel_sibling_min_group: int = Field(
        2,
        ge=1,
        description="near-twins sharing a skeleton before flagging (2 = any pair)",
    )
    xfile_method_min_statements: int = Field(
        3,
        ge=1,
        description="statements in a method before it's indexed for cross-file dedup",
    )


class JsxThreshold(BaseModel):
    """Floors for the React/JSX structural detectors."""

    model_config = ConfigDict(extra="forbid")

    max_jsx_depth: int = Field(6, ge=1, description="JSX nesting depth before flagging")
    repeated_jsx_min: int = Field(
        3, ge=1, description="identical sibling JSX blocks before 'map over data'"
    )
    repeated_jsx_min_tags: int = Field(
        2, ge=1, description="tags in a repeated JSX block before it counts"
    )


class TestThreshold(BaseModel):
    """Floors for the structural pytest test-quality detectors."""

    model_config = ConfigDict(extra="forbid")

    parametrize_min_clones: int = Field(
        3,
        ge=1,
        description="near-identical tests sharing a body before 'parametrize me'",
    )
    parametrize_min_statements: int = Field(
        2,
        ge=1,
        description="body statements before a test is considered for clustering",
    )
    setup_min_statements: int = Field(
        2, ge=1, description="shared leading statements before suggesting a fixture"
    )
    setup_min_tests: int = Field(
        3, ge=1, description="tests sharing a setup prefix before flagging"
    )
    max_mocks_per_test: int = Field(
        4, ge=1, description="mocks in one test before it's testing mocks, not behavior"
    )


class Threshold(BaseModel):
    """Threshold knobs grouped by concern. A partial override deep-merges onto the base, so a
    repo can tune one floor (e.g. ``threshold.dry.dup_block_min_statements``) without restating
    the rest."""

    model_config = ConfigDict(extra="forbid")

    oop: OopThreshold = Field(default_factory=OopThreshold)
    size: SizeThreshold = Field(default_factory=SizeThreshold)
    dry: DryThreshold = Field(default_factory=DryThreshold)
    jsx: JsxThreshold = Field(default_factory=JsxThreshold)
    test: TestThreshold = Field(default_factory=TestThreshold)

    def merged(self, override: "Threshold | None") -> "Threshold":
        if override is None:
            return self
        sparse = override.model_dump(exclude_unset=True)
        if not sparse:
            return self
        return Threshold.model_validate(_deep_merge(self.model_dump(), sparse))


class RuleConfig(BaseModel):
    """Per-rule override. All fields optional — unset means inherit."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    severity: Severity | None = None
    verdict_kind: VerdictKind | None = None
    threshold: Threshold | None = None


class CategoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    min_severity: Severity | None = None


class RolePolicy(BaseModel):
    """How a file role is audited. ``relaxed`` applies the declared rule/category
    overrides; ``strict`` ignores them (full production ruleset); ``excluded`` skips."""

    model_config = ConfigDict(extra="forbid")

    mode: RoleMode = "strict"
    rules: dict[RuleId, RuleConfig] = Field(default_factory=dict)
    categories: dict[str, CategoryConfig] = Field(default_factory=dict)


class OverrideConfig(BaseModel):
    """Per-glob (or per-role) overrides, applied last — ruff per-file-ignores model."""

    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    role: FileRole | None = None
    rules: dict[RuleId, RuleConfig] = Field(default_factory=dict)
    categories: dict[str, CategoryConfig] = Field(default_factory=dict)


class DesignSystemPrimitive(BaseModel):
    """One declared design-system primitive: the raw markup it should replace. Lets the
    project supply its own vocabulary so the auditor can check 'this should be <Badge>'
    without the tool hardcoding any component."""

    model_config = ConfigDict(extra="forbid")

    component: str  # the primitive to recommend, e.g. "Badge"
    when_class: str | None = (
        None  # className regex whose raw markup should be this primitive
    )
    requires_text: bool = (
        True  # only when the element renders text (skip icon-only backdrops)
    )
    size_override: bool = (
        False  # also flag fixed h-/w-/size- className on this component
    )


class DesignSystem(BaseModel):
    """A project's declared design system. Empty by default — the DS rules only fire when a
    repo opts in by declaring its shell / primitives."""

    model_config = ConfigDict(extra="forbid")

    ui_paths: list[str] = Field(
        default_factory=list
    )  # import paths that bypass the shell
    shell: str | None = None  # the entrypoint to recommend instead
    primitives: list[DesignSystemPrimitive] = Field(default_factory=list)


class SqlAlchemyConfig(BaseModel):
    """Declared facts about the project's SQLAlchemy engine/session, so config-dependent rules are
    accurate instead of guessing (the real factory often lives in a shared lib the auditor can't see)."""

    model_config = ConfigDict(extra="forbid")

    expire_on_commit: bool = False  # async session setting; True activates SA-GREENLET-ATTR-AFTER-COMMIT


class GlobalPaths(BaseSettings):
    """Global auditor data locations from the environment. ``home`` ← ``$AUDITOR_HOME`` (via the
    ``AUDITOR_`` prefix), defaulting to ``~/.auditor``. Lives here so the project's BaseSettings
    stay together (see ``PY-CONFIG-SCATTERED-SETTINGS``); ``auditor.paths`` re-exports the helper."""

    model_config = SettingsConfigDict(env_prefix="AUDITOR_")
    home: Path = Field(default_factory=lambda: Path.home() / ".auditor")


class AuditorSettings(BaseSettings):
    """The merged repo configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AUDITOR_", extra="forbid", validate_default=False
    )

    extends: str = "base"
    exclude: list[str] = Field(default_factory=list)
    respect_gitignore: bool = True  # skip git-ignored files (CLI: --include-gitignored to override)
    threshold: Threshold = Field(default_factory=Threshold)
    rules: dict[RuleId, RuleConfig] = Field(default_factory=dict)
    categories: dict[str, CategoryConfig] = Field(default_factory=dict)
    roles: dict[FileRole, RolePolicy] = Field(default_factory=dict)
    role_globs: dict[FileRole, list[str]] = Field(default_factory=dict)
    test_mode: RoleMode | None = None
    overrides: list[OverrideConfig] = Field(default_factory=list)
    plugins: list[str] = Field(default_factory=list)
    trust_local_plugins: bool = False
    lint_overlap: bool = False
    respect_skips: bool = True
    # PY-CONFIG-SCATTERED-SETTINGS: modules that may hold BaseSettings, and whether to also bless
    # the de-facto home (the module where settings classes already cluster).
    settings_modules: list[str] = Field(default_factory=lambda: ["config", "settings"])
    settings_cohesion: bool = True
    diff_base: str | None = (
        None  # `scan --vs-base` ref; None auto-detects main/master/develop/development
    )
    design_system: DesignSystem = Field(default_factory=DesignSystem)
    sqlalchemy: SqlAlchemyConfig = Field(default_factory=SqlAlchemyConfig)

    @field_validator("rules", mode="after")
    @classmethod
    def _check_rule_ids(
        cls, value: dict[RuleId, RuleConfig]
    ) -> dict[RuleId, RuleConfig]:
        known = REGISTRY.rule_ids()
        if known:  # only enforce once detectors are registered (two-phase load)
            for rid in value:
                if rid not in known:
                    raise ValueError(
                        f"unknown rule_id {rid!r}; run `auditor rules list` to see available rules"
                    )
        return value

    @field_validator("categories", mode="after")
    @classmethod
    def _check_categories(
        cls, value: dict[str, CategoryConfig]
    ) -> dict[str, CategoryConfig]:
        known = REGISTRY.categories()
        for cat in value:
            if cat not in known:
                raise ValueError(f"unknown category {cat!r}; known: {sorted(known)}")
        return value


# --------------------------------------------------------------------------- loading


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive dict merge; later wins. Scalars/lists replaced, dicts merged."""
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _load_profile(name_or_path: str, _seen: frozenset[str] = frozenset()) -> dict:
    """Load a built-in profile by name or a TOML file by path, resolving ``extends``."""
    if name_or_path in _seen:
        raise ValueError(f"circular profile extends: {name_or_path}")
    raw = _read_profile_toml(name_or_path)
    parent = raw.pop("extends", None)
    if parent:
        base = _load_profile(parent, _seen | {name_or_path})
        return _deep_merge(base, raw)
    return raw


def _read_profile_toml(name_or_path: str) -> dict:
    path = Path(name_or_path)
    if path.suffix == ".toml" and path.exists():
        return tomllib.loads(path.read_text())
    res = resources.files("auditor.profiles").joinpath(f"{name_or_path}.toml")
    if res.is_file():
        return tomllib.loads(res.read_text())
    raise FileNotFoundError(f"profile {name_or_path!r} not found (no built-in or file)")


def _read_repo_tomls(root: Path) -> tuple[dict, dict]:
    """Return (pyproject [tool.auditor], .auditor/config.toml) raw dicts."""
    pyproject: dict = {}
    pp = root / "pyproject.toml"
    if pp.exists():
        pyproject = tomllib.loads(pp.read_text()).get("tool", {}).get("auditor", {})
    standalone: dict = {}
    sa = root / ".auditor" / "config.toml"
    if sa.exists():
        standalone = tomllib.loads(sa.read_text())
    return pyproject, standalone


def merged_config_dict(root: Path, *, profile: str | None = None) -> dict:
    """Layer profile -> pyproject -> .auditor/config.toml into one raw dict (pre-validation).

    ``profile`` overrides the repo's ``extends`` for this run (the CLI ``--profile`` flag),
    so any repo can be audited at e.g. ``strict`` strength without editing its config.
    """
    pyproject, standalone = _read_repo_tomls(root)
    extends = profile or standalone.get("extends") or pyproject.get("extends") or "base"
    merged = _load_profile(extends)
    merged = _deep_merge(merged, pyproject)
    merged = _deep_merge(merged, standalone)
    # `extends` is consumed; keep it for visibility but it's already resolved
    merged["extends"] = extends
    return merged


def load_config(
    root: Path,
    *,
    profile: str | None = None,
    allow_local_plugins: bool = False,
    loader: "PluginLoader | None" = None,
) -> AuditorSettings:
    """Two-phase load: read raw config, load the plugins it names (so a config can
    reference plugin-contributed rules), then validate against the populated registry.

    ``profile`` overrides the repo's ``extends`` for this run. Entry-point and config-named
    plugins load unconditionally; local ``.auditor/plugins`` load only when trusted.
    """
    raw = merged_config_dict(root, profile=profile)
    loader = loader if loader is not None else PluginLoader()
    loader.load_entry_points()
    loader.load_config_modules(list(raw.get("plugins", [])))
    trusted = allow_local_plugins or bool(raw.get("trust_local_plugins", False))
    loader.load_local(root, trusted=trusted)
    return AuditorSettings.model_validate(raw)


# --------------------------------------------------------------- per-file resolution


class EffectiveRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool
    severity: Severity
    verdict_kind: VerdictKind
    threshold: Threshold
    skipped_reason: str | None = None


class ResolvedConfig:
    """Per-file effective view of the settings, given the file's role + path."""

    def __init__(
        self, settings: AuditorSettings, *, role: FileRole, rel_path: str
    ) -> None:
        self.settings = settings
        self.role = role
        self.rel_path = rel_path

    def _category_of(self, rule_id: RuleId) -> str:
        det = REGISTRY.detector(rule_id)
        return str(det.category)

    def effective(self, rule_id: RuleId) -> EffectiveRule:
        det = REGISTRY.detector(rule_id)
        category = str(det.category)
        enabled = True
        severity: Severity = det.default_severity
        verdict: VerdictKind = det.verdict_kind
        threshold = self.settings.threshold
        reason: str | None = None
        min_floor: Severity | None = None

        def apply_category(cfg: CategoryConfig | None) -> None:
            nonlocal enabled, min_floor
            if cfg is None:
                return
            if cfg.enabled is False:
                enabled = False
            if cfg.min_severity is not None:
                min_floor = (
                    cfg.min_severity
                )  # raise every rule in this category to at least this

        def apply_rule(cfg: RuleConfig | None) -> None:
            nonlocal enabled, severity, verdict, threshold
            if cfg is None:
                return
            if cfg.enabled is not None:
                enabled = cfg.enabled
            if cfg.severity is not None:
                severity = cfg.severity
            if cfg.verdict_kind is not None:
                verdict = cfg.verdict_kind
            if cfg.threshold is not None:
                threshold = threshold.merged(cfg.threshold)

        # base category + rule
        apply_category(self.settings.categories.get(category))
        apply_rule(self.settings.rules.get(rule_id))

        # role policy
        mode = self._role_mode()
        if mode == "excluded":
            return EffectiveRule(
                enabled=False,
                severity=severity,
                verdict_kind=verdict,
                threshold=threshold,
                skipped_reason=f"role {self.role.value} excluded",
            )
        if mode == "relaxed":
            rp = self.settings.roles.get(self.role)
            if rp is not None:
                apply_category(rp.categories.get(category))
                apply_rule(rp.rules.get(rule_id))
                if not enabled:
                    reason = f"relaxed for role {self.role.value}"

        # per-glob overrides (last wins)
        for ov in self.settings.overrides:
            if self._override_matches(ov):
                apply_category(ov.categories.get(category))
                apply_rule(ov.rules.get(rule_id))

        if min_floor is not None and severity_rank(severity) < severity_rank(min_floor):
            severity = (
                min_floor  # category min_severity is a floor on the rule's severity
            )

        return EffectiveRule(
            enabled=enabled,
            severity=severity,
            verdict_kind=verdict,
            threshold=threshold,
            skipped_reason=None if enabled else (reason or "disabled by config"),
        )

    def _role_mode(self) -> RoleMode:
        if self.role.is_test and self.settings.test_mode:
            return self.settings.test_mode
        rp = self.settings.roles.get(self.role)
        if rp is not None:
            return rp.mode
        # default: tests relaxed, everything else strict
        if self.role.is_test:
            return "relaxed"
        if self.role == FileRole.GENERATED:
            return "excluded"
        return "strict"

    def _override_matches(self, ov: OverrideConfig) -> bool:
        if ov.role is not None and ov.role != self.role:
            return False
        if ov.path is not None and not fnmatch(self.rel_path, ov.path):
            return False
        return ov.path is not None or ov.role is not None
