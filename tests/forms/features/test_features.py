"""Every feature module: documents round-trip, commits write what they report."""

import copy
import os
from datetime import date, datetime, timezone
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from app.constants import Commands, Feature
from app.data.guild.cogs import find_cog_by_guild_id
from app.settings.features import feature_for, feature_keys
from app.settings.features.feature import (
    CommitContext,
    GenericCogFeature,
    ListChanged,
    OpenContext,
)
from app.settings.form.form_state import Answer
from app.settings.form.form_yaml import DefinitionRegistry, DiskSource
from app.settings.form.responses.responses import (
    document_values,
    item_values,
    items_of,
    list_values,
)
from app.settings.form.responses.summary import unwrap
from tests.generators.moderations import seed_cog

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
    assert result.written == ("default_roles",)
    stored = find_cog_by_guild_id(GUILD_ID, "default_roles")
    assert stored["default_roles"] == {"style": "role", "values": "202"}
    assert stored["default_roles_bot"] == {"style": "role", "values": ["201"]}
    assert stored["enabled"] is True
    event = deps.mongo_client.events["default_roles"].find_one({"guild_id": GUILD_ID})
    assert event["event"] == "enabled" and event["user_id"] == "555"


def _guild_with(members):
    """A server whose gateway cache holds nobody: its members come from the API."""
    from types import SimpleNamespace

    async def fetch_members(limit=None):
        for member in members:
            yield member

    return SimpleNamespace(
        id=int(GUILD_ID),
        members=[],
        member_count=len(members),
        roles=[],
        fetch_members=fetch_members,
    )


async def test_a_default_roles_setup_gives_the_roles_out_after_the_commit(
    deps, monkeypatch
):
    """dr-2: "yes" on the setup's toggle gives the saved roles to who is already
    in the server, after the commit and without the commit waiting for it."""
    import asyncio
    from types import SimpleNamespace

    from app.settings.features import default_roles

    released, given = asyncio.Event(), []

    async def give(document, guild, members):
        await released.wait()
        given.append((document["default_roles"], guild, [m async for m in members]))

    monkeypatch.setattr(default_roles, "set_default_roles", give)
    members = [SimpleNamespace(bot=False), SimpleNamespace(bot=True)]
    guild = _guild_with(members)
    answers = {"default_roles": Answer("202"), "sync_existing": Answer(True)}
    context = CommitContext(GUILD_ID, "555", "pt-br", {}, NOW, "slash", "s1", guild)

    result = await feature_for("default_roles").commit(
        "setup", {"answers": answers}, context
    )

    assert result.written == ("default_roles",)
    assert given == [], "the commit never waits for the roles to be given"
    stored = find_cog_by_guild_id(GUILD_ID, "default_roles")
    assert "sync_existing" not in stored, "the choice is never saved as a setting"
    released.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert given == [({"style": "role", "values": "202"}, guild, members)]


async def test_a_default_roles_setup_without_the_toggle_gives_nothing_out(
    deps, monkeypatch
):
    from app.settings.features import default_roles

    give = AsyncMock()
    monkeypatch.setattr(default_roles, "set_default_roles", give)
    answers = {"default_roles": Answer("202"), "sync_existing": Answer(False)}
    context = CommitContext(
        GUILD_ID, "555", "pt-br", {}, NOW, "slash", "s1", _guild_with([])
    )

    await feature_for("default_roles").commit("setup", {"answers": answers}, context)

    give.assert_not_called()


async def test_a_default_roles_sync_that_fails_is_logged_not_raised(deps, monkeypatch):
    import asyncio

    from app.settings.features import default_roles

    async def refuse(document, guild, members):
        raise RuntimeError("Missing Permissions")

    errors = MagicMock()
    monkeypatch.setattr(default_roles, "set_default_roles", refuse)
    monkeypatch.setattr("app.settings.features.feature.logger.error", errors)
    answers = {"default_roles": Answer("202"), "sync_existing": Answer(True)}
    context = CommitContext(
        GUILD_ID, "555", "pt-br", {}, NOW, "slash", "s1", _guild_with([])
    )

    await feature_for("default_roles").commit("setup", {"answers": answers}, context)
    for _ in range(5):
        await asyncio.sleep(0)

    assert errors.call_count == 1
    assert "Missing Permissions" in errors.call_args.args[0]


