"""Every feature module: documents round-trip, commits write what they report."""

import asyncio
import copy
import logging
import sys
from datetime import date, datetime, timezone
from types import ModuleType, SimpleNamespace

import pytest

from app.commands.birthdays import service as birthdays
from app.constants import Commands
from app.core import i18n
from app.core.commands import Command, commands
from app.core.dates import format_mm_dd_label
from app.core.servers import server_setting
from app.core.utils import ml
from app.data import data
from app.forms.features import feature_for, feature_keys
from app.forms.features.feature import (
    CommitContext,
    GenericCogFeature,
    ListChanged,
    OpenContext,
    PanelInfo,
)
from app.forms.form.form_state import Answer
from app.forms.form.form_yaml import DefinitionRegistry, shared_vocabulary
from app.forms.form.responses.responses import (
    document_values,
    item_values,
    items_of,
    list_values,
    styled_values,
)
from app.forms.form.responses.summary import unwrap
from app.integrations.stream_elements import StreamElementsClient
from tests.generators.birthdays import saved_birthday
from tests.generators.events import lifecycle
from tests.generators.moderations import ladder_rung, recorded_server, seed_cog
from tests.helpers.forms import section
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.unit

GUILD_ID = "123456789"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
REGISTRY = DefinitionRegistry()

DOCUMENTS = {
    "default_roles": {
        "guild_id": GUILD_ID,
        "enabled": True,
        "default_roles": {"style": "role", "values": "201"},
        "default_roles_bot": {"style": "role", "values": ["202"]},
        "role_ladder": {"style": "composition", "values": [ladder_rung(7, "202")]},
    },
    "block_links": {
        "guild_id": GUILD_ID,
        "enabled": True,
        "mode": "block_all",
        "allowed_chats": {"style": "channel", "values": "100"},
        "allowed_roles": {"style": "role", "values": "201"},
        "allowed_links": {"style": "bullet", "values": ["youtube.com", "twitch.tv"]},
        "custom_links": {
            "style": "composition",
            "values": [
                {
                    "link": {
                        "value": "meusite.com.br",
                        "title": "Link ou Site",
                        "style": "code",
                    },
                    "match_type": {
                        "value": "🌐 Todos os links desse site",
                        "_raw_value": "domain",
                        "title": "Qual o Alcance da Regra?",
                    },
                },
            ],
        },
        "answer": "Nada de links aqui! :p",
    },
    "notifications_twitch": {
        "guild_id": GUILD_ID,
        "enabled": True,
        "notifications": {
            "style": "composition",
            "values": [
                {
                    "channel": {
                        "value": "100",
                        "title": "Canal de Texto",
                        "style": "channel",
                    },
                    "streamer": {"value": "gaules", "title": "Streamer"},
                    "notification_messages": {
                        "value": ["oi"],
                        "title": "Mensagens de Notificação",
                        "style": "bullet",
                    },
                }
            ],
        },
    },
    "welcome_messages": {
        "guild_id": GUILD_ID,
        "enabled": True,
        "welcome_messages_channel": {"style": "channel", "values": "101"},
        "welcome_design": "server_blur",
        "welcome_messages_title": "Oi",
        "welcome_messages": {"style": "bullet", "values": ["a", "b"]},
        "welcome_messages_footer": "tchau",
    },
    "stream_elements_commands": {
        "guild_id": GUILD_ID,
        "enabled": True,
        "streamer": "shroud",
    },
}


def _machine(document):
    """Machine values only; a scalar and its one-item list read the same."""
    flat = {}
    for key, value in document.items():
        if key in ("guild_id", "enabled", "schema_version"):
            continue
        if isinstance(value, dict) and value.get("style") == "composition":
            flat[key] = [
                {k: unwrap(v) for k, v in item.items()} for item in value["values"]
            ]
            continue
        raw = unwrap(value)
        if isinstance(raw, str):
            raw = [raw]
        flat[key] = raw
    return flat


async def _settle(feature):
    await asyncio.gather(*feature.running)


@pytest.fixture
def stream_elements(monkeypatch):
    """StreamElements as these tests answer it: a channel id per streamer, the
    commands of each channel, and `down` to fail every call as an outage does."""
    api = SimpleNamespace(channels={}, commands={}, down=False, asked=[])

    def channel_info(name):
        api.asked.append(name)
        if api.down:
            raise ConnectionError("StreamElements is down")
        return {"_id": api.channels.get(name, f"channel-of-{name}")}

    def chat_commands(channel_id):
        if api.down:
            raise ConnectionError("StreamElements is down")
        return api.commands.get(channel_id, [])

    monkeypatch.setattr(
        StreamElementsClient, "get_channel_info", staticmethod(channel_info)
    )
    monkeypatch.setattr(
        StreamElementsClient, "get_chat_commands", staticmethod(chat_commands)
    )
    return api


@pytest.fixture
def reminders(deps):
    """reminders-api as Keiko reaches it outside dev: every reminder it is asked to
    create, each answered with a new id."""
    created = []

    def create_reminder(body):
        created.append(body)
        return {"id": f"rem-{len(created)}"}

    deps.bot.config.is_dev = lambda: False
    deps.bot.reminder = SimpleNamespace(create_reminder=create_reminder)
    return created


