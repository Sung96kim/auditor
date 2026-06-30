"""SQLAlchemy framework rules (framework=sqlalchemy)."""

import ast as _ast

import pytest
from _support import rule_ids, run_audit

from auditor.config import AuditorSettings, SqlAlchemyConfig
from auditor.languages.python.detectors.sqlalchemy_rules import _refresh_effects
from auditor.models import FileRole

_IMP = "import sqlalchemy as sa\nfrom sqlalchemy.orm import mapped_column, relationship\nfrom sqlalchemy import Column, text\n"


def _ids(body: str, *, imp: str = _IMP, settings=None, rel_path="m.py") -> set[str]:
    return rule_ids(run_audit(imp + body, settings=settings, rel_path=rel_path))


# --- gate ---
def test_non_sqlalchemy_file_not_flagged():
    src = "def f():\n    return Column(default=[])\n"  # no sqlalchemy import
    assert "SA-MUTABLE-DEFAULT" not in rule_ids(run_audit(src, rel_path="m.py"))


# --- SA-MUTABLE-DEFAULT ---
@pytest.mark.parametrize("d", ["[]", "{}", "set()", "list()", "dict()"])
def test_mutable_default_fires(d):
    assert "SA-MUTABLE-DEFAULT" in _ids(f"x = mapped_column(default={d})\n")


@pytest.mark.parametrize("d", ["list", "dict", "lambda: []", "_factory", "'s'"])
def test_mutable_default_clean(d):
    assert "SA-MUTABLE-DEFAULT" not in _ids(
        f"def _factory():\n    return []\nx = mapped_column(default={d})\n"
    )


def test_mutable_default_qualified_call():
    assert "SA-MUTABLE-DEFAULT" in _ids("x = sa.Column(default=[])\n")


# --- SA-LAZY-DYNAMIC ---
@pytest.mark.parametrize("lz", ['"dynamic"', '"subquery"'])
def test_lazy_dynamic_fires(lz):
    assert "SA-LAZY-DYNAMIC" in _ids(f"r = relationship('X', lazy={lz})\n")


@pytest.mark.parametrize("lz", ['"selectin"', '"joined"', '"select"', "SOME_VAR"])
def test_lazy_dynamic_clean(lz):
    assert "SA-LAZY-DYNAMIC" not in _ids(
        f"SOME_VAR='x'\nr = relationship('X', lazy={lz})\n"
    )


def test_lazy_dynamic_aliased_import():
    src = (
        "from sqlalchemy.orm import relationship as rel\nr = rel('X', lazy='dynamic')\n"
    )
    assert "SA-LAZY-DYNAMIC" in rule_ids(
        run_audit("import sqlalchemy\n" + src, rel_path="m.py")
    )


# --- SA-NAIVE-DATETIME-DEFAULT ---
@pytest.mark.parametrize(
    "body",
    [
        "import datetime\nx = mapped_column(default=datetime.datetime.utcnow)\n",
        "from datetime import datetime\nx = mapped_column(default=datetime.utcnow)\n",
        "x = mapped_column(default=sa.func.now())\n",
    ],
)
def test_naive_datetime_fires(body):
    assert "SA-NAIVE-DATETIME-DEFAULT" in _ids(body)


@pytest.mark.parametrize(
    "body",
    [
        "x = mapped_column(default=sa.func.now(), server_default=sa.func.now())\n",
        "import datetime\nx = mapped_column(default=lambda: datetime.datetime.now(datetime.timezone.utc))\n",
        "x = mapped_column(nullable=True)\n",
    ],
)
def test_naive_datetime_clean(body):
    assert "SA-NAIVE-DATETIME-DEFAULT" not in _ids(body)


# --- SA-RAW-SQL ---
@pytest.mark.parametrize(
    "body",
    [
        "q = text(f'select {x}')\n",
        "r = conn.execute(f'select {x}')\n",
        "q = text('select ' + name)\n",
    ],
)
def test_raw_sql_fires(body):
    assert "SA-RAW-SQL" in _ids("x=1\nname='a'\nconn=None\n" + body)


@pytest.mark.parametrize(
    "body",
    [
        "q = text('select 1')\n",
        "q = text(f'select 1')\n",
        "r = conn.execute(stmt)\n",
        # interpolating a provably-numeric value can't inject (orion field_route_service.py:351)
        "q = text(f'select nextval(s) from generate_series(1,{len(new_fields)})')\n",
        "q = text(f'limit {int(n)}')\n",
        "q = text(f'offset {len(rows) + 1}')\n",
    ],
)
def test_raw_sql_clean(body):
    assert "SA-RAW-SQL" not in _ids(
        "stmt=None\nconn=None\nnew_fields=[]\nn=1\nrows=[]\n" + body
    )


# --- SA-RAW-SQL: best-effort variable tracking ---
@pytest.mark.parametrize(
    "body",
    [
        "s = f'select {x}'\nq = text(s)\n",  # f-string built in a local, then text(var)
        "s = 'select ' + name\nq = text(s)\n",  # concat built in a local
        "s = 'select '\ns += f' where id = {x}'\nq = text(s)\n",  # augmented build
        "s = f'select {x}'\nr = conn.execute(s)\n",  # execute(var)
        "stmt = 'select * from t where id = ' + name\nconn.execute(text(stmt))\n",  # nested text(var)
    ],
)
def test_raw_sql_var_tracking_fires(body):
    assert "SA-RAW-SQL" in _ids("x=1\nname='a'\nconn=None\n" + body)


