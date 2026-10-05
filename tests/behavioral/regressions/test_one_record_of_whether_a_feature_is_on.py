"""Whether a feature is on has one record: its saved document.

Broke as: "on" lived in two places that disagreed in production. The
moderations flag (`guild.moderations.<feature>`) was what /setup, its
permissions report and the guild-leave pause read; the feature's document
(`guild.<feature>` and its `enabled`) was what the bot actually ran on. Disable
wrote the flag back on, a behaviour carried over from the old engine, so /setup
kept saying "configured" for a feature that no longer existed. Pause wrote it
off, so /setup listed a paused feature under "to set up". StreamElements
documents saved without a flag never showed as configured. A guild Keiko left
only had its flagged features paused, so documents saved without a flag stayed
on (the v1 review counted two in production). Then, once /setup read the
document, a paused birthday feature said "paused" there while its own command
still decided from the flag and opened a fresh setup; the runtime cache and the
manager panel took a document saved without `enabled` for off while /setup took
it for on; a failed flag write left the cache saying on; an edit of paused
birthdays turned them back on; and a guild leave during a Redis outage waited
for Redis once per feature.

Shared behaviour: every lifecycle commit of the feature document layer
(app/settings/features/feature.py, and the birthday module's own commits), the
one reading (`is_feature_on`, app/services/cogs.py) and the one writer
(`set_feature_enabled`, app/services/moderations.py), the guild leave
(`leave_guild`, `pause_all_moderations_by_guild`), the /setup card and its
permissions report (app/services/setup.py), and the unique guild index
(app/data/indexes.py).

Guaranteed: the document decides (absent is off, `enabled: false` is paused,
`enabled: true` is on, and only a document saved before `enabled` existed asks
the old flag) and every reader asks the same function; every change still
writes the old flag with the same meaning, so v0.9.0 keeps working on the same
data, and drops the cache even when that write fails; a guild Keiko left has
nothing on in either record, for one Redis call when Redis is down; and a
collection that still holds duplicate guilds never keeps the other indexes from
being created.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from pymongo.errors import AutoReconnect, DuplicateKeyError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.constants import Commands
from app.data import indexes
from app.data import moderations as moderations_data
from app.data.birthdays import upsert_birthday_config
from app.services import cache
from app.services.moderations import update_moderations_by_guild
from app.services.setup import permission_report, setup_dashboard
from app.services.utils import ml
from app.settings.features import feature_for
from app.settings.features.feature import CommitContext, OpenContext
from app.settings.form.form_state import Answer
from tests.behavioral.contracts.test_setup_permissions import EVERYTHING
from tests.behavioral.contracts.test_setup_permissions import guild as server
from tests.behavioral.harness.locators import walk_items

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"
LEFT_GUILD_ID = "4242"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
FEATURES = [spec["command_key"] for spec in Commands.SETUP_FEATURES]
BIRTHDAYS = Commands.REMINDERS_BIRTHDAY_KEY

BLOCK_LINKS = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": "100"},
    "answer": "Nada de links!",
}

AUTO_ROLES = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "default_roles": {"style": "role", "values": "201"},
    "default_roles_bot": {"style": "role", "values": ["202"]},
}

BIRTHDAY_SETUP = {
    "channel": Answer("100"),
    "timezone": Answer("America/Sao_Paulo"),
    "notification_time": Answer("08:00"),
    "mention_everyone": Answer(False),
    "default_message_mode": Answer("default"),
    "register_now": Answer(False),
}


def context(document=None):
    return CommitContext(GUILD_ID, "555", "pt-br", document or {}, NOW, "slash", "s1")


def flag(deps, key, guild_id=GUILD_ID):
    moderations = deps.mongo_client.guild.moderations.find_one({"guild_id": guild_id})
    return (moderations or {}).get(key)


def row_of(view, command_key):
    return next(
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Button)
        and getattr(item.accessory, "command_key", None) == command_key
    )


def subtext_of(row):
    return "\n".join(
        child.content
        for child in row.children
        if isinstance(child, discord.ui.TextDisplay)
    )


def saved_before_enabled(deps, flag_value):
    """A block links document from before `enabled` was written, and its old flag."""
    deps.mongo_client.guild[Commands.BLOCK_LINKS_KEY].insert_one(
        {key: value for key, value in BLOCK_LINKS.items() if key != "enabled"}
    )
    update_moderations_by_guild(GUILD_ID, Commands.BLOCK_LINKS_KEY, flag_value)


SET_UP = ml("buttons.setup.start.label", "pt-br")
MANAGE = ml("buttons.setup.manage.label", "pt-br")
PAUSED = ml("commands.commands.setup.embed.paused", "pt-br")


async def test_after_disable_setup_offers_the_feature_again(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager(
        Commands.BLOCK_LINKS_KEY, dict(BLOCK_LINKS)
    )
    await scenario.click(ml("buttons.disable.label", locale="pt-br"))
    await scenario.submit_confirmation()

    row = row_of(await setup_dashboard(GUILD_ID, "pt-br"), Commands.BLOCK_LINKS_KEY)

    assert row.accessory.label == SET_UP, (
        "block links was disabled and /setup still said it was configured"
    )


async def test_after_pause_setup_shows_the_feature_paused_not_to_set_up(
    scenario_factory,
):
    scenario = await scenario_factory(locale="pt-br").start_manager(
        Commands.BLOCK_LINKS_KEY, dict(BLOCK_LINKS)
    )
    await scenario.click(ml("buttons.pause.label", locale="pt-br"))
    await scenario.submit_confirmation()

    row = row_of(await setup_dashboard(GUILD_ID, "pt-br"), Commands.BLOCK_LINKS_KEY)

    assert row.accessory.label == MANAGE, "a paused feature is still set up"
    assert PAUSED in subtext_of(row), "and the card says it is paused"


async def test_a_feature_saved_without_the_old_flag_is_set_up(deps):
    deps.mongo_client.guild[Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY].insert_one(
        {"guild_id": GUILD_ID, "enabled": True, "streamer": "shroud"}
    )

    row = row_of(
        await setup_dashboard(GUILD_ID, "pt-br"),
        Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
    )

    assert row.accessory.label == MANAGE, (
        "StreamElements answers `ks!` commands and /setup offered to set it up"
    )


def test_the_runtime_reads_a_document_saved_before_enabled_through_the_old_flag(
    deps,
):
    saved_before_enabled(deps, True)

    settings = cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY)

    assert settings.get("answer") == BLOCK_LINKS["answer"], (
        "the flag said on and block links stopped answering"
    )


async def test_the_panel_reads_a_document_saved_before_enabled_through_the_old_flag(
    deps,
):
    saved_before_enabled(deps, True)

    opened = await feature_for(Commands.BLOCK_LINKS_KEY).open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )

    assert opened.enabled is True, "the panel offered Unpause for a feature that is on"


async def test_setup_reads_a_document_saved_before_enabled_through_the_old_flag(
    deps,
):
    saved_before_enabled(deps, False)

    row = row_of(await setup_dashboard(GUILD_ID, "pt-br"), Commands.BLOCK_LINKS_KEY)

    assert PAUSED in subtext_of(row), "the flag said paused and /setup said on"


async def test_every_change_still_writes_the_old_flag_for_v0_9_0(deps):
    """v0.9.0 reads the flag for /setup: a rollback must find it true to the document."""
    feature = feature_for(Commands.DEFAULT_ROLES_KEY)
    answers = {"default_roles_bot": Answer(["202"]), "default_roles": Answer("201")}
    key = Commands.DEFAULT_ROLES_KEY

    await feature.commit("setup", {"answers": answers}, context())
    assert flag(deps, key) is True
    await feature.commit("pause", {}, context(AUTO_ROLES))
    assert flag(deps, key) is False
    await feature.commit("unpause", {}, context(AUTO_ROLES))
    assert flag(deps, key) is True
    await feature.commit("disable", {}, context(AUTO_ROLES))
    assert flag(deps, key) is False, "Disable turned the old flag back on"


async def test_a_failed_flag_write_still_drops_the_cached_settings(deps, monkeypatch):
    deps.mongo_client.guild[Commands.BLOCK_LINKS_KEY].insert_one(dict(BLOCK_LINKS))
    cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY)

    def refused(*args, **kwargs):
        raise AutoReconnect("connection closed")

    monkeypatch.setattr(moderations_data, "upsert_moderations_by_guild", refused)

    with pytest.raises(AutoReconnect):
        await feature_for(Commands.BLOCK_LINKS_KEY).commit(
            "pause", {}, context(BLOCK_LINKS)
        )

    assert cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY) == {}, (
        "block links was paused and the cache kept blocking for five minutes"
    )


async def test_disabling_birthdays_turns_both_records_off(deps):
    upsert_birthday_config(
        GUILD_ID, "100", False, timezone="America/Sao_Paulo", notification_time="08:00"
    )
    update_moderations_by_guild(GUILD_ID, BIRTHDAYS, True)

    await feature_for(BIRTHDAYS).commit("disable", {}, context())

    assert flag(deps, BIRTHDAYS) is False, (
        "the birthdays were deleted and the flag said they were still on"
    )
    row = row_of(await setup_dashboard(GUILD_ID, "pt-br"), BIRTHDAYS)
    assert row.accessory.label == SET_UP


async def test_a_paused_birthday_feature_opens_its_panel_with_unpause(
    deps, scenario_factory
):
    birthdays = feature_for(BIRTHDAYS)
    await birthdays.commit("setup", {"answers": BIRTHDAY_SETUP}, context())
    await birthdays.commit("pause", {}, context())

    row = row_of(await setup_dashboard(GUILD_ID, "pt-br"), BIRTHDAYS)
    assert row.accessory.label == MANAGE and PAUSED in subtext_of(row)

    scenario = await scenario_factory(locale="pt-br").start_command(BIRTHDAYS)
    scenario.expect_component(label_or_action="unpause")


async def test_editing_paused_birthdays_keeps_them_paused(deps):
    birthdays = feature_for(BIRTHDAYS)
    await birthdays.commit("setup", {"answers": BIRTHDAY_SETUP}, context())
    await birthdays.commit("pause", {}, context())

    await birthdays.commit(
        "edit", {"answers": {"notification_time": Answer("10:00")}}, context()
    )

    assert flag(deps, BIRTHDAYS) is False, "an edit turned paused birthdays back on"
    config = deps.mongo_client.guild[BIRTHDAYS].find_one({"guild_id": GUILD_ID})
    assert config["enabled"] is False
    assert config["notification_time"] == "10:00"


async def test_a_setup_records_birthdays_as_on_over_a_paused_document(deps):
    birthdays = feature_for(BIRTHDAYS)
    await birthdays.commit("setup", {"answers": BIRTHDAY_SETUP}, context())
    await birthdays.commit("pause", {}, context())

    await birthdays.commit("setup", {"answers": BIRTHDAY_SETUP}, context())

    config = deps.mongo_client.guild[BIRTHDAYS].find_one({"guild_id": GUILD_ID})
    assert config["enabled"] is True, "the document still said paused"
    assert flag(deps, BIRTHDAYS) is True
    row = row_of(await setup_dashboard(GUILD_ID, "pt-br"), BIRTHDAYS)
    assert PAUSED not in subtext_of(row)


async def test_the_permissions_report_checks_the_features_that_are_on(deps):
    deps.mongo_client.guild[Commands.DEFAULT_ROLES_KEY].insert_one(dict(AUTO_ROLES))
    deps.mongo_client.guild[Commands.BLOCK_LINKS_KEY].insert_one(
        {**BLOCK_LINKS, "enabled": False}
    )
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, Commands.WELCOME_MESSAGES_KEY: True}
    )

    embed = await permission_report(
        server({100: EVERYTHING}, {201: 1, 202: 2}), GUILD_ID, "555", "pt-br"
    )

    checked = [field.name for field in embed.fields]
    assert checked == ["👩‍🎓 " + ml("buttons.setup.default-roles.label", "pt-br")], (
        "the report must check what is on: not a flag left behind by Disable, "
        "not a paused feature, and not miss a document saved without a flag"
    )


@pytest.fixture
def events(monkeypatch):
    """The real cog's guild-leave listener, with only the greeting stubbed out."""
    from app.cogs import events as events_module

    monkeypatch.setattr(
        events_module,
        "GreetingsView",
        lambda *args, **kwargs: SimpleNamespace(send=lambda guild: None),
    )
    bot = SimpleNamespace(user=SimpleNamespace(id=99), guilds=[])
    return events_module.Events(bot)


