"""The migrations runner's rules that need no database: order, hashes and the files.

Guaranteed: `schema/` and `data/` each number their files from 0001 without a gap or a
repeat, and every order check runs per folder; ledger ids name the folder; an applied
file whose hash changed stops the run; a pending file below the last applied one of its
folder stops the run; a ledger tail newer than the code (an older image) is allowed
only when contiguous per folder; an index declared twice with two specs is refused;
the helpers write exactly the specs the old baseline held; `begin` commits once with
the ledger entry, aborts on an error or a dry run, and retries only the commit; a data
file that never enters `begin` fails; the committed snapshot is exactly what `schema/`
declares, in the helpers' notation; and a migration file imports nothing from the app
or the tests but the helpers.
"""

import ast
import importlib
import io
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest
from bson.son import SON
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import OperationFailure

from app import config as app_config
from app.constants import Commands, Migrations
from app.migrations import helpers, runner
from app.migrations.__main__ import main
from app.migrations.helpers import index, one_per, ttl

pytestmark = pytest.mark.unit

OLD_BASELINE = {
    "audit": {
        "errors": {},
        "notifications_twitch": {},
        "reminders": {
            "expires_at_1": {"key": [["expires_at", 1]], "expireAfterSeconds": 0},
        },
    },
    "configs": {"admin": {}, "data": {}, "integrations": {}},
    "events": {
        "block_links": {},
        "default_roles": {},
        "notifications_twitch": {},
        "notifications_youtube_video": {},
        "reminders_birthday": {},
        "stream_elements_commands": {},
        "welcome_messages": {},
    },
    "guild": {
        "analytics_archives": {},
        "analytics_events": {
            "ts_1": {"key": [["ts", 1]], "expireAfterSeconds": 7776000},
            "guild_id_1_ts_-1": {"key": [["guild_id", 1], ["ts", -1]]},
            "event_1_ts_-1": {"key": [["event", 1], ["ts", -1]]},
            "event_1_feature_1_ts_-1": {
                "key": [["event", 1], ["feature", 1], ["ts", -1]]
            },
            "session_id_1_ts_1": {"key": [["session_id", 1], ["ts", 1]]},
        },
        "analytics_guild_month": {
            "updated_at_1": {"key": [["updated_at", 1]], "expireAfterSeconds": 34560000},
            "guild_id_1_month_-1": {"key": [["guild_id", 1], ["month", -1]]},
        },
        "analytics_guild_profile": {
            "removed_at_1": {"key": [["removed_at", 1]], "expireAfterSeconds": 31536000},
            "last_value_at_-1": {"key": [["last_value_at", -1]]},
        },
        "block_links": "one per guild",
        "block_links_totals": {},
        "blocked_links": {
            "created_at_1": {"key": [["created_at", 1]], "expireAfterSeconds": 7776000},
            "guild_id_1_created_at_-1": {"key": [["guild_id", 1], ["created_at", -1]]},
            "guild_id_1_user_id_1_created_at_-1": {
                "key": [["guild_id", 1], ["user_id", 1], ["created_at", -1]]
            },
        },
        "default_roles": "one per guild",
        "logs": {
            "ts_1": {"key": [["ts", 1]], "expireAfterSeconds": 2592000},
            "level_1_ts_-1": {"key": [["level", 1], ["ts", -1]]},
            "guild_id_1_ts_-1": {"key": [["guild_id", 1], ["ts", -1]]},
            "session_id_1_ts_1": {"key": [["session_id", 1], ["ts", 1]]},
        },
        "logs_files": {},
        "moderations": "one per guild",
        "notifications_twitch": "one per guild",
        "notifications_youtube_video": "one per guild",
        "reminders_birthday": "one per guild",
        "stream_elements_commands": "one per guild",
        "welcome_messages": "one per guild",
    },
    "notifications": {"notifications_twitch": {}},
    "reminders": {"birthdays": {}},
}
ONE_PER_GUILD = {
    "guild_id_1": {
        "key": [["guild_id", 1]],
        "unique": True,
        "partialFilterExpression": {"guild_id": {"$type": "string"}},
    }
}


