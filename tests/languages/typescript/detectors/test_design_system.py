"""design_system.py: the opt-in, config-driven DS rules. They fire only when the project
declares its design system, and check raw markup against *that* declared vocabulary."""

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
