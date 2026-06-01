"""ManifestEntry.from_module: AST class+function manifest, exercised on the real sample-repo
fixture files (no inline module strings)."""

import ast

from _support import SAMPLE_REPO

from auditor.models import ManifestEntry, ManifestEntryKind


def _manifest(rel: str) -> dict[str, ManifestEntry]:
    src = (SAMPLE_REPO / rel).read_text()
    return {e.symbol: e for e in ManifestEntry.from_module(ast.parse(src))}


def test_model_fields_and_basemodel_flag():
    by_symbol = _manifest("src/models.py")
    opp = by_symbol["OpportunityRecord"]
    assert opp.kind is ManifestEntryKind.CLASS
    assert opp.field_count == 12
    assert "BASEMODEL" in opp.flags
    assert "DATACLASS" in by_symbol["CacheKey"].flags


def test_static_method_class_and_dataclass():
    by_symbol = _manifest("src/processing.py")
    assert "ALL_STATICMETHODS" in by_symbol["StringUtils"].flags
    assert "DATACLASS" in by_symbol["ReportBuilder"].flags
    # methods are recorded with their owner prefix
    assert by_symbol["StringUtils.slug"].kind is ManifestEntryKind.METHOD


def test_function_flags_from_fixture():
    by_symbol = _manifest("src/web.py")
    untyped = by_symbol["untyped"]
    assert untyped.kind is ManifestEntryKind.FUNCTION
    assert "UNTYPED_ARGS" in untyped.flags
    assert "UNTYPED_RETURN" in untyped.flags
    # the route handler returns dict[str, Any]
    assert "UNTYPED_DICT_RETURN" in by_symbol["health"].flags


def test_async_method_flag():
    by_symbol = _manifest("src/async_service.py")
    poll = by_symbol["Ingestor.poll_blocking"]
    assert poll.is_async is True
    assert "ASYNC" in poll.flags
