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
        (
            "PY-CORRECT-RAISE-WITHOUT-FROM",
            'try:\n    x()\nexcept ValueError:\n    raise RuntimeError("boom")',
            'try:\n    x()\nexcept ValueError as e:\n    raise RuntimeError("boom") from e',
        ),
        (
            "PY-CORRECT-NAIVE-DATETIME",
            "t = datetime.now()",
            "t = datetime.now(timezone.utc)",
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
        (
            "PY-ASYNC-UNAWAITED-COROUTINE",
            "async def g():\n    await h()\nasync def f():\n    g()",
            "async def g():\n    await h()\nasync def f():\n    await g()",
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
        (
            "PY-OOP-FIELD-COPY",
            "def __init__(self, s):\n    self.a = s.a\n    self.b = s.b\n    self.c = s.c\n    self.d = s.d\n    self.e = s.e",
            "def __init__(self, s):\n    self.a = s.a",
        ),
        (
            "PY-OOP-PARALLEL-SIBLING",
            "def to_kib(n):\n    v = n / 1024\n    return round(v, 1)\ndef to_mib(n):\n    v = n / 1048576\n    return round(v, 1)",
            "def only(n):\n    return n + 1",
        ),
        (
            "PY-OOP-MODEL-REBUILD",
            "Foo.model_rebuild()",
            "Foo.model_validate(x)",
        ),
        (
            "PY-OOP-DICT-MUTATION-BUILDER",
            'def b(d):\n    d["x"] = 1\n    return d',
            'def b(d):\n    return d["x"]',
        ),
        (
            "PY-OOP-MODULE-CONST-FOR-SUBCLASS",
            'TIMEOUT_RULE_TITLE = "t"\nTIMEOUT_RULE_STEPS = ()\n\nclass TimeoutRule(Rule):\n    pass',
            'class TimeoutRule(Rule):\n    TITLE = "t"\n    STEPS = ()',
        ),
        (
            "PY-OOP-CLOSURE-CAPTURE",
            "def outer(deps):\n    def inner(event):\n        return serialize(event, deps)\n    return inner",
            "def outer(deps):\n    return deps",
        ),
        (
            "PY-OOP-DUPLICATE-BLOCK",
            (
                "def handle(evt):\n"
                '    if evt.kind == "a":\n'
                "        record = build(evt)\n"
                "        store.save(record)\n"
                "        notify(record.id)\n"
                '    elif evt.kind == "b":\n'
                "        record = build(evt)\n"
                "        store.save(record)\n"
                "        notify(record.id)\n"
            ),
            (
                "def handle(evt):\n"
                "    record = build(evt)\n"
                "    store.save(record)\n"
                "    return record\n"
            ),
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
    "TS-XFILE-DUP-COMPONENT",
    "TS-XFILE-DUP-FUNCTION",
    "TS-XFILE-DUP-JSX-BLOCK",
    "TS-DS-DIRECT-UI-IMPORT",
    "TS-DS-INLINE-PRIMITIVE",
    "TS-DS-SIZE-OVERRIDE",
    # malware/* are tested in their own test_malware.py (per-language)
    "PY-MAL-OBFUSCATED-EXEC",
    "PY-MAL-REMOTE-EXEC",
    "PY-MAL-REVERSE-SHELL",
    "PY-MAL-DOWNLOAD-EXEC",
    "PY-MAL-CRYPTO-MINER",
    "PY-MAL-CREDENTIAL-ACCESS",
    "PY-MAL-ENCODED-BLOB",
    "PY-MAL-EXFIL-URL",
    "PY-MAL-PICKLE-REDUCE",
    "PY-MAL-DYNAMIC-IMPORT",
    "PY-MAL-SHELLCODE",
    # shell malware — covered in tests/languages/bash/test_malware.py (own case table)
    "SH-MAL-CURL-BASH",
    "SH-MAL-REVERSE-SHELL",
    "SH-MAL-FORK-BOMB",
    "SH-MAL-ENCODED-EXEC",
    "SH-MAL-DESTRUCTIVE",
    "SH-MAL-CRYPTO-MINER",
    "SH-MAL-CREDENTIAL-EXFIL",
    "SH-MAL-PERSISTENCE",
    "SH-MAL-ANTIFORENSICS",
    "SH-MAL-EXFIL-URL",
    # committed-secret sweep — covered in tests/languages/*/test_secrets.py
    "PY-SECRET-DETECTED",
    "TS-SECRET-DETECTED",
    "SH-SECRET-DETECTED",
    # supply-chain — covered in tests/languages/manifest/ and tests/languages/python/
    "MF-SUPPLY-INSTALL-HOOK",
    "PY-SUPPLY-SETUP-EXEC",
}


def all_cases() -> list[tuple[str, str, str]]:
    """Every (rule_id, bad, good) case across the Python and TS detector tables."""
    out: list[tuple[str, str, str]] = []
    for groups in (GROUPS, TS_GROUPS):
        for group in groups.values():
            out.extend(group)
    return out
