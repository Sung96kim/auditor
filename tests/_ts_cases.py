"""Per-detector (rule_id, bad, good) snippets for the TS/React detectors — the TS analog of
``tests/_detector_cases.py``. ``bad`` must flag the rule; ``good`` must not.

Every rule here is objective + framework-agnostic (a11y / structure) — the auditor encodes no
design-system vocabulary, so there are no "raw markup -> specific primitive" cases."""

GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "style": [
        (
            "TS-STYLE-DUPLICATE-IMPORT",
            'import { a } from "x";\nimport { b } from "x";\n',
            'import { a, b } from "x";\n',
        ),
    ],
    "react": [
        (
            "TS-REACT-MULTI-COMPONENT-FILE",
            "export function A() {\n  return <div />;\n}\nexport function B() {\n  return <span />;\n}\n",
            "export function A() {\n  return <div />;\n}\nfunction helper() {\n  return 1;\n}\n",
        ),
        (
            "TS-REACT-REPEATED-JSX",
            "const x = (\n  <ul>\n    <li><a>1</a></li>\n    <li><a>2</a></li>\n    <li><a>3</a></li>\n  </ul>\n);\n",
            "const x = (\n  <ul>\n    <li><a>1</a></li>\n  </ul>\n);\n",
        ),
    ],
    "a11y": [
        (
            "TS-A11Y-NONINTERACTIVE-ONCLICK",
            "const x = <div onClick={go}>label</div>;\n",
            'const x = (\n  <div role="button" tabIndex={0} onClick={go} onKeyDown={k}>\n    label\n  </div>\n);\n',
        ),
        (
            "TS-A11Y-ICON-BUTTON-NO-LABEL",
            "const x = (\n  <Button>\n    <CloseIcon />\n  </Button>\n);\n",
            'const x = (\n  <Button aria-label="close">\n    <CloseIcon />\n  </Button>\n);\n',
        ),
        (
            "TS-A11Y-IMG-NO-ALT",
            'const x = <img src="logo.png" />;\n',
            'const x = <img src="logo.png" alt="logo" />;\n',
        ),
        (
            "TS-A11Y-POSITIVE-TABINDEX",
            "const x = <div tabIndex={3}>x</div>;\n",
            "const x = <div tabIndex={0}>x</div>;\n",
        ),
    ],
}