def old_baseline():
    return {
        database: {
            collection: {
                name: runner.normal(spec)
                for name, spec in (
                    ONE_PER_GUILD if indexes == "one per guild" else indexes
                ).items()
            }
            for collection, indexes in collections.items()
        }
        for database, collections in OLD_BASELINE.items()
    }


def version(number, name="step", sha="a" * 64, structure=None, kind="schema"):
    return runner.Version(kind, f"{number:04d}", name, sha, structure or {})


def data_version(number, name="step", sha="a" * 64, up=None, check=None):
    return runner.Version("data", f"{number:04d}", name, sha, up=up, check=check)


def entry(number, name="step", sha="a" * 64, kind="schema"):
    return {"_id": f"{kind}/{number:04d}", "name": name, "sha256": sha}


def write_files(root, kind, *names, content="STRUCTURE = {}\n"):
    folder = root / kind
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_text(content)


def test_pending_files_come_schema_first_then_data_each_in_number_order(tmp_path):
    write_files(tmp_path, "schema", "0002_second.py", "0001_first.py")
    write_files(
        tmp_path,
        "data",
        "0002_fill.py",
        "0001_seed.py",
        content="def up(db):\n    pass\n",
    )

    versions, problems = runner.read_versions(tmp_path)
    current = runner.plan(versions, [entry(1, "first", versions[0].sha256)])

    assert problems == []
    assert [found.label for found in current.pending] == [
        "schema/0002_second",
        "data/0001_seed",
        "data/0002_fill",
    ]
    assert current.applied == ("schema/0001",) and current.problems == ()


def test_each_folder_numbers_its_files_from_0001():
    current = runner.plan(
        [version(1), version(2), data_version(1), data_version(2)], []
    )

    assert current.problems == ()
    assert [found.id for found in current.pending] == [
        "schema/0001",
        "schema/0002",
        "data/0001",
        "data/0002",
    ]


def test_the_ledger_names_each_file_by_its_folder():
    schema = version(1, "baseline")
    data = data_version(1, "bot_defaults")

    assert schema.identity() == {
        "_id": "schema/0001",
        "kind": "schema",
        "number": 1,
        "name": "baseline",
        "sha256": "a" * 64,
    }
    assert data.identity()["_id"] == "data/0001"
    assert data.label == "data/0001_bot_defaults"


def test_a_file_outside_the_name_pattern_is_refused(tmp_path):
    write_files(tmp_path, "schema", "0001_first.py", "1_second.py")

    versions, problems = runner.read_versions(tmp_path)

    assert [found.label for found in versions] == ["schema/0001_first"]
    assert problems == ["schema/1_second.py is not named NNNN_slug.py"]


def test_a_schema_file_with_a_data_step_and_a_data_file_without_one_are_refused(
    tmp_path,
):
    write_files(
        tmp_path,
        "schema",
        "0001_mixed.py",
        content="STRUCTURE = {}\n\n\ndef up(db):\n    pass\n",
    )
    write_files(tmp_path, "data", "0001_empty.py", content="STRUCTURE = {}\n")

    _versions, problems = runner.read_versions(tmp_path)

    assert problems == [
        "schema/0001_mixed declares STRUCTURE only, no up or check",
        "data/0001_empty defines up(db) and no STRUCTURE",
    ]


def test_a_gap_in_one_folder_stops_the_run():
    current = runner.plan([version(1), data_version(1), data_version(3)], [])

    assert current.problems == (
        "data files must be numbered from 0001 without a gap,"
        " found data/0001, data/0003",
    )


def test_ids_start_at_0001_in_each_folder():
    assert runner.plan([version(1), data_version(2)], []).problems == (
        "data files must be numbered from 0001 without a gap, found data/0002",
    )


