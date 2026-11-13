"""Every query Keiko sends is served by an index, or is listed with its reason.

Set MONGO_TEST_URL to a throwaway replica set (CI starts one). The suite drops every
database it finds there, so it refuses a server holding data it did not write.

Guaranteed: every public function of `app/data` is driven against the indexes
production builds, or is named in NOT_A_QUERY; every find, aggregate, count, update,
delete and findAndModify it sends is explained one statement at a time, and a winning
plan that reads a whole collection, or a whole index, fails the build unless its shape
is in BY_DESIGN or LEGACY; a listed shape that is no longer sent, or that an index now
serves, fails as stale, naming the PR that was to remove it.
"""

import asyncio
import copy
import functools
import importlib
import inspect
import io
import os
import pkgutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Set, Tuple

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, monitoring

import app.data
from app.constants import Commands, DBConfigs, Migrations
from app.core import analytics_reports
from app.data import (
    analytics,
    birthdays,
    block_links,
    cogs,
    logs,
    notifications_twitch,
    reminder,
    util,
)
from app.data.guild import cogs as guild_cogs
from app.data.guild import settings as guild_settings
from app.data.keiko import integrations as keiko_integrations
from app.data.keiko import settings as keiko_settings
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
EMPTY_REDIS = SimpleNamespace(
    scan_iter=lambda pattern: iter(()), get=lambda key: None, dbsize=lambda: 0
)
NO_TWITCH_SUBSCRIPTIONS = SimpleNamespace(get_subscriptions=lambda: {"data": []})

NOT_A_QUERY = {
    "analytics.build_profile_operation",
    "analytics.insert_events",
    "birthdays.build_default_item",
    "birthdays.to_summary_composition",
    "block_links.insert_blocked_link",
    "cogs.insert_cog_event",
    "cogs.insert_cog_event_async",
    "cogs.insert_error_by_command_async",
    "logs.day_bounds",
    "logs.insert_logs",
    "reminder.insert_renewal_reminder",
    "util.collection_names",
    "util.parse_insert_timestamp",
    "util.parse_update_timestamp",
}

Shape = Tuple[str, Tuple[str, ...]]

BY_DESIGN: Dict[Shape, str] = {
    ("guild.analytics_guild_profile", ()): "find_profiles reads every profile",
    ("guild.logs", ()): "iter_documents streams a whole collection to the backup",
    ("guild.settings", ()): "the /admin overview counts every server Keiko recorded",
}

LEGACY: Dict[Shape, str] = {
    ("reminders.birthdays", ("guild_id",)): "PR 1",
    ("reminders.birthdays", ("guild_id", "user_id")): "PR 1",
    ("reminders.birthdays", ("celebrated_year", "guild_id", "user_id")): "PR 1",
    ("reminders.birthdays", ("date", "guild_id")): "PR 1",
    ("reminders.birthdays", ("date", "reminder_id")): "PR 1",
    ("reminders.birthdays", ("reminder_id",)): "PR 1",
    ("audit.reminders", ("value",)): "PR 1",
    ("audit.reminders", ("reminder_id",)): "PR 1",
    ("audit.reminders", ("title", "value")): "PR 1",
    ("audit.reminders", ("expires_at", "title", "value")): "PR 1",
    ("audit.notifications_twitch", ("streamer",)): "PR 1",
    (
        "notifications.notifications_twitch",
        ("channel_id", "guild_id", "streamer"),
    ): "PR 1",
    **{(f"events.{collection}", ("guild_id",)): "PR 2" for collection in FEATURE_KEYS},
    ("guild.analytics_guild_month", ("month",)): "PR 2",
}


@dataclass(frozen=True)
class Plan:
    """One explained statement: its shape, what was sent and what reads it all."""

    shape: Shape
    statement: Dict[str, Any]
    whole_reads: Tuple[str, ...]


@dataclass(frozen=True)
class Recording:
    """What the driver reached: the data functions it called and every plan."""

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


def data_modules() -> List[Any]:
    """Every module under `app/data`, the database packages' modules included."""
    return [
        importlib.import_module(module.name)
        for module in pkgutil.walk_packages(app.data.__path__, "app.data.")
        if not module.ispkg
    ]


def public_functions() -> Dict[str, Tuple[Any, str]]:
    """Every public function `app/data` defines, by `module.function`."""
    found = {}

    for module in data_modules():
        short = module.__name__.removeprefix("app.data.")
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if function.__module__ == module.__name__ and not name.startswith("_"):
                found[f"{short}.{name}"] = (module, name)

    return found


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


