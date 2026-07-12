---
name: write-detector
description: Author a new repo-local auditor detector under .auditor/plugins/, with a required test. Use when adding a custom lint/anti-pattern rule to auditor.
paths: "**/*.py"
---

Add a repo-local detector to auditor.

## Steps

1. Ground yourself in the current API: `auditr plugins list` and `auditr rules list`. Read a nearby
   built-in detector in the `auditor` package to copy its exact base class + registration.
2. Copy `template.py` (in this skill dir) into `.auditor/plugins/<name>.py` and adapt: set `rule_id`
   (`LOCAL-` prefix), `category`, `default_severity`, `verdict_kind`, and the check logic.
3. Write the **required** test (new production code must have tests): a fixture with a known
   violation asserting the rule fires on the right line, plus a clean fixture that yields nothing.
4. Verify: `auditr plugins list` shows the detector; `auditr report <fixture> -f json` flags it.

See `references/plugin-api.md` for details. `verdict_kind`: `auto` = tool decides, `candidate` =
agent judges.