def test_two_files_with_one_number_in_one_folder_stop_the_run():
    current = runner.plan([version(1), version(2, "a"), version(2, "b")], [])

    assert current.problems == (
        "schema/0002_a and schema/0002_b share the id schema/0002",
    )


def test_an_applied_file_whose_hash_changed_stops_the_run():
    current = runner.plan([version(1, sha="b" * 64)], [entry(1, sha="a" * 64)])

    [problem] = current.problems
    assert problem.startswith("schema/0001_step changed after it was applied")
    assert "aaaaaaaaaaaa" in problem and "bbbbbbbbbbbb" in problem
    assert "write a new one" in problem


def test_an_applied_id_under_another_name_stops_the_run():
    current = runner.plan([version(1, "baseline")], [entry(1, "other")])

    assert current.problems == (
        "the ledger's schema/0001 is schema/0001_other, the file is"
        " schema/0001_baseline",
    )


def test_a_contiguous_ledger_tail_per_folder_is_an_older_image():
    current = runner.plan(
        [version(1), data_version(1)],
        [entry(1), entry(2), entry(1, kind="data"), entry(2, kind="data")],
    )

    assert current.problems == ()
    assert current.pending == ()
    assert current.newer == ("schema/0002", "data/0002")


def test_a_ledger_tail_with_a_gap_stops_the_run():
    current = runner.plan(
        [version(1), data_version(1)],
        [entry(1), entry(1, kind="data"), entry(3, kind="data")],
    )

    assert current.problems == (
        "the ledger holds data/0003 beyond this code's last data file data/0001:"
        " only a contiguous tail (an older image) is allowed",
    )


def test_a_pending_file_below_the_last_applied_one_of_its_folder_stops_the_run():
    current = runner.plan(
        [version(1), data_version(1), data_version(2), data_version(3)],
        [entry(1), entry(1, kind="data"), entry(3, kind="data")],
    )

    assert [found.label for found in current.pending] == ["data/0002_step"]
    assert current.problems == (
        "data/0002_step pending below data/0003, the last applied: files were"
        " merged out of order, renumber them after it",
    )


def test_a_schema_number_never_counts_as_applied_for_data():
    current = runner.plan([version(1), data_version(1)], [entry(1)])

    assert [found.id for found in current.pending] == ["data/0001"]
    assert current.problems == ()


def test_a_ledger_id_that_names_no_folder_stops_the_run():
    current = runner.plan([version(1)], [entry(1), {"_id": "0001"}])

    assert (
        "the ledger holds '0001', not a schema/NNNN or data/NNNN id" in current.problems
    )


@pytest.mark.parametrize(
    "declared_spec, old_spec",
    [
        (index("guild_id", "-ts"), {"key": [["guild_id", 1], ["ts", -1]]}),
        (index("-last_value_at"), {"key": [["last_value_at", -1]]}),
        (ttl("ts", days=30), {"key": [["ts", 1]], "expireAfterSeconds": 2592000}),
        (ttl("expires_at"), {"key": [["expires_at", 1]], "expireAfterSeconds": 0}),
        (ttl("ts", seconds=90), {"key": [["ts", 1]], "expireAfterSeconds": 90}),
        (one_per("guild_id"), ONE_PER_GUILD["guild_id_1"]),
        (
            index("a", "b", unique=True, partial={"a": {"$exists": True}}),
            {
                "key": [["a", 1], ["b", 1]],
                "unique": True,
                "partialFilterExpression": {"a": {"$exists": True}},
            },
        ),
    ],
)
def test_each_helper_writes_the_spec_the_old_baseline_wrote(declared_spec, old_spec):
    assert runner.normal(declared_spec) == runner.normal(old_spec)


def test_an_index_takes_the_name_mongo_would_give_it_unless_it_names_one():
    assert runner.named([index("guild_id", "-ts")])[0][0] == "guild_id_1_ts_-1"
    assert runner.named([index("guild_id", name="by_guild")]) == [
        ("by_guild", {"key": [["guild_id", 1]]})
    ]


