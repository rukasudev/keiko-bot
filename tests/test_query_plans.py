"""Every query Keiko sends is served by an index, or is listed with its reason.

Set MONGO_TEST_URL to a throwaway replica set (CI starts one). The suite drops every
database it finds there, so it refuses a server holding data it did not write.

Guaranteed: every public method of every repository `app.data.build` makes is driven
against the indexes production builds, or is named in NOT_A_QUERY; every find,
aggregate, count, update, delete and findAndModify it sends is explained one statement
at a time, and a winning plan that reads a whole collection, or a whole index, fails the
build unless its shape is in BY_DESIGN or LEGACY; a listed shape that is no longer sent,
or that an index now serves, fails as stale, naming the PR that was to remove it.
"""

import asyncio
import copy
import functools
import inspect
import io
import os
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Set, Tuple

import pytest
from pymongo import AsyncMongoClient, MongoClient, monitoring

import app.data as data_layer
from app.constants import Commands, DBConfigs, Migrations
from app.core import analytics_reports
from app.data import Data, build, data
from app.data.analytics.counts import count_id
from app.data.analytics.lifetime import admin_operation, lifetime_operation
from app.data.keiko.schedules import schedule_id
from app.migrations import runner
from app.services import admin as admin_service

MARKER = "keiko_query_plans_test"
THROWAWAY_MARKERS = {MARKER, "keiko_migrations_test"}

pytestmark = [
    pytest.mark.mongo,
    pytest.mark.skipif(
        not os.getenv("MONGO_TEST_URL"),
        reason="MONGO_TEST_URL names no throwaway Mongo",
    ),
]

FEATURE_KEYS = [spec["command_key"] for spec in Commands.SETUP_FEATURES]
FOLLOWED = {
    Commands.NOTIFICATIONS_TWITCH_KEY: "notifications.values.streamer.value",
    Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: "notifications.values.youtuber.value",
}

GUILD = "111"
OTHER_GUILD = "333"
MEMBER = "222"
OWNER = "555"
CHANNEL = "999"
REMINDER = "444"
DATE = "03-14"
MONTH = "2026-11"
FEATURE = "welcome_messages"

EXPLAINED = {"find", "aggregate", "count", "update", "delete", "findAndModify"}
STATEMENT_LISTS = {"update": "updates", "delete": "deletes"}
FILTER_FIELDS = {"find": "filter", "count": "query", "findAndModify": "query"}
SESSION_FIELDS = {
    "lsid",
    "txnNumber",
    "writeConcern",
    "readConcern",
    "autocommit",
    "startTransaction",
}
WHOLE_RANGES = {"[MinKey, MaxKey]", "[MaxKey, MinKey]"}

NOT_A_QUERY = {
    "events_activity.insert_many",
    "events_changes.insert",
    "events_interactions.insert_many",
    "guild_logs.insert_logs",
    "databases.collection_names",
}

Shape = Tuple[str, Tuple[str, ...]]

BY_DESIGN: Dict[Shape, str] = {
    ("analytics.lifetime", ()): "find_lifetimes reads every server's lifetime",
    ("migrations.changelog", ()): "the boot reads the whole ledger of applied files",
    ("guild.logs", ()): "iter_documents streams a whole collection to the backup",
    ("guild.settings", ()): "the /admin overview counts every server Keiko recorded",
}

LEGACY: Dict[Shape, str] = {
    **{
        (f"events.{collection}", ("guild_id",)): (
            "the schema file that drops the audits of before PR 2"
        )
        for collection in FEATURE_KEYS
    },
}


@dataclass(frozen=True)
class Plan:
    """One explained statement: its shape, what was sent and what reads it all."""

    shape: Shape
    statement: Dict[str, Any]
    whole_reads: Tuple[str, ...]


@dataclass(frozen=True)
class Recording:
    """What the driver reached: the repository methods it called and every plan."""

    driven: Set[str]
    plans: List[Plan]


class Recorder(monitoring.CommandListener):
    """Keeps every command that selects documents, exactly as the driver sent it."""

    def __init__(self) -> None:
        self.commands: List[Tuple[str, Dict[str, Any]]] = []

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        """Record a command the explain step reads."""
        if event.command_name in EXPLAINED:
            self.commands.append((event.database_name, copy.deepcopy(event.command)))

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        """Nothing to keep from an answer."""

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        """Nothing to keep from a failure."""


