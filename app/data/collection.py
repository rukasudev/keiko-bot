"""The narrow shape of an async Mongo client that the repositories use."""

from typing import Any, AsyncIterator, Dict, List, Mapping, Optional, Protocol, Sequence

from pymongo.results import (
    BulkWriteResult,
    DeleteResult,
    InsertManyResult,
    InsertOneResult,
    UpdateResult,
)

Document = Dict[str, Any]
Query = Mapping[str, Any]


class Cursor(Protocol):
    """The documents a find returns, sorted and cut on the server, read awaited."""

    def sort(self, key: Any, direction: Any = None, /) -> "Cursor":
        """The same documents in the order of `key`."""
        ...

    def limit(self, limit: int, /) -> "Cursor":
        """At most `limit` of the documents."""
        ...

    async def to_list(self, length: Optional[int] = None, /) -> List[Document]:
        """The next `length` documents, or every one left."""
        ...

    def __aiter__(self) -> AsyncIterator[Document]:
        """The documents one by one, fetched a batch at a time."""
        ...


class Rows(Protocol):
    """The documents an aggregation returns."""

    async def to_list(self, length: Optional[int] = None, /) -> List[Document]:
        """The next `length` documents, or every one left."""
        ...


class Collection(Protocol):
    """The operations of one collection the repositories send."""

    async def find_one(
        self, query: Query, projection: Optional[Query] = None, /
    ) -> Optional[Document]:
        """The first document matching `query`."""
        ...

    def find(self, query: Query, projection: Optional[Query] = None, /) -> Cursor:
        """Every document matching `query`."""
        ...

    async def count_documents(self, query: Query, /) -> int:
        """How many documents match `query`."""
        ...

    async def aggregate(self, pipeline: Sequence[Query], /) -> Rows:
        """The rows `pipeline` returns."""
        ...

    async def insert_one(self, document: Document, /) -> InsertOneResult:
        """Write one document."""
        ...

    async def insert_many(
        self, documents: Sequence[Document], /, ordered: bool = True
    ) -> InsertManyResult:
        """Write several documents."""
        ...

    async def update_one(
        self, query: Query, update: Query, /, upsert: bool = False
    ) -> UpdateResult:
        """Change the first document matching `query`."""
        ...

    async def update_many(
        self, query: Query, update: Query, /, upsert: bool = False
    ) -> UpdateResult:
        """Change every document matching `query`."""
        ...

    async def bulk_write(
        self, requests: Sequence[Any], /, ordered: bool = True
    ) -> BulkWriteResult:
        """Send several writes in one round trip."""
        ...

    async def delete_one(self, query: Query, /) -> DeleteResult:
        """Delete the first document matching `query`."""
        ...

    async def delete_many(self, query: Query, /) -> DeleteResult:
        """Delete every document matching `query`."""
        ...

    async def find_one_and_delete(self, query: Query, /) -> Optional[Document]:
        """Delete the first document matching `query` and return it."""
        ...


class Database(Protocol):
    """One database: its collections by name."""

    def __getitem__(self, name: str, /) -> Collection:
        """The collection called `name`."""
        ...

    async def list_collection_names(self) -> List[str]:
        """The names of the collections the database holds."""
        ...

    async def command(self, command: str, /) -> Mapping[str, Any]:
        """The server's answer to `command`."""
        ...


class Client(Protocol):
    """The client: its databases by name, and the connections it holds."""

    def __getitem__(self, name: str, /) -> Database:
        """The database called `name`."""
        ...

    async def close(self) -> None:
        """Close every connection."""
        ...


class Repository:
    """One collection's operations, over the collection given at startup."""

    def __init__(self, collection: Collection) -> None:
        self._collection = collection