class _Collection:
    def __init__(self, *ids):
        self.documents = [{"_id": found} for found in ids]

    def find(self, query, projection):
        wanted = query["_id"]["$in"]
        return [document for document in self.documents if document["_id"] in wanted]

    def insert_many(self, documents):
        self.documents += documents


def test_insert_missing_writes_only_the_ids_the_collection_lacks_and_counts_them():
    collection = _Collection("1001:welcome_messages")

    inserted = helpers.insert_missing(
        collection,
        [
            {"_id": "1001:welcome_messages", "enabled": False},
            {"_id": "1001:default_roles", "enabled": True},
        ],
    )

    assert inserted == 1
    assert collection.documents == [
        {"_id": "1001:welcome_messages"},
        {"_id": "1001:default_roles", "enabled": True},
    ]
    assert helpers.insert_missing(collection, [{"_id": "1001:default_roles"}]) == 0


def test_the_baseline_declares_exactly_what_the_old_baseline_declared():
    baseline = [found for found in runner.read_folder("schema")[0] if found.number == "0001"]

    assert runner._sorted(runner.declared(baseline)[0]) == runner._sorted(old_baseline())


def test_an_index_as_mongo_lists_it_equals_the_one_declared():
    listed = SON(
        [
            ("v", 2),
            ("key", SON([("guild_id", 1.0), ("ts", -1)])),
            ("name", "guild_id_1_ts_-1"),
            ("ns", "guild.logs"),
            ("background", True),
            ("unique", False),
            ("expireAfterSeconds", 60.0),
        ]
    )

    assert runner.normal(listed) == runner.normal(
        {"key": [["guild_id", 1], ["ts", -1]], "expireAfterSeconds": 60}
    )


@pytest.mark.parametrize(
    "declared_spec, text",
    [
        (index("guild_id", "-ts"), "index(guild_id, -ts)"),
        (ttl("ts", days=30), "ttl(ts, days=30)"),
        (ttl("expires_at"), "ttl(expires_at)"),
        (ttl("ts", seconds=90), "ttl(ts, seconds=90)"),
        (one_per("guild_id"), "one_per(guild_id)"),
        (index("guild_id", unique=True), "index(guild_id, unique=True)"),
        (
            index("a", partial={"a": {"$exists": True}}),
            'index(a, partial={"a": {"$exists": true}})',
        ),
        (index("a", name="by_a"), 'index(a, name="by_a")'),
        (
            {"key": [["about", "text"]]},
            '{"key": [["about", "text"]], "name": "about_text"}',
        ),
        (
            {"key": [["a", 1]], "sparse": True},
            '{"key": [["a", 1]], "name": "a_1", "sparse": true}',
        ),
    ],
)
def test_the_snapshot_renders_an_index_the_way_the_helpers_write_it(
    declared_spec, text
):
    [(name, spec)] = runner.named([declared_spec])

    assert runner.render_index(name, spec) == text


def test_a_live_index_renders_as_the_same_text_as_its_declaration():
    declared = runner.expected_structure(runner.read_versions()[0])
    listed = {
        database: {
            collection: {
                name: runner.normal(
                    SON(
                        [
                            ("v", 2),
                            ("key", SON([(field, float(way)) for field, way in spec["key"]])),
                            ("name", name),
                            *[(option, value) for option, value in spec.items() if option != "key"],
                        ]
                    )
                )
                for name, spec in indexes.items()
            }
            for collection, indexes in collections.items()
        }
        for database, collections in declared.items()
    }

    assert runner.render(listed) == runner.render(declared)


def test_a_later_file_cannot_redeclare_an_index_with_another_spec():
    first = version(1, structure={"guild": {"logs": [index("ts")]}})
    second = version(2, structure={"guild": {"logs": [ttl("ts", seconds=9)]}})

    _merged, problems = runner.declared([first, second])

    assert len(problems) == 1
    assert "schema/0002_step declares guild.logs ts_1" in problems[0]