def repositories(store: Data) -> Dict[str, Any]:
    """Every repository of `store`, by its name in `Data`."""
    return {
        field.name: getattr(store, field.name)
        for field in fields(Data)
        if field.name != "client"
    }


def public_methods(store: Data) -> Dict[str, Tuple[Any, str]]:
    """Every public method of every repository, by `repository.method`."""
    return {
        f"{name}.{method}": (repository, method)
        for name, repository in repositories(store).items()
        for method, _function in inspect.getmembers(type(repository), inspect.isfunction)
        if not method.startswith("_")
    }


@pytest.fixture(scope="module")
def client() -> Iterator[MongoClient]:
    """The throwaway server, emptied of everything but the test markers."""
    server = MongoClient(os.environ["MONGO_TEST_URL"], serverSelectionTimeoutMS=5000)
    names = set(server.list_database_names()) - Migrations.SYSTEM_DATABASES
    if names and not names & THROWAWAY_MARKERS:
        pytest.exit(
            f"MONGO_TEST_URL holds {sorted(names)} and no {MARKER}: "
            "not a throwaway server",
            returncode=3,
        )

    server[MARKER].owner.replace_one(
        {"_id": "owner"}, {"_id": "owner", "suite": __name__}, upsert=True
    )

    for name in names - THROWAWAY_MARKERS:
        server.drop_database(name)

    yield server
    server.close()


@pytest.fixture(scope="module")
def migrated(client: MongoClient) -> MongoClient:
    """The server after every migration, as production builds it."""
    versions, problems = runner.read_versions()
    output = io.StringIO()
    assert not problems and runner.migrate(client, versions, runner.Report(output)), (
        output.getvalue()
    )
    return client


@dataclass(frozen=True)
class Driven:
    """The repositories over the recording client, and the methods called so far."""

    store: Data
    names: Set[str]


@pytest.fixture(scope="module")
def recording(migrated: MongoClient) -> Recording:
    """Drive every repository method through a recording client, then explain each
    query."""
    recorder = Recorder()
    url = os.environ["MONGO_TEST_URL"]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(data_layer, "_INSTALLED", data_layer._INSTALLED)
        patch.setattr("app.core.cache.count_keys", lambda: 0)
        driven = asyncio.run(drive_recorded(url, recorder, patch))

    create_collections_read(migrated, recorder.commands)
    plans = [
        explained(migrated, database, statement)
        for database, command in recorder.commands
        for statement in statements(command)
    ]
    return Recording(driven=driven.names, plans=plans)


async def drive_recorded(
    url: str, recorder: Recorder, patch: pytest.MonkeyPatch
) -> Driven:
    """Install the repositories over a recording client on this loop, and drive them."""
    store = build(AsyncMongoClient(url, event_listeners=[recorder]))
    driven = Driven(store=store, names=set())
    for name, (repository, method) in public_methods(store).items():
        patch.setattr(repository, method, marking(name, getattr(repository, method), driven))
    data_layer.install(store)

    try:
        await drive()
    finally:
        await store.close()
    return driven


def marking(name: str, method: Any, driven: Driven) -> Any:
    """The method, adding its name to the driven names on every call."""
    if inspect.isasyncgenfunction(method):

        @functools.wraps(method)
        async def streamed(*args: Any, **kwargs: Any) -> Any:
            driven.names.add(name)
            async for document in method(*args, **kwargs):
                yield document

        return streamed

    @functools.wraps(method)
    async def call(*args: Any, **kwargs: Any) -> Any:
        driven.names.add(name)
        return await method(*args, **kwargs)

    return call


async def read_all(stream: Any) -> List[Any]:
    return [document async for document in stream]


async def drive() -> None:
    """Call every repository method, and the services that build their own filters."""
    now = datetime.now(timezone.utc)

    await drive_keiko()
    await drive_guild_cogs()
    await drive_events(now)
    await drive_guild_settings(now)
    await drive_members()
    await drive_schedules(now)
    await drive_subscriptions_and_messages(now)
    await drive_analytics(now)
    await drive_analytics_reports(now)
    await drive_logs_and_backup(now)
    await data().migrations_changelog.applied_ids()
    await admin_service._what_keiko_stored({GUILD})


