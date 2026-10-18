"""Every feature module: documents round-trip, commits write what they report."""

import os
from datetime import datetime, timezone
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from app.constants import Commands, Feature, GuildConstants
from app.settings.features import feature_for, feature_keys
from app.settings.features.feature import CommitContext, GenericCogFeature, OpenContext
from app.settings.form.form_state import Answer
from app.settings.form.form_yaml import DefinitionRegistry, DiskSource
from app.settings.form.responses.responses import (
    document_values,
    item_values,
    items_of,
    list_values,
)
from app.settings.form.responses.summary import unwrap

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


def test_a_feature_declared_without_a_module_gets_the_generic_feature(
    tmp_path, monkeypatch
):
    from enum import Enum

    from app.constants import FeatureSpec
    from app.settings import features
    from app.settings.form.form_yaml import registry

    class Declared(Enum):
        PLAIN_FORM = FeatureSpec(
            key="plain_form",
            group="moderations",
            namespace="plain-form",
            button_key="plain-form",
            emoji="🧪",
            module=None,
        )

    source = registry.source
    (tmp_path / "plain_form.yml").write_text(
        (source.root / "default_roles.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr(features, "Feature", Declared)
    registry.source = type(source)(tmp_path)
    try:
        feature = feature_for("plain_form")
    finally:
        registry.source = source
    assert isinstance(feature, GenericCogFeature) and feature.key == "plain_form"


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
    deps.mongo_client.guild["block_links"].insert_one(dict(document))

    await feature_for("block_links").commit(
        "toggle",
        {"key": "allowed_links", "value": website, "turned_on": turned_on},
        _context(document),
    )

    stored = deps.mongo_client.guild["block_links"].find_one({"guild_id": GUILD_ID})
    assert stored["allowed_links"] == {"style": "bullet", "values": saved}
    event = deps.mongo_client.events["block_links"].find_one({"guild_id": GUILD_ID})
    assert event["event"] == "edited", "a toggle is audited as the edit it was"


async def test_the_streamer_lookup_is_prefetched_for_the_validator(deps):
    deps.twitch.add_user("gaules", user_id="111")
    from app.settings.form.lookups import Lookup

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
    assert result.written == ("reminders_birthday", "birthdays", "moderations")
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
    assert [row.value for row in opened.panel.rows][:2] == ["100", False]
    assert [b.action for b in opened.panel.extra_buttons] == ["aside:stats"]


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
    deps, monkeypatch
):
    """Broke as: an edited streamer kept the old streamer's StreamElements channel,
    so the chat went on answering with the old streamer's commands."""
    from app.settings.features import (
        stream_elements_commands as stream_elements_feature,
    )

    deps.bot.config.is_dev = lambda: False
    monkeypatch.setattr(
        stream_elements_feature.StreamElementsClient,
        "get_channel_info",
        staticmethod(lambda name: {"_id": f"channel-of-{name}"}),
    )
    feature = feature_for("stream_elements_commands")
    saved = {
        "guild_id": GUILD_ID,
        "enabled": True,
        "streamer": "gaules",
        "channel_id": "channel-of-gaules",
    }
    deps.mongo_client.guild["stream_elements_commands"].insert_one(dict(saved))

    result = await feature.commit(
        "edit", {"answers": {"streamer": Answer("cellbit")}}, _context(saved)
    )

    stored = deps.mongo_client.guild["stream_elements_commands"].find_one(
        {"guild_id": GUILD_ID}
    )
    assert stored["streamer"] == "cellbit"
    assert stored["channel_id"] == "channel-of-cellbit"
    assert result.document["channel_id"] == "channel-of-cellbit"


async def test_the_welcome_manager_draws_previews_in_the_background(deps):
    """The design gallery opened to edit a saved welcome message had no previews:
    they were only drawn for a setup."""
    from tests.mocks.discord import create_guild, create_member

    deps.mongo_client.guild["moderations"].insert_one(
        {"guild_id": GUILD_ID, "welcome_messages": True}
    )
    deps.mongo_client.guild["welcome_messages"].insert_one(
        {"guild_id": GUILD_ID, "enabled": True, "welcome_design": "server_blur"}
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
    from app.settings.form import events as ev
    from app.settings.form.actions.action import Context
    from app.settings.form.form import decide
    from app.settings.form.form_state import Origin, Setup, new_session
    from app.settings.form.responses.responses import to_document

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
            "section:1",
            {"inputs": ["Oi {user}!", "Bem-vindo {user}", "", "Chegou!", "Divirta-se"]},
        ),
    )
    session = run(session, ev.Answered("e3", None, "welcome_config"))

    document = to_document(definition.steps, session.answers, "pt-br")
    assert document["welcome_messages_channel"] == {"style": "channel", "values": "101"}
    assert document["welcome_design"] == "server_blur"
    assert document["welcome_messages_title"] == "Oi {user}!"
    assert document["welcome_messages"] == {
        "style": "bullet",
        "values": "Bem-vindo {user};Chegou!",
    }
    assert document["welcome_messages_footer"] == "Divirta-se"
    assert "welcome_config" not in document


