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
for Redis once per feature. The database PR 1 retired the flag: the migration
gave every document saved before `enabled` its old flag once (data/0003), and
nothing reads or writes the flag since.

Shared behaviour: every lifecycle commit of the feature document layer
(app/settings/features/feature.py, and the birthday module's own commits), the
one reading (`is_feature_on`, app/core/cogs.py), the flag writers of
app/core/servers.py (`set_feature_enabled` for pause, unpause and the
first save), the guild leave (`leave_guild`, `pause_features`), the /setup card
and its permissions report (app/services/setup.py), and guild.cogs keyed
`{guild}:{cog}` (app/data/guild/cogs.py).

Guaranteed: the document decides (absent is off, `enabled: false` is paused,
`enabled: true` is on, and a document without `enabled` is off) and every reader
asks the same function; a change writes only the document and a failed write leaves
the cache agreeing with it; a guild Keiko left has nothing on, for one Redis call
when Redis is down; and a server holds one document per cog by its `_id`.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest
from pymongo.errors import AutoReconnect
from redis.exceptions import ConnectionError as RedisConnectionError

from app.data import data, wait
from app.constants import Commands
from app.core import cache
from app.core.servers import set_feature_enabled
from app.core.utils import ml
from app.services.reminders_birthdays import save_birthday_config
from app.services.setup import category_page, permission_report
from app.settings.features import feature_for
from app.settings.features.feature import CommitContext, OpenContext
from app.settings.form.form_state import Answer
from tests.behavioral.contracts.test_setup_dashboard import EVERYTHING
from tests.behavioral.contracts.test_setup_dashboard import guild as server
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import recorded_server, seed_cog

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"
UNRECORDED_GUILD_ID = "987654321"
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
}


def context(document=None):
    return CommitContext(GUILD_ID, "555", "pt-br", document or {}, NOW, "slash", "s1")


async def setup_row(command_key):
    """The row of `command_key` on the /setup page that lists it, its category's."""
    spec = next(
        feature for feature in Commands.SETUP_FEATURES if feature["command_key"] == command_key
    )
    page = await category_page(GUILD_ID, spec["category"])
    return row_of(page, command_key)


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


def saved_without_enabled(deps):
    """A block links document with no `enabled`, which the migration never leaves."""
    document = {key: value for key, value in BLOCK_LINKS.items() if key != "enabled"}
    seed_cog(deps.mongo_client, Commands.BLOCK_LINKS_KEY, document)


SET_UP = ml("buttons.setup.start.label", "pt-br")
MANAGE = ml("buttons.setup.manage.label", "pt-br")
PAUSED = " ⏸️**"


async def test_after_disable_setup_offers_the_feature_again(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager(
        Commands.BLOCK_LINKS_KEY, dict(BLOCK_LINKS)
    )
    await scenario.click(ml("buttons.disable.label", locale="pt-br"))
    await scenario.submit_confirmation()

    row = await setup_row(Commands.BLOCK_LINKS_KEY)

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

    row = await setup_row(Commands.BLOCK_LINKS_KEY)

    assert row.accessory.label == MANAGE, "a paused feature is still set up"
    assert PAUSED in subtext_of(row), "and the card says it is paused"


async def test_a_feature_saved_without_the_old_flag_is_set_up(deps):
    seed_cog(deps.mongo_client, Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY, {"guild_id": GUILD_ID, "enabled": True, "streamer": "shroud"})

    row = await setup_row(Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY)

    assert row.accessory.label == MANAGE, (
        "StreamElements answers `ks!` commands and /setup offered to set it up"
    )


def test_the_runtime_reads_a_document_without_enabled_as_off(deps):
    saved_without_enabled(deps)

    settings = cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY)

    assert settings == {}, "a document that never said on blocked links"


async def test_the_panel_reads_a_document_without_enabled_as_paused(deps):
    saved_without_enabled(deps)

    opened = await feature_for(Commands.BLOCK_LINKS_KEY).open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )

    assert opened.panel.enabled is False, "the panel offered Pause for a feature that is off"


async def test_setup_reads_a_document_without_enabled_as_paused(deps):
    saved_without_enabled(deps)

    row = await setup_row(Commands.BLOCK_LINKS_KEY)

    assert PAUSED in subtext_of(row), "the document said nothing and /setup said on"


async def test_every_change_writes_only_the_document(deps):
    feature = feature_for(Commands.DEFAULT_ROLES_KEY)
    answers = {"default_roles_bot": Answer(["202"]), "default_roles": Answer("201")}
    key = Commands.DEFAULT_ROLES_KEY

    await feature.commit("setup", {"answers": answers}, context())
    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, key))["enabled"] is True
    await feature.commit("pause", {}, context(AUTO_ROLES))
    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, key))["enabled"] is False
    await feature.commit("unpause", {}, context(AUTO_ROLES))
    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, key))["enabled"] is True
    await feature.commit("disable", {}, context(AUTO_ROLES))
    assert await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, key) is None
    assert deps.mongo_client.guild.moderations.count_documents({}) == 0, (
        "a change wrote the retired moderations flag"
    )


async def test_a_failed_pause_leaves_the_cache_agreeing_with_the_document(
    deps, monkeypatch
):

    seed_cog(deps.mongo_client, Commands.BLOCK_LINKS_KEY, dict(BLOCK_LINKS))
    cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY)

    async def refused(*args, **kwargs):
        raise AutoReconnect("connection closed")

    monkeypatch.setattr(deps.data.guild_cogs, "update_cog_by_guild", refused)

    with pytest.raises(AutoReconnect):
        await feature_for(Commands.BLOCK_LINKS_KEY).commit(
            "pause", {}, context(BLOCK_LINKS)
        )

    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, Commands.BLOCK_LINKS_KEY))["enabled"] is True
    assert cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY)["answer"], (
        "the pause was not saved and the cache said block links was off"
    )


