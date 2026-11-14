"""
Mocks de banco de dados para testes.

Este modulo contem:
- Early patching: `app.bot` e `app.redis_client` antes que modulos do app sejam importados
- MockRedisClient, MockMongoClient: mocks completos para testes unitarios
- FakeAsyncMongoClient: o lado async do mesmo store, que os repositorios de
  `app.data` recebem (find devolve um cursor async, aggregate e awaited, o resto tambem)

IMPORTANTE: Importar este modulo executa o early patching como side-effect.
Isso e intencional - o conftest.py importa este modulo antes de qualquer
modulo da app.
"""

import asyncio
import threading
from unittest.mock import MagicMock


# ============================================================================
# EARLY PATCHING - Before any app modules are imported
# ============================================================================
# Services bind `app.bot` and `app.redis_client` at import time.

class _EarlyMockRedisClient:
    """Early mock for redis_client before tests configure it properly."""
    def get(self, key):
        return None

    def set(self, key, value):
        pass

    def setex(self, key, expiration, value):
        pass

    def delete(self, *keys):
        pass

    def incrby(self, key, amount=1):
        return amount

    def expire(self, key, seconds):
        return True


import app
app.redis_client = _EarlyMockRedisClient()
app.bot = MagicMock()


# ============================================================================
# MOCK CLASSES - For unit tests
# ============================================================================

class MockRedisClient:
    """Mock do cliente Redis para testes unitarios."""

    def __init__(self):
        self._data = {}
        self._expirations = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, ex=None, nx=False):
        """redis-py's answer: True when the key was written, None when NX found it."""
        if nx and key in self._data:
            return None
        self._data[key] = value
        if ex is not None:
            self._expirations[key] = ex
        else:
            self._expirations.pop(key, None)
        return True

    def setex(self, key, expiration, value):
        self._data[key] = value
        self._expirations[key] = expiration

    def delete(self, *keys):
        for key in keys:
            self._data.pop(key, None)
            self._expirations.pop(key, None)

    def keys(self, pattern):
        import fnmatch
        return [k for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]

    def scan_iter(self, match="*", count=None):
        import fnmatch
        for key in list(self._data.keys()):
            if fnmatch.fnmatchcase(key, match):
                yield key

    def flushdb(self):
        self._data.clear()

    def ping(self):
        return True

    def incrby(self, key, amount=1):
        current = int(self._data.get(key, 0))
        self._data[key] = str(current + amount)
        return current + amount

    def mget(self, keys, *more):
        """redis-py's answer: one value per key, None for a key that does not exist."""
        names = [keys] if isinstance(keys, str) else list(keys)
        return [self._data.get(key) for key in [*names, *more]]

    def hincrby(self, name, key, amount=1):
        fields = self._data.setdefault(name, {})
        current = int(fields.get(key, 0))
        fields[key] = str(current + amount)
        return current + amount

    def hgetall(self, name):
        return dict(self._data.get(name) or {})

    def pipeline(self, transaction=True):
        return MockRedisPipeline(self)

    def expire(self, key, seconds):
        self._expirations[key] = seconds
        return True

    def ttl(self, key):
        if key not in self._data:
            return -2
        return self._expirations.get(key, -1)

    def unlink(self, *keys):
        present = [key for key in keys if key in self._data]
        self.delete(*present)
        return len(present)

    def advance(self, seconds):
        """Let `seconds` pass: every TTL shrinks and a key whose TTL runs out is gone."""
        for key in list(self._expirations):
            self._expirations[key] -= seconds
            if self._expirations[key] <= 0:
                self.delete(key)


class MockRedisPipeline:
    """Queues the commands it is given and runs them, in order, on `execute`."""

    def __init__(self, client):
        self._client = client
        self._queued = []

    def __getattr__(self, name):
        method = getattr(self._client, name)

        def queue(*args, **kwargs):
            self._queued.append((method, args, kwargs))
            return self

        return queue

    def execute(self):
        queued, self._queued = self._queued, []
        return [method(*args, **kwargs) for method, args, kwargs in queued]