def seed_left_guild(deps):
    """Block links flagged and on, welcome and StreamElements on without a flag,
    a flag left on by an old Disable, and Twitch already paused."""
    deps.mongo_client.guild.moderations.insert_one(
        {
            "guild_id": LEFT_GUILD_ID,
            "is_bot_online": True,
            Commands.BLOCK_LINKS_KEY: True,
            Commands.WELCOME_MESSAGES_KEY: False,
            Commands.DEFAULT_ROLES_KEY: True,
        }
    )
    on = {"guild_id": LEFT_GUILD_ID, "enabled": True}
    for key in (
        Commands.BLOCK_LINKS_KEY,
        Commands.WELCOME_MESSAGES_KEY,
        Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
    ):
        deps.mongo_client.guild[key].insert_one(dict(on))
    deps.mongo_client.guild[Commands.NOTIFICATIONS_TWITCH_KEY].insert_one(
        {**on, "enabled": False}
    )
    return SimpleNamespace(
        id=int(LEFT_GUILD_ID),
        member_count=12,
        owner=SimpleNamespace(id=313, mention="<@313>"),
    )


def assert_nothing_on(deps):
    still_on = [
        key
        for key in FEATURES
        if (
            deps.mongo_client.guild[key].find_one({"guild_id": LEFT_GUILD_ID}) or {}
        ).get("enabled")
    ]
    assert still_on == [], f"features still on in a guild Keiko left: {still_on}"
    moderations = deps.mongo_client.guild.moderations.find_one(
        {"guild_id": LEFT_GUILD_ID}
    )
    assert [key for key, value in moderations.items() if value is True] == []


