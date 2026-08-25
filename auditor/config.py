"""Configuration: typed Pydantic models, layered TOML loading, per-file resolution.

Layering (later wins): built-in ``extends`` profile chain -> ``pyproject [tool.auditor]``
-> ``.auditor/config.toml`` -> injected ``--config-json`` overrides. The environment is the
*lowest* layer: it only fills keys no TOML layer sets, and reaches one non-policy field (see
``_NonPolicyEnvSource``). A repo tailors rules/severities/thresholds and per-role/per-glob
policy. ``load_config`` performs the two-phase plugin/config load so a config may reference
plugin-contributed rules.
"""

# auditor: skip-file: PY-TYPING-UNTYPED-DICT  (raw-TOML layer boundary — tomllib dicts pre-validation)

import tomllib
from collections.abc import Iterator, Mapping
from fnmatch import fnmatch
from importlib import resources
from pathlib import Path
from types import UnionType
from typing import Any, ClassVar, Literal, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FieldSerializationInfo,
    field_serializer,
    field_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

import auditor.builtins  # noqa: F401  (registers built-in detectors before validation)
from auditor.models import FileRole, RuleId, Severity, VerdictKind, severity_rank
from auditor.plugins import PluginLoader
from auditor.registry import REGISTRY

RoleMode = Literal["relaxed", "strict", "excluded"]


