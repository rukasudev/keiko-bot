"""What a migration file writes with: index specs for `schema/`, `begin` for `data/`."""

import functools
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import TracebackType
from typing import Any, Dict, Mapping, Optional, Sequence, Type

from pymongo import ReadPreference
from pymongo.client_session import ClientSession
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.config import app_version
from app.constants import Migrations as constants

Spec = Dict[str, Any]


def index(
    *fields: str,
    unique: bool = False,
    partial: Optional[Mapping[str, Any]] = None,
    name: Optional[str] = None,
) -> Spec:
    """An index on `fields` in order, a field written `-field` sorting descending."""
    spec: Spec = {
        "key": [
            [field_name.removeprefix("-"), -1 if field_name.startswith("-") else 1]
            for field_name in fields
        ]
    }
    if unique:
        spec["unique"] = True
    if partial is not None:
        spec["partialFilterExpression"] = dict(partial)
    if name is not None:
        spec["name"] = name
    return spec


def ttl(field_name: str, days: Optional[int] = None, seconds: int = 0) -> Spec:
    """An index removing a document `days` plus `seconds` after its `field_name`."""
    expire_after = (days or 0) * constants.DAY_SECONDS + seconds
    return {"key": [[field_name, 1]], "expireAfterSeconds": expire_after}


def one_per(field_name: str) -> Spec:
    """A unique index allowing one document per string value of `field_name`."""
    return {
        "key": [[field_name, 1]],
        "unique": True,
        "partialFilterExpression": {field_name: {"$type": "string"}},
    }


class BudgetExceeded(Exception):
    """A data file's transaction ran past the time budget of one transaction."""


class TransactionMisuse(Exception):
    """A data file entered `begin(db)` twice, or returned without entering it."""


@dataclass
class DataRun:
    """One run of a data file: its client, its ledger entry, the dry-run flag and the
    budget, and what its transaction counted."""

    client: Any
    entry: Dict[str, Any]
    dry_run: bool
    budget_seconds: float
    began: float = field(default_factory=time.monotonic)
    entered: int = 0
    counts: Dict[str, int] = field(default_factory=dict)


def ledger_entry(
    identity: Mapping[str, Any], counts: Mapping[str, int], seconds: float
) -> Dict[str, Any]:
    """The ledger document of one applied file: its identity and how the run went."""
    return {
        **identity,
        "applied_at": datetime.now(timezone.utc),
        "seconds": round(seconds, 3),
        "counts": dict(counts),
        "app_version": app_version(),
    }


class Bound:
    """A database or a collection whose collection operations all pass one session."""

    def __init__(self, target: Any, session: ClientSession) -> None:
        self._target = target
        self._session = session

    def __getattr__(self, name: str) -> Any:
        found = getattr(self._target, name)
        if isinstance(found, (Database, Collection)):
            return Bound(found, self._session)
        if not callable(found):
            return found

        if isinstance(self._target, Collection) and name != "with_options":
            return functools.partial(found, session=self._session)
        return functools.partial(self._bound_result, found)

    def _bound_result(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        result = method(*args, **kwargs)
        if isinstance(result, (Database, Collection)):
            return Bound(result, self._session)
        return result

    def __getitem__(self, name: str) -> "Bound":
        return Bound(self._target[name], self._session)


class Transaction:
    """The client's databases bound to one transaction, and the counts it records."""

    def __init__(
        self, client: Any, session: ClientSession, counts: Dict[str, int]
    ) -> None:
        self._client = client
        self._session = session
        self._counts = counts

    def __getattr__(self, database: str) -> Bound:
        if database.startswith("_"):
            raise AttributeError(database)
        return Bound(self._client[database], self._session)

    def __getitem__(self, database: str) -> Bound:
        return Bound(self._client[database], self._session)

    def count(self, name: str, amount: int = 1) -> None:
        """Add `amount` to the count `name` the ledger entry keeps."""
        self._counts[name] = self._counts.get(name, 0) + int(amount)


def transaction_options() -> Dict[str, Any]:
    """How every migration transaction reads, writes and bounds its commit."""
    return {
        "read_concern": ReadConcern("snapshot"),
        "write_concern": WriteConcern("majority"),
        "read_preference": ReadPreference.PRIMARY,
        "max_commit_time_ms": int(constants.TRANSACTION_COMMIT_SECONDS * 1000),
    }


class TransactionBlock:
    """The `with` block of `begin`: commits the writes and the ledger entry together,
    or aborts them."""

    def __init__(self, run: DataRun) -> None:
        self.run = run
        self.session: Optional[ClientSession] = None
        self.started = 0.0

    def __enter__(self) -> Transaction:
        if self.run.entered:
            raise TransactionMisuse(
                f"{self.run.entry['_id']} entered begin(db) twice:"
                " a data file is one transaction"
            )
        self.run.entered += 1
        self.session = self.run.client.start_session()
        self.session.start_transaction(**transaction_options())
        self.started = time.monotonic()
        return Transaction(self.run.client, self.session, self.run.counts)

    def __exit__(
        self,
        kind: Optional[Type[BaseException]],
        error: Optional[BaseException],
        trace: Optional[TracebackType],
    ) -> None:
        session = self.session
        assert session is not None
        try:
            if error is not None:
                session.abort_transaction()
                return
            self._finish(session)
        finally:
            session.end_session()

    def _finish(self, session: ClientSession) -> None:
        try:
            self._check_budget()
            self.run.client.migrations.changelog.insert_one(
                ledger_entry(
                    self.run.entry, self.run.counts, time.monotonic() - self.run.began
                ),
                session=session,
            )
        except BaseException:
            session.abort_transaction()
            raise

        if self.run.dry_run:
            session.abort_transaction()
            return
        commit(session)

    def _check_budget(self) -> None:
        spent = time.monotonic() - self.started
        if spent > self.run.budget_seconds:
            raise BudgetExceeded(
                f"{self.run.entry['_id']} transaction took {spent:.1f} s, over the"
                f" {self.run.budget_seconds:.0f} s budget of one transaction:"
                " split it into smaller data files"
            )


def begin(db: DataRun) -> TransactionBlock:
    """The one transaction of a data file, as `with begin(db) as tx:`."""
    return TransactionBlock(db)


def insert_missing(collection: Any, documents: Sequence[Mapping[str, Any]]) -> int:
    """Insert the `documents` whose `_id` the collection lacks, and count them."""
    ids = [document["_id"] for document in documents]
    present = {
        found["_id"] for found in collection.find({"_id": {"$in": ids}}, {"_id": 1})
    }
    missing = [document for document in documents if document["_id"] not in present]

    if missing:
        collection.insert_many(missing)
    return len(missing)


def commit(session: ClientSession) -> None:
    """Commit, sending the commit again only while its result is unknown."""
    deadline = time.monotonic() + constants.TRANSACTION_COMMIT_SECONDS

    while True:
        try:
            session.commit_transaction()
            return
        except PyMongoError as error:
            unknown = error.has_error_label("UnknownTransactionCommitResult")
            if not unknown or time.monotonic() >= deadline:
                raise
            time.sleep(constants.COMMIT_RETRY_PAUSE_SECONDS)