@pytest.mark.parametrize("form", sorted(DOCUMENTS))
def test_a_document_survives_the_round_trip_through_answers(form):
    feature = feature_for(form)
    answers = feature.from_document(DOCUMENTS[form])
    document = feature.to_document(answers, "pt-br")
    assert _machine(document) == _machine(DOCUMENTS[form])
    assert "schema_version" not in document and document["enabled"] is True


def test_a_command_with_a_form_and_no_feature_gets_the_generic_feature(monkeypatch):
    listed = ModuleType("app.commands")
    listed.COMMANDS = (Command("default_roles"),)
    monkeypatch.setitem(sys.modules, "app.commands", listed)
    commands.cache_clear()
    try:
        feature = feature_for("default_roles")
    finally:
        commands.cache_clear()
    assert isinstance(feature, GenericCogFeature) and feature.key == "default_roles"


def test_a_key_no_feature_declares_is_refused_instead_of_guessed():
    with pytest.raises(KeyError):
        feature_for("plain_form")


def test_the_seven_features_are_registered_with_their_definitions():
    for key in feature_keys():
        assert feature_for(key).key == key
        assert REGISTRY.get(key).key == key


def test_a_legacy_block_links_document_loads_as_the_card_expects():
    legacy = {
        "guild_id": GUILD_ID,
        "enabled": True,
        "allowed_chats": {"style": "channel", "values": "100"},
        "allowed_links": ["Youtube", "Spotify"],
        "answer": "old",
    }
    answers = feature_for("block_links").from_document(legacy)
    assert answers["mode"].raw == "block_all"
    assert answers["allowed_links"].raw == ["youtube.com", "spotify.com"]
    assert answers["custom_links"].raw == ()
    assert "add_custom" not in answers


def test_an_incomplete_document_never_raises_on_load():
    answers = feature_for("welcome_messages").from_document(
        {"guild_id": GUILD_ID, "enabled": True}
    )
    assert answers == {}
    answers = feature_for("default_roles").from_document(
        {"guild_id": GUILD_ID, "default_roles": None}
    )
    assert answers["default_roles"].raw is None


def _context(document=None, locale="pt-br", guild=None):
    return CommitContext(
        GUILD_ID, "555", locale, document or {}, NOW, "slash", "s1", guild
    )


async def test_a_generic_setup_enables_the_feature_and_stores_the_document(deps):
    feature = feature_for("default_roles")
    answers = {"default_roles_bot": Answer(["201"]), "default_roles": Answer("202")}
    result = await feature.commit("setup", {"answers": answers}, _context())
    assert result.written == ("default_roles",)
    stored = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles")
    assert stored["default_roles"] == {"style": "role", "values": "202"}
    assert stored["default_roles_bot"] == {"style": "role", "values": ["201"]}
    assert stored["enabled"] is True
    event = deps.mongo_client.events.changes.find_one({"guild_id": GUILD_ID})
    assert event["type"] == "feature.enabled"
    assert event["actor"] == {"kind": "user", "id": "555"}


async def test_a_default_roles_setup_gives_the_roles_out_after_the_commit(
    deps, monkeypatch
):
    """dr-2: "yes" on the setup's toggle gives the saved roles to who is already
    in the server, after the commit and without the commit waiting for it."""
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")
    robot = create_member(guild, id=888, name="Robo", bot=True)
    member_role = guild.get_role(202)
    released = asyncio.Event()
    give = friend.add_roles

    async def held(*roles, reason=None):
        await released.wait()
        await give(*roles, reason=reason)

    monkeypatch.setattr(friend, "add_roles", held)
    answers = {"default_roles": Answer("202"), "sync_existing": Answer(True)}

    feature = feature_for("default_roles")
    result = await feature.commit("setup", {"answers": answers}, _context(guild=guild))

    assert result.written == ("default_roles",)
    assert member_role not in friend.roles, "the commit never waits for the roles"
    stored = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles")
    assert "sync_existing" not in stored, "the choice is never saved as a setting"
    released.set()
    await _settle(feature)
    assert member_role in friend.roles
    assert member_role not in robot.roles, "a bot gets only the roles saved for bots"


async def test_a_default_roles_sync_that_fails_is_logged_not_raised(
    deps, monkeypatch, caplog
):
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")

    async def refuse(*roles, reason=None):
        raise RuntimeError("Missing Permissions")

    monkeypatch.setattr(friend, "add_roles", refuse)
    answers = {"default_roles": Answer("202"), "sync_existing": Answer(True)}

    feature = feature_for("default_roles")
    with caplog.at_level(logging.ERROR):
        await feature.commit("setup", {"answers": answers}, _context(guild=guild))
        await _settle(feature)

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "Missing Permissions" in errors[0].getMessage()
    stored = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles")
    assert stored["default_roles"] == {"style": "role", "values": "202"}


async def test_a_default_roles_setup_opens_with_the_server_totals(deps):
    guild = create_guild()
    for member_id in (701, 702, 703):
        create_member(guild, id=member_id, name=f"Pessoa {member_id}")
    create_member(guild, id=888, name="Robo", bot=True)

    opened = await feature_for("default_roles").open(
        OpenContext(GUILD_ID, "555", "pt-br", guild)
    )

    assert opened.document is None
    assert opened.values == {"members_count": "3", "bots_count": "1"}