async def test_a_default_roles_setup_opens_with_the_server_totals(deps, monkeypatch):
    from types import SimpleNamespace

    from app.settings.features import default_roles

    monkeypatch.setattr(
        default_roles, "get_available_roles_by_guild", lambda guild: {"Member": "202"}
    )
    people = [SimpleNamespace(bot=False) for _ in range(3)]
    guild = _guild_with([*people, SimpleNamespace(bot=True)])

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
    assert find_cog_by_guild_id(GUILD_ID, "default_roles")["enabled"] is False
    await feature.commit("unpause", {}, context)
    assert find_cog_by_guild_id(GUILD_ID, "default_roles")["enabled"] is True
    await feature.commit("disable", {}, context)
    assert find_cog_by_guild_id(GUILD_ID, "default_roles") is None
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
    seed_cog(deps.mongo_client, "notifications_twitch", dict(document))
    item = {
        "channel": Answer("100"),
        "streamer": Answer("cellbit"),
        "notification_messages": Answer("oi"),
    }
    result = await feature.commit("add_item", {"answers": item}, _context(document))
    stored = find_cog_by_guild_id(GUILD_ID, "notifications_twitch")
    assert [i["streamer"]["value"] for i in stored["notifications"]["values"]] == [
        "gaules",
        "cellbit",
    ]
    assert stored["notifications"]["values"][1]["streamer"]["title"] == "Streamer"
    assert {c.get("user_id") for c in deps.twitch.subscribe_calls} == {"222"}
    assert (
        result.document["notifications"]["values"][1]["streamer"]["value"] == "cellbit"
    )


def _two_streamers() -> dict:
    document = copy.deepcopy(DOCUMENTS["notifications_twitch"])
    first = document["notifications"]["values"][0]
    second = copy.deepcopy(first)
    second["streamer"]["value"] = "cellbit"
    document["notifications"]["values"].append(second)
    return document


async def test_removing_an_item_unsubscribes_it(deps):
    deps.twitch.add_user("gaules", user_id="111")
    deps.twitch.subscribe_to_stream_online_event("111")
    feature = feature_for("notifications_twitch")
    document = _two_streamers()
    seed_cog(deps.mongo_client, "notifications_twitch", copy.deepcopy(document))
    await feature.commit("remove_item", {"indexes": [0]}, _context(document))
    stored = find_cog_by_guild_id(GUILD_ID, "notifications_twitch")
    assert [
        item["streamer"]["value"] for item in stored["notifications"]["values"]
    ] == ["cellbit"]
    assert deps.twitch.unsubscribe_calls


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

    stored = find_cog_by_guild_id(GUILD_ID, "notifications_twitch")
    names = [item["streamer"]["value"] for item in stored["notifications"]["values"]]
    assert names == ["cellbit"]
    assert len(deps.twitch.unsubscribe_calls) >= 2


async def test_a_removal_saves_nothing_when_one_chosen_item_changed_meanwhile(deps):
    document = _two_streamers()
    saved = copy.deepcopy(document)
    saved["notifications"]["values"][1]["streamer"]["value"] = "shroud"
    seed_cog(deps.mongo_client, "notifications_twitch", copy.deepcopy(saved))

    with pytest.raises(ListChanged):
        await feature_for("notifications_twitch").commit(
            "remove_item", {"indexes": [0, 1]}, _context(document)
        )

    stored = find_cog_by_guild_id(GUILD_ID, "notifications_twitch")
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

    stored = find_cog_by_guild_id(GUILD_ID, "block_links")
    assert stored["allowed_links"] == {"style": "bullet", "values": saved}
    event = deps.mongo_client.events["block_links"].find_one({"guild_id": GUILD_ID})
    assert event["event"] == "edited", "a toggle is audited as the edit it was"


async def test_the_youtuber_picture_is_the_medium_thumbnail(deps, monkeypatch):
    """P3 (beta.2): the 88 px default thumbnail blurs under the card section."""
    from app.settings.form.lookups import Lookup

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
    config = find_cog_by_guild_id(GUILD_ID, "reminders_birthday")
    assert config["channel"] == "100" and config["notification_time"] == "08:00"
    assert config["enabled"] is True
    item = deps.mongo_client.guild["members"].find_one({"_id": f"{GUILD_ID}:555"})
    assert item["options"]["reminders_birthday"]["date"] == "05-12"