def test_an_index_with_the_same_name_and_another_spec_is_a_conflict_naming_both():
    wanted = version(1, structure={"guild": {"logs": [ttl("ts", seconds=9)]}})
    live = {"guild": {"logs": {"ts_1": {"key": [["ts", 1]], "expireAfterSeconds": 5}}}}

    changes, problems = runner.structure_changes(live, [wanted])

    assert changes == {"schema/0001_step": []}
    assert problems == [
        'index guild.logs ts_1 exists as {"expireAfterSeconds": 5, "key": [["ts", 1]]},'
        ' this code declares {"expireAfterSeconds": 9, "key": [["ts", 1]]}'
    ]


def test_an_index_covering_the_same_keys_under_another_name_is_a_conflict():
    wanted = version(1, structure={"guild": {"logs": [index("ts")]}})
    live = {"guild": {"logs": {"by_ts": {"key": [["ts", 1]]}}}}

    _changes, problems = runner.structure_changes(live, [wanted])

    assert len(problems) == 1 and "by_ts" in problems[0] and "ts_1" in problems[0]


def test_each_missing_piece_is_created_once_by_the_first_file_declaring_it():
    first = version(1, structure={"guild": {"logs": [index("ts")]}})
    second = version(2, structure={"guild": {"logs": [index("ts"), index("a")]}})

    changes, problems = runner.structure_changes({"guild": {}}, [first, second])

    assert problems == []
    assert [change.describe() for change in changes["schema/0001_step"]] == [
        "collection guild.logs",
        "index guild.logs index(ts)",
    ]
    assert [change.describe() for change in changes["schema/0002_step"]] == [
        "index guild.logs index(a)"
    ]


def test_the_committed_snapshot_is_what_the_schema_files_declare():
    versions, problems = runner.read_versions()
    _merged, conflicts = runner.declared(versions)

    assert problems == [] and conflicts == []
    assert runner.SNAPSHOT.read_text() == runner.render(
        runner.expected_structure(versions)
    ), "run: python -m app.migrations snapshot --declared > app/migrations/snapshot.json"


def test_the_snapshot_holds_one_line_per_collection_with_the_ledger_included():
    snapshot = json.loads(runner.SNAPSHOT.read_text())

    assert snapshot["guild.logs"] == [
        "index(guild_id, -ts)",
        "index(level, -ts)",
        "index(session_id, ts)",
        "ttl(ts, days=30)",
    ]
    assert snapshot["guild.moderations"] == ["one_per(guild_id)"]
    assert snapshot["audit.reminders"] == ["ttl(expires_at)"]
    assert snapshot["migrations.lock"] == ["ttl(expires_at)"]
    assert snapshot["migrations.changelog"] == []
    assert list(snapshot) == sorted(snapshot)


def test_the_shipped_files_form_a_clean_plan_from_an_empty_ledger():
    versions, _problems = runner.read_versions()
    current = runner.plan(versions, [])

    assert current.problems == ()
    assert [found.label for found in current.pending] == [
        "schema/0001_baseline",
        "schema/0002_guild_and_keiko",
        "schema/0003_members_and_schedules",
        "schema/0004_subscriptions_and_messages",
        "schema/0005_events",
        "data/0001_bot_defaults",
        "data/0002_server_settings",
        "data/0003_cogs_and_keiko",
        "data/0004_members_and_schedules",
        "data/0005_subscriptions_and_messages",
        "data/0006_events",
    ]


def _imports(path):
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))
    return modules


def _internal(module):
    return module.startswith(".") or module.split(".")[0] in {"app", "tests"}


def test_a_migration_file_imports_nothing_from_the_app_but_the_helpers():
    paths = [
        path
        for kind in runner.KINDS
        for path in (runner.MIGRATIONS / kind).glob("0*.py")
    ]

    versions, _problems = runner.read_versions()

    assert len(paths) == len(versions)
    for path in paths:
        internal = {module for module in _imports(path) if _internal(module)}
        assert internal <= {"app.migrations.helpers"}, path.name