class OopThreshold(BaseModel):
    """Floors for the OOP/composition-shape detectors."""

    model_config = ConfigDict(extra="ignore")

    wall_kwarg_min: int = Field(
        12, ge=1, description="kwargs in a constructor call before it's a 'wall'"
    )
    flat_field_min: int = Field(
        10, ge=1, description="fields in a flat model before it should be grouped"
    )
    field_copy_min: int = Field(
        4,
        ge=1,
        description="same-name copies from one source (`self.x = src.x` assigns or "
        "`Result(x=src.x, …)` constructor kwargs) before it's field-by-field copying",
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
    cli_logic_min_calls: int = Field(
        3,
        ge=1,
        description="subprocess/file-mutation calls in one CLI-module function before "
        "it's domain logic living in the CLI layer",
    )


class SizeThreshold(BaseModel):
    """Floors for the size/complexity detectors."""

    model_config = ConfigDict(extra="ignore")

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
    comment_block_max_lines: int = Field(
        3,
        ge=1,
        description="prose comment lines in a contiguous block before it's too long",
    )


class DryThreshold(BaseModel):
    """Floors for the duplication / parameterize-me detectors."""

    model_config = ConfigDict(extra="ignore")

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

    model_config = ConfigDict(extra="ignore")

    max_jsx_depth: int = Field(6, ge=1, description="JSX nesting depth before flagging")
    repeated_jsx_min: int = Field(
        3, ge=1, description="identical sibling JSX blocks before 'map over data'"
    )
    repeated_jsx_min_tags: int = Field(
        2, ge=1, description="tags in a repeated JSX block before it counts"
    )


class TestThreshold(BaseModel):
    """Floors for the structural pytest test-quality detectors."""

    model_config = ConfigDict(extra="ignore")

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

    model_config = ConfigDict(extra="ignore")

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
        return Threshold.model_validate(deep_merge(self.model_dump(), sparse))


class RuleConfig(BaseModel):
    """Per-rule override. All fields optional — unset means inherit."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool | None = None
    severity: Severity | None = None
    verdict_kind: VerdictKind | None = None
    threshold: Threshold | None = None


class CategoryConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool | None = None
    min_severity: Severity | None = None


class RolePolicy(BaseModel):
    """How a file role is audited. ``relaxed`` applies the declared rule/category
    overrides; ``strict`` ignores them (full production ruleset); ``excluded`` skips."""

    model_config = ConfigDict(extra="ignore")

    mode: RoleMode = "strict"
    rules: dict[RuleId, RuleConfig] = Field(default_factory=dict)
    categories: dict[str, CategoryConfig] = Field(default_factory=dict)


class OverrideConfig(BaseModel):
    """Per-glob (or per-role) overrides, applied last — ruff per-file-ignores model."""

    model_config = ConfigDict(extra="ignore")

    path: str | None = None
    role: FileRole | None = None
    rules: dict[RuleId, RuleConfig] = Field(default_factory=dict)
    categories: dict[str, CategoryConfig] = Field(default_factory=dict)


class DesignSystemPrimitive(BaseModel):
    """One declared design-system primitive: the raw markup it should replace. Lets the
    project supply its own vocabulary so the auditor can check 'this should be <Badge>'
    without the tool hardcoding any component."""

    model_config = ConfigDict(extra="ignore")

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

    model_config = ConfigDict(extra="ignore")

    ui_paths: list[str] = Field(
        default_factory=list
    )  # import paths that bypass the shell
    shell: str | None = None  # the entrypoint to recommend instead
    primitives: list[DesignSystemPrimitive] = Field(default_factory=list)


class SqlAlchemyConfig(BaseModel):
    """Declared facts about the project's SQLAlchemy engine/session, so config-dependent rules are
    accurate instead of guessing (the real factory often lives in a shared lib the auditor can't see).
    """

    model_config = ConfigDict(extra="ignore")

    expire_on_commit: bool = (
        False  # async session setting; True activates SA-GREENLET-ATTR-AFTER-COMMIT
    )
    async_session: bool = (
        False  # ORM runs under AsyncSession; True activates SA-IMPLICIT-LAZY-ASYNC
    )


class GraphConfig(BaseModel):
    """[tool.auditor.graph] — the semantic graph is opt-in (needs the `graph` extra)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    name_similarity_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    knn_k: int = Field(default=8, ge=1)
    cluster_floor: float = Field(default=0.45, ge=0.0, le=1.0)
    stopwords: list[str] = Field(
        default_factory=list
    )  # repo-specific, added on top of english/IDF
    detect: bool = True
    god_concept_sigma: float = Field(default=3.0, ge=0.0)
    scattered_min_modules: int = Field(default=5, ge=1)
    scattered_min_ratio: float = Field(default=0.5, ge=0.0, le=1.0)
    naming_verb_distance: float = Field(default=0.15, ge=0.0)
    naming_object_jaccard: float = Field(default=0.6, ge=0.0, le=1.0)
    naming_min_verb_count: int = Field(default=20, ge=1)


class MalwareScanConfig(BaseModel):
    """[tool.auditor.malware_scan] — opt-in shell-out scans: ClamAV (file contents)
    and osv-scanner (known-bad dependency versions). No pip extra; the backends are
    system binaries resolved at runtime (see auditor.malware.tools)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    content: bool = True  # ClamAV pass
    dependencies: bool = True  # osv-scanner pass
    include_vendored: bool = (
        True  # scan node_modules/.venv/vendor — where payloads live
    )
    max_file_size_mb: int = Field(default=50, ge=1)
    include_vulnerabilities: bool = False  # OSV: also report CVEs, not just MAL-*
    scan_timeout_s: int = Field(default=600, ge=1)


class GlobalPaths(BaseSettings):
    """Global auditor settings from the environment (``AUDITOR_`` prefix). ``home`` ←
    ``$AUDITOR_HOME`` (default ``~/.auditor``); ``code_mode`` ← ``$AUDITOR_CODE_MODE`` gates the
    experimental Code Mode MCP transform. Lives here so the project's BaseSettings stay together
    (see ``PY-CONFIG-SCATTERED-SETTINGS``); ``auditor.paths`` re-exports the ``home`` helper.

    ``env_ignore_empty`` makes ``AUDITOR_HOME=`` mean unset rather than ``Path(".")``, which would
    write the shared index and every repo's state into whatever directory the user is standing in.
    """

    model_config = SettingsConfigDict(env_prefix="AUDITOR_", env_ignore_empty=True)
    home: Path = Field(default_factory=lambda: Path.home() / ".auditor")
    code_mode: bool = False


class _NonPolicyEnvSource(EnvSettingsSource):
    """The ``AUDITOR_`` env source narrowed to the keys the environment may set.

    Repo policy is shared through git and drives CI, so the environment must not reach it: an
    ``AUDITOR_*`` var could otherwise disable a rule the repo never mentions. The allow-list fails
    closed, so a field nobody classified stays policy instead of shipping env-reachable.
    """

    ENV_SETTABLE: ClassVar[frozenset[str]] = frozenset({"respect_gitignore"})

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        """The env value for one field, or a miss for every field outside the allow-list.

        Filtering here rather than on the result matters: the base source JSON-decodes complex
        fields first, so a policy key with an unparseable value would hard-fail the command.
        """
        if field_name not in self.ENV_SETTABLE:
            return None, field_name, False
        return super().get_field_value(field, field_name)


class AuditorSettings(BaseSettings):
    """The merged repo configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AUDITOR_", extra="ignore", validate_default=False
    )

    extends: str = "base"
    exclude: list[str] = Field(default_factory=list)
    resolve_packages: list[str] = Field(
        default_factory=list
    )  # package-name prefixes whose installed source the callee resolver may read (default: none)
    respect_gitignore: bool = (
        True  # skip git-ignored files (CLI: --include-gitignored to override)
    )
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
    observer_allowed: bool = (
        True  # repo's hard opt-out for the graph observer; never under graph.*
    )
    # PY-CONFIG-SCATTERED-SETTINGS: modules that may hold BaseSettings, and whether to also bless
    # the de-facto home (the module where settings classes already cluster).
    settings_modules: list[str] = Field(default_factory=lambda: ["config", "settings"])
    settings_cohesion: bool = True
    # CLI frameworks whose free-function-command idiom should exempt a module from the OOP
    # orchestrator / cross-file duplicate-function heuristics (they thread a context object and
    # repeat passthrough shapes by design). Extend for an in-house CLI framework.
    cli_frameworks: list[str] = Field(default_factory=lambda: ["typer", "click"])
    diff_base: str | None = (
        None  # `scan --vs-base` ref; None auto-detects main/master/develop/development
    )
    design_system: DesignSystem = Field(default_factory=DesignSystem)
    sqlalchemy: SqlAlchemyConfig = Field(default_factory=SqlAlchemyConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    malware_scan: MalwareScanConfig = Field(default_factory=MalwareScanConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _NonPolicyEnvSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

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

    @field_serializer("rules")
    def _ser_rules(
        self, value: dict[RuleId, RuleConfig], info: FieldSerializationInfo
    ) -> dict[RuleId, object]:
        # Coerce any raw-dict rule value (e.g. from model_copy(update=...), which skips
        # validation) into a RuleConfig before dumping, so serialization never emits a
        # PydanticSerializationUnexpectedValue warning.
        return {
            rid: (
                rc if isinstance(rc, RuleConfig) else RuleConfig.model_validate(rc)
            ).model_dump(mode=info.mode)
            for rid, rc in value.items()
        }

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


def deep_merge(
    base: Mapping[str, object], override: Mapping[str, object]
) -> dict[str, object]:
    """Recursive dict merge; later wins. Scalars/lists replaced, dicts merged."""
    out: dict[str, object] = dict(base)
    for key, val in override.items():
        current = out.get(key)
        if isinstance(val, dict) and isinstance(current, dict):
            out[key] = deep_merge(current, val)
        else:
            out[key] = val
    return out


def _walk_unknown(
    model: type[BaseModel], raw: dict[str, object], prefix: str
) -> Iterator[str]:
    for key, value in raw.items():
        path = f"{prefix}{key}"
        field = model.model_fields.get(key)
        if field is None:
            yield path
        else:
            yield from _walk_unknown_value(field.annotation, value, path)


def _walk_unknown_value(annotation: object, value: object, path: str) -> Iterator[str]:
    """Descend one field's declared type into the raw value, so a typo inside a nested table,
    a keyed table or a list of tables reports its full dotted path."""
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        # A key is unknown only if no member declares it; taking members[0] alone reported
        # every key valid for a later member of a two-model union.
        per_member = [
            set(_walk_unknown_value(arg, value, path))
            for arg in get_args(annotation)
            if arg is not type(None)
        ]
        if per_member:
            yield from sorted(set.intersection(*per_member))
    elif origin is dict and isinstance(value, dict):
        item = get_args(annotation)[1]
        for key, entry in value.items():
            yield from _walk_unknown_value(item, entry, f"{path}.{key}")
    elif origin is list and isinstance(value, list):
        item = get_args(annotation)[0]
        for index, entry in enumerate(value):
            yield from _walk_unknown_value(item, entry, f"{path}[{index}]")
    elif (
        isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
        and isinstance(value, dict)
    ):
        yield from _walk_unknown(annotation, value, f"{path}.")


def unknown_config_keys(raw: dict[str, object], model: type[BaseModel]) -> list[str]:
    """Dotted paths in a raw config dict that ``model`` does not declare. The models ignore
    extras (D8), so this is how a typo or a key from a newer auditor is still reported."""
    return sorted(_walk_unknown(model, raw, ""))


class ConfigReport(BaseModel):
    """A loaded config plus the keys no model declares.

    The unknown keys are a value, never a ``warnings.warn``: only the CLI edge decides whether a
    human sees them, and it prints to stderr so machine output on stdout stays parseable.
    """

    model_config = ConfigDict(frozen=True)

    settings: AuditorSettings
    unknown_keys: tuple[str, ...] = ()


def _load_profile(name_or_path: str, _seen: frozenset[str] = frozenset()) -> dict:
    """Load a built-in profile by name or a TOML file by path, resolving ``extends``."""
    if name_or_path in _seen:
        raise ValueError(f"circular profile extends: {name_or_path}")
    raw = _read_profile_toml(name_or_path)
    parent = raw.pop("extends", None)
    if parent:
        base = _load_profile(parent, _seen | {name_or_path})
        return deep_merge(base, raw)
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


def is_configured(root: Path) -> bool:
    """True if the repo has been configured — a standalone ``.auditor/config.toml``
    or a ``[tool.auditor]`` table in ``pyproject.toml`` (the two sources
    ``_read_repo_tomls`` layers together)."""
    if (root / ".auditor" / "config.toml").exists():
        return True
    pp = root / "pyproject.toml"
    if not pp.exists():
        return False
    try:
        data = tomllib.loads(pp.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "auditor" in data.get("tool", {})


def merged_config_dict(
    root: Path, *, profile: str | None = None, overrides: dict | None = None
) -> dict:
    """Layer profile -> pyproject -> .auditor/config.toml -> injected ``overrides`` into one raw
    dict (pre-validation). ``profile`` overrides the repo's ``extends`` for this run; ``overrides``
    (e.g. CLI ``--config-json``) is the highest config layer."""
    pyproject, standalone = _read_repo_tomls(root)
    overrides = overrides or {}
    extends = (
        profile
        or overrides.get("extends")
        or standalone.get("extends")
        or pyproject.get("extends")
        or "base"
    )
    merged = _load_profile(extends)
    merged = deep_merge(merged, pyproject)
    merged = deep_merge(merged, standalone)
    merged = deep_merge(merged, overrides)
    merged["extends"] = extends
    return merged


def unknown_repo_keys(
    root: Path, *, profile: str | None = None, overrides: dict | None = None
) -> list[str]:
    """Unknown keys in the config ``root`` resolves to, from the raw merge alone. For commands
    whose real load happens deeper in the stack, so they do not pay for a second plugin load."""
    return unknown_config_keys(
        merged_config_dict(root, profile=profile, overrides=overrides), AuditorSettings
    )


def load_config_report(
    root: Path,
    *,
    profile: str | None = None,
    allow_local_plugins: bool = False,
    loader: "PluginLoader | None" = None,
    overrides: dict | None = None,
) -> ConfigReport:
    """Two-phase load: read raw config, load the plugins it names (so a config can
    reference plugin-contributed rules), then validate against the populated registry.

    ``profile`` overrides the repo's ``extends`` for this run. Entry-point and config-named
    plugins load unconditionally; local ``.auditor/plugins`` load only when trusted.
    ``overrides`` deep-merges onto the loaded config as the highest layer. Unknown keys are
    returned on the report, never warned about here.
    """
    raw = merged_config_dict(root, profile=profile, overrides=overrides)
    loader = loader if loader is not None else PluginLoader()
    loader.load_entry_points()
    loader.load_config_modules(list(raw.get("plugins", [])))
    trusted = allow_local_plugins or bool(raw.get("trust_local_plugins", False))
    loader.load_local(root, trusted=trusted)
    return ConfigReport(
        settings=AuditorSettings.model_validate(raw),
        unknown_keys=tuple(unknown_config_keys(raw, AuditorSettings)),
    )


def load_config(
    root: Path,
    *,
    profile: str | None = None,
    allow_local_plugins: bool = False,
    loader: "PluginLoader | None" = None,
    overrides: dict | None = None,
) -> AuditorSettings:
    """The merged, validated repo configuration. Use :func:`load_config_report` when the caller
    also has to surface the keys no model declares."""
    return load_config_report(
        root,
        profile=profile,
        allow_local_plugins=allow_local_plugins,
        loader=loader,
        overrides=overrides,
    ).settings


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
