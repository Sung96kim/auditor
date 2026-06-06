"""design_system.py: the opt-in, config-driven DS rules. They fire only when the project
declares its design system, and check raw markup against *that* declared vocabulary."""

import pytest
from _support import rule_ids, run_ts_audit

from auditor.config import AuditorSettings

_DS = AuditorSettings.model_validate(
    {
        "design_system": {
            "ui_paths": ["@/components/ui"],
            "shell": "@/lib/ui",
            "primitives": [
                {"component": "Badge", "when_class": r"rounded-full bg-\w+-\d+/\d+"},
                {"component": "Button", "size_override": True},
            ],
        }
    }
)


def test_ds_rules_silent_without_a_declared_design_system():
    src = (
        'import { Button } from "@/components/ui/button";\n'
        'const x = <span className="rounded-full bg-red-500/10">Err</span>;\n'
    )
    found = rule_ids(run_ts_audit(src))  # default settings — no design_system
    assert not {r for r in found if r.startswith("TS-DS-")}


def test_direct_ui_import_flagged_when_declared():
    src = 'import { Button } from "@/components/ui/button";\n'
    assert "TS-DS-DIRECT-UI-IMPORT" in rule_ids(run_ts_audit(src, settings=_DS))


def test_direct_ui_import_exempts_the_ui_layer_itself():
    src = 'import { cn } from "@/components/ui/utils";\n'
    found = rule_ids(
        run_ts_audit(src, settings=_DS, rel_path="src/components/ui/badge.tsx")
    )
    assert "TS-DS-DIRECT-UI-IMPORT" not in found


def test_inline_primitive_matches_declared_pattern_with_text():
    bad = 'const x = <span className="rounded-full bg-emerald-500/10">Active</span>;\n'
    assert "TS-DS-INLINE-PRIMITIVE" in rule_ids(run_ts_audit(bad, settings=_DS))


def test_inline_primitive_skips_icon_only_backdrop():
    # requires_text defaults True — a coloured disc behind an icon is not the primitive
    src = (
        'const x = <div className="rounded-full bg-amber-500/10"><WarnIcon /></div>;\n'
    )
    assert "TS-DS-INLINE-PRIMITIVE" not in rule_ids(run_ts_audit(src, settings=_DS))


def test_inline_primitive_skips_the_primitive_itself():
    src = 'const x = <Badge className="rounded-full bg-red-500/10">Err</Badge>;\n'
    assert "TS-DS-INLINE-PRIMITIVE" not in rule_ids(run_ts_audit(src, settings=_DS))


def test_size_override_flagged_for_declared_component():
    bad = 'const x = <Button className="h-7 w-7" />;\n'
    good = 'const x = <Button size="icon" />;\n'
    assert "TS-DS-SIZE-OVERRIDE" in rule_ids(run_ts_audit(bad, settings=_DS))
    assert "TS-DS-SIZE-OVERRIDE" not in rule_ids(run_ts_audit(good, settings=_DS))


# Additional real-world cases (verified against the declared _DS): each `bad` flags the rule and
# the `good` look-alike (shell import / icon-only / size prop) stays quiet.
_DS_CASES = [
    (
        "TS-DS-DIRECT-UI-IMPORT",
        'import { Input } from "@/components/ui/input";\nexport function SearchBar() {\n  return <Input placeholder="Search..." />;\n}\n',
        'import { Input } from "@/lib/ui";\nexport function SearchBar() {\n  return <Input placeholder="Search..." />;\n}\n',
    ),
    (
        "TS-DS-DIRECT-UI-IMPORT",
        'import { Select, SelectItem, SelectTrigger } from "@/components/ui/select";\n',
        'import { Select, SelectItem } from "@/lib/selects";\n',
    ),
    (
        "TS-DS-INLINE-PRIMITIVE",
        'const x = <span className="rounded-full bg-blue-500/10 text-sm px-2">Active</span>;\n',
        'const x = <div className="rounded-full bg-blue-500/10 p-1"><StatusIcon /></div>;\n',
    ),
    (
        "TS-DS-INLINE-PRIMITIVE",
        'const x = <div className="rounded-full bg-yellow-500/20 font-medium">Warning</div>;\n',
        'const x = <Badge className="rounded-full bg-yellow-500/20">Warning</Badge>;\n',
    ),
    (
        "TS-DS-SIZE-OVERRIDE",
        'const x = <Button className="h-9 w-32 font-semibold" onClick={handleSubmit}>Submit</Button>;\n',
        'const x = <Button size="sm" onClick={handleSubmit}>Submit</Button>;\n',
    ),
    (
        "TS-DS-SIZE-OVERRIDE",
        'const x = <Button className="h-8 w-8 rounded-full" variant="ghost"><TrashIcon /></Button>;\n',
        'const x = <Button size="icon" variant="ghost"><TrashIcon /></Button>;\n',
    ),
]


@pytest.mark.parametrize("rule_id, bad, good", _DS_CASES, ids=[c[0] for c in _DS_CASES])
def test_ds_flags_bad_ignores_good(rule_id, bad, good):
    assert rule_id in rule_ids(run_ts_audit(bad, settings=_DS))
    assert rule_id not in rule_ids(run_ts_audit(good, settings=_DS))