def test_the_import_rule_allows_the_standard_library_and_refuses_the_app():
    assert not _internal("hashlib")
    assert not _internal("zoneinfo")
    assert _internal("app.constants")
    assert _internal("tests.conftest")
    assert _internal("..helpers")


def test_the_baseline_keeps_the_retention_the_constants_name():
    guild = runner.declared_structure()["guild"]

    assert guild["blocked_links"]["created_at_1"]["expireAfterSeconds"] == (
        Commands.BLOCK_LINKS_EVENTS_TTL_SECONDS
    )
    assert guild["analytics_events"]["ts_1"]["expireAfterSeconds"] == (
        Commands.ANALYTICS_EVENTS_TTL_SECONDS
    )
    assert guild["analytics_guild_month"]["updated_at_1"]["expireAfterSeconds"] == (
        Commands.ANALYTICS_MONTHLY_TTL_SECONDS
    )
    assert guild["analytics_guild_profile"]["removed_at_1"]["expireAfterSeconds"] == (
        Commands.ANALYTICS_PROFILE_TTL_SECONDS
    )
    assert (
        guild["logs"]["ts_1"]["expireAfterSeconds"] == Commands.DEBUG_LOGS_TTL_SECONDS
    )


def test_the_baseline_configs_hold_no_secret():
    bot_defaults = importlib.import_module("app.migrations.data.0001_bot_defaults")
    defaults = json.dumps([bot_defaults.BOT_DEFAULTS, bot_defaults.NOTION_DEFAULTS])

    for word in ("token", "secret", "password", "key", "url"):
        assert word not in defaults.lower(), word