async def test_pause_unpause_and_disable_move_the_document(deps):
    feature = feature_for("default_roles")
    seed_cog(deps.mongo_client, "default_roles", dict(DOCUMENTS["default_roles"]))
    context = _context(DOCUMENTS["default_roles"])
    await feature.commit("pause", {}, context)
    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles"))[
        "enabled"
    ] is False
    await feature.commit("unpause", {}, context)
    assert (await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles"))[
        "enabled"
    ] is True
    await feature.commit("disable", {}, context)
    assert (
        await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles") is None
    )
    events = lifecycle(deps.mongo_client, GUILD_ID, "default_roles")
    assert events == ["paused", "unpaused", "disabled"]


async def test_adding_an_item_appends_it_and_subscribes_outside_dev(deps):
    deps.bot.config.is_dev = lambda: False
    deps.twitch.add_user("cellbit", user_id="222")
    feature = feature_for("notifications_twitch")
    document = DOCUMENTS["notifications_twitch"]
    seed_cog(deps.mongo_client, "notifications_twitch", dict(document))
    item = {
        "channel": Answer("100"),
        "streamer": Answer("cellbit"),
        "notification_messages": Answer("oi"),
    }
    result = await feature.commit("add_item", {"answers": item}, _context(document))
    stored = await data().guild_cogs.find_cog_by_guild_id(
        GUILD_ID, "notifications_twitch"
    )
    saved = items_of(stored, "notifications")
    assert [item_values(entry)["streamer"] for entry in saved] == ["gaules", "cellbit"]
    assert saved[1]["streamer"]["title"], "the item is saved with its field labels"
    assert {c.get("user_id") for c in deps.twitch.subscribe_calls} == {"222"}
    assert item_values(items_of(result.document, "notifications")[1]) == item_values(
        saved[1]
    )


def _two_streamers() -> dict:
    document = copy.deepcopy(DOCUMENTS["notifications_twitch"])
    first = document["notifications"]["values"][0]
    second = copy.deepcopy(first)
    second["streamer"]["value"] = "cellbit"
    document["notifications"]["values"].append(second)
    return document


async def test_several_items_go_in_one_save_and_each_lets_its_subscription_go(deps):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.add_user("cellbit", user_id="222")
    deps.twitch.add_user("shroud", user_id="333")
    for user_id in ("111", "222", "333"):
        deps.twitch.subscribe_to_stream_online_event(user_id)
    document = _two_streamers()
    third = copy.deepcopy(document["notifications"]["values"][0])
    third["streamer"]["value"] = "shroud"
    document["notifications"]["values"].append(third)
    seed_cog(deps.mongo_client, "notifications_twitch", copy.deepcopy(document))

    await feature_for("notifications_twitch").commit(
        "remove_item", {"indexes": [0, 2]}, _context(document)
    )

    stored = await data().guild_cogs.find_cog_by_guild_id(
        GUILD_ID, "notifications_twitch"
    )
    names = [
        item_values(item)["streamer"] for item in items_of(stored, "notifications")
    ]
    assert names == ["cellbit"]
    kept = {sub["user_id"] for sub in deps.twitch.get_subscriptions()["data"]}
    assert kept == {"222"}, "Twitch keeps only the streamer still followed"


async def test_a_removal_saves_nothing_when_one_chosen_item_changed_meanwhile(deps):
    document = _two_streamers()
    saved = copy.deepcopy(document)
    saved["notifications"]["values"][1]["streamer"]["value"] = "shroud"
    seed_cog(deps.mongo_client, "notifications_twitch", copy.deepcopy(saved))

    with pytest.raises(ListChanged):
        await feature_for("notifications_twitch").commit(
            "remove_item", {"indexes": [0, 1]}, _context(document)
        )

    stored = await data().guild_cogs.find_cog_by_guild_id(
        GUILD_ID, "notifications_twitch"
    )
    assert len(stored["notifications"]["values"]) == 2


async def test_a_list_the_feature_needs_never_loses_its_last_item(deps):
    document = _two_streamers()
    seed_cog(deps.mongo_client, "notifications_twitch", copy.deepcopy(document))

    with pytest.raises(ListChanged):
        await feature_for("notifications_twitch").commit(
            "remove_item", {"indexes": [0, 1]}, _context(document)
        )


@pytest.mark.parametrize(
    "website, turned_on, saved",
    [
        ("spotify.com", True, ["youtube.com", "twitch.tv", "spotify.com"]),
        ("youtube.com", False, ["twitch.tv"]),
        ("youtube.com", True, ["youtube.com", "twitch.tv"]),
        ("spotify.com", False, ["youtube.com", "twitch.tv"]),
    ],
    ids=["turned-on", "turned-off", "already-on", "already-off"],
)
async def test_a_website_turns_on_or_off_in_the_choice_as_it_is_saved(
    deps, website, turned_on, saved
):
    document = {
        "guild_id": GUILD_ID,
        "enabled": True,
        "mode": "block_all",
        "allowed_links": {"style": "bullet", "values": ["youtube.com", "twitch.tv"]},
    }
    seed_cog(deps.mongo_client, "block_links", dict(document))

    await feature_for("block_links").commit(
        "toggle",
        {"key": "allowed_links", "value": website, "turned_on": turned_on},
        _context(document),
    )

    stored = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "block_links")
    assert stored["allowed_links"] == {"style": "code", "values": saved}
    event = deps.mongo_client.events.changes.find_one({"guild_id": GUILD_ID})
    assert event["type"] == "config.changed", "a toggle is recorded as the edit it was"


