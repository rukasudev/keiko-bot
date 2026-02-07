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
        pattern = pattern.replace('*', '.*')
        return [k for k in self._data.keys() if fnmatch.fnmatch(k, pattern)]

    def scan_iter(self, pattern):
        import fnmatch
        pattern = pattern.replace('*', '.*')
        for key in self._data.keys():
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

    def insert_one(self, doc):
        self._data.append(doc.copy())
        return MagicMock(inserted_id="mock_id")

    def update_one(self, filter_dict, update, upsert=False):
        for doc in self._data:
            if all(doc.get(k) == v for k, v in filter_dict.items()):
                if "$set" in update:
                    doc.update(update["$set"])
                return MagicMock(modified_count=1)
        if upsert:
            new_doc = filter_dict.copy()
            if "$set" in update:
                new_doc.update(update["$set"])
            self._data.append(new_doc)
            return MagicMock(modified_count=0, upserted_id="mock_id")
        return MagicMock(modified_count=0)

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
