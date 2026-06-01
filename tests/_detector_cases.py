"""Per-detector positive/negative fixtures, grouped by the source module that defines the
rule. Each per-module test file imports its group; test_detectors.py checks total coverage.

Not collected by pytest (no ``test_`` prefix) — this is shared data."""

from _ts_cases import GROUPS as TS_GROUPS

# generated bodies for the threshold-driven rules
_WALL = "Model(" + ", ".join(f"a{i}=1" for i in range(12)) + ")"
_FLAT = "class M(BaseModel):\n" + "".join(f"    f{i}: int\n" for i in range(12))
_DISPATCH = "def f(x):\n    if x==0: return 0\n" + "".join(
    f"    elif x=={i}: return {i}\n" for i in range(1, 5)
)
_GOD = "class C:\n" + "".join(f"    def m{i}(self): return {i}\n" for i in range(21))
_COMPLEX = (
    "def f(x):\n"
    + "".join(f"    if x=={i}: x+=1\n" for i in range(11))
    + "    return x"
)
_BIG = "x = 1\n" * 801

# module -> list of (rule_id, bad_source, good_source)
GROUPS: dict[str, list[tuple[str, str, str]]] = {
    "style": [
        ("PY-STYLE-FILE-SIZE", _BIG, "x = 1\n"),
        (
            "PY-STYLE-INLINE-IMPORT",
            "def f():\n    import os\n    return os",
            "import os\ndef f():\n    return os",
        ),
        (
            "PY-STYLE-IF-FALSE-IMPORT",
            "if False:\n    import os\n",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import os\n",
        ),
    ],
    "typing_rules": [
        (
            "PY-TYPING-MISSING-HINTS",
            "def f(x):\n    return x",
            "def f(x: int) -> int:\n    return x",
        ),
        (
            "PY-TYPING-UNTYPED-DICT",
            "def f() -> dict[str, Any]:\n    return {}",
            "def f() -> int:\n    return 1",
        ),
    ],
    "correctness": [
        (
            "PY-CORRECT-BROAD-EXCEPT",
            "try:\n    x()\nexcept Exception:\n    pass",
            "try:\n    x()\nexcept ValueError as e:\n    log(e)",
        ),
        (
            "PY-CORRECT-SWALLOWED-EXCEPTION",
            "try:\n    x()\nexcept ValueError:\n    pass",
            "try:\n    x()\nexcept ValueError:\n    log()",
        ),
    ],
    "async_rules": [
        (
            "PY-ASYNC-SYNC-IO",
            "async def f():\n    time.sleep(1)\n    await g()",
            "async def f():\n    await g()",
        ),
        (
            "PY-ASYNC-UNLOCKED-LAZY-INIT",
            "class C:\n    def m(self):\n        if self._x is None:\n            self._x = 1",
            "class C:\n    def m(self):\n        with self._lock:\n            if self._x is None:\n                self._x = 1",
        ),
        (
            "PY-ASYNC-DANGLING-TASK",
            "async def f():\n    asyncio.create_task(g())\n    await h()",
            "async def f():\n    t = asyncio.create_task(g())\n    await t",
        ),
        (
            "PY-ASYNC-SEQUENTIAL-AWAITS",
            "async def f():\n    for x in xs:\n        await g(x)",
            "async def f():\n    await asyncio.gather(*[g(x) for x in xs])",
        ),
        (
            "PY-ASYNC-NO-AWAIT-BODY",
            "async def f():\n    return 1",
            "async def f():\n    return await g()",
        ),
    ],
    "config_rules": [
        ("PY-CONFIG-ADHOC-ENV", "x = os.environ.get('X')", "x = get_settings().x"),
        (
            "PY-CONFIG-IMPORT-TIME-IO",
            "data = requests.get('u')",
            "def load():\n    return requests.get('u')",
        ),
    ],
    "oop": [
        (
            "PY-OOP-DATACLASS-IN-PYDANTIC",
            "@dataclass\nclass C:\n    x: int = 1",
            "class C(BaseModel):\n    x: int",
        ),
        ("PY-OOP-CONSTRUCTOR-WALL", _WALL, "Model(a=1)"),
        (
            "PY-OOP-FLAT-FIELD-MODEL",
            _FLAT,
            "class M(BaseModel):\n    a: int\n    b: int",
        ),
        (
            "PY-OOP-THIN-WRAPPER",
            "def f(x):\n    return g(x)",
            "def f(x):\n    y = g(x)\n    return y + 1",
        ),
        (
            "PY-OOP-BUILDER-CLASS",
            "class FooBuilder:\n    def build(self):\n        return 1",
            "class FooBuilder:\n    def build(self):\n        return 1\n    def other(self):\n        return 2",
        ),
        (
            "PY-OOP-DISPATCH-LADDER",
            _DISPATCH,
            "def f(x):\n    if x:\n        return 1\n    return 2",
        ),
        (
            "PY-OOP-STATIC-METHOD-CLASS",
            "class C:\n    @staticmethod\n    def f():\n        return 1",
            "class C:\n    def f(self):\n        return 1",
        ),
        (
            "PY-OOP-LONG-PARAM-LIST",
            "def f(a, b, c, d, e, g, h):\n    return 1",
            "def f(a, b):\n    return 1",
        ),
        ("PY-OOP-GOD-CLASS", _GOD, "class C:\n    def m(self):\n        return 1"),
        ("PY-OOP-HIGH-COMPLEXITY", _COMPLEX, "def f(x):\n    return x + 1"),
        (
            "PY-OOP-FREE-FN-ORCHESTRATOR",
            "def build_a(data):\n    return data\ndef build_b(data):\n    return build_a(data)\ndef build_c(data):\n    return build_b(data)",
            "def only(x):\n    return x",
        ),
    ],
    "security/injection": [
        ("PY-SEC-DANGEROUS-EVAL", "eval(user_input)", "eval('1')"),
        (
            "PY-SEC-SHELL-INJECTION",
            "subprocess.run(cmd, shell=True)",
            "subprocess.run(['ls'])",
        ),
        (
            "PY-SEC-SQL-STRING-BUILD",
            "def q(cur, x):\n    cur.execute(f'select {x}')",
            "def q(cur, x):\n    cur.execute('select ?', (x,))",
        ),
        (
            "PY-SEC-DJANGO-RAW-SQL",
            "def q(x):\n    return Model.objects.raw(f'select {x}')",
            "def q(x):\n    return Model.objects.raw('select 1')",
        ),
        ("PY-SEC-PATH-TRAVERSAL", "open('/data/' + name)", "open('/data/file.txt')"),
    ],
    "security/deserialize": [
        ("PY-SEC-UNSAFE-DESERIALIZE", "pickle.loads(b)", "json.loads(b)"),
        (
            "PY-SEC-XXE-UNSAFE-XML",
            "xml.etree.ElementTree.parse(f)",
            "import defusedxml.ElementTree\ndefusedxml.ElementTree.parse(f)",
        ),
    ],
    "security/crypto": [
        ("PY-SEC-WEAK-HASH", "hashlib.md5(b)", "hashlib.sha256(b)"),
        (
            "PY-SEC-INSECURE-RANDOM",
            "token = random.randint(0, 9999)",
            "idx = random.randint(0, 9)",
        ),
        ("PY-SEC-HARDCODED-SECRET", "password = 'hunter2'", "password = get_secret()"),
    ],
    "security/network": [
        (
            "PY-SEC-INSECURE-TLS",
            "requests.get(u, verify=False)",
            "requests.get(u, timeout=5)",
        ),
        ("PY-SEC-REQUEST-NO-TIMEOUT", "requests.get(u)", "requests.get(u, timeout=5)"),
        ("PY-SEC-BIND-ALL-INTERFACES", "host = '0.0.0.0'", "host = '127.0.0.1'"),
        (
            "PY-SEC-PARAMIKO-AUTOADD",
            "client.set_missing_host_key_policy(paramiko.AutoAddPolicy())",
            "client.set_missing_host_key_policy(paramiko.RejectPolicy())",
        ),
        (
            "PY-SEC-SSRF",
            "def fetch(url):\n    return requests.get(url, timeout=5)",
            "def fetch():\n    return requests.get('http://fixed', timeout=5)",
        ),
    ],
    "security/framework": [
        ("PY-SEC-FLASK-DEBUG", "app.run(debug=True)", "app.run(debug=False)"),
        (
            "PY-SEC-JINJA-AUTOESCAPE-OFF",
            "env = Environment(loader=l)",
            "env = Environment(loader=l, autoescape=True)",
        ),
        ("PY-SEC-ASSERT-FOR-SECURITY", "assert user.is_admin", "assert x == 1"),
        ("PY-SEC-INSECURE-TEMPFILE", "p = tempfile.mktemp()", "p = tempfile.mkstemp()"),
    ],
}

# Rules covered outside the good/bad table (need fs/cross-file context).
TESTED_SEPARATELY = {
    "PY-STYLE-STALE-COMMENT",
    "PY-XFILE-DUP-MODEL",
    "PY-XFILE-DUP-FUNCTION",
    "PY-XFILE-PARALLEL-SIBLING",
    "TS-XFILE-DUP-COMPONENT",
    "TS-XFILE-DUP-FUNCTION",
    "TS-XFILE-DUP-JSX-BLOCK",
}


def all_cases() -> list[tuple[str, str, str]]:
    """Every (rule_id, bad, good) case across the Python and TS detector tables."""
    out: list[tuple[str, str, str]] = []
    for groups in (GROUPS, TS_GROUPS):
        for group in groups.values():
            out.extend(group)
    return out