async def test_the_youtuber_picture_is_the_medium_thumbnail(deps, monkeypatch):
    """P3 (beta.2): the 88 px default thumbnail blurs under the card section."""
    from app.forms.form.lookups import Lookup

    deps.youtube.add_channel("UC1", "Gaules", custom_url="@gaules")
    thumbnails = {"default": {"url": "small.jpg"}, "medium": {"url": "medium.jpg"}}
    monkeypatch.setattr(
        deps.youtube, "get_channel_info", lambda channel: {"thumbnails": thumbnails}
    )

    found = await feature_for("notifications_youtube_video").prefetch(
        Lookup(("youtube",), "gaules"), OpenContext(GUILD_ID, "555", "pt-br")
    )

    assert found["youtube"]["profile_image"] == "medium.jpg"


async def test_the_streamer_lookup_is_prefetched_for_the_validator(deps):
    deps.twitch.add_user("gaules", user_id="111")
    from app.forms.form.lookups import Lookup

    feature = feature_for("notifications_twitch")
    context = OpenContext(GUILD_ID, "555", "pt-br")
    found = await feature.prefetch(Lookup(("twitch",), "gaules"), context)
    assert found["twitch"]["user_id"] == "111"
    assert "gaules" in found["twitch"]["profile_image"]
    assert await feature.prefetch(Lookup(("twitch",), "nobody"), context) == {
        "twitch": {"user_id": None, "profile_image": ""}
    }
    assert await feature.prefetch(Lookup(("youtube",), "gaules"), context) == {}


async def test_the_birthday_setup_writes_the_config_and_the_first_member(deps):
    feature = feature_for("reminders_birthday")
    answers = {
        "channel": Answer("100"),
        "timezone": Answer("America/Sao_Paulo"),
        "notification_time": Answer("08:00"),
        "mention": Answer("none"),
        "default_message_mode": Answer("default"),
        "reminders_birthday": Answer(
            (
                {
                    "user": Answer("555"),
                    "date": Answer("05-12"),
                    "use_custom_message": Answer("default"),
                    "use_custom_image": Answer("default"),
                },
            )
        ),
    }
    result = await feature.commit("setup", {"answers": answers}, _context())
    assert result.written == ("reminders_birthday", "birthdays")
    config = await data().guild_cogs.find_cog_by_guild_id(
        GUILD_ID, "reminders_birthday"
    )
    assert config["channel"] == "100" and config["notification_time"] == "08:00"
    assert config["enabled"] is True
    item = deps.mongo_client.guild["members"].find_one({"_id": f"{GUILD_ID}:555"})
    assert item["options"]["reminders_birthday"]["date"] == "05-12"


async def test_the_birthday_setup_saves_each_member_with_their_own_message(deps):
    """A member the setup lists keeps the message written for them, and the server
    keeps Keiko's message as its default when the setup wrote none."""
    answers = {
        "channel": Answer("111"),
        "mention": Answer("everyone"),
        "reminders_birthday": Answer(
            (
                {
                    "user": Answer("222"),
                    "date": Answer("05-15"),
                    "use_custom_message": Answer("custom"),
                    "custom_message_title": Answer("Parabens"),
                    "custom_message_content": Answer("Feliz aniversario, {user}"),
                    "use_custom_image": Answer("default"),
                },
            )
        ),
    }

    await feature_for("reminders_birthday").commit(
        "setup", {"answers": answers}, _context()
    )

    config = await birthdays.birthday_config(GUILD_ID)
    assert (config["channel"], config["mention"]) == ("111", "everyone")
    assert config["default_message"] == {
        "mode": "default",
        "title": None,
        "content": None,
    }
    saved = await birthdays.find_birthday(GUILD_ID, "222")
    assert saved["date"] == "05-15"
    assert saved["message"] == {
        "mode": "custom",
        "title": "Parabens",
        "content": "Feliz aniversario, {user}",
    }
    assert saved["image"] == {"mode": "default", "url": None}


async def test_the_birthday_setup_saves_the_hour_the_zone_the_roles_and_the_message(
    deps,
):
    """The setup's hour, roles and server message are the configuration; the zone it
    picks becomes the server's, and a setup with no member saves no birthday."""
    answers = {
        "channel": Answer("111"),
        "mention": Answer("roles"),
        "mention_roles": Answer(("1", "2")),
        "timezone": Answer("America/Sao_Paulo"),
        "notification_time": Answer("12:00"),
        "default_message_mode": Answer("custom"),
        "default_message_title": Answer("Parabens, {user}!"),
        "default_message_content": Answer("Hoje tem festa no {server}."),
    }

    await feature_for("reminders_birthday").commit(
        "setup", {"answers": answers}, _context()
    )

    config = await birthdays.birthday_config(GUILD_ID)
    assert config["mention"] == "roles" and config["mention_roles"] == ["1", "2"]
    assert config["notification_time"] == "12:00"
    assert config["default_message"] == {
        "mode": "custom",
        "title": "Parabens, {user}!",
        "content": "Hoje tem festa no {server}.",
    }
    zone = await server_setting(GUILD_ID, Commands.SERVER_TIMEZONE_KEY)
    assert zone == "America/Sao_Paulo"
    assert await birthdays.find_birthdays_by_guild(GUILD_ID) == []


