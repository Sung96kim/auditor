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
        (
            "PY-STYLE-FILE-SIZE",
            "# padding\n" * 802,
            "# small file\nx = 1\n",
        ),
        (
            "PY-STYLE-IF-FALSE-IMPORT",
            "if False:\n    import numpy as np\n    import pandas as pd\n",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import numpy as np\n    import pandas as pd\n",
        ),
        (
            "PY-STYLE-INLINE-IMPORT",
            "class Serializer:\n    def to_json(self, obj):\n        import json\n        return json.dumps(obj)\n",
            "import json\nclass Serializer:\n    def to_json(self, obj: object) -> str:\n        return json.dumps(obj)\n",
        ),
        (
            "PY-STYLE-INLINE-IMPORT",
            "def get_hash(data: bytes) -> str:\n    from hashlib import sha256\n    return sha256(data).hexdigest()\n",
            "from hashlib import sha256\ndef get_hash(data: bytes) -> str:\n    return sha256(data).hexdigest()\n",
        ),
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
            "def parse_response(payload, status_code):\n    if status_code != 200:\n        return None\n    return payload.get('data')\n",
            "from typing import Any\ndef parse_response(payload: dict[str, Any], status_code: int) -> Any:\n    if status_code != 200:\n        return None\n    return payload.get('data')\n",
        ),
        (
            "PY-TYPING-MISSING-HINTS",
            "def build_headers(token: str):\n    return {'Authorization': f'Bearer {token}'}\n",
            "def build_headers(token: str) -> dict[str, str]:\n    return {'Authorization': f'Bearer {token}'}\n",
        ),
        (
            "PY-TYPING-UNTYPED-DICT",
            "from typing import Any\ndef serialize_user(user) -> dict[str, Any]:\n    return {'id': user.id, 'email': user.email}\n",
            "from pydantic import BaseModel\nclass UserOut(BaseModel):\n    id: int\n    email: str\ndef serialize_user(user) -> UserOut:\n    return UserOut(id=user.id, email=user.email)\n",
        ),
        (
            "PY-TYPING-UNTYPED-DICT",
            "from typing import Any, Dict\ndef update_settings(data: Dict[str, Any]) -> None:\n    db.settings.update(data)\n",
            "from app.models import SettingsUpdate\ndef update_settings(data: SettingsUpdate) -> None:\n    db.settings.update(data.model_dump())\n",
        ),
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
            "try:\n    result = db.query(sql)\nexcept:\n    return None\n",
            "try:\n    result = db.query(sql)\nexcept psycopg2.OperationalError as exc:\n    logger.error('db error', exc_info=exc)\n    raise\n",
        ),
        (
            "PY-CORRECT-BROAD-EXCEPT",
            "def load_config(path):\n    try:\n        return json.load(open(path))\n    except BaseException:\n        pass\n",
            "def load_config(path):\n    try:\n        return json.load(open(path))\n    except (FileNotFoundError, json.JSONDecodeError) as exc:\n        logger.warning('config load failed: %s', exc)\n        return {}\n",
        ),
        (
            "PY-CORRECT-BROAD-EXCEPT",
            "def ping(host: str) -> bool:\n    try:\n        socket.connect((host, 80))\n        return True\n    except Exception:\n        return False\n",
            "def ping(host: str) -> bool:\n    try:\n        socket.connect((host, 80))\n        return True\n    except OSError as exc:\n        logger.debug('ping failed: %s', exc)\n        return False\n",
        ),
        (
            "PY-CORRECT-NAIVE-DATETIME",
            "from datetime import datetime\nexpires_at = datetime.utcnow()\n",
            "from datetime import datetime, timezone\nexpires_at = datetime.now(timezone.utc)\n",
        ),
        (
            "PY-CORRECT-NAIVE-DATETIME",
            "import datetime\nrecorded = datetime.datetime.now()\n",
            "import datetime\nrecorded = datetime.datetime.now(tz=datetime.timezone.utc)\n",
        ),
        (
            "PY-CORRECT-RAISE-WITHOUT-FROM",
            "try:\n    fh = open(path)\nexcept OSError:\n    raise StorageError('cannot open file')\n",
            "try:\n    fh = open(path)\nexcept OSError as exc:\n    raise StorageError('cannot open file') from exc\n",
        ),
        (
            "PY-CORRECT-RAISE-WITHOUT-FROM",
            "try:\n    val = cfg['timeout']\nexcept KeyError:\n    raise ValueError('missing required config key timeout')\n",
            "try:\n    val = cfg['timeout']\nexcept KeyError as err:\n    raise ValueError('missing required config key timeout') from err\n",
        ),
        (
            "PY-CORRECT-SWALLOWED-EXCEPTION",
            "def coerce(v):\n    try:\n        return int(v)\n    except TypeError:\n        pass\n",
            "def coerce(v):\n    try:\n        return int(v)\n    except TypeError as exc:\n        logger.debug('coerce failed for %r: %s', v, exc)\n        return None\n",
        ),
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
            "PY-ASYNC-DANGLING-TASK",
            "import asyncio\nasync def process_events(queue):\n    asyncio.ensure_future(drain(queue))\n    await asyncio.sleep(0)\n",
            "import asyncio\nasync def process_events(queue):\n    pending = set()\n    t = asyncio.ensure_future(drain(queue))\n    pending.add(t)\n    await asyncio.gather(*pending)\n",
        ),
        (
            "PY-ASYNC-DANGLING-TASK",
            "import asyncio\nasync def handle_request(req):\n    asyncio.create_task(emit_audit_log(req.user_id))\n    return {'ok': True}\n",
            "import asyncio\nasync def handle_request(req):\n    background_tasks = set()\n    t = asyncio.create_task(emit_audit_log(req.user_id))\n    background_tasks.add(t)\n    return {'ok': True}\n",
        ),
        (
            "PY-ASYNC-NO-AWAIT-BODY",
            "async def get_current_user(token: str) -> dict:\n    payload = jwt_decode(token)\n    return payload\n",
            "async def get_current_user(token: str) -> dict:\n    payload = await verify_token(token)\n    return payload\n",
        ),
        (
            "PY-ASYNC-NO-AWAIT-BODY",
            "class NotificationService:\n    async def build_message(self, user_id: int, event: str) -> str:\n        return f'User {user_id}: {event}'\n",
            "class NotificationService:\n    async def build_message(self, user_id: int, event: str) -> str:\n        user = await self.repo.get_user(user_id)\n        return f'{user.name}: {event}'\n",
        ),
        (
            "PY-ASYNC-SEQUENTIAL-AWAITS",
            "async def enrich_profiles(user_ids: list[int]) -> list[dict]:\n    profiles = []\n    for uid in user_ids:\n        profile = await fetch_profile(uid)\n        profiles.append(profile)\n    return profiles\n",
            "async def enrich_profiles(user_ids: list[int]) -> list[dict]:\n    return await asyncio.gather(*[fetch_profile(uid) for uid in user_ids])\n",
        ),
        (
            "PY-ASYNC-SEQUENTIAL-AWAITS",
            "async def replay_events(stream) -> None:\n    async for event in stream:\n        await process_event(event)\n",
            "async def replay_events(stream) -> None:\n    events = [event async for event in stream]\n    await asyncio.gather(*[process_event(e) for e in events])\n",
        ),
        (
            "PY-ASYNC-SYNC-IO",
            "async def fetch_weather(city: str) -> dict:\n    resp = requests.get(f'https://api.example.invalid/weather?q={city}')\n    return resp.json()\n",
            "async def fetch_weather(city: str) -> dict:\n    async with httpx.AsyncClient() as client:\n        resp = await client.get(f'https://api.example.invalid/weather?q={city}')\n    return resp.json()\n",
        ),
        (
            "PY-ASYNC-SYNC-IO",
            "async def poll_until_ready(job_id: str, retries: int = 5) -> str:\n    for _ in range(retries):\n        status = await get_status(job_id)\n        if status == 'done':\n            return status\n        time.sleep(2)\n    raise TimeoutError(job_id)\n",
            "async def poll_until_ready(job_id: str, retries: int = 5) -> str:\n    for _ in range(retries):\n        status = await get_status(job_id)\n        if status == 'done':\n            return status\n        await asyncio.sleep(2)\n    raise TimeoutError(job_id)\n",
        ),
        (
            "PY-ASYNC-UNAWAITED-COROUTINE",
            "class OrderService:\n    async def _persist(self, order: dict) -> None:\n        await self._db.insert(order)\n\n    async def create_order(self, data: dict) -> dict:\n        order = build_order(data)\n        self._persist(order)\n        return order\n",
            "class OrderService:\n    async def _persist(self, order: dict) -> None:\n        await self._db.insert(order)\n\n    async def create_order(self, data: dict) -> dict:\n        order = build_order(data)\n        await self._persist(order)\n        return order\n",
        ),
        (
            "PY-ASYNC-UNAWAITED-COROUTINE",
            "async def send_email(to: str, body: str) -> None:\n    await smtp_client.send(to, body)\n\nasync def notify_user(user_id: int, msg: str) -> None:\n    email = await resolve_email(user_id)\n    send_email(email, msg)\n",
            "async def send_email(to: str, body: str) -> None:\n    await smtp_client.send(to, body)\n\nasync def notify_user(user_id: int, msg: str) -> None:\n    email = await resolve_email(user_id)\n    await send_email(email, msg)\n",
        ),
        (
            "PY-ASYNC-UNLOCKED-LAZY-INIT",
            "class DatabasePool:\n    def ensure_pool(self):\n        if self._pool is None:\n            self._pool = create_pool(self._dsn)\n",
            "class DatabasePool:\n    def ensure_pool(self):\n        with self._lock:\n            if self._pool is None:\n                self._pool = create_pool(self._dsn)\n",
        ),
        (
            "PY-ASYNC-UNLOCKED-LAZY-INIT",
            "class ConfigCache:\n    def get_config(self):\n        if self._config is None:\n            self._config = load_remote_config(self._url)\n        return self._config\n",
            "class ConfigCache:\n    def get_config(self):\n        with self._rlock:\n            if self._config is None:\n                self._config = load_remote_config(self._url)\n        return self._config\n",
        ),
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
        (
            "PY-CONFIG-ADHOC-ENV",
            "class PaymentClient:\n    def __init__(self):\n        self.api_key = os.getenv('STRIPE_SECRET_KEY')\n        self.base_url = os.getenv('PAYMENT_API_URL', 'https://api.example.invalid')\n",
            "class PaymentClient:\n    def __init__(self, settings: AppSettings):\n        self.api_key = settings.stripe_secret_key\n        self.base_url = settings.payment_api_url\n",
        ),
        (
            "PY-CONFIG-ADHOC-ENV",
            "def is_debug_mode() -> bool:\n    return os.environ['DEBUG'] == '1'\n",
            "def is_debug_mode(settings: AppSettings) -> bool:\n    return settings.debug\n",
        ),
        (
            "PY-CONFIG-IMPORT-TIME-IO",
            "import httpx\n\nFEATURE_FLAGS = httpx.get('https://flags.example.invalid/v1/flags').json()\n",
            "import httpx\n\ndef load_feature_flags() -> dict:\n    return httpx.get('https://flags.example.invalid/v1/flags').json()\n",
        ),
        (
            "PY-CONFIG-IMPORT-TIME-IO",
            "import socket\n\n_REGISTRY_HOST = socket.gethostbyname('consul.example.invalid')\n",
            "import socket\n\ndef resolve_registry() -> str:\n    return socket.gethostbyname('consul.example.invalid')\n",
        ),
        ("PY-CONFIG-ADHOC-ENV", "x = os.environ.get('X')", "x = get_settings().x"),
        (
            "PY-CONFIG-IMPORT-TIME-IO",
            "data = requests.get('u')",
            "def load():\n    return requests.get('u')",
        ),
    ],
    "oop": [
        (
            "PY-OOP-BUILDER-CLASS",
            (
                "class ReportBuilder:\n"
                "    def __init__(self, title, rows):\n"
                "        self._title = title\n"
                "        self._rows = rows\n"
                "    def build(self):\n"
                "        return {'title': self._title, 'rows': self._rows}\n"
            ),
            (
                "class ReportBuilder:\n"
                "    def __init__(self, title, rows):\n"
                "        self._title = title\n"
                "        self._rows = rows\n"
                "    def build(self):\n"
                "        return {'title': self._title, 'rows': self._rows}\n"
                "    def validate(self):\n"
                "        return len(self._rows) > 0\n"
            ),
        ),
        (
            "PY-OOP-CLOSURE-CAPTURE",
            (
                "def make_handler(config):\n"
                "    def handle(request):\n"
                "        return process(request, config)\n"
                "    return handle\n"
            ),
            (
                "def make_handler(config, logger):\n"
                "    logger.info('setup', extra=config.to_dict())\n"
                "    return config\n"
            ),
        ),
        (
            "PY-OOP-CONSTRUCTOR-WALL",
            "Model(" + ", ".join(f"p{i}={i}" for i in range(13)) + ")",
            "UserProfile(name='Alice', email='alice@example.com', role='admin')",
        ),
        (
            "PY-OOP-DATACLASS-IN-PYDANTIC",
            (
                "from dataclasses import dataclass\n"
                "@dataclass\n"
                "class UserRecord:\n"
                "    user_id: int\n"
                "    email: str\n"
                "    active: bool = True\n"
            ),
            (
                "from pydantic import BaseModel\n"
                "class UserRecord(BaseModel):\n"
                "    user_id: int\n"
                "    email: str\n"
                "    active: bool = True\n"
            ),
        ),
        (
            "PY-OOP-DICT-MUTATION-BUILDER",
            (
                "def enrich_payload(payload):\n"
                "    payload['timestamp'] = now()\n"
                "    payload['version'] = '2'\n"
                "    return payload\n"
            ),
            (
                "def enrich_payload(payload):\n"
                "    return {\n"
                "        **payload,\n"
                "        'timestamp': now(),\n"
                "        'version': '2',\n"
                "    }\n"
            ),
        ),
        (
            "PY-OOP-DISPATCH-LADDER",
            (
                "def handle_event(event):\n"
                "    if event.type == 'created': on_created(event)\n"
                "    elif event.type == 'updated': on_updated(event)\n"
                "    elif event.type == 'deleted': on_deleted(event)\n"
                "    elif event.type == 'published': on_published(event)\n"
                "    elif event.type == 'archived': on_archived(event)\n"
            ),
            (
                "HANDLERS = {\n"
                "    'created': on_created,\n"
                "    'updated': on_updated,\n"
                "    'deleted': on_deleted,\n"
                "}\n"
                "def handle_event(event):\n"
                "    handler = HANDLERS.get(event.type)\n"
                "    if handler:\n"
                "        handler(event)\n"
            ),
        ),
        (
            "PY-OOP-DUPLICATE-BLOCK",
            (
                "def process(event):\n"
                "    if event.source == 'webhook':\n"
                "        record = parse(event.data)\n"
                "        validate(record)\n"
                "        store.insert(record)\n"
                "    elif event.source == 'polling':\n"
                "        record = parse(event.data)\n"
                "        validate(record)\n"
                "        store.insert(record)\n"
            ),
            (
                "def _save(event):\n"
                "    record = parse(event.data)\n"
                "    validate(record)\n"
                "    store.insert(record)\n"
                "def process(event):\n"
                "    _save(event)\n"
            ),
        ),
        (
            "PY-OOP-FIELD-COPY",
            (
                "class OrderSummary:\n"
                "    def __init__(self, order):\n"
                "        self.id = order.id\n"
                "        self.user_id = order.user_id\n"
                "        self.total = order.total\n"
                "        self.status = order.status\n"
                "        self.created_at = order.created_at\n"
            ),
            (
                "class OrderSummary:\n"
                "    def __init__(self, order):\n"
                "        self.id = order.id\n"
                "        self.status = order.status\n"
            ),
        ),
        (
            "PY-OOP-FLAT-FIELD-MODEL",
            (
                "class PaymentRecord(BaseModel):\n"
                "    payment_id: str\n"
                "    user_id: str\n"
                "    amount: float\n"
                "    currency: str\n"
                "    status: str\n"
                "    method: str\n"
                "    card_last4: str\n"
                "    card_brand: str\n"
                "    billing_name: str\n"
                "    billing_zip: str\n"
                "    created_at: str\n"
                "    updated_at: str\n"
            ),
            (
                "class CardInfo(BaseModel):\n"
                "    last4: str\n"
                "    brand: str\n"
                "\n"
                "class PaymentRecord(BaseModel):\n"
                "    payment_id: str\n"
                "    user_id: str\n"
                "    amount: float\n"
                "    card: CardInfo\n"
            ),
        ),
        (
            "PY-OOP-FREE-FN-ORCHESTRATOR",
            (
                "def validate_pipeline(pipeline):\n"
                "    if not pipeline.steps:\n"
                "        raise ValueError('empty')\n"
                "    return pipeline\n"
                "\n"
                "def enrich_pipeline(pipeline):\n"
                "    pipeline = validate_pipeline(pipeline)\n"
                "    pipeline.metadata = load_meta(pipeline.id)\n"
                "    return pipeline\n"
                "\n"
                "def execute_pipeline(pipeline):\n"
                "    pipeline = enrich_pipeline(pipeline)\n"
                "    for step in pipeline.steps:\n"
                "        step.run()\n"
                "    return pipeline\n"
            ),
            (
                "def validate(pipeline):\n"
                "    return bool(pipeline.steps)\n"
                "\n"
                "def summarize(pipeline):\n"
                "    return len(pipeline.steps)\n"
            ),
        ),
        (
            "PY-OOP-GOD-CLASS",
            "class AppManager:\n"
            + "".join(f"    def do_task_{i}(self): return {i}\n" for i in range(21)),
            (
                "class OrderManager:\n"
                "    def create(self, data): return data\n"
                "    def cancel(self, order_id): return order_id\n"
                "    def get(self, order_id): return order_id\n"
            ),
        ),
        (
            "PY-OOP-HIGH-COMPLEXITY",
            (
                "def validate_order(order):\n"
                "    if not order.id: return False\n"
                "    if not order.user_id: return False\n"
                "    if order.total < 0: return False\n"
                "    if not order.items: return False\n"
                "    if order.status not in ('pending', 'confirmed'): return False\n"
                "    if order.currency not in ('USD', 'EUR'): return False\n"
                "    if not order.shipping_address: return False\n"
                "    if not order.billing_address: return False\n"
                "    if order.discount < 0: return False\n"
                "    if order.tax < 0: return False\n"
                "    if order.created_at is None: return False\n"
                "    return True\n"
            ),
            (
                "def validate_order(order):\n"
                "    return OrderSchema.model_validate(order)\n"
            ),
        ),
        (
            "PY-OOP-LONG-PARAM-LIST",
            (
                "def create_notification(user_id, title, body, channel, priority, template_id, metadata):\n"
                "    return Notification(user_id=user_id, title=title, body=body,\n"
                "                        channel=channel, priority=priority)\n"
            ),
            (
                "def create_notification(request):\n"
                "    return Notification.from_request(request)\n"
            ),
        ),
        (
            "PY-OOP-MODEL-REBUILD",
            ("class Config(BaseModel):\n    val: int\nConfig.model_rebuild()\n"),
            "Config.model_validate({'val': 1})\n",
        ),
        (
            "PY-OOP-MODULE-CONST-FOR-SUBCLASS",
            (
                "PAYMENT_RULE_TITLE = 'Payment Validation'\n"
                "PAYMENT_RULE_STEPS = ('check_amount', 'check_currency')\n"
                "\n"
                "class PaymentRule(Rule):\n"
                "    pass\n"
            ),
            (
                "class PaymentRule(Rule):\n"
                "    TITLE = 'Payment Validation'\n"
                "    STEPS = ('check_amount', 'check_currency')\n"
            ),
        ),
        (
            "PY-OOP-PARALLEL-SIBLING",
            (
                "def serialize_user(obj):\n"
                "    data = obj.to_dict()\n"
                "    data['type'] = 'user'\n"
                "    return json.dumps(data)\n"
                "\n"
                "def serialize_admin(obj):\n"
                "    data = obj.to_dict()\n"
                "    data['type'] = 'admin'\n"
                "    return json.dumps(data)\n"
            ),
            (
                "def serialize_entity(entity, entity_type):\n"
                "    data = entity.to_dict()\n"
                "    data['type'] = entity_type\n"
                "    return json.dumps(data)\n"
            ),
        ),
        (
            "PY-OOP-STATIC-METHOD-CLASS",
            (
                "class StringUtils:\n"
                "    @staticmethod\n"
                "    def slugify(text):\n"
                "        return text.lower().replace(' ', '-')\n"
                "    @staticmethod\n"
                "    def truncate(text, max_len):\n"
                "        return text[:max_len]\n"
            ),
            (
                "def slugify(text):\n"
                "    return text.lower().replace(' ', '-')\n"
                "def truncate(text, max_len):\n"
                "    return text[:max_len]\n"
            ),
        ),
        (
            "PY-OOP-THIN-WRAPPER",
            "def get_user(user_id):\n    return fetch_user(user_id)\n",
            (
                "def get_user(user_id):\n"
                "    user = fetch_user(user_id)\n"
                "    if not user.active:\n"
                "        raise ValueError('inactive user')\n"
                "    return user\n"
            ),
        ),
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
        (
            "PY-SEC-DANGEROUS-EVAL",
            "def run_script(code):\n    exec(code)\n",
            "def run_script():\n    exec('print(1)')\n",
        ),
        (
            "PY-SEC-DANGEROUS-EVAL",
            "def dynamic(src, mode):\n    code = compile(src, '<string>', mode)\n    return code\n",
            "def dynamic():\n    code = compile('1+1', '<string>', 'eval')\n    return code\n",
        ),
        (
            "PY-SEC-SHELL-INJECTION",
            "import os\ndef run(cmd):\n    os.system(cmd)\n",
            "import subprocess\ndef run(cmd):\n    subprocess.run(cmd, shell=False)\n",
        ),
        (
            "PY-SEC-SHELL-INJECTION",
            "import subprocess\ndef deploy(branch):\n    subprocess.Popen(f'git checkout {branch}', shell=True)\n",
            "import subprocess\ndef deploy(branch):\n    subprocess.run(['git', 'checkout', branch])\n",
        ),
        (
            "PY-SEC-SQL-STRING-BUILD",
            "def search(cur, term):\n    cur.execute('SELECT * FROM products WHERE name=\"{}\"'.format(term))\n",
            "def search(cur, term):\n    cur.execute('SELECT * FROM products WHERE name=?', (term,))\n",
        ),
        (
            "PY-SEC-SQL-STRING-BUILD",
            "def get_user(cur, user_id):\n    cur.execute('SELECT * FROM users WHERE id=%s' % user_id)\n",
            "def get_user(cur, user_id):\n    cur.execute('SELECT * FROM users WHERE id=%s', (user_id,))\n",
        ),
        (
            "PY-SEC-DJANGO-RAW-SQL",
            "def get_items(kind):\n    return Item.objects.raw(f'SELECT * FROM item WHERE kind={kind}')\n",
            "def get_items(kind):\n    return Item.objects.filter(kind=kind)\n",
        ),
        (
            "PY-SEC-PATH-TRAVERSAL",
            "def read_log(filename):\n    with open(f'/var/log/app/{filename}') as f:\n        return f.read()\n",
            "def read_log():\n    with open('/var/log/app/latest.log') as f:\n            return f.read()\n",
        ),
        (
            "PY-SEC-PATH-TRAVERSAL",
            "def serve_file(name):\n    return open('/uploads/' + name).read()\n",
            "from pathlib import Path\ndef serve_file(name: str) -> bytes:\n    base = Path('/uploads')\n    safe = (base / name).resolve()\n    return safe.read_bytes()\n",
        ),
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
        (
            "PY-SEC-UNSAFE-DESERIALIZE",
            "import marshal\ndef load_code(data):\n    return marshal.loads(data)\n",
            "import json\ndef load_code(data: bytes) -> object:\n    return json.loads(data)\n",
        ),
        (
            "PY-SEC-UNSAFE-DESERIALIZE",
            "import yaml\ndef parse_config(stream):\n    return yaml.load(stream)\n",
            "import yaml\ndef parse_config(stream: str) -> dict:\n    return yaml.safe_load(stream)\n",
        ),
        (  # yaml.unsafe_load is always unsafe; full_load (good) closed the RCE vectors
            "PY-SEC-UNSAFE-DESERIALIZE",
            "import yaml\ndef parse_config(stream):\n    return yaml.unsafe_load(stream)\n",
            "import yaml\ndef parse_config(stream: str):\n    return yaml.full_load(stream)\n",
        ),
        (  # dill is pickle-family — dill.loads runs code; dill.dumps (good) only serializes
            "PY-SEC-UNSAFE-DESERIALIZE",
            "import dill\ndef load_model(blob):\n    return dill.loads(blob)\n",
            "import dill\ndef dump_model(model) -> bytes:\n    return dill.dumps(model)\n",
        ),
        (  # aliased import: `import pickle as pkl` then `pkl.loads(...)`
            "PY-SEC-UNSAFE-DESERIALIZE",
            "import pickle as pkl\ndef load(blob):\n    return pkl.loads(blob)\n",
            "import json as pkl\ndef load(blob: str):\n    return pkl.loads(blob)\n",
        ),
        (
            "PY-SEC-XXE-UNSAFE-XML",
            "import xml.etree.ElementTree\ndef parse_response(body):\n    return xml.etree.ElementTree.fromstring(body)\n",
            "from defusedxml import ElementTree\ndef parse_response(body: bytes):\n    return ElementTree.fromstring(body)\n",
        ),
        (
            "PY-SEC-XXE-UNSAFE-XML",
            "import xml.dom.minidom\ndef parse_doc(raw):\n    return xml.dom.minidom.parseString(raw)\n",
            "from defusedxml.minidom import parseString\ndef parse_doc(raw: bytes):\n    return parseString(raw)\n",
        ),
        ("PY-SEC-UNSAFE-DESERIALIZE", "pickle.loads(b)", "json.loads(b)"),
        (
            "PY-SEC-XXE-UNSAFE-XML",
            "xml.etree.ElementTree.parse(f)",
            "import defusedxml.ElementTree\ndefusedxml.ElementTree.parse(f)",
        ),
    ],
    "security/crypto": [
        (
            "PY-SEC-WEAK-HASH",
            "import hashlib\ndigest = hashlib.md5(password.encode()).hexdigest()\n",
            "import hashlib\ndigest = hashlib.sha256(password.encode()).hexdigest()\n",
        ),
        (
            "PY-SEC-WEAK-HASH",
            "import hashlib\nhash_val = hashlib.sha1(data).digest()\n",
            "import hashlib\nhash_val = hashlib.sha512(data).digest()\n",
        ),
        (
            "PY-SEC-WEAK-HASH",
            "import hashlib\nfp = hashlib.new('md5', content).hexdigest()\n",
            "import hashlib\nfp = hashlib.new('sha256', content).hexdigest()\n",
        ),
        (  # imported-name form: `from hashlib import md5` (no `hashlib.` prefix on the call)
            "PY-SEC-WEAK-HASH",
            "from hashlib import md5\ndigest = md5(payload).hexdigest()\n",
            "from hashlib import sha256\ndigest = sha256(payload).hexdigest()\n",
        ),
        (  # aliased module: `import hashlib as h` then `h.md5(...)`
            "PY-SEC-WEAK-HASH",
            "import hashlib as h\nfp = h.sha1(blob).hexdigest()\n",
            "import hashlib as h\nfp = h.sha384(blob).hexdigest()\n",
        ),
        (
            "PY-SEC-INSECURE-RANDOM",
            "import random\ntoken = random.choice('abcdefghijklmnopqrstuvwxyz0123456789' * 32)\n",
            "import secrets\ntoken = secrets.token_urlsafe(32)\n",
        ),
        (
            "PY-SEC-INSECURE-RANDOM",
            "import random\ndef generate_otp():\n    return random.randint(100000, 999999)\n",
            "import secrets\ndef generate_otp():\n    return secrets.randbelow(900000) + 100000\n",
        ),
        (
            "PY-SEC-INSECURE-RANDOM",
            "import random\nsecret_key = random.getrandbits(256)\n",
            "import random\njitter = random.uniform(0.5, 1.5)\n",
        ),
        (
            "PY-SEC-HARDCODED-SECRET",
            "api_key = 'sk-proj-abcdefghijklmnopqrstuvwxyz012345'\n",
            "api_key = os.environ['OPENAI_API_KEY']\n",
        ),
        (
            "PY-SEC-HARDCODED-SECRET",
            "db_password = 'S3cr3tP@ssw0rd!'\n",
            "db_password = settings.db_password\n",
        ),
        (
            "PY-SEC-HARDCODED-SECRET",
            "SECRET_TOKEN = 'hard-coded-jwt-secret-value-here'\n",
            "SECRET_TOKEN = os.getenv('JWT_SECRET')\n",
        ),
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
            "import requests\nresponse = requests.post('https://internal.example.invalid/api', json=payload, verify=False)\n",
            "import requests\nresponse = requests.post('https://internal.example.invalid/api', json=payload, timeout=10)\n",
        ),
        (
            "PY-SEC-INSECURE-TLS",
            "import httpx\nclient = httpx.Client(verify=False)\nresp = client.get('https://api.example.invalid/data')\n",
            "import httpx\nclient = httpx.Client(timeout=30)\nresp = client.get('https://api.example.invalid/data')\n",
        ),
        (
            "PY-SEC-INSECURE-TLS",
            "import ssl\nctx = ssl._create_unverified_context()\n",
            "import ssl\nctx = ssl.create_default_context()\n",
        ),
        (
            "PY-SEC-REQUEST-NO-TIMEOUT",
            "import requests\ndef sync_data():\n    resp = requests.post('https://api.example.invalid/sync', json=data)\n    return resp.json()\n",
            "import requests\ndef sync_data():\n    resp = requests.post('https://api.example.invalid/sync', json=data, timeout=30)\n    return resp.json()\n",
        ),
        (
            "PY-SEC-REQUEST-NO-TIMEOUT",
            "import httpx\nresult = httpx.get('https://api.example.invalid/items')\n",
            "import httpx\nresult = httpx.get('https://api.example.invalid/items', timeout=5)\n",
        ),
        (
            "PY-SEC-REQUEST-NO-TIMEOUT",
            "import requests\nrequests.delete('https://api.example.invalid/resource/1')\n",
            "import requests\nrequests.delete('https://api.example.invalid/resource/1', timeout=10)\n",
        ),
        (
            "PY-SEC-BIND-ALL-INTERFACES",
            "app.run(host='0.0.0.0', port=8080)\n",
            "app.run(host='127.0.0.1', port=8080)\n",
        ),
        (
            "PY-SEC-BIND-ALL-INTERFACES",
            "server.bind(('0.0.0.0', 9000))\n",
            "server.bind(('127.0.0.1', 9000))\n",
        ),
        (
            "PY-SEC-PARAMIKO-AUTOADD",
            "import paramiko\nssh = paramiko.SSHClient()\nssh.set_missing_host_key_policy(paramiko.WarningPolicy())\n",
            "import paramiko\nssh = paramiko.SSHClient()\nssh.set_missing_host_key_policy(paramiko.RejectPolicy())\n",
        ),
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
        (
            "PY-SEC-SSRF",
            "def proxy(target):\n    return httpx.get(target)\n",
            "import httpx\nAPI = 'https://api.example.com'\ndef proxy():\n    return httpx.get(API, timeout=5)\n",
        ),
        (
            "PY-SEC-SSRF",
            "def send_webhook(payload):\n    return requests.post(payload['callback_url'], json=payload)\n",
            "def send_webhook(payload):\n    return requests.post('https://hooks.example.com/notify', json=payload, timeout=5)\n",
        ),
    ],
    "security/framework": [
        (
            "PY-SEC-FLASK-DEBUG",
            "from flask import Flask\napp = Flask(__name__)\napp.run(host='0.0.0.0', port=5000, debug=True)\n",
            "from flask import Flask\napp = Flask(__name__)\napp.run(host='0.0.0.0', port=5000, debug=False)\n",
        ),
        (
            "PY-SEC-FLASK-DEBUG",
            "blueprint.run(debug=True, use_reloader=True)\n",
            "blueprint.run(debug=app.config.get('DEBUG', False))\n",
        ),
        (
            "PY-SEC-JINJA-AUTOESCAPE-OFF",
            "from jinja2 import Environment, FileSystemLoader\nenv = Environment(loader=FileSystemLoader('templates'))\n",
            "from jinja2 import Environment, FileSystemLoader, select_autoescape\nenv = Environment(loader=FileSystemLoader('templates'), autoescape=select_autoescape(['html', 'xml']))\n",
        ),
        (
            "PY-SEC-JINJA-AUTOESCAPE-OFF",
            "from jinja2 import Environment\nenv = Environment(autoescape=False)\n",
            "from jinja2 import Environment\nenv = Environment(autoescape=True)\n",
        ),
        (
            "PY-SEC-ASSERT-FOR-SECURITY",
            "def view(request):\n    assert request.user.is_admin, 'admin required'\n    return render_admin()\n",
            "def view(request):\n    if not request.user.is_admin:\n        raise PermissionError('admin required')\n    return render_admin()\n",
        ),
        (
            "PY-SEC-ASSERT-FOR-SECURITY",
            "def delete_item(user, item_id):\n    assert authorized(user, item_id)\n    db.delete(item_id)\n",
            "def delete_item(user, item_id):\n    if not check_permission(user, item_id):\n        raise PermissionDenied()\n    db.delete(item_id)\n",
        ),
        (
            "PY-SEC-INSECURE-TEMPFILE",
            "import tempfile\ntmp_path = tempfile.mktemp(suffix='.csv')\nwith open(tmp_path, 'w') as f:\n    f.write(data)\n",
            "import tempfile\nfd, tmp_path = tempfile.mkstemp(suffix='.csv')\nwith os.fdopen(fd, 'w') as f:\n    f.write(data)\n",
        ),
        (
            "PY-SEC-INSECURE-TEMPFILE",
            "output_path = '/tmp/report_output.csv'\nwith open(output_path, 'w') as f:\n    f.write(content)\n",
            "import tempfile\nwith tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:\n    f.write(content)\n    tmp_path = f.name\n",
        ),
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