async def test_the_stream_elements_lookup_counts_enabled_commands(deps, monkeypatch):
    """The review shows how many commands will load, so the streamer lookup also
    asks StreamElements for the channel and counts its enabled commands."""
    from app.settings.features import (
        stream_elements_commands as stream_elements_feature,
    )
    from app.settings.form.lookups import Lookup

    client = stream_elements_feature.StreamElementsClient
    monkeypatch.setattr(
        client, "get_channel_info", staticmethod(lambda name: {"_id": "se1"})
    )
    monkeypatch.setattr(
        client,
        "get_chat_commands",
        staticmethod(
            lambda channel_id: [
                {"enabled": True, "command": "mouse"},
                {"enabled": False, "command": "chair"},
                {"enabled": True, "command": "setup"},
            ]
        ),
    )
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
    deps, monkeypatch
):
    """The panel says how many commands are loaded and offers to list them."""
    from app.settings.features import (
        stream_elements_commands as stream_elements_feature,
    )

    deps.mongo_client.guild["moderations"].insert_one(
        {"guild_id": GUILD_ID, "stream_elements_commands": True}
    )
    deps.mongo_client.guild["stream_elements_commands"].insert_one(
        {
            "guild_id": GUILD_ID,
            "enabled": True,
            "streamer": "shroud",
        }
    )
    monkeypatch.setattr(
        stream_elements_feature,
        "get_commands_in_cache_or_populate",
        lambda channel_id, user: {"!mouse": "a", "!setup": "b"},
    )

    opened = await feature_for("stream_elements_commands").open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )

    assert "2" in opened.panel.info and "shroud" in opened.panel.info
    actions = [button.action for button in opened.panel.extra_buttons]
    assert actions == ["aside:commands"]


async def test_a_stream_elements_outage_leaves_no_count(deps, monkeypatch):
    from app.settings.features import (
        stream_elements_commands as stream_elements_feature,
    )
    from app.settings.form.lookups import Lookup

    def unreachable(name):
        raise ConnectionError("StreamElements is down")

    monkeypatch.setattr(
        stream_elements_feature.StreamElementsClient,
        "get_channel_info",
        staticmethod(unreachable),
    )
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


FEATURES_FOLDER = os.path.join("app", "settings", "features")


SHARED_MODULES = {"__init__", "feature", "subscriptions"}


LOCALES = ("en-us", "pt-br")


def _specs():
    return [feature.value for feature in Feature]


async def _registered_subgroups():
    from app.cogs import integrations, moderations, notifications

    bot = MagicMock()
    cogs = []
    bot.add_cog = AsyncMock(side_effect=cogs.append)
    for package in (moderations, notifications, integrations):
        await package.setup(bot)

    return {
        (cog.app_command.name, subgroup._locale_name.extras["namespace"])
        for cog in cogs
        for subgroup in cog.app_command.commands
    }


def test_every_form_on_disk_is_a_declared_feature_and_back():
    """Each feature is declared once, and its form, its module and its command agree.

    `Feature` in `app/constants.py` is the one table every feature list derives
    from, except `COMMANDS_LIST`, which wave 3 derives: the key, the slash
    command's group and namespace, the /setup row, what the feature needs and the
    module that saves it. `SETUP_FEATURES`, `FEATURE_COMMANDS`, the moderation
    defaults, `feature_keys` and the module `feature_for` imports come from it;
    what cannot be derived must agree with it: the form YAML named after the key,
    the module the spec names, the subgroup the cogs register under the group with
    the namespace, and the copy.
    """
    assert set(DiskSource().list()) == set(feature_keys())


def test_every_module_a_feature_names_exists_and_saves_that_feature():
    for spec in _specs():
        if spec.module is None:
            continue
        module = import_module(f"app.settings.features.{spec.module}")
        assert module.FEATURE.key == spec.key
        assert feature_for(spec.key) is module.FEATURE


def test_every_feature_module_is_named_by_a_declared_feature():
    modules = {
        filename.removesuffix(".py")
        for filename in os.listdir(FEATURES_FOLDER)
        if filename.endswith(".py")
    }
    named = {spec.module for spec in _specs() if spec.module is not None}
    assert modules - SHARED_MODULES == named


async def test_every_feature_command_is_registered_where_its_spec_says(deps):
    declared = {(spec.group, spec.namespace) for spec in _specs()}
    assert await _registered_subgroups() == declared


def test_the_lists_the_bot_reads_are_derived_from_the_declaration():
    keys = [spec.key for spec in _specs()]
    assert [entry["command_key"] for entry in Commands.SETUP_FEATURES] == keys
    assert list(Commands.FEATURE_COMMANDS) == keys
    defaults = GuildConstants.COGS_MODERATIONS_COMMANDS_DEFAULT
    assert {key for key in defaults if key != GuildConstants.IS_BOT_ONLINE} == set(keys)
    assert [
        entry["command_key"]
        for entry in Commands.SETUP_FEATURES
        if entry.get("answers_prefix")
    ] == [spec.key for spec in _specs() if spec.answers_prefix]


@pytest.mark.parametrize("locale", LOCALES)
def test_every_feature_has_its_command_and_setup_copy(locale):
    def copy(folder):
        path = os.path.join("app", "languages", folder, f"{folder}.{locale}.yml")
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)[locale]

    commands, buttons = copy("commands"), copy("buttons")
    for spec in _specs():
        assert commands["groups"][spec.group]
        assert commands["commands"][spec.namespace]["name"]
        assert commands["commands"][spec.namespace]["subgroup"]
        assert buttons["setup"][spec.button_key]["label"]
        assert commands["commands"]["setup"]["embed"]["features"][spec.button_key]


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


def test_a_list_nothing_was_saved_under_reads_empty():
    assert list_values(DOCUMENT, "notifications") == []
    assert list_values({"custom_links": None}, "custom_links") == []


def test_the_settings_read_as_their_machine_values():
    values = document_values(DOCUMENT)
    assert values["mode"] == "block_all"
    assert values["allowed_chats"] == ["100"]
    assert "guild_id" not in values and "enabled" not in values