async def test_disabling_birthdays_leaves_nothing_on(deps):
    await save_birthday_config(
        GUILD_ID, "100", False, notification_time="08:00"
    )
    await set_feature_enabled(GUILD_ID, BIRTHDAYS, True)

    await feature_for(BIRTHDAYS).commit("disable", {}, context())

    assert await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, BIRTHDAYS) is None
    row = await setup_row(BIRTHDAYS)
    assert row.accessory.label == SET_UP


async def test_a_paused_birthday_feature_opens_its_panel_with_unpause(
    deps, scenario_factory
):
    birthdays = feature_for(BIRTHDAYS)
    await birthdays.commit("setup", {"answers": BIRTHDAY_SETUP}, context())
    await birthdays.commit("pause", {}, context())

    row = await setup_row(BIRTHDAYS)
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

    config = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, BIRTHDAYS)
    assert config["enabled"] is False, "an edit turned paused birthdays back on"
    assert config["notification_time"] == "10:00"


async def test_a_setup_records_birthdays_as_on_over_a_paused_document(deps):
    birthdays = feature_for(BIRTHDAYS)
    await birthdays.commit("setup", {"answers": BIRTHDAY_SETUP}, context())
    await birthdays.commit("pause", {}, context())

    await birthdays.commit("setup", {"answers": BIRTHDAY_SETUP}, context())

    config = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, BIRTHDAYS)
    assert config["enabled"] is True, "the document still said paused"
    row = await setup_row(BIRTHDAYS)
    assert PAUSED not in subtext_of(row)


async def test_the_permissions_report_checks_the_features_that_are_on(deps):
    seed_cog(deps.mongo_client, Commands.DEFAULT_ROLES_KEY, dict(AUTO_ROLES))
    seed_cog(deps.mongo_client, Commands.BLOCK_LINKS_KEY, {**BLOCK_LINKS, "enabled": False})

    embed = await permission_report(
        server({100: EVERYTHING}, {201: 1, 202: 2}), GUILD_ID, "555", "pt-br"
    )

    checked = [field.name for field in embed.fields]
    assert checked == ["👩‍🎓 " + ml("buttons.setup.default-roles.label", "pt-br")], (
        "the report must check what is on, and not a paused feature"
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
    """Block links, welcome and StreamElements on, and Twitch already paused."""
    recorded_server(deps.mongo_client, LEFT_GUILD_ID)
    on = {"guild_id": LEFT_GUILD_ID, "enabled": True}
    for key in (
        Commands.BLOCK_LINKS_KEY,
        Commands.WELCOME_MESSAGES_KEY,
        Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
    ):
        seed_cog(deps.mongo_client, key, dict(on))
    seed_cog(deps.mongo_client, Commands.NOTIFICATIONS_TWITCH_KEY, {**on, "enabled": False})
    return SimpleNamespace(
        id=int(LEFT_GUILD_ID),
        member_count=12,
        owner=None, owner_id=313,
    )


async def assert_nothing_on(deps):
    still_on = [
        key
        for key in FEATURES
        if (
            await data().guild_cogs.find_cog_by_guild_id(LEFT_GUILD_ID, key) or {}
        ).get("enabled")
    ]
    assert still_on == [], f"features still on in a guild Keiko left: {still_on}"
    server = await data().guild_settings.find_settings(LEFT_GUILD_ID)
    assert server["present"] is False and server["left_at"] is not None


async def test_a_guild_keiko_left_has_nothing_on(deps, events):
    guild = seed_left_guild(deps)

    await events.on_guild_remove(guild)

    await assert_nothing_on(deps)
    paused = sorted(
        key
        for key in FEATURES
        if deps.mongo_client.events.changes.find_one(
            {
                "guild_id": LEFT_GUILD_ID,
                "cog": key,
                "type": "feature.paused",
                "actor.kind": "bot",
            }
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

    await assert_nothing_on(deps)
    assert len(calls) == 1, f"{len(calls)} Redis timeouts for one guild leaving"


async def test_leaving_a_guild_is_one_awaited_call(deps):
    """What `on_guild_remove` awaits for the guild Keiko left."""
    from app.core.servers import leave_guild

    seed_left_guild(deps)
    cache.get_cog_data_or_populate(LEFT_GUILD_ID, Commands.BLOCK_LINKS_KEY)

    await leave_guild(LEFT_GUILD_ID, "99")

    await assert_nothing_on(deps)
    assert deps.redis_client.keys(f"cache:cog:{LEFT_GUILD_ID}:*") == []


def test_a_server_holds_one_document_per_cog_by_its_id(deps):
    for _attempt in range(2):
        wait(data().guild_cogs.insert_cog_by_guild_id(Commands.BLOCK_LINKS_KEY, dict(BLOCK_LINKS)))

    saved = list(deps.mongo_client.guild.cogs.find({"guild_id": GUILD_ID}))
    assert [document["_id"] for document in saved] == [f"{GUILD_ID}:block_links"]


def test_turning_on_a_feature_a_server_never_saved_creates_nothing(deps):
    wait(set_feature_enabled(UNRECORDED_GUILD_ID, Commands.BLOCK_LINKS_KEY, True))

    assert wait(data().guild_cogs.find_cog_by_guild_id(UNRECORDED_GUILD_ID, Commands.BLOCK_LINKS_KEY)) is None
    assert deps.mongo_client.guild.moderations.count_documents({}) == 0
