"""
Mocks de banco de dados para testes.

Este modulo contem:
- Early patching: mocks instalados antes que modulos do app sejam importados
- MockRedisClient, MockMongoClient: mocks completos para testes unitarios

IMPORTANTE: Importar este modulo executa o early patching como side-effect.
Isso e intencional - o conftest.py importa este modulo antes de qualquer
modulo da app.
"""

from unittest.mock import MagicMock


# ============================================================================
# EARLY PATCHING - Before any app modules are imported
# ============================================================================
# This is necessary because app.data.* modules import mongo_client at load time

class _EarlyMockMongoClient:
    """Early mock for mongo_client before tests configure it properly."""
    def __getitem__(self, name):
        return _EarlyMockDatabase()

    def __getattr__(self, name):
        return _EarlyMockDatabase()


class _EarlyMockDatabase:
    """Early mock database."""
    def __getitem__(self, name):
        return _EarlyMockCollection()

    def __getattr__(self, name):
        return _EarlyMockCollection()


class _EarlyMockCollection:
    """Early mock collection."""
    def find_one(self, *args, **kwargs):
        return None

    def find(self, *args, **kwargs):
        return []

    def count_documents(self, *args, **kwargs):
        return 0

    def insert_one(self, *args, **kwargs):
        return MagicMock(inserted_id="mock")

    def update_one(self, *args, **kwargs):
        return MagicMock(modified_count=0)

    def delete_one(self, *args, **kwargs):
        return MagicMock(deleted_count=0)


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


# Install early mocks into app module before anything imports from it
import app
app.mongo_client = _EarlyMockMongoClient()
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

    def set(self, key, value):
        self._data[key] = value

    def setex(self, key, expiration, value):
        self._data[key] = value

    def delete(self, *keys):
        for key in keys:
            self._data.pop(key, None)

    def keys(self, pattern):
        import fnmatch
        return [k for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]

    def scan_iter(self, pattern):
        import fnmatch
        for key in list(self._data.keys()):
            if fnmatch.fnmatch(key, pattern):
                yield key

    def flushdb(self):
        self._data.clear()

    def ping(self):
        return True

    def incrby(self, key, amount=1):
        current = int(self._data.get(key, 0))
        self._data[key] = str(current + amount)
        return current + amount

    def expire(self, key, seconds):
        self._expirations[key] = seconds
        return True

    def ttl(self, key):
        return self._expirations.get(key, -1)


class MockCursor:
    """Mock de um cursor MongoDB."""

    def __init__(self, data):
        self._data = data

    def sort(self, field, direction=-1):
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


class MockMongoCollection:
    """Mock de uma collection MongoDB."""

    def __init__(self):
        self._data = []

    def find_one(self, filter_dict):
        for doc in self._data:
            if all(doc.get(k) == v for k, v in filter_dict.items()):
                return doc.copy()
        return None

    def find(self, filter_dict=None, projection=None):
        filter_dict = filter_dict or {}
        results = []
        for doc in self._data:
            match = True
            for k, v in filter_dict.items():
                # Suporte a queries aninhadas como "notifications.values.streamer.value"
                if '.' in k:
                    parts = k.split('.')
                    current = doc
                    for part in parts:
                        if isinstance(current, dict):
                            current = current.get(part)
                        elif isinstance(current, list):
                            found = False
                            for item in current:
                                if isinstance(item, dict) and part in item:
                                    current = item.get(part)
                                    found = True
                                    break
                            if not found:
                                current = None
                                break
                        else:
                            current = None
                            break
                    if current != v:
                        match = False
                        break
                elif doc.get(k) != v:
                    match = False
                    break
            if match:
                results.append(doc.copy())
        return MockCursor(results)

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
            if all(doc.get(k) == v for k, v in filter_dict.items()):
                _apply_update(doc, update, inserted=False)
                return MagicMock(modified_count=1)
        if upsert:
            new_doc = filter_dict.copy()
            _apply_update(new_doc, update, inserted=True)
            self._data.append(new_doc)
            return MagicMock(modified_count=0, upserted_id="mock_id")
        return MagicMock(modified_count=0)

    def bulk_write(self, operations, ordered=True):
        for operation in operations:
            self.update_one(
                operation._filter, operation._doc, upsert=operation._upsert
            )
        return MagicMock(modified_count=len(operations))

    def delete_one(self, filter_dict):
        for i, doc in enumerate(self._data):
            if all(doc.get(k) == v for k, v in filter_dict.items()):
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
            if all(doc.get(k) == v for k, v in filter_dict.items()):
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

    async def list_collection_names(self):
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
