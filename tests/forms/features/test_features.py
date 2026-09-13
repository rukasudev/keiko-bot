"""Every feature module: documents round-trip, commits write what they report."""

from datetime import datetime, timezone

import pytest

from app.forms.definitions.registry import DefinitionRegistry
from app.forms.engine.documents import unwrap
from app.forms.engine.session import Answer
from app.forms.features import feature_for, feature_keys
from app.forms.features.generic import GenericCogFeature
from app.forms.features.protocol import CommitContext, OpenContext

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


@pytest.mark.parametrize("form", sorted(DOCUMENTS))
def test_a_document_survives_the_round_trip_through_answers(form):
    feature = feature_for(form)
    answers = feature.from_document(DOCUMENTS[form])
    document = feature.to_document(answers, "pt-br")
    assert _machine(document) == _machine(DOCUMENTS[form])
    assert "schema_version" not in document and document["enabled"] is True


def test_a_form_without_a_module_gets_the_generic_feature(tmp_path):
    from app.forms.definitions.registry import registry

    source = registry.source
    (tmp_path / "plain_form.yml").write_text(
        (source.root / "default_roles.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    registry.source = type(source)(tmp_path)
    try:
        feature = feature_for("plain_form")
    finally:
        registry.source = source
    assert isinstance(feature, GenericCogFeature) and feature.key == "plain_form"


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


def _context(document=None, locale="pt-br"):
    return CommitContext(GUILD_ID, "555", locale, document or {}, NOW, "slash", "s1")


async def test_a_generic_setup_enables_the_feature_and_stores_the_document(deps):
    feature = feature_for("default_roles")
    answers = {"default_roles_bot": Answer(["201"]), "default_roles": Answer("202")}
    result = await feature.commit("setup", {"answers": answers}, _context())
    assert result.written == ("moderations", "default_roles")
    stored = deps.mongo_client.guild["default_roles"].find_one({"guild_id": GUILD_ID})
    assert stored["default_roles"] == {"style": "role", "values": "202"}
    assert stored["default_roles_bot"] == {"style": "role", "values": ["201"]}
    assert (
        deps.mongo_client.guild["moderations"].find_one({"guild_id": GUILD_ID})[
            "default_roles"
        ]
        is True
    )
    event = deps.mongo_client.events["default_roles"].find_one({"guild_id": GUILD_ID})
    assert event["event"] == "enabled" and event["user_id"] == "555"


async def test_pause_unpause_and_disable_move_the_flags_and_the_document(deps):
    feature = feature_for("default_roles")
    deps.mongo_client.guild["default_roles"].insert_one(
        dict(DOCUMENTS["default_roles"])
    )
    context = _context(DOCUMENTS["default_roles"])
    await feature.commit("pause", {}, context)
    assert (
        deps.mongo_client.guild["default_roles"].find_one({"guild_id": GUILD_ID})[
            "enabled"
        ]
        is False
    )
    assert (
        deps.mongo_client.guild["moderations"].find_one({"guild_id": GUILD_ID})[
            "default_roles"
        ]
        is False
    )
    await feature.commit("unpause", {}, context)
    assert (
        deps.mongo_client.guild["default_roles"].find_one({"guild_id": GUILD_ID})[
            "enabled"
        ]
        is True
    )
    await feature.commit("disable", {}, context)
    assert (
        deps.mongo_client.guild["default_roles"].find_one({"guild_id": GUILD_ID})
        is None
    )
    events = [
        e["event"]
        for e in deps.mongo_client.events["default_roles"].find({"guild_id": GUILD_ID})
    ]
    assert events == ["paused", "unpaused", "disabled"]


async def test_adding_an_item_appends_it_and_subscribes_outside_dev(deps):
    deps.bot.config.is_dev = lambda: False
    deps.twitch.add_user("cellbit", user_id="222")
    feature = feature_for("notifications_twitch")
    document = DOCUMENTS["notifications_twitch"]
    deps.mongo_client.guild["notifications_twitch"].insert_one(dict(document))
    item = {
        "channel": Answer("100"),
        "streamer": Answer("cellbit"),
        "notification_messages": Answer("oi"),
    }
    result = await feature.commit("add_item", {"answers": item}, _context(document))
    stored = deps.mongo_client.guild["notifications_twitch"].find_one(
        {"guild_id": GUILD_ID}
    )
    assert [i["streamer"]["value"] for i in stored["notifications"]["values"]] == [
        "gaules",
        "cellbit",
    ]
    assert stored["notifications"]["values"][1]["streamer"]["title"] == "Streamer"
    assert {c.get("user_id") for c in deps.twitch.subscribe_calls} == {"222"}
    assert (
        result.document["notifications"]["values"][1]["streamer"]["value"] == "cellbit"
    )


async def test_removing_an_item_unsubscribes_it(deps):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.subscribe_to_stream_online_event("111")
    feature = feature_for("notifications_twitch")
    document = DOCUMENTS["notifications_twitch"]
    deps.mongo_client.guild["notifications_twitch"].insert_one(dict(document))
    await feature.commit(
        "remove_item",
        {"index": 0, "item": document["notifications"]["values"][0]},
        _context(document),
    )
    stored = deps.mongo_client.guild["notifications_twitch"].find_one(
        {"guild_id": GUILD_ID}
    )
    assert stored["notifications"]["values"] == []
    assert deps.twitch.unsubscribe_calls


async def test_the_streamer_lookup_is_prefetched_for_the_validator(deps):
    deps.twitch.add_user("gaules", user_id="111")
    feature = feature_for("notifications_twitch")
    context = OpenContext(GUILD_ID, "555", "pt-br")
    assert await feature.prefetch("streamer", {"inputs": ["Gaules"]}, context) == {
        "twitch": {"user_id": "111"}
    }
    assert await feature.prefetch("streamer", {"inputs": ["nobody"]}, context) == {
        "twitch": {"user_id": None}
    }
    assert await feature.prefetch("channel", None, context) == {}


async def test_the_birthday_setup_writes_the_config_and_the_first_member(deps):
    feature = feature_for("reminders_birthday")
    answers = {
        "channel": Answer("100"),
        "timezone": Answer("America/Sao_Paulo"),
        "notification_time": Answer("08:00"),
        "mention_everyone": Answer(False),
        "default_message_mode": Answer("default"),
        "register_now": Answer(True),
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
    config = deps.mongo_client.guild["reminders_birthday"].find_one(
        {"guild_id": GUILD_ID}
    )
    assert config["channel_id"] == "100" and config["notification_time"] == "08:00"
    item = deps.mongo_client.reminders["birthdays"].find_one(
        {"guild_id": GUILD_ID, "user_id": "555"}
    )
    assert item["date"] == "05-12"


async def test_the_birthday_panel_opens_with_its_own_rows(deps):
    from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
    from app.services.moderations import update_moderations_by_guild

    upsert_birthday_config(
        GUILD_ID, "100", False, timezone="America/Sao_Paulo", notification_time="08:00"
    )
    upsert_birthday_item(GUILD_ID, "555", "05-12")
    update_moderations_by_guild(GUILD_ID, "reminders_birthday", True)
    opened = await feature_for("reminders_birthday").open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )
    assert opened.document["reminders_birthday"]["values"][0]["user"]["value"] == "555"
    assert [row.value for row in opened.rows][:2] == ["100", False]
    assert [b.action for b in opened.extra_buttons] == ["aside:stats"]


async def test_an_unconfigured_feature_opens_empty(deps):
    opened = await feature_for("block_links").open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )
    assert opened.document is None and opened.refusal is None