class MockCursor:
    """Mock de um cursor MongoDB."""

    def __init__(self, data):
        self._data = data

    def sort(self, field, direction=-1):
        """Really sorts: a test asserting order must fail when order is wrong."""
        keys = field if isinstance(field, list) else [(field, direction)]
        for key, key_direction in reversed(keys):
            self._data.sort(
                key=lambda doc: (doc.get(key) is None, doc.get(key)),
                reverse=key_direction == -1,
            )
        return self

    def batch_size(self, size):
        return self

    def limit(self, count):
        self._data = self._data[:count]
        return self

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def _resolve_path(doc, dotted_key, create=False):
    """Walk a dotted update path, returning the owning dict and the leaf name."""
    parts = dotted_key.split(".")
    current = doc
    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, dict):
            if not create:
                return None, parts[-1]
            nested = {}
            current[part] = nested
        current = nested
    return current, parts[-1]


def _apply_update(doc, update, inserted):
    """The update operators the app actually uses, dotted paths included."""
    for key, value in (update.get("$set") or {}).items():
        owner, leaf = _resolve_path(doc, key, create=True)
        owner[leaf] = value

    if inserted:
        for key, value in (update.get("$setOnInsert") or {}).items():
            owner, leaf = _resolve_path(doc, key, create=True)
            owner[leaf] = value

    for key in update.get("$unset") or {}:
        owner, leaf = _resolve_path(doc, key)
        if owner is not None:
            owner.pop(leaf, None)

    for key, amount in (update.get("$inc") or {}).items():
        owner, leaf = _resolve_path(doc, key, create=True)
        owner[leaf] = (owner.get(leaf) or 0) + amount

    for key, value in (update.get("$max") or {}).items():
        owner, leaf = _resolve_path(doc, key, create=True)
        current = owner.get(leaf)
        if current is None or value > current:
            owner[leaf] = value

    for key, value in (update.get("$min") or {}).items():
        owner, leaf = _resolve_path(doc, key, create=True)
        current = owner.get(leaf)
        if current is None or value < current:
            owner[leaf] = value

    for key, value in (update.get("$addToSet") or {}).items():
        owner, leaf = _resolve_path(doc, key, create=True)
        existing = owner.get(leaf)
        if not isinstance(existing, list):
            existing = []
            owner[leaf] = existing
        if value not in existing:
            existing.append(value)


def _same_document(actual, expected):
    """BSON equality: an embedded document matches field by field and in order."""
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and list(actual) == list(expected)
            and all(_same_document(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_same_document(one, other) for one, other in zip(actual, expected))
        )
    return actual == expected


def _matches_value(actual, expected):
    """Equality, plus the comparison operators a range query needs."""
    if not isinstance(expected, dict):
        return actual == expected
    if not expected or not all(str(key).startswith("$") for key in expected):
        return _same_document(actual, expected)

    operators = {
        "$gte": lambda a, b: a is not None and a >= b,
        "$gt": lambda a, b: a is not None and a > b,
        "$lte": lambda a, b: a is not None and a <= b,
        "$lt": lambda a, b: a is not None and a < b,
        "$ne": lambda a, b: b not in a if isinstance(a, list) else a != b,
        "$in": lambda a, b: a in b,
        "$exists": lambda a, b: (a is not None) == b,
    }

    for operator, argument in expected.items():
        check = operators.get(operator)
        if check is None:
            return actual == expected
        if not check(actual, argument):
            return False
    return True


def _field_values(doc, dotted_key):
    """Every value a dotted path reaches, walking into each item of a list on the way."""
    found = [doc]
    for part in dotted_key.split("."):
        reached = []
        for current in found:
            if isinstance(current, dict):
                if part in current:
                    reached.append(current[part])
            elif isinstance(current, list):
                reached.extend(
                    item[part] for item in current if isinstance(item, dict) and part in item
                )
        found = reached
    return found or [None]


def _matches(doc, filter_dict):
    """Whether a document matches every field of a filter, dotted paths included."""
    for key, expected in filter_dict.items():
        if "." not in key:
            if not _matches_value(doc.get(key), expected):
                return False
            continue
        if not any(_matches_value(value, expected) for value in _field_values(doc, key)):
            return False
    return True


