"""One frozen model per CLI payload: the wire contract ``present`` dumps and ``render_*`` reads.

The keys are declared once, so a renderer that reads a field the command never sets fails loudly
at runtime instead of rendering a silently blank column, and ``--json`` cannot drift from the
pretty output.
"""

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, RootModel

from auditor.models import FileRole, IndexEntry, ManifestEntry
from auditor.registry import RuleRow


class CrossfileReport(BaseModel):
    """``auditor crossfile``."""

    model_config = ConfigDict(frozen=True)

    cross_file_findings: int


class DiscoveredFile(BaseModel):
    """One auditable file and the role the classifier gave it."""

    model_config = ConfigDict(frozen=True)

    file: str
    role: FileRole


class DiscoverReport(RootModel[tuple[DiscoveredFile, ...]]):
    """``auditor discover``: a JSON array, one object per auditable file."""

    model_config = ConfigDict(frozen=True)


class ManifestReport(RootModel[tuple[ManifestEntry, ...]]):
    """``auditor manifest``: a JSON array of the file's class and function entries."""

    model_config = ConfigDict(frozen=True)


class ConfigCheckReport(BaseModel):
    """``auditor config check``: the keys neither settings model declares, per family."""

    model_config = ConfigDict(frozen=True)

    root: str
    policy_unknown: tuple[str, ...] = ()
    user_unknown: tuple[str, ...] = ()


class InitReport(BaseModel):
    """``auditor init``: where the home is, what was written, and what needs the user's attention.

    ``schema_path`` carries the ``schema`` key: a field named ``schema`` shadows a deprecated
    ``BaseModel`` method, so the wire name is a serialization alias.
    """

    model_config = ConfigDict(frozen=True)

    home: str
    config: str
    schema_path: str = Field(serialization_alias="schema")
    repo_dir: str | None = None
    written: tuple[str, ...] = ()
    checked: bool = False
    unknown_keys: tuple[str, ...] = ()
    moved_from: str | None = None
    migrated: bool = False
    legacy_status: str | None = None


class IndexAddReport(BaseModel):
    """``auditor index add``: the repo-relative paths registered as the audit scope."""

    model_config = ConfigDict(frozen=True)

    added: tuple[str, ...] = ()


class IndexListReport(RootModel[tuple[IndexEntry, ...]]):
    """``auditor index list``: one row per indexed file."""

    model_config = ConfigDict(frozen=True)


class RepoRow(BaseModel):
    """One row of the shared index's repo registry."""

    model_config = ConfigDict(frozen=True)

    repo: str
    name: str
    last_scanned: float


class IndexReposReport(RootModel[tuple[RepoRow, ...]]):
    """``auditor index repos``."""

    model_config = ConfigDict(frozen=True)


class IndexForgetReport(BaseModel):
    """``auditor index forget``."""

    model_config = ConfigDict(frozen=True)

    repo: str
    removed: bool


class IgnoreAddReport(BaseModel):
    """``auditor ignore add``. ``note`` explains a line-level add that found no current finding."""

    model_config = ConfigDict(frozen=True)

    id: int
    rule_id: str
    file: str | None = None
    line: int | None = None
    reason: str | None = None
    note: str | None = None


class IgnoreRow(BaseModel):
    """One persisted ignore, as stored."""

    model_config = ConfigDict(frozen=True)

    id: int
    rule_id: str
    file: str | None = None
    line: int | None = None
    evidence_hash: str | None = None
    reason: str | None = None
    created_at: float


class IgnoreListReport(RootModel[tuple[IgnoreRow, ...]]):
    """``auditor ignore list``."""

    model_config = ConfigDict(frozen=True)


class IgnoreRmReport(BaseModel):
    """``auditor ignore rm``."""

    model_config = ConfigDict(frozen=True)

    removed: bool
    selector: str


class IgnoreClearReport(BaseModel):
    """``auditor ignore clear``."""

    model_config = ConfigDict(frozen=True)

    cleared: int


class RulesListReport(RootModel[tuple[RuleRow, ...]]):
    """``auditor rules list``, over the registry's own catalogue row."""

    model_config = ConfigDict(frozen=True)


class DetectorInfo(BaseModel):
    """One registered detector in the plugin snapshot.

    ``extra="forbid"``: the registry is the source, so a key it gains must be declared here or
    fail loudly, never be dropped on the way to the wire.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    framework: str | None = None
    source: str


class SourceInfo(BaseModel):
    """A registered language auditor or reporter, and where it came from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str


class PluginsReport(BaseModel):
    """``auditor plugins list``: the registry snapshot plus the loader's warnings."""

    model_config = ConfigDict(frozen=True)

    detectors: dict[str, DetectorInfo] = Field(default_factory=dict)
    languages: dict[str, SourceInfo] = Field(default_factory=dict)
    reporters: dict[str, SourceInfo] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @classmethod
    def of(
        cls, snapshot: dict[str, object], *, warnings: Sequence[str]
    ) -> "PluginsReport":
        """Validate a ``Registry.snapshot()`` and the loader's warnings into one payload."""
        return cls.model_validate({**snapshot, "warnings": tuple(warnings)})


class GraphBuildReport(BaseModel):
    """``auditor graph build``: what the build landed. Validated from ``GraphBuilder.rebuild``'s
    summary, which the MCP tool returns as-is."""

    model_config = ConfigDict(frozen=True)

    nodes: int
    edges: int
    clusters: int
    unresolved: int
    findings: int
    refined: int
    expired: int
