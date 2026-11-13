"""Every server Keiko is in has its settings: language, time zone and prefix.

Shared behaviour: the `settings` sub-document of `guild.moderations`, written by
`join_guild` (`on_guild_join`, and `Events.on_ready` for a server joined while the
bot was offline) with `guess_server_settings`, read by `server_settings` and its
async readers through the cog cache, and saved one key at a time by
`save_server_setting` (`app/core/moderations.py`); the prefix memo of
`app/core/cache.py`; the `{context:server_*}` tokens the adapter fills.
Consumers: the public messages (PR15 package B), the settings screen of
/configurar (package C), `bot.command_prefix`, `Events.on_message`, the prefix hint
and the StreamElements form.

Guaranteed: only a server Keiko records for the first time gets the guess, and a
returning one keeps what its admin saved; nothing on a read guesses, a server
without settings raises `ServerSettingsMissing`; a save refuses a value its key
cannot hold and drops the cached record and the prefix memo; leaving a server
never touches its settings.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.cogs import events as events_module
from app.constants import Commands, DBConfigs
from app.exceptions import InvalidServerSetting, ServerSettingsMissing
from app.core import cache
from app.core import moderations as moderations_service
from app.settings.discord.callbacks import RUNTIME
from tests.behavioral.harness.driver import FormScenario
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("server_settings")]

SAVED = {
    "language": "en-us",
    "timezone": "Europe/Berlin",
    "prefix": "!!",
}


def guild_double(guild_id=777, owner_id=313, locale="pt-BR"):
    return SimpleNamespace(
        id=guild_id,
        owner_id=owner_id,
        member_count=120,
        owner=None,
        preferred_locale=locale,
    )


@pytest.fixture
def events(deps, monkeypatch):
    monkeypatch.setattr(
        events_module, "GreetingsView",
        lambda *args, **kwargs: SimpleNamespace(send=AsyncMock()),
    )
    deps.bot.config.PREFIX = "ke!"
    deps.bot.synced = True
    deps.bot.guilds = []
    return events_module.Events(deps.bot)


def settings_of(deps, guild_id):
    document = deps.mongo_client.guild.moderations.find_one({"guild_id": str(guild_id)})
    return (document or {}).get("settings")


@pytest.mark.parametrize(
    "locale, language, timezone",
    [
        (discord.Locale.brazil_portuguese, "pt-br", "America/Sao_Paulo"),
        (discord.Locale.british_english, "en-us", "Europe/London"),
        (discord.Locale.american_english, "en-us", "UTC"),
        (discord.Locale.spain_spanish, "en-us", "UTC"),
    ],
)
async def test_a_new_server_gets_the_guess_once(deps, events, locale, language, timezone):
    await events.on_guild_join(guild_double(locale=locale))

    assert settings_of(deps, 777) == {
        "language": language,
        "timezone": timezone,
        "prefix": "ke!",
    }
    documents = list(deps.mongo_client.guild.moderations.find({"guild_id": "777"}))
    assert len(documents) == 1


async def test_a_returning_server_keeps_the_settings_its_admin_saved(deps, events):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": "777", "is_bot_online": False, "settings": dict(SAVED)}
    )

    await events.on_guild_join(guild_double(locale=discord.Locale.brazil_portuguese))

    document = deps.mongo_client.guild.moderations.find_one({"guild_id": "777"})
    assert document["settings"] == SAVED
    assert document["is_bot_online"] is True


async def test_a_server_joined_while_the_bot_was_offline_is_recorded_on_ready(
    deps, events, monkeypatch, caplog
):
    """Discord fires no `on_guild_join` for a server added while Keiko was down."""
    moderations = deps.mongo_client.guild.moderations
    moderations.insert_one({"guild_id": "1", "is_bot_online": True, "settings": dict(SAVED)})
    moderations.insert_one({"guild_id": "2", "is_bot_online": True})
    deps.bot.guilds = [
        guild_double(guild_id=1),
        guild_double(guild_id=2, locale=discord.Locale.british_english),
        guild_double(guild_id=3),
    ]
    queries = []
    find = moderations.find
    monkeypatch.setattr(
        moderations, "find",
        lambda *args, **kwargs: (queries.append(args), find(*args, **kwargs))[1],
    )

    with caplog.at_level(logging.INFO):
        await events.on_ready()

    assert settings_of(deps, 1) == SAVED, "a server with settings keeps them"
    assert settings_of(deps, 2)["timezone"] == "Europe/London"
    assert settings_of(deps, 3) == {
        "language": "pt-br",
        "timezone": "America/Sao_Paulo",
        "prefix": "ke!",
    }
    assert moderations.find_one({"guild_id": "3"})["owner_id"] == "313"
    asked = [query for query in queries if "settings" in query[0]]
    assert len(asked) == 1, "one query finds the servers that already have settings"
    assert "📌 Prefix: ke!" in caplog.text, "the ready line prints the default prefix"


async def test_leaving_a_server_flips_nothing_inside_its_settings(deps):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": "777", "is_bot_online": True, "block_links": True,
         "settings": dict(SAVED)}
    )

    moderations_service.leave_guild("777", "99")

    document = deps.mongo_client.guild.moderations.find_one({"guild_id": "777"})
    assert document["settings"] == SAVED
    assert document["block_links"] is False and document["is_bot_online"] is False


@pytest.mark.parametrize(
    "reader", ["server_language", "server_timezone", "server_prefix"]
)
@pytest.mark.parametrize(
    "document",
    [
        None,
        {"guild_id": "777", "is_bot_online": True},
        {
            "guild_id": "777",
            "settings": {"language": "pt-br", "timezone": "UTC", "mention": "everyone"},
        },
    ],
    ids=["none", "bare", "partial"],
)
async def test_a_server_without_settings_raises_instead_of_guessing(deps, reader, document):
    if document:
        deps.mongo_client.guild.moderations.insert_one(document)

    with pytest.raises(ServerSettingsMissing, match="guild 777"):
        await getattr(moderations_service, reader)("777")
    with pytest.raises(ServerSettingsMissing, match="guild 777"):
        moderations_service.server_settings("777")


async def test_the_readers_return_what_the_server_saved(deps):
    recorded_server(deps.mongo_client, "777", **SAVED)

    assert moderations_service.server_settings("777") == SAVED
    assert await moderations_service.server_language("777") == "en-us"
    assert await moderations_service.server_timezone("777") == "Europe/Berlin"
    assert await moderations_service.server_prefix("777") == "!!"


def test_the_prefixes_a_server_may_pick_are_one_closed_list_in_order():
    """Lucas (PR15 round 2, e14): the prefix is picked from a list, not typed."""
    assert Commands.SERVER_PREFIXES == (
        "k!", "ke!", "ke?", "ke$", "kb!", "kb?", "kb$", "k?", "k$",
    )


@pytest.mark.parametrize(
    "key, value",
    [
        ("language", "pt-br"),
        ("language", "en-us"),
        ("timezone", "America/Sao_Paulo"),
        ("timezone", "UTC"),
        *(
            ("prefix", prefix)
            for prefix in ("k!", "ke!", "ke?", "ke$", "kb!", "kb?", "kb$", "k?", "k$")
        ),
    ],
)
async def test_a_value_its_key_holds_is_saved(deps, key, value):
    recorded_server(deps.mongo_client, "777", **SAVED)

    moderations_service.save_server_setting("777", key, value)

    assert settings_of(deps, 777) == {**SAVED, key: value}


@pytest.mark.parametrize(
    "key, value",
    [
        ("language", "es-es"),
        ("language", "PT-BR"),
        ("language", ""),
        ("timezone", "Mars/Olympus"),
        ("timezone", "america/sao_paulo"),
        ("timezone", "../etc/passwd"),
        ("timezone", ""),
        ("mention", "everyone"),
        ("mention", "here"),
        ("prefix", ""),
        ("prefix", "!"),
        ("prefix", "ks!"),
        ("prefix", "abcde"),
        ("prefix", "K!"),
        ("prefix", "k!!"),
        ("prefix", " k!"),
        ("prefix", 5),
        ("colour", "blue"),
    ],
)
async def test_a_value_its_key_cannot_hold_is_refused_and_nothing_is_saved(deps, key, value):
    recorded_server(deps.mongo_client, "777", **SAVED)

    with pytest.raises(InvalidServerSetting) as refused:
        moderations_service.save_server_setting("777", key, value)

    assert refused.value.key == key
    assert settings_of(deps, 777) == SAVED


async def test_a_save_drops_the_cached_record_and_the_prefix_memo(deps):
    recorded_server(deps.mongo_client, "777", **SAVED)
    assert await moderations_service.server_prefix("777") == "!!"
    assert moderations_service.server_settings("777")["language"] == "en-us"

    moderations_service.save_server_setting("777", "prefix", "k?")
    moderations_service.save_server_setting("777", "language", "pt-br")

    assert await moderations_service.server_prefix("777") == "k?"
    assert moderations_service.server_settings("777")["language"] == "pt-br"
    assert await moderations_service.server_language("777") == "pt-br"


async def test_the_prefix_is_read_once_and_again_only_after_its_memo_expires(
    deps, monkeypatch
):
    recorded_server(deps.mongo_client, "777", **SAVED)
    reads = []
    read = moderations_service.server_settings
    monkeypatch.setattr(
        moderations_service, "server_settings",
        lambda guild_id: (reads.append(guild_id), read(guild_id))[1],
    )
    now = [1000.0]
    monkeypatch.setattr(cache, "monotonic", lambda: now[0])

    assert await moderations_service.server_prefix("777") == "!!"
    deps.mongo_client.guild.moderations.update_one(
        {"guild_id": "777"}, {"$set": {"settings.prefix": "kk!"}}
    )
    cache.remove_cog_cache_by_guild("777", Commands.MODERATIONS_KEY)
    assert await moderations_service.server_prefix("777") == "!!"
    assert reads == ["777"], "a second message reads nothing"

    now[0] += DBConfigs.COG_CACHE_TTL_SECONDS + 1

    assert await moderations_service.server_prefix("777") == "kk!"
    assert reads == ["777", "777"]


async def test_the_bot_reads_a_message_with_its_servers_prefix_and_a_dm_with_the_default(
    deps, monkeypatch
):
    from app.bot import server_command_prefix

    recorded_server(deps.mongo_client, "777", prefix="!!")
    bot = SimpleNamespace(config=SimpleNamespace(PREFIX="ke!"))
    monkeypatch.setattr(moderations_service.app_module, "bot", bot)
    in_a_server = SimpleNamespace(guild=SimpleNamespace(id=777))

    assert await server_command_prefix(bot, in_a_server) == "!!"

    monkeypatch.setattr(
        moderations_service, "server_settings",
        lambda guild_id: pytest.fail("a direct message has no server to read"),
    )
    assert await server_command_prefix(bot, SimpleNamespace(guild=None)) == "ke!"


async def test_a_form_reads_the_servers_settings_as_context_tokens(deps):
    guild = create_guild(id=777)
    scenario = FormScenario(
        guild=guild,
        user=create_member(guild, id=555, name="Tester"),
        locale="pt-br",
        mongo=deps.mongo_client,
    )
    recorded_server(deps.mongo_client, guild.id, **SAVED)

    await scenario.start("default_roles")

    [session_id] = RUNTIME.sessions
    context = await RUNTIME._context(RUNTIME.store.get(session_id))
    assert (
        context.prefix,
        context.server_language,
        context.server_timezone,
    ) == ("!!", "en-us", "Europe/Berlin")


async def test_a_form_on_a_server_missing_one_setting_raises_settings_missing(deps):
    guild = create_guild(id=777)
    scenario = FormScenario(
        guild=guild,
        user=create_member(guild, id=555, name="Tester"),
        locale="pt-br",
        mongo=deps.mongo_client,
    )
    deps.mongo_client.guild.moderations.update_one(
        {"guild_id": "777"},
        {"$set": {"settings": {"language": "pt-br", "timezone": "UTC"}}},
    )

    with pytest.raises(ServerSettingsMissing, match="guild 777"):
        await scenario.start("default_roles")


def test_the_operator_lists_are_every_declared_feature():
    assert Commands.COMMANDS_LIST == [
        "welcome_messages",
        "default_roles",
        "block_links",
        "notifications_twitch",
        "notifications_youtube_video",
        "reminders_birthday",
        "stream_elements_commands",
    ]
    assert Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY in Commands.COMMANDS_LIST


async def test_the_guild_inspection_prints_the_settings_as_one_line(deps):
    from app.views.log_inspection import LogInspectionView

    view = LogInspectionView(None, None, "777")
    embed = discord.Embed()

    view._add_moderations_fields(
        embed,
        {"guild_id": "777", "stream_elements_commands": True, "settings": dict(SAVED)},
    )

    fields = {field.name: field.value for field in embed.fields}
    assert fields == {
        "settings": "language: en-us · timezone: Europe/Berlin · prefix: !!"
    }
