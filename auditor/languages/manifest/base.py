"""Manifest audit context + ``ManifestDetector`` base.

A manifest is parsed once into a structured ``data`` payload plus its raw ``lines`` (so a finding
can anchor to the offending line). Detectors branch on ``manifest_type`` and ignore manifests they
don't handle, so one detector can span package.json / pyproject / requirements as coverage grows.
"""

import json
from typing import TYPE_CHECKING, Any, ClassVar

from auditor.languages.base import Detector, LineIndexed
from auditor.models import FileRole, Finding

if TYPE_CHECKING:
    from auditor.config import ResolvedConfig

NPM = "npm"
UNKNOWN = "unknown"


def manifest_type(file_path: str) -> str:
    """Classify a manifest by its basename. Only ``package.json`` is handled today; everything else
    resolves to ``unknown`` — the auditor does not do dependency-graph scanning, so pyproject/
    requirements manifests are not parsed here (a filename + parser is all it'd take to add one)."""
    return NPM if file_path.rsplit("/", 1)[-1] == "package.json" else UNKNOWN


def _parse(  # auditor: skip: PY-TYPING-UNTYPED-DICT  (JSON parse boundary — arbitrary manifest structure)
    kind: str, source: str
) -> dict[str, Any]:
    """Structured payload for the manifest, or ``{}`` if it doesn't parse (a malformed manifest is
    surfaced as 'nothing to check', never an exception)."""
    if kind != NPM:
        return {}
    try:
        data = json.loads(source)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class ManifestContext(LineIndexed):
    """Everything a manifest detector needs for one file."""

    __slots__ = (
        "file_path",
        "source",
        "lines",
        "role",
        "config",
        "manifest_type",
        "data",
    )

    def __init__(
        self,
        *,
        file_path: str,
        source: str,
        role: FileRole,
        config: "ResolvedConfig",
    ) -> None:
        self.file_path = file_path
        self.source = source
        self.lines = source.splitlines()
        self.role = role
        self.config = config
        self.manifest_type = manifest_type(file_path)
        self.data = _parse(self.manifest_type, source)

    def line_of(self, *needles: str) -> int:
        """1-indexed line of the first raw line containing every needle (a best-effort anchor for
        a JSON/TOML key, whose value sits on its own line), else 1."""
        for i, text in enumerate(self.lines, 1):
            if all(n in text for n in needles):
                return i
        return 1


class ManifestDetector(Detector):
    """Base for manifest rules. Subclass, set the ClassVars, implement ``run``."""

    abstract: ClassVar[bool] = True
    language: ClassVar[str] = "manifest"

    def run(self, ctx: "ManifestContext") -> list[Finding]:  # type: ignore[override]
        raise NotImplementedError