@pytest.mark.parametrize(
    "body",
    [
        "s = 'select 1'\nq = text(s)\n",  # constant var
        "s = f'limit {int(n)}'\nq = text(s)\n",  # numeric interpolation in a var
        "s = None\nr = conn.execute(s)\n",  # non-str var
        "s = base_query\nq = text(s)\n",  # var assigned from another (unknown) var → can't prove unsafe
    ],
)
def test_raw_sql_var_tracking_clean(body):
    assert "SA-RAW-SQL" not in _ids("x=1\nn=1\nconn=None\nbase_query='select 1'\n" + body)


def test_raw_sql_var_tracking_inside_function_fires():
    src = (
        "def run(conn, user_id):\n"
        "    stmt = f'select * from t where id = {user_id}'\n"
        "    return conn.execute(text(stmt))\n"
    )
    assert "SA-RAW-SQL" in _ids(src)


# --- SA-RAW-SQL: dialect-preparer quoting is the safe pattern, not a false positive ---
@pytest.mark.parametrize(
    "body",
    [
        "q = text(f'TRUNCATE {prep.quote(t)}')\n",  # inline: quoted identifier interpolated
        "q = text('TRUNCATE ' + ', '.join(prep.quote(x) for x in tables))\n",  # inline: quoted join
        # the standard safe pattern: build the statement in locals via the preparer, then text(stmt)
        "ids = ', '.join(prep.quote(x) for x in tables)\n"
        "stmt = f'TRUNCATE TABLE {ids} RESTART IDENTITY CASCADE'\n"
        "conn.execute(text(stmt))\n",
        "parts = [prep.quote(x) for x in tables]\n"
        "stmt = 'TRUNCATE ' + ', '.join(parts)\n"
        "conn.execute(text(stmt))\n",
    ],
)
def test_raw_sql_quoted_identifier_clean(body):
    assert "SA-RAW-SQL" not in _ids("prep=None\nt='x'\ntables=[]\nconn=None\n" + body)


def test_raw_sql_unquoted_join_var_fires():
    # a join of a *raw* (non-quoted) iterable is NOT the safe pattern → still flagged
    src = "cols = ', '.join(user_cols)\nq = text('select ' + cols)\n"
    assert "SA-RAW-SQL" in _ids("user_cols=[]\n" + src)


# --- SA-ASYNC-EXPIRE-ON-COMMIT ---
@pytest.mark.parametrize(
    "body",
    [
        "s = sa.ext.asyncio.async_sessionmaker(engine)\n",
        "from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession\ns = async_sessionmaker(engine)\n",
        "from sqlalchemy.orm import sessionmaker\nfrom sqlalchemy.ext.asyncio import AsyncSession\ns = sessionmaker(engine, class_=AsyncSession)\n",
    ],
)
def test_expire_on_commit_fires(body):
    assert "SA-ASYNC-EXPIRE-ON-COMMIT" in _ids("engine=None\n" + body)


@pytest.mark.parametrize(
    "body",
    [
        "from sqlalchemy.ext.asyncio import async_sessionmaker\ns = async_sessionmaker(engine, expire_on_commit=False)\n",
        "from sqlalchemy.orm import sessionmaker\ns = sessionmaker(engine)\n",  # plain sync factory — not flagged
    ],
)
def test_expire_on_commit_clean(body):
    assert "SA-ASYNC-EXPIRE-ON-COMMIT" not in _ids("engine=None\n" + body)


# --- SA-GREENLET-ATTR-AFTER-COMMIT (config-gated) ---
_ON = AuditorSettings(sqlalchemy=SqlAlchemyConfig(expire_on_commit=True))


def _ids_on(src: str) -> set[str]:
    return rule_ids(run_audit(src, settings=_ON, rel_path="m.py"))


_FN = (
    "from sqlalchemy.ext.asyncio import AsyncSession\n"
    "async def f(session):\n"
    "    user = User()\n"
    "    session.add(user)\n"
    "    await session.commit()\n"
    "    return user.email\n"  # attr access after commit on an add'd ORM obj
)


def test_greenlet_fires_when_config_enabled():
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in rule_ids(
        run_audit("import sqlalchemy\n" + _FN, settings=_ON, rel_path="m.py")
    )


def test_greenlet_dormant_by_default():
    # default expire_on_commit=False -> rule never fires
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rule_ids(
        run_audit("import sqlalchemy\n" + _FN, rel_path="m.py")
    )


def test_greenlet_clean_access_before_commit():
    src = (
        "import sqlalchemy\nasync def f(session):\n"
        "    user = User()\n    session.add(user)\n    x = user.email\n    await session.commit()\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rule_ids(
        run_audit(src, settings=_ON, rel_path="m.py")
    )


def test_greenlet_clean_non_orm_object():
    # `.get` on a plain dict must not be treated as an ORM access
    src = (
        "import sqlalchemy\nasync def f(session):\n"
        "    meta = {}\n    await session.commit()\n    return meta.get('k')\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rule_ids(
        run_audit(src, settings=_ON, rel_path="m.py")
    )


