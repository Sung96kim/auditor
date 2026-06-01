"""manifest.py: AST class+function manifest."""

import ast

from auditor.languages.python.manifest import ManifestBuilder
from auditor.models import ManifestEntryKind

_SRC = '''
from dataclasses import dataclass
from pydantic import BaseModel

class Payload(BaseModel):
    a: int
    b: str

@dataclass
class Row:
    x: int

class StringUtils:
    @staticmethod
    def slug(value):
        return value

    @staticmethod
    def trim(value):
        return value

class Service:
    @staticmethod
    def helper():
        return 1

    async def fetch(self, url: str) -> dict[str, Any]:
        return {}

def free(x):
    return x
'''


def _manifest():
    return ManifestBuilder(ast.parse(_SRC)).build()


def test_manifest_entries():
    by_symbol = {e.symbol: e for e in _manifest()}
    assert by_symbol["Payload"].kind is ManifestEntryKind.CLASS
    assert by_symbol["Payload"].field_count == 2
    assert "BASEMODEL" in by_symbol["Payload"].flags
    assert "DATACLASS" in by_symbol["Row"].flags
    assert "ALL_STATICMETHODS" in by_symbol["StringUtils"].flags
    assert "ALL_STATICMETHODS" not in by_symbol["Service"].flags  # mixed static/instance


def test_function_flags():
    by_symbol = {e.symbol: e for e in _manifest()}
    fetch = by_symbol["Service.fetch"]
    assert fetch.kind is ManifestEntryKind.METHOD
    assert fetch.is_async is True
    assert "ASYNC" in fetch.flags
    assert "UNTYPED_DICT_RETURN" in fetch.flags
    free = by_symbol["free"]
    assert free.kind is ManifestEntryKind.FUNCTION
    assert "UNTYPED_RETURN" in free.flags
    assert "UNTYPED_ARGS" in free.flags