@pytest.fixture(scope="module")
def recording(migrated: MongoClient) -> Recording:
    """Drive every data function through recording clients, then explain each query."""
    recorder = Recorder()
    driven: Set[str] = set()
    url = os.environ["MONGO_TEST_URL"]

    with (
        MongoClient(url, event_listeners=[recorder]) as mongo,
        pytest.MonkeyPatch.context() as patch,
    ):
        for name, (module, attribute) in public_functions().items():
            function = getattr(module, attribute)
            patch.setattr(module, attribute, marking(name, function, driven))
        for module in data_modules():
            if "mongo_client" in vars(module):
                patch.setattr(module, "mongo_client", mongo)
        patch.setattr(admin_service, "redis_client", EMPTY_REDIS)
        asyncio.run(drive_with_motor(url, recorder, patch))

    create_collections_read(migrated, recorder.commands)
    plans = [
        explained(migrated, database, statement)
        for database, command in recorder.commands
        for statement in statements(command)
    ]
    return Recording(driven=driven, plans=plans)


def marking(name: str, function: Any, driven: Set[str]) -> Any:
    """The function, adding its name to `driven` on every call."""

    @functools.wraps(function)
    def call(*args: Any, **kwargs: Any) -> Any:
        driven.add(name)
        return function(*args, **kwargs)

    return call


async def drive_with_motor(
    url: str, recorder: Recorder, patch: pytest.MonkeyPatch
) -> None:
    """Run the driver with a recording motor client in every data module."""
    motor = AsyncIOMotorClient(url, event_listeners=[recorder])
    for module in data_modules():
        if "motor_client" in vars(module):
            patch.setattr(module, "motor_client", motor)

    try:
        await drive()
    finally:
        motor.close()


async def drive() -> None:
    """Call every query of `app/data` and of the services that build their filters."""
    now = datetime.now(timezone.utc)

    drive_keiko()
    await drive_guild_cogs()
    await drive_events()
    drive_guild_settings(now)
    drive_birthdays()
    drive_block_links()
    drive_notifications()
    drive_reminders(now)
    drive_analytics(now)
    drive_analytics_reports(now)
    drive_logs_and_backup(now)
    admin_service._what_keiko_stored(NO_TWITCH_SUBSCRIPTIONS, {GUILD})


def drive_keiko() -> None:
    keiko_settings.find_keiko_settings()
    keiko_settings.update_presence({DBConfigs.KEIKO_STATUS: "online"})
    keiko_settings.update_operator({DBConfigs.ADMIN_GUILD_ID: 10})
    keiko_integrations.find_integration(DBConfigs.INTEGRATION_NOTION)


async def drive_guild_cogs() -> None:
    for cog in FEATURE_KEYS:
        record = {"guild_id": GUILD, "enabled": True}
        guild_cogs.insert_cog_by_guild_id(cog, dict(record))
        guild_cogs.find_cog_by_guild_id(GUILD, cog)
        guild_cogs.find_all_cogs(cog)
        guild_cogs.find_all_cogs(cog, fields=("guild_id", "enabled"))
        guild_cogs.find_enabled_cogs(cog)
        guild_cogs.update_cog_by_guild(GUILD, cog, {"enabled": False})
        guild_cogs.delete_cog_by_guild_id(GUILD, cog)

        await guild_cogs.insert_cog_by_guild_id_async(cog, dict(record))
        await guild_cogs.find_cog_by_guild_id_async(GUILD, cog)
        await guild_cogs.update_cog_by_guild_async(GUILD, cog, {"enabled": False})
        await guild_cogs.set_cog_state_async(GUILD, cog, {"channel_id": CHANNEL})
        await guild_cogs.update_cog_if_unchanged_async(
            GUILD, cog, "enabled", False, True
        )
        await guild_cogs.delete_cog_by_guild_id_async(GUILD, cog)

    guild_cogs.find_cogs_by_guild(GUILD)
    for cog, option in FOLLOWED.items():
        for enabled_only in (False, True):
            guild_cogs.find_cogs_by_option(cog, option, "followed", enabled_only)
            guild_cogs.count_cogs_by_option(cog, option, "followed", enabled_only)


async def drive_events() -> None:
    for collection in FEATURE_KEYS:
        list(cogs.find_cog_events_by_guild_id(GUILD, collection))
        await cogs.find_cog_events_by_guild_id_async(GUILD, collection)


