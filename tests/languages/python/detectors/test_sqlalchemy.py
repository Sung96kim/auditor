"""SQLAlchemy framework rules (framework=sqlalchemy)."""

import pytest
from _support import rule_ids, run_audit

from auditor.config import AuditorSettings, SqlAlchemyConfig
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
    ],
)
def test_raw_sql_clean(body):
    assert "SA-RAW-SQL" not in _ids("stmt=None\nconn=None\n" + body)


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
