"""auditor — a token-efficient repo auditor for coding agents.

Public API:
    from auditor import scan_file, scan_path, load_config, render, ScanEngine, IndexStore
"""

from auditor.config import AuditorSettings, ResolvedConfig, load_config
from auditor.engine import ScanEngine, audit_target
from auditor.index import IndexStore
from auditor.models import (
    Category,
    FileRole,
    Finding,
    IndexEntry,
    ManifestEntry,
    ScanResult,
    Severity,
    VerdictKind,
)
from auditor.reporters import render

__all__ = [
    "AuditorSettings",
    "Category",
    "FileRole",
    "Finding",
    "IndexEntry",
    "IndexStore",
    "ManifestEntry",
    "ResolvedConfig",
    "ScanEngine",
    "ScanResult",
    "Severity",
    "VerdictKind",
    "audit_target",
    "load_config",
    "render",
]

__version__ = "0.1.0"
