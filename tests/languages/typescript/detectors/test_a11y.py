"""TS detectors in a11y.py: flags each anti-pattern, ignores the clean version."""

import pytest
from _support import rule_ids, run_ts_audit
from _ts_cases import GROUPS

_CASES = GROUPS["a11y"]


@pytest.mark.parametrize("rule_id, bad, good", _CASES, ids=[c[0] for c in _CASES])
def test_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_ts_audit(bad)), f"{rule_id} did not flag its anti-pattern"
    assert rule_id not in rule_ids(run_ts_audit(good)), f"{rule_id} false-positived on clean code"


def test_onclick_on_native_interactive_element_is_fine():
    # a real <button>/<a> already has keyboard + focus semantics
    for src in ("<button onClick={go}>x</button>;\n", '<a href="/x" onClick={go}>x</a>;\n'):
        assert "TS-A11Y-NONINTERACTIVE-ONCLICK" not in rule_ids(run_ts_audit(src))


def test_noninteractive_onclick_generalizes_beyond_div():
    assert "TS-A11Y-NONINTERACTIVE-ONCLICK" in rule_ids(
        run_ts_audit("<li onClick={go}>row</li>;\n")
    )


def test_icon_button_with_text_is_not_flagged():
    src = "const x = (\n  <Button>\n    <CloseIcon /> Close\n  </Button>\n);\n"
    assert "TS-A11Y-ICON-BUTTON-NO-LABEL" not in rule_ids(run_ts_audit(src))


def test_icon_button_matches_any_button_named_component():
    # not hardcoded to "Button" — any *Button component counts
    assert "TS-A11Y-ICON-BUTTON-NO-LABEL" in rule_ids(
        run_ts_audit("const x = <IconButton><Gear /></IconButton>;\n".replace("Gear", "GearIcon"))
    )


def test_decorative_img_with_aria_hidden_is_fine():
    assert "TS-A11Y-IMG-NO-ALT" not in rule_ids(
        run_ts_audit('<img src="d.png" aria-hidden />;\n')
    )


def test_negative_tabindex_is_fine():
    assert "TS-A11Y-POSITIVE-TABINDEX" not in rule_ids(
        run_ts_audit("<div tabIndex={-1}>x</div>;\n")
    )