def test_no_discord_id_is_written_in_the_migrations():
    for path in sorted(runner.MIGRATIONS.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json"}:
            found = re.findall(r"(?<!\d)\d{17,20}(?!\d)", path.read_text())

            assert found == [], path.name


class _Ledger:
    def __init__(self, ids):
        self.ids = ids

    def find(self, query, projection):
        return [{"_id": found} for found in self.ids]


def _ledger_client(*ids):
    return SimpleNamespace(migrations=SimpleNamespace(changelog=_Ledger(list(ids))))


def test_the_boot_check_names_every_file_the_ledger_lacks_in_both_folders():
    client = _ledger_client("schema/0001")

    with pytest.raises(runner.MigrationsPending) as raised:
        runner.require_applied(
            client, [version(1), version(2, "indexes"), data_version(1, "seed")]
        )

    assert "schema/0002_indexes, data/0001_seed" in str(raised.value)
    assert "schema/0001" not in str(raised.value)
    assert "make migrate" in str(raised.value)


def test_the_boot_check_accepts_a_ledger_newer_than_the_code_in_each_folder():
    client = _ledger_client("schema/0001", "schema/0002", "data/0001", "data/0002")

    runner.require_applied(client, [version(1), data_version(1)])


def test_the_boot_check_refuses_a_misnamed_file(tmp_path):
    write_files(tmp_path, "schema", "0001_first.py", "2_second.py")

    with pytest.raises(runner.MigrationsPending) as raised:
        runner.require_applied(_ledger_client("schema/0001"), root=tmp_path)

    assert "schema/2_second.py is not named NNNN_slug.py" in str(raised.value)


def test_the_boot_check_refuses_two_files_with_one_number():
    client = _ledger_client("data/0001", "data/0002")

    with pytest.raises(runner.MigrationsPending) as raised:
        runner.require_applied(
            client, [data_version(1), data_version(2, "a"), data_version(2, "b")]
        )

    assert "data/0002_a and data/0002_b share the id data/0002" in str(raised.value)


class _Session:
    def __init__(self, log, commit_failures=()):
        self.log = log
        self.commit_failures = list(commit_failures)

    def start_transaction(self, **_options):
        self.log.append("start")

    def commit_transaction(self):
        self.log.append("commit")
        if self.commit_failures:
            raise self.commit_failures.pop(0)

    def abort_transaction(self):
        self.log.append("abort")

    def end_session(self):
        self.log.append("end")


@pytest.fixture
def mongo(monkeypatch):
    """A real client that never connects: its inserts and its sessions are logged."""

    def start(commit_failures=()):
        client = MongoClient("mongodb://127.0.0.1:1", connect=False)
        log = []
        session = _Session(log, commit_failures)
        client.start_session = lambda: session

        def insert_one(collection, document, session=None):
            passed = "with the session" if session is not None else "without"
            log.append(f"{collection.full_name} {document['_id']} {passed}")

        monkeypatch.setattr(Collection, "insert_one", insert_one)
        client.log = log
        return client

    return start


def _run(client, dry_run=False, budget=Migrations.TRANSACTION_BUDGET_SECONDS):
    return helpers.DataRun(
        client, data_version(1, "seed").identity(), dry_run, budget
    )


def _write(run):
    with helpers.begin(run) as tx:
        tx.configs.data.insert_one({"_id": "bot"})
        tx.count("bots", True)
        tx.count("bots", 2)


def test_begin_commits_the_writes_and_the_ledger_entry_in_one_transaction(mongo):
    client = mongo()
    run = _run(client)

    _write(run)

    assert client.log == [
        "start",
        "configs.data bot with the session",
        "migrations.changelog data/0001 with the session",
        "commit",
        "end",
    ]
    assert run.counts == {"bots": 3} and run.entered == 1


def test_begin_aborts_and_reraises_when_the_block_fails(mongo):
    client = mongo()

    with pytest.raises(RuntimeError, match="broke"):
        with helpers.begin(_run(client)) as tx:
            tx.configs.data.insert_one({"_id": "bot"})
            raise RuntimeError("broke")

    assert client.log == ["start", "configs.data bot with the session", "abort", "end"]


def test_a_dry_run_aborts_after_the_ledger_entry_and_keeps_the_counts(mongo):
    client = mongo()
    run = _run(client, dry_run=True)

    _write(run)

    assert client.log == [
        "start",
        "configs.data bot with the session",
        "migrations.changelog data/0001 with the session",
        "abort",
        "end",
    ]
    assert run.counts == {"bots": 3}


def test_a_block_over_the_budget_aborts_before_the_ledger_entry(mongo):
    client = mongo()

    with pytest.raises(helpers.BudgetExceeded, match="data/0001"):
        _write(_run(client, budget=-1.0))

    assert "migrations.changelog data/0001 with the session" not in client.log
    assert client.log[-2:] == ["abort", "end"]


def _commit_error(label):
    error = OperationFailure("commit lost")
    error._add_error_label(label)
    return error


def test_a_collection_reached_through_options_still_writes_in_the_transaction(mongo):
    client = mongo()

    with helpers.begin(_run(client)) as tx:
        tx.configs.get_collection("data").insert_one({"_id": "named"})
        tx.configs.data.with_options(write_concern=None).insert_one({"_id": "tuned"})

    assert "configs.data named with the session" in client.log
    assert "configs.data tuned with the session" in client.log


def test_only_a_commit_whose_result_is_unknown_is_sent_again(mongo):
    unknown = _commit_error("UnknownTransactionCommitResult")
    client = mongo(commit_failures=[unknown, unknown])

    _write(_run(client))

    assert client.log.count("commit") == 3
    assert client.log.count("configs.data bot with the session") == 1, "writes run once"


def test_a_commit_that_failed_for_another_reason_is_not_sent_again(mongo):
    client = mongo(commit_failures=[_commit_error("TransientTransactionError")])

    with pytest.raises(OperationFailure):
        _write(_run(client))

    assert client.log.count("commit") == 1


def test_a_data_file_that_enters_begin_twice_fails_and_writes_nothing(mongo):
    client = mongo()

    def twice(db):
        with helpers.begin(db):
            with helpers.begin(db):
                pass

    with pytest.raises(helpers.TransactionMisuse, match="data/0001 entered begin"):
        runner.apply_data(client, data_version(1, "twice", up=twice))

    assert client.log == ["start", "abort", "end"]


def test_a_data_file_that_never_enters_begin_fails(mongo):
    client = mongo()
    lazy = data_version(1, "lazy", up=lambda db: None)

    with pytest.raises(helpers.TransactionMisuse, match="data/0001_lazy returned"):
        runner.apply_data(client, lazy)

    assert client.log == []


def test_a_dry_run_rehearses_each_data_file_and_reports_its_counts(mongo):
    client = mongo()
    seed = data_version(1, "seed", up=_write)
    current = runner.plan([seed], [])
    out = io.StringIO()

    assert runner.rehearse(client, current, {}, runner.Report(out))
    assert 'dry     data/0001_seed would write {"bots": 3}' in out.getvalue()
    assert "commit" not in client.log


def test_the_runner_takes_the_url_from_the_environment_without_reading_dotenv(
    monkeypatch,
):
    monkeypatch.delenv("APPLICATION_ENVIRONMENT", raising=False)
    monkeypatch.setenv("MONGO_URL", "mongodb://throwaway:27018")
    monkeypatch.setattr(
        app_config, "load_dotenv", lambda *args, **kwargs: pytest.fail(".env read")
    )

    assert app_config.mongo_url() == "mongodb://throwaway:27018"


def test_the_runner_reads_only_the_mongo_parameter_in_production(monkeypatch):
    asked = []

    class Ssm:
        def get_parameter(self, Name, WithDecryption):
            asked.append((Name, WithDecryption))
            return {"Parameter": {"Value": "mongodb+srv://production"}}

    monkeypatch.setenv("APPLICATION_ENVIRONMENT", "prod")
    monkeypatch.setattr(app_config.boto3, "client", lambda service, region_name: Ssm())

    assert app_config.mongo_url() == "mongodb+srv://production"
    assert asked == [("/keiko/mongo/url", True)]


def test_the_cli_prints_the_declared_structure_without_mongo():
    out = io.StringIO()

    assert main(["snapshot", "--declared"], out=out) == 0
    assert out.getvalue() == runner.SNAPSHOT.read_text()


def test_the_cli_refuses_an_unknown_command():
    with pytest.raises(SystemExit) as raised:
        main(["rollback"], out=io.StringIO())

    assert raised.value.code == 2


SUBSCRIPTIONS = importlib.import_module(
    "app.migrations.data.0005_subscriptions_and_messages"
)


def test_the_hub_state_of_a_youtuber_keeps_the_newest_stamp_of_its_rows():
    hub_states = SUBSCRIPTIONS._hub_states
    early, late = datetime(2026, 10, 1), datetime(2026, 10, 5)

    states = dict(
        hub_states([
            {"value": "pewdiepie", "hub_confirmed_at": early, "lapse_reported_at": None},
            {"value": "pewdiepie", "hub_confirmed_at": late},
            {"value": "mrbeast", "lapse_reported_at": late},
        ])
    )

    assert states == {
        "pewdiepie": {"hub_confirmed_at": late},
        "mrbeast": {"lapse_reported_at": late},
    }


def test_a_lapse_report_older_than_the_hub_confirmation_is_not_copied():
    hub_states = SUBSCRIPTIONS._hub_states
    early, late = datetime(2026, 10, 1), datetime(2026, 10, 5)

    states = dict(
        hub_states([
            {"value": "pewdiepie", "hub_confirmed_at": late, "lapse_reported_at": early},
        ])
    )

    assert states == {"pewdiepie": {"hub_confirmed_at": late}}


def test_the_new_homes_are_read_by_id_and_need_no_index():
    structure = runner.declared_structure()

    assert structure["keiko"]["subscriptions"] == {}
    assert structure["guild"]["messages"] == {}