async def test_a_guild_keiko_left_has_nothing_on(deps, events):
    guild = seed_left_guild(deps)

    await events.on_guild_remove(guild)

    assert_nothing_on(deps)
    paused = sorted(
        key
        for key in FEATURES
        if deps.mongo_client.events[key].find_one(
            {"guild_id": LEFT_GUILD_ID, "event": Commands.PAUSED_KEY}
        )
    )
    assert paused == sorted(
        [
            Commands.BLOCK_LINKS_KEY,
            Commands.WELCOME_MESSAGES_KEY,
            Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
        ]
    ), "the audit trail names what was on, and only that"


async def test_a_guild_keiko_left_while_redis_is_down_costs_one_redis_call(
    deps, events, monkeypatch
):
    guild = seed_left_guild(deps)
    calls = []

    def down(*args, **kwargs):
        calls.append(args)
        raise RedisConnectionError("Error 111 connecting to redis. Connection refused.")

    for method in ("get", "setex", "delete", "keys"):
        monkeypatch.setattr(deps.redis_client, method, down)

    await events.on_guild_remove(guild)

    assert_nothing_on(deps)
    assert len(calls) == 1, f"{len(calls)} Redis timeouts for one guild leaving"


async def test_leaving_a_guild_is_one_call_a_worker_thread_can_run(deps):
    """What `on_guild_remove` hands to `asyncio.to_thread` once it moves off the loop."""
    from app.services.moderations import leave_guild

    seed_left_guild(deps)
    cache.get_cog_data_or_populate(LEFT_GUILD_ID, Commands.BLOCK_LINKS_KEY)

    await asyncio.to_thread(leave_guild, LEFT_GUILD_ID, "99")

    assert_nothing_on(deps)
    assert deps.redis_client.keys(f"guild:{LEFT_GUILD_ID}:*") == []