async def drive_keiko() -> None:
    settings = data().keiko_settings
    await settings.find_keiko_settings()
    await settings.update_presence({DBConfigs.KEIKO_STATUS: "online"})
    await settings.update_operator({DBConfigs.ADMIN_GUILD_ID: 10})
    await data().keiko_integrations.find_integration(DBConfigs.INTEGRATION_NOTION)


async def drive_guild_cogs() -> None:
    cogs = data().guild_cogs
    for cog in FEATURE_KEYS:
        record = {"guild_id": GUILD, "enabled": True}
        await cogs.insert_cog_by_guild_id(cog, dict(record))
        await cogs.find_cog_by_guild_id(GUILD, cog)
        await cogs.find_all_cogs(cog)
        await cogs.find_all_cogs(cog, fields=("guild_id", "enabled"))
        await cogs.find_enabled_cogs(cog)
        await cogs.find_enabled_guild_ids(cog)
        await cogs.update_cog_by_guild(GUILD, cog, {"enabled": False})
        await cogs.set_cog_state(GUILD, cog, {"channel_id": CHANNEL})
        await cogs.update_cog_if_unchanged(GUILD, cog, "enabled", False, True)
        await cogs.delete_cog_by_guild_id(GUILD, cog)

    await cogs.find_cogs_by_guild(GUILD)
    for cog, option in FOLLOWED.items():
        for enabled_only in (False, True):
            await cogs.find_cogs_by_option(cog, option, "followed", enabled_only)
            await cogs.count_cogs_by_option(cog, option, "followed", enabled_only)


async def drive_events(now: datetime) -> None:
    since = now - timedelta(days=1)
    lifecycle = ["feature.enabled", "feature.unpaused"]
    blocked = ["link.blocked"]
    setup = ["feature.setup_opened", "setup.completed"]
    changes = data().events_changes
    activity = data().events_activity
    interactions = data().events_interactions

    await changes.find_by_guild(GUILD)
    await changes.find_by_guild(GUILD, types=lifecycle, limit=5)
    await changes.find_by_guild(GUILD, FEATURE, lifecycle, since, 1)
    await changes.delete_by_guild(OTHER_GUILD)
    await changes.delete_by_guild(OTHER_GUILD, FEATURE)

    await activity.find_by_guild(GUILD)
    await activity.find_by_guild(GUILD, FEATURE, blocked, since, 5)
    await activity.find_by_guild(GUILD, FEATURE, blocked, limit=5, actor_id=MEMBER)
    await activity.count_by_field(GUILD, FEATURE, "props.host", blocked, since, 3)
    await activity.count_by_field(GUILD, FEATURE, "actor.id", blocked)
    await read_all(activity.iter_between(since, now))
    await activity.delete_by_guild(OTHER_GUILD)
    await activity.delete_by_guild(OTHER_GUILD, FEATURE)

    await interactions.find_by_guild(GUILD)
    await interactions.find_by_guild(GUILD, FEATURE, setup, since, 5)
    await interactions.find_by_type(setup)
    await interactions.find_by_type(setup, FEATURE, since, 5)
    await interactions.find_by_session("session", 5)
    await read_all(interactions.iter_between(since, now))
    await interactions.delete_by_guild(OTHER_GUILD)


async def drive_guild_settings(now: datetime) -> None:
    settings = data().guild_settings
    await settings.record_join(GUILD, OWNER, {"language": "en-us"})
    await settings.find_settings(GUILD)
    await settings.find_guild_ids_with_settings([GUILD, OTHER_GUILD])
    await settings.set_option(GUILD, "language", "pt-br")
    await settings.count_servers_by_owner(OWNER)
    await settings.find_servers_by_owner(OWNER)
    await settings.count_absent([GUILD, OTHER_GUILD])
    await settings.record_leave(GUILD)
    await admin_service.servers_overview(now)