def test_a_new_birthday_gets_keiko_message_and_image_and_no_edit_yet():
    item = birthdays.default_birthday("05-15")

    assert (item["date"], item["month"], item["day"]) == ("05-15", 5, 15)
    assert item["message"]["mode"] == "default"
    assert item["image"]["mode"] == "default"
    assert item["self_edit_count"] == 0


def test_a_member_rewrites_their_own_birthday_only_once():
    assert birthdays.can_self_edit_birthday({"self_edit_count": 0}) is True
    assert birthdays.can_self_edit_birthday({"self_edit_count": 1}) is False
    assert birthdays.get_self_edit_count({"self_edit_count": "invalid"}) == 0


async def test_the_birthday_stats_count_the_months_and_the_dates(deps):
    for user_id, mm_dd in (("1", "05-15"), ("2", "05-15"), ("3", "06-01")):
        saved_birthday(GUILD_ID, user_id, mm_dd)

    stats = await birthdays.get_birthday_stats(GUILD_ID, today=date(2026, 5, 2))

    assert stats.total == 3
    assert stats.this_month == 2
    assert stats.busiest_month == (5, 2)
    assert stats.quietest_month == (6, 1)
    assert stats.most_common_date == ("05-15", 2)


async def test_a_birthday_on_a_date_that_has_a_reminder_reuses_it(deps, reminders):
    saved_birthday(GUILD_ID, "777", "05-15", reminder_id="rem-0")

    await birthdays.upsert_birthday(GUILD_ID, "555", "05-15")

    assert (await birthdays.find_birthday(GUILD_ID, "555"))["date"] == "05-15"
    assert reminders == [], "the date's reminder is shared, never created twice"
    schedule = await data().keiko_schedules.find_schedule(
        birthdays.birthday_schedule_id(GUILD_ID, "05-15")
    )
    assert schedule["reminder_id"] == "rem-0"


async def test_a_birthday_on_a_new_date_gets_a_reminder_in_the_servers_zone(
    deps, reminders
):
    """The date's reminder fires at the hour the birthdays are set to, in the zone
    of the server, and its schedule keeps the reminder it got."""
    recorded_server(deps.mongo_client, GUILD_ID, timezone="America/Sao_Paulo")
    await birthdays.save_birthday_config(GUILD_ID, "100", notification_time="12:00")

    await birthdays.upsert_birthday(GUILD_ID, "555", "05-15")

    [created] = reminders
    assert created["title"] == Commands.REMINDER_API_TITLE_BIRTHDAY
    assert (created["timezone"], created["time_tz"]) == ("America/Sao_Paulo", "12:00")
    assert created["notes"] == "05-15"
    schedule = await data().keiko_schedules.find_schedule(
        birthdays.birthday_schedule_id(GUILD_ID, "05-15")
    )
    assert schedule["reminder_id"] == "rem-1" and schedule["due_at"] is not None
    assert (await birthdays.find_birthday(GUILD_ID, "555"))["date"] == "05-15"


def test_a_birthday_message_uses_the_servers_default_before_keikos():
    title, content = birthdays.resolve_message(
        {"message": {"mode": "default", "title": None, "content": None}},
        {
            "default_message": {
                "mode": "custom",
                "title": "Servidor feliz, {user}",
                "content": "Hoje e dia de bolo em {server}.",
            }
        },
        "pt-br",
    )

    assert title == "Servidor feliz, {user}"
    assert content == "Hoje e dia de bolo em {server}."


async def test_the_birthday_panel_opens_with_its_own_rows(deps):
    from app.core.servers import set_feature_enabled

    await birthdays.save_birthday_config(
        GUILD_ID, "100", "roles", notification_time="08:00", mention_roles=["201"]
    )
    saved_birthday(GUILD_ID, "555", "05-12")
    await set_feature_enabled(GUILD_ID, "reminders_birthday", True)
    opened = await feature_for("reminders_birthday").open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )
    assert opened.document["reminders_birthday"]["values"][0]["user"]["value"] == "555"
    some_roles = shared_vocabulary().texts["mention_roles"].get("pt-br")
    assert [row.value for row in opened.panel.rows][:3] == [
        "100",
        some_roles,
        ["201"],
    ], "who I ping by its name on the card, then the roles it names"
    assert opened.panel.extra_buttons == (), "the statistics are every panel's"
    assert "stats" in feature_for("reminders_birthday").asides()


def test_the_birthday_feature_previews_the_celebration():
    """The setup and the member card both offer a preview, so the feature has
    a preview side action like the welcome message does."""
    assert "preview" in feature_for("reminders_birthday").asides()


