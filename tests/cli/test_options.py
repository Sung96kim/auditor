"""Shared CLI option shape: the flags in `cli/options.py` and the confirmations that mirror
each other across commands."""

import re

import pytest
from _support import invoke

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _help(*command: str) -> str:
    """`--help` output with styling and whitespace removed, so neither rich's colors (on under
    CI) nor its wrapping can split a token."""
    result = invoke(*command, "--help")
    assert result.exit_code == 0, result.output
    return "".join(_ANSI.sub("", result.output).split())


@pytest.mark.parametrize(
    "command",
    [("index", "forget"), ("malware", "install"), ("self", "update")],
    ids=lambda c: " ".join(c),
)
def test_yes_flag_has_a_short_alias(command):
    """Every confirmation flag spells the same pair, so `-y` works on all of them."""
    out = _help(*command)
    assert "--yes" in out and "-y" in out


@pytest.mark.parametrize(
    "command",
    [("rules", "list"), ("plugins", "list"), ("index", "forget")],
    ids=lambda c: " ".join(c),
)
def test_root_option_documents_itself(command):
    """`-r`/`--root` carries help text wherever it is mounted."""
    out = _help(*command)
    assert "--root" in out and "Repowhoseconfigandpluginsload" in out
