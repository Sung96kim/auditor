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