def test_the_preview_of_a_card_reads_what_is_written_on_it():
    """Broke as: Preview on the welcome card answered nothing at all. While the
    card is open its answers live as one draft under the card's own key, and
    the card key produces no view, so the preview found no message to draw."""
    feature = feature_for("welcome_messages")
    answers = {
        "welcome_config": Answer(
            None,
            {
                "welcome_messages_channel": "101",
                "welcome_messages_title": "Chegou gente nova!",
                "welcome_messages": "Oi {user}!;Olha quem chegou",
                "welcome_messages_footer": "Divirta-se!",
                "welcome_design": "custom_only",
                "welcome_custom_image": "https://cdn.discordapp.com/a/b/banner.png",
            },
        )
    }

    views = feature.responses_for_preview(answers, "pt-br")

    values = {view["key"]: view.get("_raw_value", view.get("value")) for view in views}
    assert values.get("welcome_messages_title") == "Chegou gente nova!"
    assert "Olha quem chegou" in str(values.get("welcome_messages"))
    assert values.get("welcome_design") == "custom_only"


def test_the_preview_of_an_item_card_reads_the_fields_of_that_item():
    """Broke as: previewing from a Twitch item card found no message at all and
    Discord refused an empty message. The card lives inside the composition, so
    its fields are not produced by any top level step."""
    feature = feature_for("notifications_twitch")
    answers = {
        "notification": Answer(
            None,
            {
                "channel": "100",
                "streamer": "gaules",
                "notification_messages": "{streamer} ao vivo!;bora?",
            },
        )
    }

    views = feature.responses_for_preview(answers, "pt-br")

    values = {view["key"]: view.get("_raw_value", view.get("value")) for view in views}
    assert values.get("streamer") == "gaules"
    assert "bora?" in str(values.get("notification_messages"))


async def test_an_unconfigured_feature_opens_empty(deps):
    opened = await feature_for("block_links").open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )
    assert opened.document is None and opened.refusal is None


async def test_editing_the_streamer_refreshes_the_stream_elements_channel(
    deps, stream_elements
):
    """Broke as: an edited streamer kept the old streamer's StreamElements channel,
    so the chat went on answering with the old streamer's commands."""
    deps.bot.config.is_dev = lambda: False
    feature = feature_for("stream_elements_commands")
    saved = {
        "guild_id": GUILD_ID,
        "enabled": True,
        "streamer": "gaules",
        "state": {"channel_id": "channel-of-gaules"},
    }
    seed_cog(deps.mongo_client, "stream_elements_commands", dict(saved))

    result = await feature.commit(
        "edit", {"answers": {"streamer": Answer("cellbit")}}, _context(saved)
    )

    stored = await data().guild_cogs.find_cog_by_guild_id(
        GUILD_ID, "stream_elements_commands"
    )
    assert stored["streamer"] == "cellbit"
    assert stored["state"]["channel_id"] == "channel-of-cellbit"
    assert result.document["state"]["channel_id"] == "channel-of-cellbit"


async def test_a_setup_saves_the_stream_elements_channel_in_dev(deps, stream_elements):
    """Broke as: the setup skipped the StreamElements channel when `is_dev()`, so
    the dev bot saved a streamer without a channel and answered no command."""
    deps.bot.config.is_dev = lambda: True
    feature = feature_for("stream_elements_commands")

    looked_up = await feature.commit(
        "setup",
        {
            "answers": {
                "streamer": Answer("shroud"),
                "stream_elements_channel_id": Answer("se-shroud"),
            }
        },
        _context(),
    )
    stored = await data().guild_cogs.find_cog_by_guild_id(
        GUILD_ID, "stream_elements_commands"
    )
    assert stored["state"]["channel_id"] == "se-shroud"
    assert looked_up.document["state"]["channel_id"] == "se-shroud"
    assert stream_elements.asked == [], "the channel the lookup found was asked again"

    deps.mongo_client.guild.cogs.delete_one(
        {"_id": f"{GUILD_ID}:stream_elements_commands"}
    )
    await feature.commit(
        "setup", {"answers": {"streamer": Answer("gaules")}}, _context()
    )
    stored = await data().guild_cogs.find_cog_by_guild_id(
        GUILD_ID, "stream_elements_commands"
    )
    assert stored["state"]["channel_id"] == "channel-of-gaules"
    assert stream_elements.asked == ["gaules"]


async def test_the_welcome_manager_draws_previews_in_the_background(deps):
    """The design gallery opened to edit a saved welcome message had no previews:
    they were only drawn for a setup."""
    seed_cog(
        deps.mongo_client,
        "welcome_messages",
        {"guild_id": GUILD_ID, "enabled": True, "welcome_design": "server_blur"},
    )
    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")
    opened = await feature_for("welcome_messages").open(
        OpenContext(GUILD_ID, "555", "pt-br", guild, member)
    )

    assert opened.document is not None
    assert opened.pending_previews is not None
    opened.pending_previews.close()