def _inserted(filter_dict):
    """The document an upsert starts from: the filter's plain equalities."""
    document = {}
    for key, value in filter_dict.items():
        if isinstance(value, dict) and any(str(name).startswith("$") for name in value):
            continue
        owner, leaf = _resolve_path(document, key, create=True)
        owner[leaf] = value
    return document


def _grouped(rows, specification):
    """`$group` by one `$path`, counting each group with `{"$sum": 1}`."""
    path = specification["_id"].removeprefix("$")
    counters = [name for name in specification if name != "_id"]
    groups = {}
    for row in rows:
        value = _field_values(row, path)[0]
        key = repr(value)
        group = groups.setdefault(key, {"_id": value, **{name: 0 for name in counters}})
        for name in counters:
            group[name] += specification[name]["$sum"]
    return list(groups.values())


class MockMongoCollection:
    """Mock de uma collection MongoDB."""

    def __init__(self):
        self._data = []

    def find_one(self, filter_dict=None, projection=None):
        for doc in self._data:
            if _matches(doc, filter_dict or {}):
                return doc.copy()
        return None

    def find(self, filter_dict=None, projection=None):
        filter_dict = filter_dict or {}
        return MockCursor([doc.copy() for doc in self._data if _matches(doc, filter_dict)])

    def count_documents(self, filter_dict=None):
        return len(list(self.find(filter_dict or {})))

    def create_index(self, keys, **options):
        """Indexes are a real-driver concern; offline they are a no-op."""
        return "mock_index"

    def insert_one(self, doc):
        self._data.append(doc.copy())
        return MagicMock(inserted_id="mock_id")

    def insert_many(self, docs, ordered=True):
        for doc in docs:
            self._data.append(doc.copy())
        return MagicMock(inserted_ids=["mock_id"] * len(docs))

    def update_one(self, filter_dict, update, upsert=False):
        for doc in self._data:
            if _matches(doc, filter_dict):
                _apply_update(doc, update, inserted=False)
                return MagicMock(matched_count=1, modified_count=1)
        if upsert:
            new_doc = _inserted(filter_dict)
            _apply_update(new_doc, update, inserted=True)
            self._data.append(new_doc)
            return MagicMock(matched_count=0, modified_count=0, upserted_id="mock_id")
        return MagicMock(matched_count=0, modified_count=0)

    def update_many(self, filter_dict, update, upsert=False):
        """Every match, not just the first: the real driver's semantics."""
        modified = 0
        for doc in self._data:
            if _matches(doc, filter_dict):
                _apply_update(doc, update, inserted=False)
                modified += 1
        if not modified and upsert:
            new_doc = _inserted(filter_dict)
            _apply_update(new_doc, update, inserted=True)
            self._data.append(new_doc)
            return MagicMock(matched_count=0, modified_count=0, upserted_id="mock_id")
        return MagicMock(matched_count=modified, modified_count=modified)

    def bulk_write(self, operations, ordered=True):
        for operation in operations:
            self.update_one(
                operation._filter, operation._doc, upsert=operation._upsert
            )
        return MagicMock(modified_count=len(operations))

    def aggregate(self, pipeline):
        """The stages the event counts send: $match, $group by one path, $sort, $limit."""
        rows = [doc.copy() for doc in self._data]
        for stage in pipeline:
            (operator, argument), = stage.items()
            if operator == "$match":
                rows = [row for row in rows if _matches(row, argument)]
            elif operator == "$group":
                rows = _grouped(rows, argument)
            elif operator == "$sort":
                rows = MockCursor(rows).sort(list(argument.items()))._data
            elif operator == "$limit":
                rows = rows[:argument]
            else:
                raise NotImplementedError(operator)
        return MockCursor(rows)

    def find_one_and_delete(self, filter_dict):
        for index, doc in enumerate(self._data):
            if _matches(doc, filter_dict):
                return self._data.pop(index)
        return None

    def delete_one(self, filter_dict):
        for i, doc in enumerate(self._data):
            if _matches(doc, filter_dict):
                self._data.pop(i)
                return MagicMock(deleted_count=1)
        return MagicMock(deleted_count=0)

    def delete_many(self, filter_dict=None):
        if not filter_dict:
            count = len(self._data)
            self._data.clear()
            return MagicMock(deleted_count=count)

        to_delete = []
        for i, doc in enumerate(self._data):
            if _matches(doc, filter_dict):
                to_delete.append(i)

        for i in reversed(to_delete):
            self._data.pop(i)

        return MagicMock(deleted_count=len(to_delete))


