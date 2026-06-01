"""reporters/base.py: the render() dispatch + unknown-format error."""

import pytest
from _support import demo_result

from auditor.reporters import render


def test_render_dispatches_known_formats():
    for fmt in ("json", "sarif", "md"):
        assert render([demo_result()], fmt)


def test_unknown_format_errors():
    with pytest.raises(ValueError, match="unknown format"):
        render([demo_result()], "xml")