def test_greenlet_clean_in_test_role():
    # the running-app risk, not test setup — tests configure their own sessions (dogfood: 87% noise)
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rule_ids(
        run_audit(
            "import sqlalchemy\n" + _FN,
            settings=_ON,
            role=FileRole.TEST,
            rel_path="test_m.py",
        )
    )


def test_greenlet_clean_when_refreshed_after_commit():
    # regression (orion): the canonical `commit(); refresh(obj); use obj` un-expires obj → safe
    src = (
        "import sqlalchemy\nasync def f(session):\n"
        "    user = User()\n"
        "    session.add(user)\n"
        "    await session.commit()\n"
        "    await session.refresh(user)\n"
        "    return user.email\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_clean_when_requeried_after_commit():
    # obj reassigned from a query after the commit → fresh, not expired
    src = (
        "import sqlalchemy\nasync def f(session):\n"
        "    user = User()\n"
        "    session.add(user)\n"
        "    await session.commit()\n"
        "    user = session.scalar_one(q)\n"
        "    return user.email\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_fires_when_recommitted_after_refresh():
    # refresh un-expires, but a SECOND commit re-expires → the later access is risky again
    src = (
        "import sqlalchemy\nasync def f(session):\n"
        "    user = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    await session.refresh(user)\n"
        "    await session.commit()\n"
        "    return user.email\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in _ids_on(src)


def test_greenlet_clean_object_created_after_prior_commit():
    # regression (orion field_links): obj is constructed AFTER the earlier commit (so that commit
    # can't have expired it) and read BEFORE the next commit → safe
    src = (
        "import sqlalchemy\nasync def f(session):\n"
        "    await session.commit()\n"
        "    child = Child()\n"
        "    session.add(child)\n"
        "    cid = child.id\n"
        "    await session.commit()\n"
        "    return cid\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_clean_list_append_after_commit():
    # regression (orion copy_workflow): a list passed to add_all is a collection, not an ORM
    # object — building it with .append after a commit must not flag
    src = (
        "import sqlalchemy\nasync def f(session):\n"
        "    await session.commit()\n"
        "    links = []\n"
        "    links.append(make())\n"
        "    session.add_all(links)\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


# --- SA-GREENLET via query source (scalar_one / scalar_one_or_none / session.get) ---------


@pytest.mark.parametrize(
    "src",
    [
        # scalar_one source
        (
            "import sqlalchemy\n"
            "async def f(s):\n"
            "    user = s.scalar_one(q)\n"
            "    await s.commit()\n"
            "    return user.email\n"
        ),
        # scalar_one_or_none source
        (
            "import sqlalchemy\n"
            "async def f(s):\n"
            "    user = s.scalar_one_or_none(q)\n"
            "    await s.commit()\n"
            "    return user.email\n"
        ),
        # session.get(Model, pk) with >=2 args
        (
            "import sqlalchemy\n"
            "async def f(s):\n"
            "    user = s.get(Model, pk)\n"
            "    await s.commit()\n"
            "    return user.email\n"
        ),
    ],
)
def test_greenlet_fires_via_query_source(src):
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in rule_ids(
        run_audit(src, settings=_ON, rel_path="m.py")
    )


def test_greenlet_no_commit_does_not_fire():
    # async fn with ORM obj but NO commit -> rule must NOT fire
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    user = s.scalar_one(q)\n"
        "    return user.email\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in rule_ids(
        run_audit(src, settings=_ON, rel_path="m.py")
    )


# --- SA-MUTABLE-DEFAULT ImportFrom-only gate -----------------------------------------------


def test_mutable_default_fires_with_importfrom_only():
    # ImportFrom (no bare `import sqlalchemy`) should still trigger _imports_sqlalchemy
    src = "from sqlalchemy.orm import mapped_column\nx = mapped_column(default=[])\n"
    assert "SA-MUTABLE-DEFAULT" in rule_ids(run_audit(src, rel_path="m.py"))


# --- SA-MUTABLE-DEFAULT aliased import -----------------------------------------------------


def test_mutable_default_aliased_import_fires():
    src = (
        "import sqlalchemy\n"
        "from sqlalchemy.orm import mapped_column as mc\n"
        "x = mc(default=[])\n"
    )
    assert "SA-MUTABLE-DEFAULT" in rule_ids(run_audit(src, rel_path="m.py"))


# --- SA-RAW-SQL constant-only concat clean -------------------------------------------------


def test_raw_sql_constant_concat_clean():
    # concat of two constants -> not interpolated -> must NOT fire
    src = _IMP + "q = text('SELECT ' + '1')\n"
    assert "SA-RAW-SQL" not in rule_ids(run_audit(src, rel_path="m.py"))


# --- SA-IMPLICIT-LAZY-ASYNC (config-gated: dormant unless async_session=True) ---
_ASYNC = AuditorSettings(sqlalchemy=SqlAlchemyConfig(async_session=True))


@pytest.mark.parametrize(
    "rel",
    [
        "rel: Mapped[list['X']] = relationship('X')",  # collection
        "rel: Mapped['X'] = relationship('X')",  # scalar (lazy bites either way in async)
        "rel = relationship('X')",  # unannotated
    ],
)
def test_implicit_lazy_async_fires_when_declared(rel):
    assert "SA-IMPLICIT-LAZY-ASYNC" in _ids(f"class M:\n    {rel}\n", settings=_ASYNC)


def test_implicit_lazy_async_dormant_by_default():
    # default async_session=False -> can't tell sync from async per-file -> never fires
    assert "SA-IMPLICIT-LAZY-ASYNC" not in _ids(
        "class M:\n    rel = relationship('X')\n"
    )


@pytest.mark.parametrize(
    "lz", ['"selectin"', '"raise"', '"joined"', '"select"', "SOME"]
)
def test_implicit_lazy_async_explicit_lazy_clean(lz):
    body = f"SOME='x'\nclass M:\n    rel = relationship('X', lazy={lz})\n"
    assert "SA-IMPLICIT-LAZY-ASYNC" not in _ids(body, settings=_ASYNC)


def test_implicit_lazy_async_aliased_import():
    src = (
        "import sqlalchemy\n"
        "from sqlalchemy.orm import relationship as rel\n"
        "class M:\n    r = rel('X')\n"
    )
    assert "SA-IMPLICIT-LAZY-ASYNC" in rule_ids(
        run_audit(src, settings=_ASYNC, rel_path="m.py")
    )


# --- SA-JOINED-COLLECTION ---
@pytest.mark.parametrize(
    "ann", ["Mapped[list['X']]", "list['X']", "List['X']", "Mapped[set['X']]"]
)
def test_joined_collection_fires(ann):
    assert "SA-JOINED-COLLECTION" in _ids(
        f"class M:\n    items: {ann} = relationship('X', lazy='joined')\n"
    )


@pytest.mark.parametrize(
    "case",
    [
        "parent: Mapped['X'] = relationship('X', lazy='joined')",  # scalar M2O: fine
        "items: Mapped[list['X']] = relationship('X', lazy='selectin')",  # collection but selectin
        "items: Mapped[list['X']] = relationship('X')",  # no lazy at all
        "items = relationship('X', lazy='joined')",  # unannotated -> can't confirm collection
    ],
)
def test_joined_collection_clean(case):
    assert "SA-JOINED-COLLECTION" not in _ids(f"class M:\n    {case}\n")


# --- _refresh_effects (callee-resolution helper) -------------------------------------------


def _fn(src: str) -> _ast.FunctionDef | _ast.AsyncFunctionDef:
    return next(
        n
        for n in _ast.walk(_ast.parse(src))
        if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
    )


def test_refresh_effects_direct():
    assert _refresh_effects(_fn("def reload(s, o):\n    s.refresh(o)\n")) == (
        frozenset({1}),
        frozenset(),
    )


def test_refresh_effects_elements():
    fn = _fn("def reload_all(s, objs):\n    for o in objs:\n        s.refresh(o)\n")
    assert _refresh_effects(fn) == (frozenset(), frozenset({1}))


def test_refresh_effects_conditional_is_empty():
    fn = _fn("def maybe(s, o, c):\n    if c:\n        s.refresh(o)\n")
    assert _refresh_effects(fn) == (frozenset(), frozenset())


def test_refresh_effects_await_form():
    assert _refresh_effects(
        _fn("async def reload(s, o):\n    await s.refresh(o)\n")
    ) == (
        frozenset({1}),
        frozenset(),
    )


def test_refresh_effects_with_body_counts():
    fn = _fn("def reload(s, o):\n    with lock():\n        s.refresh(o)\n")
    assert _refresh_effects(fn) == (frozenset({1}), frozenset())


def test_refresh_effects_try_body_counts():
    fn = _fn(
        "def reload(s, o):\n    try:\n        s.refresh(o)\n    except Exception:\n        pass\n"
    )
    assert _refresh_effects(fn) == (frozenset({1}), frozenset())


def test_refresh_effects_except_body_does_not_count():
    fn = _fn(
        "def reload(s, o):\n    try:\n        x()\n    except Exception:\n        s.refresh(o)\n"
    )
    assert _refresh_effects(fn) == (frozenset(), frozenset())


def test_refresh_effects_while_body_does_not_count():
    fn = _fn("def reload(s, o):\n    while True:\n        s.refresh(o)\n")
    assert _refresh_effects(fn) == (frozenset(), frozenset())


def test_refresh_effects_conditional_inside_bulk_loop_does_not_count():
    fn = _fn(
        "def reload_all(s, objs, c):\n    for o in objs:\n        if c:\n            s.refresh(o)\n"
    )
    assert _refresh_effects(fn) == (frozenset(), frozenset())


def test_refresh_effects_nested_loop_elements_does_not_count():
    fn = _fn(
        "def reload_all(s, a, objs):\n    for x in a:\n        for o in objs:\n            s.refresh(o)\n"
    )
    assert _refresh_effects(fn) == (frozenset(), frozenset())


# =============================================================================
# Adversarial edge-case tests — characterise CURRENT behaviour (all must PASS)
# =============================================================================

# --- SA-GREENLET-ATTR-AFTER-COMMIT advanced edge cases --------------------------------


def test_greenlet_two_commits_refresh_only_first_still_fires():
    """Refresh after the 1st commit un-expires; the 2nd commit re-expires.
    The attribute access after the 2nd commit is still risky → must fire."""
    src = (
        "import sqlalchemy\n"
        "async def f(session):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    await session.refresh(obj)\n"
        "    await session.commit()\n"
        "    return obj.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in _ids_on(src)


def test_greenlet_two_orm_objects_refresh_only_one_fires_on_unrefreshed():
    """After a single commit, a.x is refreshed but b.y is not.
    The finding must be on b.y's line (line 9), NOT on a.x's line (line 9 as well,
    but only b.y appears in the same return — verify exactly one finding, on b.y)."""
    src = (
        "import sqlalchemy\n"
        "async def f(session):\n"
        "    a = session.scalar_one(q1)\n"
        "    b = session.scalar_one(q2)\n"
        "    session.add(a)\n"
        "    session.add(b)\n"
        "    await session.commit()\n"
        "    await session.refresh(a)\n"
        "    return a.x + b.y\n"
    )
    result = run_audit(src, settings=_ON, rel_path="m.py")
    greenlet = [
        f for f in result.findings if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    # exactly one finding — on b.y, not a.x (a was refreshed)
    assert len(greenlet) == 1
    assert "b.y" in greenlet[0].message


def test_greenlet_attribute_chain_fires_on_first_segment():
    """obj.rel.id after commit: only the first Attribute node (obj.rel) has a
    Name as its value, so the rule fires once at the obj.rel access line.
    The chained .id is an Attribute-of-Attribute and is NOT flagged separately."""
    src = (
        "import sqlalchemy\n"
        "async def f(session):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    return obj.rel.id\n"
    )
    result = run_audit(src, settings=_ON, rel_path="m.py")
    greenlet = [
        f for f in result.findings if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    # fires once, specifically on `obj.rel` (line 5)
    assert len(greenlet) == 1
    assert "obj.rel" in greenlet[0].message


def test_greenlet_flush_not_commit_does_not_expire():
    """session.flush() is NOT a commit — attributes are not expired.
    Accessing obj.x after flush must NOT fire."""
    src = (
        "import sqlalchemy\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "async def f(session):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.flush()\n"
        "    return obj.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_begin_context_manager_not_detected():
    """async with session.begin(): ... auto-commits on exit but the rule only detects
    literal .commit() calls — this is a known limitation / scope boundary.
    Accessing obj.x after the begin() block must NOT fire."""
    # Known limitation: session.begin() creates an implicit commit boundary that
    # this rule cannot statically detect. The rule only flags explicit .commit() calls.
    src = (
        "import sqlalchemy\n"
        "async def f(session):\n"
        "    obj = session.scalar_one(q)\n"
        "    async with session.begin():\n"
        "        session.add(obj)\n"
        "    return obj.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_nested_async_fn_analyzed_in_own_scope():
    """Outer async def has commit+access → fires on outer's obj.email.
    Inner async def has no commit → does NOT fire for inner's obj2.name.
    Each function is analyzed independently; the outer finding is on obj.email only."""
    src = (
        "import sqlalchemy\n"
        "async def outer(session):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    email = obj.email\n"
        "\n"
        "    async def inner(session2):\n"
        "        obj2 = session2.scalar_one(q2)\n"
        "        return obj2.name\n"
    )
    result = run_audit(src, settings=_ON, rel_path="m.py")
    greenlet = [
        f for f in result.findings if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    # Outer fires once (obj.email). Inner has no commit so it does NOT fire.
    assert len(greenlet) == 1
    assert "obj.email" in greenlet[0].message


def test_greenlet_commit_in_if_branch_access_in_same_branch_fires():
    """commit() inside an if-branch followed by attribute access in the same branch.
    ast.walk visits all nodes unconditionally, so the commit is found and the access
    after it fires (the rule does not track branch reachability)."""
    src = (
        "import sqlalchemy\n"
        "async def f(session, c):\n"
        "    obj = session.scalar_one(q)\n"
        "    if c:\n"
        "        await session.commit()\n"
        "        return obj.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in _ids_on(src)


# --- SA-IMPLICIT-LAZY-ASYNC edge cases -----------------------------------------------

_ASYNC = AuditorSettings(sqlalchemy=SqlAlchemyConfig(async_session=True))


def test_implicit_lazy_async_non_constant_lazy_not_flagged():
    """lazy=SOME_VAR: kwarg() returns a Name node (not None), but the check requires
    kwarg == None to trigger. Non-constant lazy values are NOT flagged."""
    body = "SOME_VAR = 'selectin'\nclass M:\n    x = relationship('X', lazy=SOME_VAR)\n"
    assert "SA-IMPLICIT-LAZY-ASYNC" not in _ids(body, settings=_ASYNC)


def test_implicit_lazy_async_fires_inside_class_with_mapped_annotation():
    """relationship() with no explicit lazy= inside a class body (Mapped[...] annotation)
    with async_session=True → fired."""
    body = "class User:\n    items: Mapped[list['Tag']] = relationship('Tag')\n"
    assert "SA-IMPLICIT-LAZY-ASYNC" in _ids(body, settings=_ASYNC)


def test_implicit_lazy_async_aliased_import_fires():
    """from sqlalchemy.orm import relationship as rel; then rel('X') with no lazy=
    and async_session=True → fired (alias resolves to 'relationship')."""
    src = (
        "import sqlalchemy\n"
        "from sqlalchemy.orm import relationship as rel\n"
        "class M:\n"
        "    x = rel('X')\n"
    )
    assert "SA-IMPLICIT-LAZY-ASYNC" in rule_ids(
        run_audit(src, settings=_ASYNC, rel_path="m.py")
    )


# --- SA-JOINED-COLLECTION edge cases -------------------------------------------------


@pytest.mark.parametrize("ctype", ["set", "Sequence", "frozenset"])
def test_joined_collection_fires_for_non_list_collection_types(ctype):
    """Mapped[set[X]], Mapped[Sequence[X]], Mapped[frozenset[X]] with lazy="joined"
    are all collection types and must fire — cartesian-product risk applies to any
    to-many relationship regardless of container type."""
    body = (
        f"class M:\n    items: Mapped[{ctype}[X]] = relationship('X', lazy='joined')\n"
    )
    assert "SA-JOINED-COLLECTION" in _ids(body)


def test_joined_collection_scalar_mapped_not_flagged():
    """Mapped[X] (no subscripted container) is a many-to-one scalar relationship.
    lazy="joined" on a scalar is efficient (single JOIN, no multiplication) — NOT flagged."""
    body = "class M:\n    parent: Mapped[X] = relationship('X', lazy='joined')\n"
    assert "SA-JOINED-COLLECTION" not in _ids(body)


def test_joined_collection_non_relationship_value_not_flagged():
    """An annotated assignment ``x: Mapped[list[Y]] = compute()`` has a collection
    annotation but the VALUE is not a relationship() call — NOT flagged."""
    body = "class M:\n    x: Mapped[list[Y]] = compute()\n"
    assert "SA-JOINED-COLLECTION" not in _ids(body)


# --- SA-MUTABLE-DEFAULT edge cases ---------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    ["dict()", "set()", "list()"],
)
def test_mutable_default_empty_ctor_fires(expr):
    """Empty-constructor calls dict()/set()/list() as column defaults are mutable
    and shared — the rule must fire for all three."""
    assert "SA-MUTABLE-DEFAULT" in _ids(f"x = mapped_column(default={expr})\n")


@pytest.mark.parametrize(
    "expr",
    ["dict", "list", "lambda: {}"],
)
def test_mutable_default_callable_form_clean(expr):
    """Bare callables dict/list and lambda expressions are safe (called fresh each time)
    and must NOT fire."""
    assert "SA-MUTABLE-DEFAULT" not in _ids(f"x = mapped_column(default={expr})\n")


# --- SA-RAW-SQL edge cases -----------------------------------------------------------


def test_raw_sql_interpolated_execute_fires():
    """conn.execute(f'... {x}') with a variable in the f-string → SQL injection risk → fired."""
    body = "x = 1\nconn = None\nconn.execute(f'SELECT * FROM t WHERE id = {x}')\n"
    assert "SA-RAW-SQL" in _ids(body)


def test_raw_sql_const_plus_const_clean():
    """text('a' + 'b') — BinOp where BOTH sides are string constants → no injection → NOT fired."""
    body = "q = text('SELECT ' + '1')\n"
    assert "SA-RAW-SQL" not in _ids(body)


def test_raw_sql_const_plus_var_fires():
    """text('SELECT ' + name) — BinOp where right side is a Name → injection risk → fired."""
    body = "name = 'users'\nq = text('SELECT * FROM ' + name)\n"
    assert "SA-RAW-SQL" in _ids(body)


def test_raw_sql_fstring_no_placeholder_clean():
    """text(f'static') — an f-string with NO FormattedValue nodes has no interpolated values,
    so _is_interpolated returns False. Characterised behaviour: NOT fired."""
    body = "q = text(f'SELECT 1')\n"
    # f-string with no {} placeholders has no FormattedValue nodes → treated as constant → clean
    assert "SA-RAW-SQL" not in _ids(body)


# --- SA-ASYNC-EXPIRE-ON-COMMIT edge cases -------------------------------------------


def test_expire_on_commit_sessionmaker_with_async_session_fires():
    """sessionmaker(engine, class_=AsyncSession) without expire_on_commit=False
    creates an async session that expires on commit → fired."""
    body = (
        "from sqlalchemy.orm import sessionmaker\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "engine = None\n"
        "s = sessionmaker(engine, class_=AsyncSession)\n"
    )
    assert "SA-ASYNC-EXPIRE-ON-COMMIT" in _ids(body)


def test_expire_on_commit_sessionmaker_with_async_session_expire_false_clean():
    """sessionmaker(engine, class_=AsyncSession, expire_on_commit=False) → explicitly
    opts out of expiry → NOT fired."""
    body = (
        "from sqlalchemy.orm import sessionmaker\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n"
        "engine = None\n"
        "s = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)\n"
    )
    assert "SA-ASYNC-EXPIRE-ON-COMMIT" not in _ids(body)


def test_expire_on_commit_plain_sync_sessionmaker_clean():
    """Plain sync sessionmaker(engine) without class_=AsyncSession is a sync session
    factory — greenlet expiry is not a concern → NOT fired."""
    body = (
        "from sqlalchemy.orm import sessionmaker\n"
        "engine = None\n"
        "s = sessionmaker(engine)\n"
    )
    assert "SA-ASYNC-EXPIRE-ON-COMMIT" not in _ids(body)


# =============================================================================
# OBSCURE EDGE-CASE TESTS — pin actual current behaviour (all PASS)
# Classification key: [CORRECT] / [FN-GAP] / [BUG]
# =============================================================================

# ---------------------------------------------------------------------------
# GREENLET obscure cases (cases 1-9)
# ---------------------------------------------------------------------------


def test_greenlet_walrus_operator_tracked():
    """Case 1 — walrus/assignment-expr ``(obj := s.scalar_one(q))`` binds via ``ast.NamedExpr``.
    ``_orm_names`` now recognises NamedExpr ORM-source binds, so ``obj`` is tracked and the
    post-commit ``obj.x`` access fires."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    if (obj := s.scalar_one(q)) is not None:\n"
        "        await s.commit()\n"
        "        return obj.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in _ids_on(src)


def test_greenlet_fstring_attribute_after_commit_fires():
    """Case 2 — [CORRECT]: ``f'{obj.email}'`` after commit — the ``obj.email`` Attribute node
    lives inside a ``FormattedValue`` inside a ``JoinedStr``, but ``ast.walk`` descends into it,
    so the rule fires correctly."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    obj = s.scalar_one(q)\n"
        "    await s.commit()\n"
        '    return f"{obj.email}"\n'
    )
    result = run_audit(src, settings=_ON, rel_path="m.py")
    greenlet = [
        f for f in result.findings if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    assert len(greenlet) == 1
    assert "obj.email" in greenlet[0].message


def test_greenlet_comprehension_attribute_after_commit_fires():
    """Case 3 — [CORRECT]: ``[obj.x for _ in range(3)]`` after commit — ``ast.walk``
    descends into ``ListComp`` generators, so the ``obj.x`` Attribute is found and flagged."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    obj = s.scalar_one(q)\n"
        "    await s.commit()\n"
        "    return [obj.x for _ in range(3)]\n"
    )
    result = run_audit(src, settings=_ON, rel_path="m.py")
    greenlet = [
        f for f in result.findings if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    assert len(greenlet) == 1
    assert "obj.x" in greenlet[0].message


def test_greenlet_refresh_keyword_arg_recognised_clears():
    """Case 4 — ``session.refresh(instance=obj)`` passes the object as a keyword argument.
    ``_refresh_target`` now reads the ``instance=`` kwarg, so ``obj`` is freshened after the
    commit and the access is NOT flagged."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    obj = s.scalar_one(q)\n"
        "    await s.commit()\n"
        "    await s.refresh(instance=obj)\n"  # kwarg form — now recognised as a full refresh
        "    return obj.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_partial_refresh_only_freshens_named_attr():
    """Case 5 — ``session.refresh(obj, ['email'])`` is a partial refresh: only 'email' is
    reloaded. Accessing the refreshed attribute is safe, but accessing a non-refreshed
    attribute after the commit still fires (attribute-scoped freshening)."""
    refreshed = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    obj = s.scalar_one(q)\n"
        "    await s.commit()\n"
        "    await s.refresh(obj, ['email'])\n"
        "    return obj.email\n"  # the refreshed attr → safe
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(refreshed)

    not_refreshed = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    obj = s.scalar_one(q)\n"
        "    await s.commit()\n"
        "    await s.refresh(obj, ['email'])\n"
        "    return obj.other\n"  # NOT refreshed → still expired → fires
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in _ids_on(not_refreshed)


def test_greenlet_expire_without_commit_not_flagged():
    """Case 6 — [CORRECT / scoped]: ``session.expire(obj)`` manually expires the object
    but there is no ``commit()`` call. The rule is triggered only by commits; a bare
    ``expire()`` with no subsequent commit is NOT flagged (correct within rule scope)."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    obj = s.scalar_one(q)\n"
        "    s.expire(obj)\n"  # no commit — rule only tracks commit boundaries
        "    return obj.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_refresh_attribute_arg_does_not_freshen_obj():
    """Case 7 — [FN-GAP]: ``session.refresh(self.something)`` where the argument is an
    ``ast.Attribute`` node (not a ``Name``).  ``_freshen_lines`` only adds ``Name`` args, so
    passing an Attribute arg does NOT freshen the ``obj`` Name → ``obj.x`` remains expired
    and the rule fires correctly (correct detection).  The complementary FN-gap: there is
    no way to track that ``self.something`` was refreshed if *it* were the ORM target."""
    src = (
        "import sqlalchemy\n"
        "async def f(session, self):\n"
        "    obj = session.scalar_one(q)\n"
        "    await session.commit()\n"
        "    await session.refresh(self.something)\n"  # arg is Attribute — does not freshen obj
        "    return obj.x\n"
    )
    # refresh(Attribute) does not count as freshening obj → still fires
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" in _ids_on(src)


def test_greenlet_tuple_unpack_execute_not_tracked():
    """Case 8 — [CORRECT]: ``a, b = await session.execute(q)`` — ``execute`` returns a
    ``Result``, NOT ORM objects, so it is deliberately not an ORM source. The names are
    correctly not tracked (no false positive on a Result tuple)."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    a, b = await s.execute(q)\n"
        "    await s.commit()\n"
        "    return a.x\n"
    )
    assert "SA-GREENLET-ATTR-AFTER-COMMIT" not in _ids_on(src)


def test_greenlet_tuple_unpack_from_orm_source_tracked():
    """``_orm_names`` extracts the element names of a tuple target when the value IS an ORM
    source, so each unpacked name is tracked and fires after a commit."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    a, b = s.scalar_one(q)\n"  # syntactic: an ORM source bound to a tuple target
        "    await s.commit()\n"
        "    return a.x + b.y\n"
    )
    result = run_audit(src, settings=_ON, rel_path="m.py")
    greenlet = [
        f for f in result.findings if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    assert len(greenlet) == 2


def test_greenlet_chained_assign_both_names_tracked():
    """Case 9 — [CORRECT]: ``a = b = session.scalar_one(q)`` produces a single
    ``ast.Assign`` with ``targets=[Name('a'), Name('b')]``.  ``_orm_names`` iterates all
    targets, so both ``a`` and ``b`` are registered.  Both ``a.x`` and ``b.y`` after commit
    fire, producing exactly two findings."""
    src = (
        "import sqlalchemy\n"
        "async def f(s):\n"
        "    a = b = s.scalar_one(q)\n"
        "    await s.commit()\n"
        "    return a.x + b.y\n"
    )
    result = run_audit(src, settings=_ON, rel_path="m.py")
    greenlet = [
        f for f in result.findings if f.rule_id == "SA-GREENLET-ATTR-AFTER-COMMIT"
    ]
    # both a.x and b.y are flagged — exactly two findings
    assert len(greenlet) == 2
    messages = {f.message for f in greenlet}
    assert any("a.x" in m for m in messages)
    assert any("b.y" in m for m in messages)


# ---------------------------------------------------------------------------
# _refresh_effects unit cases (cases 10-12)
# ---------------------------------------------------------------------------


def test_refresh_effects_partial_refresh_is_not_a_full_effect():
    """Case 10 — ``s.refresh(o, ['x'])`` is a *partial* refresh (attribute_names given), so it
    does NOT prove the helper fully freshens ``o``.  ``_refresh_effects`` reports no direct effect:
    a caller can't treat passing an object to this helper as a full reload."""
    fn = _fn("def r(s, o):\n    s.refresh(o, ['x'])\n")
    assert _refresh_effects(fn) == (frozenset(), frozenset())


def test_refresh_effects_full_refresh_is_direct():
    """A full refresh (no attribute_names) IS a direct effect on its parameter."""
    fn = _fn("def r(s, o):\n    s.refresh(o)\n")
    assert _refresh_effects(fn) == (frozenset({1}), frozenset())


def test_refresh_effects_vararg_param_not_indexed():
    """Case 11 — [FN-GAP]: ``def r(s, *objs): for o in objs: s.refresh(o)`` — ``objs``
    is a vararg (lives in ``fn.args.vararg``, not in ``posonlyargs + args``).  ``_refresh_effects``
    builds its index only from ``posonlyargs + args``, so ``objs`` has no index → the loop
    body is not attributed → both direct and elements are empty.
    FN-gap: vararg bulk-refresh helpers are not recognised."""
    fn = _fn("def r(s, *objs):\n    for o in objs:\n        s.refresh(o)\n")
    # vararg not in index → empty result
    assert _refresh_effects(fn) == (frozenset(), frozenset())


def test_refresh_effects_keyword_only_param_not_indexed():
    """Case 12 — [FN-GAP]: ``def r(s, *, obj): s.refresh(obj)`` — ``obj`` is keyword-only
    (lives in ``fn.args.kwonlyargs``, not ``posonlyargs + args``).  Not present in the index
    built by ``_refresh_effects`` → refresh is not attributed → both sets are empty.
    FN-gap: keyword-only refresh helpers are not recognised."""
    fn = _fn("def r(s, *, obj):\n    s.refresh(obj)\n")
    # kwonly not in posonlyargs+args index → empty result
    assert _refresh_effects(fn) == (frozenset(), frozenset())


# ---------------------------------------------------------------------------
# SA-RAW-SQL obscure cases (cases 13-14)
# ---------------------------------------------------------------------------


def test_raw_sql_percent_format_caught():
    """Case 13 — ``text('select * from %s' % x)`` — a ``%``-formatted string literal with a
    non-constant operand. ``_is_interpolated`` now handles ``BinOp(Mod)`` over a str literal, so
    the injection vector fires; a numeric operand (``% len(rows)``) stays safe."""
    assert "SA-RAW-SQL" in _ids("x = 'users'\nq = text('select * from %s' % x)\n")
    # provably-numeric operand → not injectable
    assert "SA-RAW-SQL" not in _ids("q = text('limit %d' % len(rows))\n")
    # all-constant → not injectable
    assert "SA-RAW-SQL" not in _ids("q = text('select %s' % 'literal')\n")


def test_raw_sql_dot_format_caught():
    """Case 14 — ``text('select * from {}'.format(x))`` — a ``str.format()`` call on a literal
    with a non-constant argument. ``_is_interpolated`` now inspects ``.format()`` calls, so it
    fires; a constant argument stays safe."""
    assert "SA-RAW-SQL" in _ids("x = 'users'\nq = text('select * from {}'.format(x))\n")
    # constant argument → not injectable
    assert "SA-RAW-SQL" not in _ids("q = text('select {}'.format('literal'))\n")
