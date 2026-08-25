"""One frozen model per CLI payload: the wire contract ``present`` dumps and ``render_*`` reads.

The keys are declared once, so a renderer that reads a field the command never sets fails loudly
at runtime instead of rendering a silently blank column. Values a renderer derives for display
alone (a joined name, a column total) stay in the renderer and are not on the wire.
"""

from collections.abc import Sequence

from pydantic import ConfigDict, Field

from auditor.models import FileRole, IndexEntry, ManifestEntry
from auditor.payload import WirePayload, WireRows
from auditor.registry import RuleRow


class CrossfileReport(WirePayload):
    """``auditor crossfile``."""

    cross_file_findings: int


class DiscoveredFile(WirePayload):
    """One auditable file and the role the classifier gave it."""

    file: str
    role: FileRole


class DiscoverReport(WireRows[DiscoveredFile]):
    """``auditor discover``: a JSON array, one object per auditable file."""


class ManifestReport(WireRows[ManifestEntry]):
    """``auditor manifest``: a JSON array of the file's class and function entries."""


class ConfigCheckReport(WirePayload):
    """``auditor config check``: the keys neither settings model declares, per family."""

    root: str
    policy_unknown: tuple[str, ...] = ()
    user_unknown: tuple[str, ...] = ()


class InitReport(WirePayload):
    """``auditor init``: where the home is, what was written, and what needs the user's attention.

    ``schema_path`` carries the ``schema`` key: a field named ``schema`` shadows a deprecated
    ``BaseModel`` method, so the wire name is a serialization alias.
    """

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


class IndexAddReport(WirePayload):
    """``auditor index add``: the repo-relative paths registered as the audit scope."""

    added: tuple[str, ...] = ()


class IndexListReport(WireRows[IndexEntry]):
    """``auditor index list``: one row per indexed file."""


class RepoRow(WirePayload):
    """One row of the shared index's repo registry."""

    repo: str
    name: str
    last_scanned: float


class IndexReposReport(WireRows[RepoRow]):
    """``auditor index repos``."""


class IndexForgetReport(WirePayload):
    """``auditor index forget``."""

    repo: str
    removed: bool


class IgnoreAddReport(WirePayload):
    """``auditor ignore add``. ``note`` explains a line-level add that found no current finding."""

    id: int
    rule_id: str
    file: str | None = None
    line: int | None = None
    reason: str | None = None
    note: str | None = None


class IgnoreRow(WirePayload):
    """One persisted ignore, as stored."""

    id: int
    rule_id: str
    file: str | None = None
    line: int | None = None
    evidence_hash: str | None = None
    reason: str | None = None
    created_at: float


class IgnoreListReport(WireRows[IgnoreRow]):
    """``auditor ignore list``."""


class IgnoreRmReport(WirePayload):
    """``auditor ignore rm``."""

    removed: bool
    selector: str


class IgnoreClearReport(WirePayload):
    """``auditor ignore clear``."""

    cleared: int


class RulesListReport(WireRows[RuleRow]):
    """``auditor rules list``, over the registry's own catalogue row."""


class DetectorInfo(WirePayload):
    """One registered detector in the plugin snapshot.

    ``extra="forbid"``: the registry is the source, so a key it gains must be declared here or
    fail loudly, never be dropped on the way to the wire.
    """

    model_config = ConfigDict(extra="forbid")

    category: str
    framework: str | None = None
    source: str


class SourceInfo(WirePayload):
    """A registered language auditor or reporter, and where it came from."""

    model_config = ConfigDict(extra="forbid")

    source: str


class PluginsReport(WirePayload):
    """``auditor plugins list``: the registry snapshot plus the loader's warnings.

    ``extra="forbid"`` for the reason the entries carry it, one level up: a section the registry
    gains has to be declared here or fail loudly, never vanish between the snapshot and the wire.
    """

    model_config = ConfigDict(extra="forbid")

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


class GraphBuildReport(WirePayload):
    """``auditor graph build``: what the build landed. Validated from ``GraphBuilder.rebuild``'s
    summary, which the MCP tool returns as-is."""

    nodes: int
    edges: int
    clusters: int
    unresolved: int
    findings: int
    refined: int
    expired: int
