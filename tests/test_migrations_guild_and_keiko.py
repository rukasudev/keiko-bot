"""What data/0002 and data/0003 make of one old document, with no database.

Guaranteed: a guild.moderations document becomes one guild.settings document keyed by
its guild id, with the settings it saved or the guessed ones, its owner and whether
Keiko is there, and none of the feature flags; a feature document becomes one guild.cogs
document keyed `{guild}:{cog}`, its form's answers in `options` and what Keiko learned in
`state`, `enabled` taken from the old flag only when the document has none; the
birthday config keeps only the form's keys, drops only its zone, its language and the
envelope, and names any other key; Keiko's configs become one keiko.settings
document whose operator ids are numbers.
"""

import hashlib
import importlib
from datetime import datetime, timezone

import pytest


JOINED = datetime(2025, 3, 1, tzinfo=timezone.utc)
LEFT = datetime(2026, 2, 1, tzinfo=timezone.utc)
ZONES = frozenset({"America/Sao_Paulo", "UTC", "Europe/Lisbon"})
FLAGS = {
    "is_bot_online": True,
    "welcome_messages": True,
    "default_roles": False,
    "block_links": False,
    "notifications_twitch": False,
    "notifications_youtube_video": False,
    "reminders_birthday": True,
    "stream_elements_commands": False,
}


@pytest.fixture(scope="module")
def settings():
    return importlib.import_module("app.migrations.data.0002_server_settings")


@pytest.fixture(scope="module")
def cogs():
    return importlib.import_module("app.migrations.data.0003_cogs_and_keiko")


def _server(**fields):
    return {
        "_id": "object-id",
        "guild_id": "1001",
        "owner_id": "2002",
        **FLAGS,
        "created_at": JOINED,
        "updated_at": LEFT,
        **fields,
    }


def test_a_server_without_settings_gets_the_guessed_ones_and_no_flag(settings):
    document = settings.settings_document(
        _server(), {"timezone": "Europe/Lisbon"}, "ke!", ZONES
    )

    assert document == {
        "_id": "1001",
        "options": {"language": "pt-br", "timezone": "Europe/Lisbon", "prefix": "ke!"},
        "state": {
            "owner_id": "2002",
            "present": True,
            "joined_at": JOINED,
            "left_at": None,
        },
        "created_at": JOINED,
        "updated_at": LEFT,
    }


def test_the_zone_of_a_server_without_a_birthday_config_follows_its_language(settings):
    document = settings.settings_document(_server(), {}, "ke!", ZONES)

    assert document["options"]["timezone"] == "America/Sao_Paulo"


def test_a_server_keeps_the_settings_it_saved_and_loses_only_an_invalid_one(settings):
    saved = {"language": "en-us", "timezone": "Mars/Olympus", "prefix": "k?"}

    document = settings.settings_document(
        _server(settings=saved), {"timezone": "Europe/Lisbon"}, "ke!", ZONES
    )

    assert document["options"] == {
        "language": "en-us",
        "timezone": "Europe/Lisbon",
        "prefix": "k?",
    }


def test_a_server_keiko_left_is_absent_since_its_last_write(settings):
    document = settings.settings_document(
        _server(is_bot_online=False), {}, "ke!", ZONES
    )

    assert document["state"]["present"] is False
    assert document["state"]["left_at"] == LEFT


def test_the_language_table_is_keyed_by_a_hash_that_names_no_id(settings, monkeypatch):
    guild_key = hashlib.sha256(b"1001").hexdigest()[:16]
    monkeypatch.setitem(settings.LANGUAGES_BY_GUILD, guild_key, "en-us")

    document = settings.settings_document(_server(), {}, "ke!", ZONES)

    assert document["options"]["language"] == "en-us"
    assert document["options"]["timezone"] == "UTC"
    assert all(
        len(key) == 16 and set(key) <= set("0123456789abcdef")
        for key in settings.LANGUAGES_BY_GUILD
    )