def test_moderations_and_every_feature_collection_hold_one_document_per_guild():
    unique = {
        collection
        for collection, keys, options in indexes.INDEXES
        if keys == [("guild_id", 1)]
        and options.get("unique")
        and options.get("partialFilterExpression") == {"guild_id": {"$type": "string"}}
    }

    assert unique == {"moderations", *FEATURES}


class _Collection:
    def __init__(self, name, created):
        self.name = name
        self.created = created

    def create_index(self, keys, **options):
        if self.name == "moderations" and options.get("unique"):
            raise DuplicateKeyError(
                "E11000 duplicate key error collection: guild.moderations "
                'index: guild_id_1 dup key: { guild_id: "4242" }'
            )
        self.created.append((self.name, keys, options))


class _Database:
    def __init__(self, created):
        self.created = created

    def __getitem__(self, name):
        return _Collection(name, self.created)


def test_a_collection_with_duplicate_guilds_never_stops_the_other_indexes(
    monkeypatch,
):
    created = []
    monkeypatch.setattr(indexes, "mongo_client", SimpleNamespace(guild=_Database(created)))

    with pytest.raises(Exception, match="guild.moderations"):
        indexes.ensure_indexes()

    expected = [
        (collection, keys, options)
        for collection, keys, options in indexes.INDEXES
        if not (collection == "moderations" and options.get("unique"))
    ]
    assert created == expected, "every other index is still created"


def test_a_flag_for_a_guild_without_moderations_creates_them_with_the_defaults(deps):
    update_moderations_by_guild(GUILD_ID, Commands.BLOCK_LINKS_KEY, True)

    moderations = deps.mongo_client.guild.moderations.find_one({"guild_id": GUILD_ID})
    assert moderations[Commands.BLOCK_LINKS_KEY] is True
    assert moderations["is_bot_online"] is True
    assert moderations[Commands.WELCOME_MESSAGES_KEY] is False
    assert moderations["created_at"] and moderations["updated_at"]