async def drive_members() -> None:
    cog, field = "reminders_birthday", "last_celebrated_year"
    members = data().guild_members
    await members.set_member_option(GUILD, MEMBER, cog, {"date": DATE}, display_name="Ana")
    await members.find_member_option(GUILD, MEMBER, cog)
    await members.find_members_by_option(GUILD, cog)
    await members.find_members_by_option(GUILD, cog, {"date": DATE})
    await members.count_members_by_option(GUILD, cog, {"date": DATE})
    await members.claim_member_state(GUILD, MEMBER, cog, field, 2026, "Ana")
    await members.restore_member_state(GUILD, MEMBER, cog, field, 2026, None)
    await members.unset_member_option(GUILD, MEMBER, cog)
    await members.unset_option_in_guild(GUILD, cog)


async def drive_schedules(now: datetime) -> None:
    schedules = data().keiko_schedules
    birthday = schedule_id("birthday", GUILD, DATE)
    await schedules.upsert_schedule(birthday, "birthday", REMINDER, due_at=now)
    await schedules.find_schedule(birthday)
    await schedules.find_schedule_by_reminder(REMINDER)
    await schedules.find_schedules_by_kind("youtube_renewal")
    await schedules.find_schedules_by_reminder(REMINDER)
    await schedules.find_schedules_by_reminder(REMINDER, "birthday")
    await schedules.find_schedules_missing_reminder("birthday", 50)
    await schedules.set_schedule_due(birthday, now)
    await schedules.extend_schedule_expiry(birthday, now)
    await schedules.delete_schedule_without_reminder(birthday)
    await schedules.delete_schedule(birthday)


async def drive_subscriptions_and_messages(now: datetime) -> None:
    subscriptions = data().keiko_subscriptions
    messages = data().guild_messages
    await subscriptions.update_subscription_state(
        "twitch", "streamer", {"last_live_started_at": now}
    )
    await subscriptions.subscription_state("twitch", "streamer")
    await subscriptions.count_subscriptions("twitch")
    await messages.save_message(GUILD, CHANNEL, "notifications_twitch", "streamer", "1")
    await messages.find_message(GUILD, CHANNEL, "notifications_twitch", "streamer")


async def drive_analytics(now: datetime) -> None:
    store = data()
    await store.analytics_counts.increment({count_id(GUILD, MONTH): {"days.12.value": 1}}, now)
    await store.analytics_counts.find_by_guild(GUILD)
    await store.analytics_counts.find_by_guild(GUILD, MONTH)
    await store.analytics_counts.find_by_month(MONTH)
    await store.analytics_lifetime.update([
        lifetime_operation(GUILD, inc={"totals.value_events": 1}),
        admin_operation(GUILD, MEMBER, 25),
    ])
    await store.analytics_lifetime.find_lifetime(GUILD)
    await store.analytics_lifetime.find_lifetimes()
    await store.analytics_lifetime.find_quiet(now)
    day = f"day:{MONTH}"
    await store.analytics_keiko.increment({day: {"days.12.value": 1}}, now, {day: {"month": MONTH}})
    await store.analytics_keiko.find_counters([f"day:{MONTH}", f"commands:{MONTH}"])
    await store.traces_runs.insert_runs([{"trace_id": "abc123", "ts": now, "guild_id": GUILD}])
    await store.guild_analytics_archives.find_archived_month(MONTH)
    await store.guild_analytics_archives.mark_month_archived(MONTH, {"archived_at": now})
    await admin_service.forget_guild(OTHER_GUILD)


async def drive_analytics_reports(now: datetime) -> None:
    await analytics_reports.funnel(FEATURE)
    await analytics_reports.guilds_at_risk()
    await analytics_reports.command_usage(now - timedelta(days=30))
    await analytics_reports._setup_events(None, None, None)
    await analytics_reports._setup_events(FEATURE, GUILD, now)
    for feature in (None, FEATURE):
        await analytics_reports.step_durations(feature)
        await analytics_reports.choice_distribution(feature)
        await analytics_reports.friction(feature)
    await analytics_reports.session_events("session")
    await analytics_reports.guild_timeline(GUILD)


async def drive_logs_and_backup(now: datetime) -> None:
    await read_all(data().guild_logs.iter_logs_for_day(now))
    await data().guild_logs_files.update_retention({"checked_at": now})
    await data().guild_logs_files.find_retention()
    await read_all(data().databases.iter_documents("guild", "logs"))


def create_collections_read(
    client: MongoClient, commands: List[Tuple[str, Dict[str, Any]]]
) -> None:
    """Create each collection a command named, so no explain answers EOF."""
    for database, command in commands:
        collection = command[next(iter(command))]
        if collection not in client[database].list_collection_names():
            client[database].create_collection(collection)


