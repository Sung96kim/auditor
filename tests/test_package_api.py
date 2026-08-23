"""The ``auditor`` package surface: ``__all__`` resolves, and the docstrings that advertise API
names can resolve them."""

import re

import pytest

import auditor
import auditor.engine
import auditor.registry

_ADVERTISED = re.search(r"from auditor import ([^\n]+)", auditor.__doc__ or "")
#: modules whose docstring advertises API names in double backticks
_ADVERTISING_MODULES = [auditor, auditor.engine, auditor.registry]


def _provided(module) -> set[str]:
    """Names the module offers: its own attributes, plus the methods of classes it defines."""
    names = set(vars(module))
    for value in vars(module).values():
        if isinstance(value, type) and value.__module__ == module.__name__:
            names |= set(vars(value))
    return names


@pytest.mark.parametrize("name", sorted(auditor.__all__))
def test_exported_name_resolves(name):
    assert getattr(auditor, name, None) is not None


def test_docstring_advertises_only_exported_names():
    assert _ADVERTISED is not None, (
        "the module docstring lost its `from auditor import` line"
    )
    advertised = {n.strip() for n in _ADVERTISED.group(1).split(",")}
    assert advertised <= set(auditor.__all__)


@pytest.mark.parametrize("module", _ADVERTISING_MODULES, ids=lambda m: m.__name__)
def test_docstring_names_only_symbols_the_module_provides(module):
    """A docstring that names ``symbol`` must be able to resolve it, so prose cannot drift off
    the code it describes."""
    named = set(re.findall(r"``([A-Za-z_][A-Za-z_0-9]*)``", module.__doc__ or ""))
    missing = named - _provided(module)
    assert not missing, f"{module.__name__} advertises {sorted(missing)}"
