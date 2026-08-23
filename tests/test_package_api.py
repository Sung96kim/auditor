"""The ``auditor`` package surface: ``__all__`` resolves, and the docstring advertises only
names it actually exports."""

import re

import pytest

import auditor

_ADVERTISED = re.search(r"from auditor import ([^\n]+)", auditor.__doc__ or "")


@pytest.mark.parametrize("name", sorted(auditor.__all__))
def test_exported_name_resolves(name):
    assert getattr(auditor, name, None) is not None


def test_docstring_advertises_only_exported_names():
    assert _ADVERTISED is not None, (
        "the module docstring lost its `from auditor import` line"
    )
    advertised = {n.strip() for n in _ADVERTISED.group(1).split(",")}
    assert advertised <= set(auditor.__all__)