def statements(command: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The command as one explainable command per statement, with no session fields."""
    name = next(iter(command))
    bare = {
        key: value
        for key, value in command.items()
        if key not in SESSION_FIELDS and not key.startswith("$")
    }
    listed = STATEMENT_LISTS.get(name)
    if listed is None:
        return [bare]
    return [{**bare, listed: [statement]} for statement in bare[listed]]


def filter_keys(statement: Dict[str, Any]) -> Tuple[str, ...]:
    """The sorted top-level fields the statement selects documents by."""
    name = next(iter(statement))
    if name == "aggregate":
        first = statement["pipeline"][0] if statement["pipeline"] else {}
        selection = first.get("$match") or {}
    elif name in STATEMENT_LISTS:
        selection = statement[STATEMENT_LISTS[name]][0]["q"]
    else:
        selection = statement.get(FILTER_FIELDS[name]) or {}
    return tuple(sorted(selection))


def explained(client: MongoClient, database: str, statement: Dict[str, Any]) -> Plan:
    """The statement's winning plan, reduced to the stages that read everything."""
    answer = client[database].command(
        {"explain": statement, "verbosity": "queryPlanner"}
    )
    namespace = f"{database}.{statement[next(iter(statement))]}"
    return Plan(
        shape=(namespace, filter_keys(statement)),
        statement=statement,
        whole_reads=tuple(whole_reads(answer)),
    )


def whole_reads(node: Any, winning: bool = False) -> Iterator[str]:
    """Every winning-plan stage that reads a whole collection or a whole index."""
    if isinstance(node, list):
        for item in node:
            yield from whole_reads(item, winning)
        return
    if not isinstance(node, dict):
        return

    stage = node.get("stage")
    if winning and stage == "COLLSCAN":
        yield "COLLSCAN"
    if winning and stage == "IXSCAN" and _covers_whole_index(node):
        yield f"IXSCAN over all of {node.get('indexName')}"

    for key, value in node.items():
        if key != "rejectedPlans":
            yield from whole_reads(value, winning or key == "winningPlan")


def _covers_whole_index(stage: Dict[str, Any]) -> bool:
    bounds = (stage.get("indexBounds") or {}).values()
    return bool(bounds) and all(set(ranges) <= WHOLE_RANGES for ranges in bounds)


def listed_as(shape: Shape) -> str:
    """Why a shape may read everything: by design, or the PR that removes it."""
    if shape in BY_DESIGN:
        return f"by design: {BY_DESIGN[shape]}"
    return f"legacy, removed in {LEGACY[shape]}"


def test_every_repository_method_is_driven(recording: Recording) -> None:
    public = set(public_methods(build(_Unreached())))

    assert public - recording.driven - NOT_A_QUERY == set(), (
        "drive these in tests/test_query_plans.py, or name them in NOT_A_QUERY"
    )
    assert NOT_A_QUERY - public == set(), "NOT_A_QUERY names methods app/data lost"


class _Unreached:
    """A client the repositories are built over only to be listed."""

    def __getitem__(self, name: str) -> "_Unreached":
        return self


def test_no_query_reads_a_whole_collection_unless_listed(recording: Recording) -> None:
    unlisted = {
        plan.shape: f"{', '.join(plan.whole_reads)} for {plan.statement}"
        for plan in recording.plans
        if plan.whole_reads and plan.shape not in BY_DESIGN | LEGACY
    }

    assert unlisted == {}, "add an index for each shape, or list it with its reason"


def test_every_listed_shape_is_still_sent_and_still_reads_everything(
    recording: Recording,
) -> None:
    sent = {plan.shape for plan in recording.plans}
    reading_everything = {plan.shape for plan in recording.plans if plan.whole_reads}

    stale = {
        shape: (
            f"no longer sent ({listed_as(shape)})"
            if shape not in sent
            else f"an index serves it now ({listed_as(shape)})"
        )
        for shape in BY_DESIGN | LEGACY
        if shape not in reading_everything
    }

    assert stale == {}, "remove these shapes from BY_DESIGN or LEGACY"


def test_the_allowlists_do_not_overlap() -> None:
    assert set(BY_DESIGN) & set(LEGACY) == set()