def test_the_welcome_card_saves_the_document_the_welcome_service_reads():
    """The welcome card must save the keys and shapes send_welcome_message reads:
    the channel as {style, values}, the design key, the title and footer, and the
    messages as one ;-joined text the service splits."""
    from app.forms.form import events as ev
    from app.forms.form.actions.action import Context
    from app.forms.form.form import decide
    from app.forms.form.form_state import Origin, Setup, new_session
    from app.forms.form.responses.responses import to_document

    definition = REGISTRY.get("welcome_messages")
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin(GUILD_ID, "555", "pt-br"),
        ttl_seconds=60,
    ).at("welcome_config")

    def run(current, event):
        return decide(definition, current, event, Context()).session

    session = run(
        session,
        ev.Drafted("e1", None, "welcome_config", {"welcome_messages_channel": ["101"]}),
    )
    session = run(
        session,
        ev.Answered(
            "e2",
            None,
            section(definition.step("welcome_config"), "messages"),
            {"inputs": ["Oi {user}!", "Bem-vindo {user}", "", "Chegou!", "Divirta-se"]},
        ),
    )
    session = run(session, ev.Answered("e3", None, "welcome_config"))

    document = to_document(definition.steps, session.answers, "pt-br")
    assert document["welcome_messages_channel"] == {"style": "channel", "values": "101"}
    assert document["welcome_design"] == "server_blur"
    assert document["welcome_messages_title"] == "Oi {user}!"
    assert document["welcome_messages"] == {
        "style": "code-block",
        "values": "Bem-vindo {user};Chegou!",
    }
    assert document["welcome_messages_footer"] == "Divirta-se"
    assert "welcome_config" not in document


async def test_the_stream_elements_lookup_counts_enabled_commands(
    deps, stream_elements
):
    """The review shows how many commands will load, so the streamer lookup also
    asks StreamElements for the channel and counts its enabled commands."""
    from app.forms.form.lookups import Lookup

    stream_elements.channels["shroud"] = "se1"
    stream_elements.commands["se1"] = [
        {"enabled": True, "command": "mouse"},
        {"enabled": False, "command": "chair"},
        {"enabled": True, "command": "setup"},
    ]
    deps.twitch.add_user("shroud", user_id="37402112")
    feature = feature_for("stream_elements_commands")

    found = await feature.prefetch(
        Lookup(("twitch", "stream_elements"), "shroud"),
        OpenContext(GUILD_ID, "555", "pt-br", prefix="ks!"),
    )

    assert found == {
        "twitch": {
            "user_id": "37402112",
            "profile_image": (
                "https://static-cdn.jtvnw.net/user-default-pictures/shroud.jpg"
            ),
        },
        "stream_elements": {
            "channel_id": "se1",
            "enabled_commands": 2,
            "top_commands": "`!mouse`, `!setup`",
            "keiko_commands": "`ks!mouse`, `ks!setup`",
        },
    }


async def test_the_stream_elements_panel_says_how_many_commands_it_answers(
    deps, stream_elements
):
    """The panel says how many commands are loaded and offers to list them."""
    seed_cog(
        deps.mongo_client,
        "stream_elements_commands",
        {"guild_id": GUILD_ID, "enabled": True, "streamer": "shroud"},
    )
    stream_elements.commands["channel-of-shroud"] = [
        {"enabled": True, "command": "mouse", "reply": "a"},
        {"enabled": True, "command": "setup", "reply": "b"},
    ]
    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")

    opened = await feature_for("stream_elements_commands").open(
        OpenContext(GUILD_ID, "555", "pt-br", guild, member)
    )

    assert "2" in opened.panel.info and "shroud" in opened.panel.info
    assert opened.panel.info_action is not None
    assert opened.panel.info_action.action == "aside:commands"
    assert not opened.panel.extra_buttons, "the list opens from inside the card (N1)"


async def test_the_stream_elements_panel_tells_no_commands_from_a_failure(
    deps, stream_elements
):
    """Broke as: the panel's count read a streamer with no active command and a
    StreamElements failure alike, as "I couldn't reach StreamElements"."""
    seed_cog(
        deps.mongo_client,
        "stream_elements_commands",
        {
            "guild_id": GUILD_ID,
            "enabled": True,
            "streamer": "shroud",
            "state": {"channel_id": "c1"},
        },
    )
    namespace = "stream_elements_commands"

    async def panel_line():
        opened = await feature_for("stream_elements_commands").open(
            OpenContext(GUILD_ID, "555", "pt-br")
        )
        return opened.panel.info

    empty = await panel_line()
    deps.redis_client.delete("cache:stream_elements:commands:c1")
    stream_elements.down = True
    failed = await panel_line()

    assert empty == ml(f"{namespace}.ui.records.commands.empty", "pt-br").replace(
        "$streamer", "shroud"
    )
    unreachable = i18n.block(f"{namespace}.command.panel.info", PanelInfo).overrides[0]
    assert failed == unreachable.description.get("pt-br")


async def test_a_stream_elements_outage_leaves_no_count(deps, stream_elements):
    from app.forms.form.lookups import Lookup

    stream_elements.down = True
    deps.twitch.add_user("shroud", user_id="37402112")

    found = await feature_for("stream_elements_commands").prefetch(
        Lookup(("twitch", "stream_elements"), "shroud"),
        OpenContext(GUILD_ID, "555", "pt-br"),
    )

    assert found == {
        "twitch": {
            "user_id": "37402112",
            "profile_image": (
                "https://static-cdn.jtvnw.net/user-default-pictures/shroud.jpg"
            ),
        }
    }