async def test_the_birthday_panel_opens_with_its_own_rows(deps):
    from app.core.servers import set_feature_enabled
    from app.services.reminders_birthdays import save_birthday_config
    from tests.generators.birthdays import saved_birthday

    save_birthday_config(GUILD_ID, "100", False, notification_time="08:00")
    saved_birthday(GUILD_ID, "555", "05-12")
    set_feature_enabled(GUILD_ID, "reminders_birthday", True)
    opened = await feature_for("reminders_birthday").open(
        OpenContext(GUILD_ID, "555", "pt-br")
    )
    assert opened.document["reminders_birthday"]["values"][0]["user"]["value"] == "555"
    assert [row.value for row in opened.panel.rows][:2] == ["100", False]
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
        "state": {"channel_id": "channel-of-gaules"},
    }
    seed_cog(deps.mongo_client, "stream_elements_commands", dict(saved))

    result = await feature.commit(
        "edit", {"answers": {"streamer": Answer("cellbit")}}, _context(saved)
    )

    stored = find_cog_by_guild_id(GUILD_ID, "stream_elements_commands")
    assert stored["streamer"] == "cellbit"
    assert stored["state"]["channel_id"] == "channel-of-cellbit"
    assert result.document["state"]["channel_id"] == "channel-of-cellbit"


async def test_a_setup_saves_the_stream_elements_channel_in_dev(deps, monkeypatch):
    """Broke as: the setup skipped the StreamElements channel when `is_dev()`, so
    the dev bot saved a streamer without a channel and answered no command."""
    from app.settings.features import (
        stream_elements_commands as stream_elements_feature,
    )

    deps.bot.config.is_dev = lambda: True
    asked = []
    monkeypatch.setattr(
        stream_elements_feature.StreamElementsClient,
        "get_channel_info",
        staticmethod(lambda name: asked.append(name) or {"_id": f"channel-of-{name}"}),
    )
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
    stored = find_cog_by_guild_id(GUILD_ID, "stream_elements_commands")
    assert stored["state"]["channel_id"] == "se-shroud"
    assert looked_up.document["state"]["channel_id"] == "se-shroud"
    assert asked == [], "the channel the streamer lookup found was looked up again"

    deps.mongo_client.guild.cogs.delete_one(
        {"_id": f"{GUILD_ID}:stream_elements_commands"}
    )
    await feature.commit(
        "setup", {"answers": {"streamer": Answer("gaules")}}, _context()
    )
    stored = find_cog_by_guild_id(GUILD_ID, "stream_elements_commands")
    assert stored["state"]["channel_id"] == "channel-of-gaules"
    assert asked == ["gaules"]