def test_a_feature_document_splits_into_its_answers_and_its_bookkeeping(cogs):
    document = cogs.cog_document(
        "welcome_messages",
        {
            "_id": "object-id",
            "guild_id": "1001",
            "enabled": False,
            "welcome_messages_channel": {"style": "channel", "values": "300"},
            "welcome_messages_title": "Hi",
            "created_at": JOINED,
            "updated_at": LEFT,
        },
        FLAGS,
    )

    assert document == {
        "_id": "1001:welcome_messages",
        "guild_id": "1001",
        "cog": "welcome_messages",
        "enabled": False,
        "options": {
            "welcome_messages_channel": {"style": "channel", "values": "300"},
            "welcome_messages_title": "Hi",
        },
        "state": {},
        "created_at": JOINED,
        "updated_at": LEFT,
    }


def test_a_document_saved_before_enabled_existed_takes_its_old_flag_once(cogs):
    on = cogs.cog_document("welcome_messages", {"guild_id": "1001"}, FLAGS)
    off = cogs.cog_document("default_roles", {"guild_id": "1001"}, FLAGS)
    unknown = cogs.cog_document("block_links", {"guild_id": "1001"}, {})

    assert (on["enabled"], off["enabled"], unknown["enabled"]) == (True, False, False)


def test_what_keiko_found_out_on_its_own_moves_to_state(cogs):
    document = cogs.cog_document(
        "stream_elements_commands",
        {"guild_id": "1001", "enabled": True, "streamer": "shroud", "channel_id": "5f",
         "schema_version": 1},
        FLAGS,
    )

    assert document["options"] == {"streamer": "shroud"}
    assert document["state"] == {"channel_id": "5f", "schema_version": 1}


def test_the_birthday_config_keeps_only_the_forms_keys(cogs):
    message = {"mode": "custom", "title": "Yay", "content": "Happy birthday"}

    document = cogs.cog_document(
        "reminders_birthday",
        {
            "guild_id": "1001",
            "channel_id": "400",
            "mention_everyone": True,
            "locale": "pt-br",
            "timezone": "America/Sao_Paulo",
            "notification_time": "09:00",
            "default_message": message,
        },
        FLAGS,
    )

    assert document["enabled"] is True
    assert document["options"] == {
        "channel": "400",
        "mention_everyone": True,
        "notification_time": "09:00",
        "default_message": message,
    }


def test_the_birthday_config_drops_only_the_zone_the_language_and_the_envelope(cogs):
    production = {
        "_id": "object-id",
        "guild_id": "1001",
        "enabled": True,
        "channel_id": "400",
        "mention_everyone": True,
        "locale": "pt-br",
        "timezone": "America/Sao_Paulo",
        "notification_time": "09:00",
        "default_message": {"mode": "default"},
        "created_at": JOINED,
        "updated_at": LEFT,
        "schema_version": 1,
    }

    assert cogs.unmapped("reminders_birthday", production) == []
    assert cogs.unmapped(
        "reminders_birthday", {**production, "role_id": "77", "image": "a.png"}
    ) == ["image", "role_id"]
    assert cogs.unmapped("welcome_messages", {"anything": 1}) == []


def test_keikos_configs_become_one_document_with_numeric_operator_ids(cogs):
    bot = {"_id": "object-id", "prefix": "ke!", "status": "online", "activity": 0,
           "description": "/help", "owner_id": None}
    admin = {"_id": "object-id", "admin_guild_id": "123", "admin_logs_channel_id": 456}

    assert cogs.keiko_document(bot, admin) == {
        "_id": "keiko",
        "options": {
            "presence": {"status": "online", "activity": 0, "description": "/help"},
            "prefix": "ke!",
            "owner_id": None,
            "operator": {"admin_guild_id": 123, "admin_logs_channel_id": 456},
        },
    }
