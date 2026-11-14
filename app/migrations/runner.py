"""The migrations runner: plan, check, apply, verify and render the database."""

import functools
import hashlib
import importlib.util
import json
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    TextIO,
    Tuple,
)

import certifi
from pymongo import MongoClient
from pymongo.errors import (
    CollectionInvalid,
    DuplicateKeyError,
    PyMongoError,
    ServerSelectionTimeoutError,
)
from pymongo.write_concern import WriteConcern

from app.config import app_version
from app.constants import DBConfigs
from app.constants import Migrations as constants
from app.migrations.helpers import (
    DataRun,
    Spec,
    TransactionMisuse,
    ledger_entry,
    one_per,
    ttl,
)

Structure = Dict[str, Dict[str, Dict[str, Spec]]]
Declaration = Dict[str, Dict[str, List[Spec]]]
UpStep = Callable[[DataRun], None]
CheckStep = Callable[[DataRun], List[str]]

MIGRATIONS = Path(__file__).parent
SNAPSHOT = MIGRATIONS / "snapshot.json"
KINDS = ("schema", "data")
VERSION_FILE = re.compile(r"(\d{4})_([a-z0-9_]+)\.py")
LEDGER_ID = re.compile(r"(schema|data)/(\d{4})")
LEDGER_STRUCTURE: Declaration = {
    "migrations": {"changelog": [], "lock": [ttl("expires_at")]}
}