class MockMongoDatabase:
    """Mock de um database MongoDB."""

    def __init__(self):
        self._collections = {}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = MockMongoCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self[name]

    def list_collection_names(self):
        return list(self._collections.keys())


class MockMongoClient:
    """Mock do cliente MongoDB para testes unitarios."""

    def __init__(self):
        self._databases = {}

    def __getitem__(self, name):
        if name not in self._databases:
            self._databases[name] = MockMongoDatabase()
        return self._databases[name]

    def __getattr__(self, name):
        return self[name]

    def list_database_names(self):
        return list(self._databases.keys())


class FakeAsyncCursor:
    """The awaited side of a cursor: `sort` and `limit` chain, `to_list` and `async for`
    read."""

    def __init__(self, cursor):
        self._cursor = cursor
        self._items = None

    def sort(self, field, direction=-1):
        self._cursor.sort(field, direction)
        return self

    def limit(self, count):
        self._cursor.limit(count)
        return self

    async def to_list(self, length=None):
        items = self._remaining()
        taken = list(items if length is None else items[:length])
        del items[: len(taken)]
        return taken

    def __aiter__(self):
        return self

    async def __anext__(self):
        items = self._remaining()
        if not items:
            raise StopAsyncIteration
        return items.pop(0)

    def _remaining(self):
        if self._items is None:
            self._items = list(self._cursor)
        return self._items


class FakeAsyncCollection:
    """An AsyncMongoClient-shaped collection over the same in-memory documents, reached
    only when used, as Mongo creates a collection only once something touches it."""

    def __init__(self, database, name):
        self._database = database
        self._name = name

    @property
    def _collection(self):
        return self._database[self._name]

    def find(self, *args, **kwargs):
        return FakeAsyncCursor(self._collection.find(*args, **kwargs))

    async def aggregate(self, pipeline):
        return FakeAsyncCursor(self._collection.aggregate(pipeline))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        method = getattr(self._collection, name)

        async def call(*args, **kwargs):
            return method(*args, **kwargs)

        return call


class FakeAsyncDatabase:
    def __init__(self, database):
        self._database = database

    def __getitem__(self, name):
        return FakeAsyncCollection(self._database, name)

    def __getattr__(self, name):
        return self[name]

    async def list_collection_names(self):
        return self._database.list_collection_names()

    async def command(self, command):
        return {"ok": 1.0}


class FakeAsyncMongoClient:
    """The async client over a MockMongoClient: every write lands in the same store."""

    def __init__(self, backing):
        self.backing = backing
        self.closed = False

    def __getitem__(self, name):
        return FakeAsyncDatabase(self.backing[name])

    def __getattr__(self, name):
        return self[name]

    async def close(self):
        self.closed = True


def settled(coroutine):
    """The answer of a coroutine over the fake, which never waits on anything, run to
    its end at once; a coroutine that would wait fails the test instead."""
    try:
        coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    coroutine.close()
    raise RuntimeError("a coroutine over the fake Mongo waited on something real")


class SettlingLoop:
    """The loop `app.data.wait` falls back to with no loop bound: a loop of the calling
    thread's own, or, on a thread whose loop is already running (a test calling a
    thread job directly), the coroutine settled where it is, over the fake."""

    _threads = threading.local()

    def run_until_complete(self, awaitable):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._own().run_until_complete(awaitable)
        return settled(awaitable)

    def is_closed(self):
        return False

    def _own(self):
        loop = getattr(self._threads, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            self._threads.loop = loop
        return loop


def fake_store():
    """The in-memory store the installed repositories write to, read and seeded sync."""
    from app.data import data

    return data().client.backing


def answering(value):
    """A stand-in for a coroutine function: awaited with any arguments, it answers
    `value`."""
    async def call(*_args, **_kwargs):
        return value

    return call