async def test_the_welcome_manager_draws_previews_in_the_background(deps):
    """The design gallery opened to edit a saved welcome message had no previews:
    they were only drawn for a setup."""
    from tests.mocks.discord import create_guild, create_member

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
        "style": "code-block",
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

    seed_cog(
        deps.mongo_client,
        "stream_elements_commands",
        {
            "guild_id": GUILD_ID,
            "enabled": True,
            "streamer": "shroud",
        },
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
    assert opened.panel.info_action is not None
    assert opened.panel.info_action.action == "aside:commands"
    assert not opened.panel.extra_buttons, "the list opens from inside the card (N1)"


async def test_the_stream_elements_panel_tells_no_commands_from_a_failure(
    deps, monkeypatch
):
    """Broke as: the panel's count read a streamer with no active command and a
    StreamElements failure alike, as "I couldn't reach StreamElements"."""
    from app.core.utils import ml
    from app.services import stream_elements as stream_elements_service

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
    client = stream_elements_service.StreamElementsClient
    namespace = "commands.commands.commons.stream-elements-manager"

    async def panel_line():
        opened = await feature_for("stream_elements_commands").open(
            OpenContext(GUILD_ID, "555", "pt-br")
        )
        return opened.panel.info

    monkeypatch.setattr(client, "get_chat_commands", staticmethod(lambda channel: []))
    empty = await panel_line()
    deps.redis_client.delete("stream_elements:channel:c1:commands")

    def down(channel):
        raise ConnectionError("StreamElements is down")

    monkeypatch.setattr(client, "get_chat_commands", staticmethod(down))
    failed = await panel_line()

    assert empty == ml(f"{namespace}.commands.empty", "pt-br").replace(
        "$streamer", "shroud"
    )
    assert failed == ml(f"{namespace}.loaded.unknown", "pt-br")


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
    from: the key, the slash command's group and namespace, the /setup row, what
    the feature needs and the module that saves it. `SETUP_FEATURES`,
    `FEATURE_COMMANDS`, `COMMANDS_LIST`, the moderation defaults, `feature_keys`
    and the module `feature_for` imports come from it;
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
    assert Commands.COMMANDS_LIST == keys
    assert list(Commands.FEATURE_COMMANDS) == keys
    assert len({entry["command_key"] for entry in Commands.SETUP_FEATURES}) == len(keys)
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


@pytest.mark.parametrize(
    "today, first_page, next_one",
    [
        (date(2026, 6, 20), "- <@777>: 12 de junho", "<@555>"),
        (date(2026, 5, 20), "- <@555>: 12 de maio", "<@777>"),
    ],
    ids=["june", "may"],
)
async def test_the_birthday_panel_counts_from_the_day_its_clock_gives(
    deps, monkeypatch, today, first_page, next_one
):
    """The panel's next birthdays and the month it opens on follow the
    feature's clock (`BirthdayFeature.today`), never the machine's date."""
    from app.services.reminders_birthdays import save_birthday_config
    from app.settings.features.reminders_birthday import FEATURE
    from tests.generators.birthdays import saved_birthday

    save_birthday_config(GUILD_ID, "100", False, notification_time="08:00")
    saved_birthday(GUILD_ID, "555", "05-12")
    saved_birthday(GUILD_ID, "777", "06-12")
    monkeypatch.setattr(FEATURE, "today", lambda: today)

    opened = await FEATURE.open(OpenContext(GUILD_ID, "555", "pt-br"))

    pages = opened.panel.pages
    assert (pages[0].title, pages[0].lines[0]) == ("Aniversários", first_page)
    upcoming = next(row for row in opened.panel.rows if "Próximos" in row.title)
    assert upcoming.value[0].startswith(next_one), upcoming.value


async def test_the_birthday_panel_shows_its_list_even_with_no_birthday(deps):
    """N2: with no birthday the panel kept no block for the list, so its Add
    sat alone in the bottom row; the list keeps its title and says it is empty."""
    from app.services.reminders_birthdays import save_birthday_config
    from app.settings.features.reminders_birthday import FEATURE

    save_birthday_config(GUILD_ID, "100", False)

    opened = await FEATURE.open(OpenContext(GUILD_ID, "555", "pt-br"))

    [page] = opened.panel.pages
    assert (page.title, page.lines) == ("Aniversários", ())
    assert page.description.startswith("Ainda não tenho nenhum aniversário")


async def test_a_found_member_shows_whose_messages_greet_them(deps):
    """N2: the member's screen shows the messages that will celebrate them,
    labelled by whose they are: the member's own, the server's default, or
    Keiko's default when nobody wrote one; the picture is the platform's, read
    from the server's members (review fixes K2)."""
    from app.services.reminders_birthdays import save_birthday_config
    from app.settings.features.reminders_birthday import FEATURE
    from tests.generators.birthdays import saved_birthday
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=777, name="Amiga")
    save_birthday_config(GUILD_ID, "100", False)
    saved_birthday(GUILD_ID, "777", "06-12")
    saved_birthday(
        GUILD_ID,
        "888",
        "07-01",
        message={"mode": "custom", "title": "Oi {user}", "content": "Parabéns!"},
    )

    opened = await FEATURE.open(OpenContext(GUILD_ID, "555", "pt-br", guild))
    found = opened.panel.found

    keiko = "💬 **Mensagens de parabéns (padrão do Keiko):**\n```• 🎂 É aniversário"
    assert found["777"].lines[0].startswith(keiko)
    assert found["888"].lines == (
        "💬 **Mensagens de parabéns:**\n```• Oi {user}\n• Parabéns!```",
    )

    save_birthday_config(
        GUILD_ID,
        "100",
        False,
        default_message={"mode": "custom", "title": "Viva", "content": "Hoje!"},
    )
    reopened = await FEATURE.open(OpenContext(GUILD_ID, "555", "pt-br", guild))
    assert reopened.panel.found["777"].lines == (
        "💬 **Mensagens de parabéns (padrão do servidor):**\n```• Viva\n• Hoje!```",
    )