DOCUMENT = {
    "guild_id": "1",
    "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": ["100"]},
    "custom_links": {
        "style": "composition",
        "values": [
            {
                "link": {"value": "meusite.com.br", "title": "Link", "style": "code"},
                "match_type": {
                    "value": "🌐 Todos os links desse site",
                    "_raw_value": "domain",
                    "title": "Como Devo Considerar?",
                },
            },
            {"link": {"value": "docs.example.org", "title": "Link"}},
        ],
    },
}


def test_a_saved_list_reads_as_its_machine_values_never_its_labels():
    """What a saved document reads as, for the readers that act on it."""
    assert list_values(DOCUMENT, "custom_links") == [
        {"link": "meusite.com.br", "match_type": "domain"},
        {"link": "docs.example.org"},
    ]


def test_one_saved_item_reads_as_its_machine_values():
    first = items_of(DOCUMENT, "custom_links")[0]
    assert item_values(first) == {"link": "meusite.com.br", "match_type": "domain"}


def test_an_item_reads_the_values_of_the_entries_saved_in_one_style():
    first, second = items_of(DOCUMENT, "custom_links")
    assert styled_values(first, "code") == ["meusite.com.br"]
    assert styled_values(second, "code") == []
    assert styled_values(first, "channel") == []


def test_a_list_nothing_was_saved_under_reads_empty():
    assert list_values(DOCUMENT, "notifications") == []
    assert list_values({"custom_links": None}, "custom_links") == []


def test_the_settings_read_as_their_machine_values():
    values = document_values(DOCUMENT)
    assert values["mode"] == "block_all"
    assert values["allowed_chats"] == ["100"]
    assert "guild_id" not in values and "enabled" not in values


def _birthday_copy(key):
    return ml(f"reminders_birthday.{key}", "pt-br")


@pytest.mark.parametrize(
    "today, first, next_one",
    [
        (date(2026, 6, 20), ("777", "06-12"), "<@555>"),
        (date(2026, 5, 20), ("555", "05-12"), "<@777>"),
    ],
    ids=["june", "may"],
)
async def test_the_birthday_panel_counts_from_the_day_its_clock_gives(
    deps, monkeypatch, today, first, next_one
):
    """The panel's next birthdays and the month it opens on follow the
    feature's clock (`BirthdayFeature.today`), never the machine's date."""
    await birthdays.save_birthday_config(
        GUILD_ID, "100", False, notification_time="08:00"
    )
    saved_birthday(GUILD_ID, "555", "05-12")
    saved_birthday(GUILD_ID, "777", "06-12")
    monkeypatch.setattr(birthdays.FEATURE, "today", lambda: today)

    opened = await birthdays.FEATURE.open(OpenContext(GUILD_ID, "555", "pt-br"))

    pages = opened.panel.pages
    user_id, mm_dd = first
    assert pages[0].title == _birthday_copy("command.panel.pages.title")
    assert pages[0].lines[0] == f"- <@{user_id}>: {format_mm_dd_label(mm_dd, 'pt-br')}"
    upcoming = next(
        row
        for row in opened.panel.rows
        if row.title == _birthday_copy("command.panel.rows.next")
    )
    assert upcoming.value[0].startswith(next_one), upcoming.value


async def test_the_birthday_panel_shows_its_list_even_with_no_birthday(deps):
    """N2: with no birthday the panel kept no block for the list, so its Add
    sat alone in the bottom row; the list keeps its title and says it is empty."""
    await birthdays.save_birthday_config(GUILD_ID, "100", False)

    opened = await birthdays.FEATURE.open(OpenContext(GUILD_ID, "555", "pt-br"))

    [page] = opened.panel.pages
    assert (page.title, page.lines) == (_birthday_copy("command.panel.pages.title"), ())
    assert page.description == _birthday_copy("command.panel.pages.empty")


async def test_a_found_member_shows_whose_messages_greet_them(deps):
    """N2: the member's screen shows the messages that will celebrate them,
    labelled by whose they are: the member's own, the server's default, or
    Keiko's default when nobody wrote one; the picture is the platform's, read
    from the server's members (review fixes K2)."""
    guild = create_guild()
    create_member(guild, id=777, name="Amiga")
    await birthdays.save_birthday_config(GUILD_ID, "100", False)
    saved_birthday(GUILD_ID, "777", "06-12")
    saved_birthday(
        GUILD_ID,
        "888",
        "07-01",
        message={"mode": "custom", "title": "Oi {user}", "content": "Parabéns!"},
    )

    def written_by(line, source, *texts):
        label = _birthday_copy(f"command.panel.authors.{source}")
        assert all(text in line for text in (label, *texts)), line

    opened = await birthdays.FEATURE.open(OpenContext(GUILD_ID, "555", "pt-br", guild))
    found = opened.panel.found

    written_by(
        found["777"].lines[0],
        "keiko",
        birthdays.birthday_default_text("title", "pt-br"),
    )
    [own] = found["888"].lines
    written_by(own, "member", "Oi {user}", "Parabéns!")

    await birthdays.save_birthday_config(
        GUILD_ID,
        "100",
        False,
        default_message={"mode": "custom", "title": "Viva", "content": "Hoje!"},
    )
    reopened = await birthdays.FEATURE.open(
        OpenContext(GUILD_ID, "555", "pt-br", guild)
    )
    [server] = reopened.panel.found["777"].lines
    written_by(server, "server", "Viva", "Hoje!")