def drive_guild_settings(now: datetime) -> None:
    guild_settings.record_join(GUILD, OWNER, {"language": "en-us"})
    guild_settings.find_settings(GUILD)
    guild_settings.find_guild_ids_with_settings([GUILD, OTHER_GUILD])
    guild_settings.set_option(GUILD, "language", "pt-br")
    guild_settings.count_servers_by_owner(OWNER)
    guild_settings.find_servers_by_owner(OWNER)
    guild_settings.count_absent([GUILD, OTHER_GUILD])
    guild_settings.record_leave(GUILD)
    admin_service.servers_overview(now)


def drive_birthdays() -> None:
    birthdays.upsert_birthday_item(GUILD, MEMBER, DATE)
    birthdays.find_birthday_item(GUILD, MEMBER)
    birthdays.has_birthday_reminder(REMINDER)
    birthdays.find_birthday_items_by_reminder_and_date(REMINDER, DATE)
    birthdays.mark_birthday_celebrated(GUILD, MEMBER, 2026)
    birthdays.find_birthday_items_by_guild(GUILD)
    birthdays.find_reminder_id_by_guild_and_date(GUILD, DATE)
    birthdays.find_birthdays_missing_reminder()
    birthdays.set_reminder_id_for_guild_and_date(GUILD, DATE, REMINDER)
    birthdays.count_birthday_items_by_guild_and_date(GUILD, DATE)
    birthdays.remove_birthday_item(GUILD, MEMBER)
    birthdays.delete_birthday_items_by_guild(GUILD)


def drive_block_links() -> None:
    block_links.count_blocked_links(1, {"example.com": 1}, datetime.now(timezone.utc))
    block_links.find_blocked_links_by_guild(GUILD)
    block_links.find_blocked_links_by_guild(GUILD, user_id=MEMBER, limit=5)
    block_links.count_blocked_links_by_guild(GUILD)
    block_links.delete_blocked_links_by_guild(GUILD)


def drive_notifications() -> None:
    notifications_twitch.update_last_stream_date("streamer", "2026-11-12")
    notifications_twitch.find_last_stream_date("streamer")
    notifications_twitch.save_stream_notification(GUILD, CHANNEL, "streamer", "1")
    notifications_twitch.find_stream_notification(GUILD, CHANNEL, "streamer")


def drive_reminders(now: datetime) -> None:
    reminder.find_reminder_by_value("youtuber")
    reminder.find_reminder_by_id(REMINDER)
    reminder.stamp_hub_confirmation("youtuber", now)
    reminder.mark_lapse_reported("youtuber", now)
    reminder.delete_reminder_by_id(REMINDER)


def drive_analytics(now: datetime) -> None:
    analytics.increment_months({f"{GUILD}|{MONTH}": {"days.12.value": 1}})
    analytics.update_profiles(
        [analytics.build_profile_operation(GUILD, inc={"totals.value_events": 1})]
    )
    analytics.find_profile(GUILD)
    analytics.find_profiles()
    analytics.find_events_by_guild(GUILD, limit=5)
    analytics.find_events_since("value.delivered", now)
    analytics.find_events_since("value.delivered", now, guild_id=GUILD)
    list(analytics.iter_events_between(now - timedelta(days=1), now))
    analytics.find_month_buckets(GUILD)
    analytics.find_month_buckets_by_month(MONTH)
    analytics.find_archived_month(MONTH)
    analytics.mark_month_archived(MONTH, {"archived_at": now})
    analytics.delete_analytics_by_guild(OTHER_GUILD)


def drive_analytics_reports(now: datetime) -> None:
    analytics_reports.funnel(FEATURE)
    analytics_reports._setup_events(None, None, None)
    analytics_reports._setup_events(FEATURE, GUILD, now)
    for feature in (None, FEATURE):
        analytics_reports.step_durations(feature)
        analytics_reports.choice_distribution(feature)
        analytics_reports.friction(feature)
    analytics_reports.session_events("session")


def drive_logs_and_backup(now: datetime) -> None:
    list(logs.iter_logs_for_day(now))
    logs.update_retention({"checked_at": now})
    logs.find_retention()
    list(util.iter_documents("guild", "logs"))


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


def test_every_public_data_function_is_driven(recording: Recording) -> None:
    public = set(public_functions())

    assert public - recording.driven - NOT_A_QUERY == set(), (
        "drive these in tests/test_query_plans.py, or name them in NOT_A_QUERY"
    )
    assert NOT_A_QUERY - public == set(), "NOT_A_QUERY names functions app/data lost"


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