@dataclass(frozen=True)
class Version:
    """One file of `schema/` or `data/`: its folder, number, name, hash and content."""

    kind: str
    number: str
    name: str
    sha256: str
    structure: Declaration = field(default_factory=dict)
    up: Optional[UpStep] = None
    check: Optional[CheckStep] = None

    @property
    def id(self) -> str:
        """The ledger's id of this file, `schema/0001`."""
        return f"{self.kind}/{self.number}"

    @property
    def label(self) -> str:
        """The file as its folder and name say it, `schema/0001_baseline`."""
        return f"{self.kind}/{self.number}_{self.name}"

    def identity(self) -> Dict[str, Any]:
        """The ledger fields that name this file."""
        return {
            "_id": self.id,
            "kind": self.kind,
            "number": int(self.number),
            "name": self.name,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class Plan:
    """What the ledger and the code say together, and what is wrong between them."""

    applied: Tuple[str, ...]
    pending: Tuple[Version, ...]
    newer: Tuple[str, ...]
    problems: Tuple[str, ...]


@dataclass(frozen=True)
class Change:
    """A collection or an index a schema file creates because the database lacks it."""

    database: str
    collection: str
    index: Optional[str] = None
    spec: Optional[Spec] = None

    def describe(self) -> str:
        """The change as one line of operator text."""
        where = f"{self.database}.{self.collection}"
        if self.index is None or self.spec is None:
            return f"collection {where}"
        return f"index {where} {render_index(self.index, self.spec)}"


class LockHeld(Exception):
    """Another run holds the lock, or this run lost it."""


class MigrationsPending(RuntimeError):
    """The ledger lacks a file this code has, or a file is refused, so the bot must
    not start."""


class Report:
    """The run's output, one line per step, remembering whether a step failed."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.failed = False

    def line(self, status: str, text: str) -> None:
        """Print one step; a `fail` marks the run as failed."""
        if status == "fail":
            self.failed = True
        print(f"{status:<8}{text}", file=self.out, flush=True)


def read_versions(root: Path = MIGRATIONS) -> Tuple[List[Version], List[str]]:
    """Every file of `schema/` then of `data/`, each in number order, and the files
    refused by their name or their content."""
    versions: List[Version] = []
    problems: List[str] = []
    for kind in KINDS:
        found, refused = read_folder(kind, root)
        versions += found
        problems += refused
    return versions, problems


def read_folder(kind: str, root: Path = MIGRATIONS) -> Tuple[List[Version], List[str]]:
    """The files of one folder in number order, and the ones it refuses."""
    versions: List[Version] = []
    problems: List[str] = []
    for path in sorted((root / kind).glob("*.py")):
        if path.name == "__init__.py":
            continue
        match = VERSION_FILE.fullmatch(path.name)
        if not match:
            problems.append(f"{kind}/{path.name} is not named NNNN_slug.py")
            continue
        version, refused = _load(path, kind, match.group(1), match.group(2))
        versions.append(version)
        problems += refused
    return sorted(versions, key=lambda version: version.label), problems


def _load(path: Path, kind: str, number: str, name: str) -> Tuple[Version, List[str]]:
    spec = importlib.util.spec_from_file_location(
        f"keiko_migration_{kind}_{path.stem}", path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    structure = getattr(module, "STRUCTURE", None)
    up = getattr(module, "up", None)
    check = getattr(module, "check", None)
    version = Version(
        kind=kind,
        number=number,
        name=name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        structure=structure or {},
        up=up,
        check=check,
    )

    if kind == "schema" and (structure is None or up or check):
        return version, [f"{version.label} declares STRUCTURE only, no up or check"]
    if kind == "data" and (up is None or structure is not None):
        return version, [f"{version.label} defines up(db) and no STRUCTURE"]
    return version, []


def plan(versions: Sequence[Version], ledger: Sequence[Mapping[str, Any]]) -> Plan:
    """Compare the code's files with the ledger, one folder at a time, refusing an
    untrusted order."""
    recorded = {str(entry["_id"]): entry for entry in ledger}
    problems = _code_problems(versions)
    problems += [
        f"the ledger holds {entry_id!r}, not a schema/NNNN or data/NNNN id"
        for entry_id in sorted(recorded)
        if not LEDGER_ID.fullmatch(entry_id)
    ]
    folders = [
        _folder_plan(
            kind, [found for found in versions if found.kind == kind], recorded
        )
        for kind in KINDS
    ]
    return Plan(
        applied=tuple(entry for folder in folders for entry in folder.applied),
        pending=tuple(version for folder in folders for version in folder.pending),
        newer=tuple(entry for folder in folders for entry in folder.newer),
        problems=tuple(
            problems + [problem for folder in folders for problem in folder.problems]
        ),
    )


def _folder_plan(
    kind: str, versions: Sequence[Version], recorded: Mapping[str, Mapping[str, Any]]
) -> Plan:
    numbered = sorted(
        int(match.group(2))
        for match in (LEDGER_ID.fullmatch(entry_id) for entry_id in recorded)
        if match and match.group(1) == kind
    )
    applied: List[str] = []
    pending: List[Version] = []
    problems: List[str] = []
    for version in _first_of_each_id(versions):
        entry = recorded.get(version.id)
        if entry is None:
            pending.append(version)
            continue
        applied.append(version.id)
        problems += _entry_problems(version, entry)

    last_code = max((int(version.number) for version in versions), default=0)
    newer = [number for number in numbered if number > last_code]
    if newer != list(range(last_code + 1, last_code + 1 + len(newer))):
        problems.append(
            f"the ledger holds {_ids(kind, newer)} beyond this code's last {kind}"
            f" file {kind}/{last_code:04d}: only a contiguous tail (an older image)"
            " is allowed"
        )
    last_applied = numbered[-1] if numbered else 0
    late = [version.label for version in pending if int(version.number) < last_applied]
    if late:
        problems.append(
            f"{', '.join(late)} pending below {kind}/{last_applied:04d}, the last"
            " applied: files were merged out of order, renumber them after it"
        )
    return Plan(
        applied=tuple(applied),
        pending=tuple(pending),
        newer=tuple(f"{kind}/{number:04d}" for number in newer),
        problems=tuple(problems),
    )


def _code_problems(versions: Sequence[Version]) -> List[str]:
    problems = []
    for kind in KINDS:
        by_number: Dict[str, List[str]] = {}
        for version in versions:
            if version.kind == kind:
                by_number.setdefault(version.number, []).append(version.label)
        for number, labels in sorted(by_number.items()):
            if len(labels) > 1:
                problems.append(f"{' and '.join(labels)} share the id {kind}/{number}")
        numbers = sorted(int(number) for number in by_number)
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(
                f"{kind} files must be numbered from 0001 without a gap,"
                f" found {_ids(kind, numbers)}"
            )
    return problems


def _first_of_each_id(versions: Sequence[Version]) -> List[Version]:
    seen: Set[str] = set()
    first = []
    for version in versions:
        if version.id not in seen:
            seen.add(version.id)
            first.append(version)
    return first


def _entry_problems(version: Version, entry: Mapping[str, Any]) -> List[str]:
    if entry.get("name") != version.name:
        return [
            f"the ledger's {version.id} is {version.id}_{entry.get('name')},"
            f" the file is {version.label}"
        ]
    if entry.get("sha256") != version.sha256:
        return [
            f"{version.label} changed after it was applied (ledger sha256"
            f" {str(entry.get('sha256'))[:12]}, file {version.sha256[:12]}):"
            " never edit an applied file, write a new one"
        ]
    return []


def _ids(kind: str, numbers: Sequence[int]) -> str:
    return ", ".join(f"{kind}/{number:04d}" for number in numbers) or "none"


def normal(spec: Mapping[str, Any]) -> Spec:
    """An index spec as declared or as Mongo lists it, reduced to what defines it."""
    reduced: Spec = {}
    for option, value in spec.items():
        if option in {"v", "ns", "name", "background"}:
            continue
        if option in {"unique", "sparse"} and value is False:
            continue
        if option == "key":
            pairs = value.items() if isinstance(value, Mapping) else value
            reduced["key"] = [[field, _plain(direction)] for field, direction in pairs]
        else:
            reduced[option] = _plain(value)
    return dict(sorted(reduced.items()))


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def default_name(key: Sequence[Sequence[Any]]) -> str:
    """The name Mongo gives an index on `key` when none is given, `guild_id_1_ts_-1`."""
    return "_".join(f"{field}_{direction}" for field, direction in key)


def named(specs: Sequence[Spec]) -> List[Tuple[str, Spec]]:
    """Each spec a schema file lists, under its name and reduced by `normal`."""
    reduced = [(spec.get("name"), normal(spec)) for spec in specs]
    return [(name or default_name(spec["key"]), spec) for name, spec in reduced]


def declared(versions: Sequence[Version]) -> Tuple[Structure, List[str]]:
    """The structure every schema file declares together, and any index declared
    twice with two specs."""
    merged: Structure = {}
    problems = []
    for version in versions:
        for database, collections in version.structure.items():
            for collection, specs in collections.items():
                target = merged.setdefault(database, {}).setdefault(collection, {})
                for name, wanted in named(specs):
                    if name in target and target[name] != wanted:
                        problems.append(
                            f"{version.label} declares {database}.{collection}"
                            f" {name} as {_json(wanted)}, an earlier one as"
                            f" {_json(target[name])}"
                        )
                    target.setdefault(name, wanted)
    return merged, problems


@functools.lru_cache(maxsize=1)
def declared_structure() -> Structure:
    """What the shipped schema files declare together, read from disk once."""
    return declared(read_folder("schema")[0])[0]


def expected_structure(versions: Sequence[Version]) -> Structure:
    """What a database migrated by these files holds: theirs plus the ledger's."""
    merged, _problems = declared(versions)
    for database, collections in LEDGER_STRUCTURE.items():
        for collection, specs in collections.items():
            merged.setdefault(database, {})[collection] = dict(named(specs))
    return _sorted(merged)


def _sorted(structure: Structure) -> Structure:
    return {
        database: {
            collection: dict(sorted(indexes.items()))
            for collection, indexes in sorted(collections.items())
        }
        for database, collections in sorted(structure.items())
    }


def live_structure(client: Any) -> Structure:
    """Every collection and index of the non-system databases, as Mongo lists them."""
    structure: Structure = {}
    for database in client.list_database_names():
        if database in constants.SYSTEM_DATABASES:
            continue
        collections: Dict[str, Dict[str, Spec]] = {}
        for info in client[database].list_collections():
            name = info["name"]
            if info.get("type", "collection") != "collection":
                continue
            if name.startswith("system."):
                continue
            collections[name] = {
                index["name"]: normal(index)
                for index in client[database][name].list_indexes()
                if index["name"] != "_id_"
            }
        structure[database] = collections
    return _sorted(structure)


def render_index(name: str, spec: Mapping[str, Any]) -> str:
    """An index in the notation of the helpers, or its spec as JSON when the helpers
    cannot write it."""
    key = spec["key"]
    options = {option: value for option, value in spec.items() if option != "key"}
    single_field = key[0][0] if len(key) == 1 and key[0][1] == 1 else None
    default = name == default_name(key)

    if not all(direction in (1, -1) for _field, direction in key):
        return _json({"name": name, **spec})
    if default and single_field and normal(spec) == normal(one_per(single_field)):
        return f"one_per({single_field})"
    if default and single_field and set(options) == {"expireAfterSeconds"}:
        return _ttl_text(single_field, options["expireAfterSeconds"])
    if set(options) <= {"unique", "partialFilterExpression"}:
        return _index_text(name, key, options, default)
    return _json({"name": name, **spec})


def _ttl_text(field_name: str, seconds: int) -> str:
    if seconds == 0:
        return f"ttl({field_name})"
    if seconds % constants.DAY_SECONDS == 0:
        return f"ttl({field_name}, days={seconds // constants.DAY_SECONDS})"
    return f"ttl({field_name}, seconds={seconds})"


def _index_text(
    name: str, key: Sequence[Sequence[Any]], options: Mapping[str, Any], default: bool
) -> str:
    arguments = [f"-{field}" if direction == -1 else field for field, direction in key]
    if options.get("unique"):
        arguments.append("unique=True")
    if "partialFilterExpression" in options:
        arguments.append(f"partial={_json(options['partialFilterExpression'])}")
    if not default:
        arguments.append(f"name={_json(name)}")
    return f"index({', '.join(arguments)})"


def render(structure: Structure) -> str:
    """The structure as `snapshot.json` holds it: one line per collection, its
    indexes in the helpers' notation, sorted."""
    lines = [
        f"  {_json(f'{database}.{collection}')}:"
        f" {_json(sorted(render_index(name, spec) for name, spec in indexes.items()))}"
        for database, collections in sorted(structure.items())
        for collection, indexes in sorted(collections.items())
    ]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def structure_changes(
    live: Structure, versions: Sequence[Version]
) -> Tuple[Dict[str, List[Change]], List[str]]:
    """What each schema file would create, and each index conflicting with a live
    one."""
    changes: Dict[str, List[Change]] = {}
    problems: List[str] = []
    planned: Set[Tuple[str, str, Optional[str]]] = set()
    for version in versions:
        mine = changes.setdefault(version.label, [])
        for database, collections in version.structure.items():
            for collection, specs in collections.items():
                existing = live.get(database, {}).get(collection)
                if existing is None and (database, collection, None) not in planned:
                    planned.add((database, collection, None))
                    mine.append(Change(database, collection))
                for name, wanted in named(specs):
                    conflict = _conflict(database, collection, name, wanted, existing)
                    if conflict:
                        problems.append(conflict)
                    elif name not in (existing or {}) and (
                        (database, collection, name) not in planned
                    ):
                        planned.add((database, collection, name))
                        mine.append(Change(database, collection, name, wanted))
    return changes, problems


def _conflict(
    database: str,
    collection: str,
    name: str,
    wanted: Spec,
    existing: Optional[Dict[str, Spec]],
) -> Optional[str]:
    where = f"{database}.{collection}"
    for other, spec in (existing or {}).items():
        if other == name and spec != wanted:
            return (
                f"index {where} {name} exists as {_json(spec)},"
                f" this code declares {_json(wanted)}"
            )
        if other != name and _same_coverage(spec, wanted):
            return (
                f"index {where} {other} {_json(spec)} already covers what this code"
                f" names {name} {_json(wanted)}"
            )
    return None


def _same_coverage(one: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    return all(
        one.get(option) == other.get(option)
        for option in ("key", "partialFilterExpression", "collation")
    )


def duplicates(client: Any, change: Change) -> List[str]:
    """The values a unique index to create would refuse, a few of them at most."""
    spec = change.spec or {}
    if not spec.get("unique"):
        return []
    fields = [field for field, _direction in spec["key"]]
    rows = client[change.database][change.collection].aggregate(
        [
            {"$match": spec.get("partialFilterExpression", {})},
            {
                "$group": {
                    "_id": {
                        f"k{position}": f"${field}"
                        for position, field in enumerate(fields)
                    },
                    "documents": {"$sum": 1},
                }
            },
            {"$match": {"documents": {"$gt": 1}}},
            {"$limit": 3},
        ]
    )
    return [
        f"{change.database}.{change.collection}: {row['documents']} documents share"
        f" {', '.join(fields)} = {_json(list(row['_id'].values()))}, so the unique"
        f" index {change.index} cannot be built"
        for row in rows
    ]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def connect(url: str, production: bool) -> MongoClient[Dict[str, Any]]:
    """A Mongo client for one run, with production's TLS when it runs there."""
    return MongoClient(
        url,
        tls=production,
        tlsCAFile=certifi.where() if production else None,
        serverSelectionTimeoutMS=int(
            DBConfigs.MONGO_SERVER_SELECTION_TIMEOUT_SECONDS * 1000
        ),
        connectTimeoutMS=int(DBConfigs.MONGO_CONNECT_TIMEOUT_SECONDS * 1000),
    )


def check_server(client: Any, report: Report) -> bool:
    """Wait for a writable primary, then require a replica set and a recent server."""
    deadline = time.monotonic() + DBConfigs.MONGO_BOOT_WAIT_SECONDS

    while True:
        try:
            hello = client.admin.command("hello")
        except ServerSelectionTimeoutError as error:
            if time.monotonic() >= deadline:
                report.line("fail", f"mongo unreachable: {type(error).__name__}")
                return False
            continue
        except PyMongoError as error:
            report.line("fail", f"mongo refused hello: {type(error).__name__}: {error}")
            return False

        if hello.get("isWritablePrimary") or hello.get("msg") == "isdbgrid":
            break
        if time.monotonic() >= deadline:
            report.line("fail", "mongo answers but no member is a writable primary")
            return False
        time.sleep(1)

    replica_set = hello.get("setName") or ("sharded" if hello.get("msg") else None)
    if not replica_set:
        report.line(
            "fail",
            "mongo is a standalone server: transactions need a replica set"
            " (docs/migrations.md, converting a local volume)",
        )
        return False
    version = client.admin.command("buildInfo")["version"]
    major_minor = tuple(int(part) for part in version.split(".")[:2])
    if major_minor < constants.MIN_SERVER_VERSION:
        wanted = ".".join(str(part) for part in constants.MIN_SERVER_VERSION)
        report.line("fail", f"mongo {version} is older than {wanted}")
        return False
    report.line("ok", f"mongo {version}, replica set {replica_set}, primary writable")
    return True


def holder() -> str:
    """Who takes the lock: this host and this process."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def prepare_ledger(client: Any) -> None:
    """Create the ledger and the lock, with the TTL index that frees a dead lock."""
    changes: List[Change] = []
    for database, collections in LEDGER_STRUCTURE.items():
        for collection, specs in collections.items():
            changes.append(Change(database, collection))
            changes.extend(
                Change(database, collection, name, spec) for name, spec in named(specs)
            )

    apply_structure(client, changes)


def take_lock(client: Any, who: str) -> datetime:
    """Insert the one lock document, or name the run that already holds it."""
    locks = client.migrations.lock
    now = _now()
    locks.delete_one({"_id": "lock", "expires_at": {"$lt": now}})
    expires_at = now + timedelta(seconds=constants.LOCK_SECONDS)
    try:
        locks.insert_one(
            {
                "_id": "lock",
                "holder": who,
                "app_version": app_version(),
                "taken_at": now,
                "expires_at": expires_at,
            }
        )
    except DuplicateKeyError:
        raise LockHeld(_holder_text(locks.find_one({"_id": "lock"}))) from None
    return expires_at


def extend_lock(client: Any, who: str) -> None:
    """Push the lock's expiry forward, or stop when another run took it."""
    expires_at = _now() + timedelta(seconds=constants.LOCK_SECONDS)
    result = client.migrations.lock.update_one(
        {"_id": "lock", "holder": who}, {"$set": {"expires_at": expires_at}}
    )
    if result.matched_count != 1:
        raise LockHeld(f"lost: {_holder_text(client.migrations.lock.find_one())}")


def release_lock(client: Any, who: str) -> None:
    """Delete the lock when this run still holds it."""
    client.migrations.lock.delete_one({"_id": "lock", "holder": who})


def _holder_text(lock: Optional[Mapping[str, Any]]) -> str:
    if not lock:
        return "held by nobody now"
    return (
        f"held by {lock.get('holder')} ({lock.get('app_version')})"
        f" since {lock.get('taken_at')}, until {lock.get('expires_at')}"
    )


def read_ledger(client: Any) -> List[Dict[str, Any]]:
    """Every ledger entry, in id order."""
    return list(client.migrations.changelog.find({}).sort("_id", 1))


def apply_structure(client: Any, changes: Sequence[Change]) -> None:
    """Create each missing collection and index; creating one that exists is a no-op."""
    for change in changes:
        database = client[change.database]
        if change.index is None:
            try:
                database.create_collection(change.collection)
            except CollectionInvalid:
                pass
            continue
        spec = change.spec or {}
        options = {key: value for key, value in spec.items() if key != "key"}
        database[change.collection].create_index(
            [tuple(pair) for pair in spec["key"]], name=change.index, **options
        )


def apply_schema(
    client: Any, version: Version, changes: Sequence[Change]
) -> Dict[str, int]:
    """Create a schema file's missing pieces, then record it in the ledger."""
    started = time.monotonic()
    apply_structure(client, changes)

    counts = {
        "collections_created": sum(change.index is None for change in changes),
        "indexes_created": sum(change.index is not None for change in changes),
    }
    ledger = client.migrations.changelog.with_options(
        write_concern=WriteConcern("majority")
    )
    ledger.insert_one(
        ledger_entry(version.identity(), counts, time.monotonic() - started)
    )
    return counts


def apply_data(client: Any, version: Version, dry_run: bool = False) -> DataRun:
    """Run a data file's `up`, which commits its writes with its ledger entry, or
    aborts them on a dry run."""
    run = data_run(client, version, dry_run)
    if version.up:
        version.up(run)

    if run.entered != 1:
        raise TransactionMisuse(
            f"{version.label} returned without entering begin(db):"
            " a data file writes in one transaction"
        )
    return run


def data_run(client: Any, version: Version, dry_run: bool) -> DataRun:
    """The `db` a data file's `check` and `up` receive."""
    return DataRun(
        client=client,
        entry=version.identity(),
        dry_run=dry_run,
        budget_seconds=constants.TRANSACTION_BUDGET_SECONDS,
    )


def rehearse(
    client: Any, current: Plan, changes: Mapping[str, List[Change]], report: Report
) -> bool:
    """List what each schema file would create, then run each data file in a
    transaction that is aborted."""
    for label, version_changes in changes.items():
        for change in version_changes:
            report.line("dry", f"{label} would create {change.describe()}")

    for version in current.pending:
        if version.kind != "data":
            continue
        try:
            run = apply_data(client, version, dry_run=True)
        except Exception as error:
            report.line(
                "fail",
                f"dry run stopped at {version.label}: {type(error).__name__}: {error}",
            )
            return False
        report.line("dry", f"{version.label} would write {_json(run.counts)}")
    report.line("ok", "dry run wrote nothing: each transaction was aborted")
    return True


def _report_plan(current: Plan, report: Report) -> bool:
    for problem in current.problems:
        report.line("fail", problem)
    if current.problems:
        return False

    pending = ", ".join(version.label for version in current.pending)
    report.line(
        "ok",
        f"ledger: {len(current.applied)} applied, {len(current.pending)} pending"
        + (f" ({pending})" if pending else ""),
    )
    if current.newer:
        report.line(
            "info",
            f"ledger holds {', '.join(current.newer)} beyond this code"
            " (an older image runs): left as is",
        )
    return True


def preflight(
    client: Any, versions: Sequence[Version], current: Plan, report: Report
) -> Optional[Dict[str, List[Change]]]:
    """Run every check due before a write; the changes to make when all of them pass."""
    _merged, problems = declared(versions)
    for problem in problems:
        report.line("fail", problem)

    for version in current.pending:
        if version.check:
            _run_check(client, version, report)

    schema = [version for version in current.pending if version.kind == "schema"]
    changes, conflicts = structure_changes(live_structure(client), schema)
    for conflict in conflicts:
        report.line("fail", conflict)
    for version_changes in changes.values():
        for change in version_changes:
            for duplicate in duplicates(client, change):
                report.line("fail", duplicate)

    if report.failed:
        return None
    total = sum(len(version_changes) for version_changes in changes.values())
    report.line(
        "ok", f"structure: {total} to create, no conflict with the live indexes"
    )
    return changes


def _run_check(client: Any, version: Version, report: Report) -> None:
    try:
        found = version.check(data_run(client, version, True)) if version.check else []
    except Exception as error:
        found = [f"raised {type(error).__name__}: {error}"]
    for problem in found:
        report.line("fail", f"check {version.label}: {problem}")
    if not found:
        report.line("ok", f"check {version.label}")


def migrate(
    client: Any,
    versions: Sequence[Version],
    report: Report,
    dry_run: bool = False,
    who: Optional[str] = None,
) -> bool:
    """Check everything, then apply each pending schema file and then each pending
    data file in order, under the lock."""
    if not check_server(client, report):
        return False

    who = who or holder()
    locked = False
    try:
        if dry_run:
            if not _lock_free(client, report):
                return False
        else:
            prepare_ledger(client)
            expires_at = take_lock(client, who)
            locked = True
            report.line("ok", f"lock taken by {who} until {expires_at:%H:%M:%S}Z")
        return _checked_run(client, versions, report, dry_run, who)
    except LockHeld as error:
        report.line("fail", f"lock {error}")
        return False
    finally:
        if locked:
            release_lock(client, who)


def _checked_run(
    client: Any, versions: Sequence[Version], report: Report, dry_run: bool, who: str
) -> bool:
    current = plan(versions, read_ledger(client))
    if not _report_plan(current, report):
        return False
    changes = preflight(client, versions, current, report)
    if changes is None:
        return False

    if not current.pending:
        report.line("ok", "nothing to apply")
        return True
    if dry_run:
        return rehearse(client, current, changes, report)
    return _apply_all(client, current.pending, changes, report, who)


def _lock_free(client: Any, report: Report) -> bool:
    lock = client.migrations.lock.find_one({"_id": "lock"})
    expires_at = (lock or {}).get("expires_at")
    if lock and expires_at and expires_at.replace(tzinfo=timezone.utc) > _now():
        report.line("fail", f"lock {_holder_text(lock)}")
        return False
    report.line("ok", "lock free (a dry run does not take it)")
    return True


def _apply_all(
    client: Any,
    pending: Sequence[Version],
    changes: Mapping[str, List[Change]],
    report: Report,
    who: str,
) -> bool:
    for version in pending:
        extend_lock(client, who)
        started = time.monotonic()
        try:
            if version.kind == "schema":
                counts = apply_schema(client, version, changes.get(version.label, []))
            else:
                counts = apply_data(client, version).counts
        except Exception as error:
            report.line(
                "fail",
                f"{version.label} not applied: {type(error).__name__}: {error}"
                " (the files before it stay applied)",
            )
            return False
        report.line(
            "apply",
            f"{version.label}: {_json(counts)} in {time.monotonic() - started:.2f} s",
        )
    report.line("ok", f"{len(pending)} applied")
    return True


def status(client: Any, versions: Sequence[Version], report: Report) -> bool:
    """Print each folder's files as applied, pending or newer, and the lock."""
    if not check_server(client, report):
        return False
    ledger = read_ledger(client)
    current = plan(versions, ledger)
    recorded = {str(entry["_id"]): entry for entry in ledger}

    for kind in KINDS:
        mine = [version for version in versions if version.kind == kind]
        newer = [entry for entry in current.newer if entry.startswith(f"{kind}/")]
        applied = sum(version.id in recorded for version in mine)
        report.line(
            "info",
            f"{kind}/: {applied} applied, {len(mine) - applied} pending"
            + (f", {len(newer)} newer" if newer else ""),
        )
        for version in mine:
            _report_version(version, recorded.get(version.id), report)
        for entry_id in newer:
            name = recorded[entry_id].get("name")
            report.line("newer", f"{entry_id}_{name} (not in this code)")

    for problem in current.problems:
        report.line("fail", problem)
    lock = client.migrations.lock.find_one({"_id": "lock"})
    report.line("info", f"lock {_holder_text(lock)}" if lock else "lock free")
    return not current.problems


def _report_version(
    version: Version, entry: Optional[Mapping[str, Any]], report: Report
) -> None:
    if entry is None:
        report.line("pending", version.label)
        return
    report.line(
        "applied",
        f"{version.label} at {entry.get('applied_at'):%Y-%m-%d %H:%M:%S}Z"
        f" by {entry.get('app_version')} in {entry.get('seconds')} s,"
        f" counts {_json(entry.get('counts', {}))}",
    )


def verify(client: Any, expected: Structure, report: Report) -> bool:
    """Compare the live database with the expected structure; extras are only listed."""
    live = live_structure(client)
    indexes = 0
    for database, collections in expected.items():
        for collection, wanted in collections.items():
            present = live.get(database, {}).get(collection)
            indexes += _compare(f"{database}.{collection}", present, wanted, report)
        for collection in sorted(set(live.get(database, {})) - set(collections)):
            report.line("extra", f"collection {database}.{collection}")
    for database in sorted(set(live) - set(expected)):
        report.line("extra", f"database {database}")

    if report.failed:
        return False
    total = sum(len(found) for found in expected.values())
    report.line("ok", f"verify: {total} collections and {indexes} indexes as expected")
    return True


def _compare(
    where: str,
    present: Optional[Dict[str, Spec]],
    wanted: Dict[str, Spec],
    report: Report,
) -> int:
    if present is None:
        report.line("fail", f"missing collection {where}")
        return 0

    for name, spec in wanted.items():
        if name not in present:
            report.line(
                "fail", f"missing index {where} {name} {render_index(name, spec)}"
            )
        elif present[name] != spec:
            report.line(
                "fail",
                f"index {where} {name} is {render_index(name, present[name])},"
                f" expected {render_index(name, spec)}",
            )
    for name in sorted(set(present) - set(wanted)):
        report.line("extra", f"index {where} {render_index(name, present[name])}")
    return len(wanted)


def require_recorded(
    recorded: Set[str],
    versions: Optional[Sequence[Version]] = None,
    root: Path = MIGRATIONS,
) -> None:
    """Stop the boot when a file is refused or shares a number, or when `recorded`,
    the ids the ledger holds, lacks a file this code has."""
    problems: List[str] = []
    if versions is None:
        found, problems = read_versions(root)
        versions = found
    problems += _code_problems(versions)
    if problems:
        raise MigrationsPending(
            f"Migration files refused: {'; '.join(problems)}."
            " Fix them before the bot starts"
        )

    pending = [version.label for version in versions if version.id not in recorded]
    if pending:
        raise MigrationsPending(
            f"Migrations not applied: {', '.join(pending)}."
            " Run `make migrate` (production: the release deploy runs them)"
        )
