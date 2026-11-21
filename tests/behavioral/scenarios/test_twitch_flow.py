"""Behavioral scenarios: /notificações twitch lives, from the setup to the panel.

Guaranteed: the setup subscribes the streamer it saves through the real lookup
(typed with or without `@`), the item card shows the streamer's picture and saves
exactly what the notifier reads, a new notice mentions @everyone and Some roles asks
for a role before it saves, the panel lists the roles only while the notice mentions
them (YouTube shares the card), an edit moves the subscription and rewrites the
messages, removing the only streamer asks and turns Twitch off, a removal that raced
another panel drops nothing, and Disable lets every streamer go.

Runs with the production dev gate open (`config.is_dev()` false), so the
subscription hooks run against the recording MockTwitchAPI; only the Twitch HTTP
boundary is fake.
"""

import json

import discord
import pytest

from app.constants import KeikoIcons
from app.core.commands import setup_features
from app.core.utils import command_text, ml
from app.data import data, wait
from tests.behavioral.flows import twitch, youtube
from tests.behavioral.flows.common import GUILD_ID, open_manager, seed_document
from tests.behavioral.harness.copy_keys import copy_text

pytestmark = pytest.mark.behavioral

LOCALE = "pt-br"
GAULES_ID = "181077473"
SHROUD_ID = "37402112"
CARD = "notifications_twitch.form.steps.notifications.steps.notification"


def _section(name):
    return copy_text(f"{CARD}.sections.{name}.label", LOCALE)


def _option(name):
    return copy_text(f"{CARD}.sections.mention.options.{name}.label", LOCALE)


def _texts(scenario):
    return [
        item.content
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _role_selects(scenario):
    return [
        item
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.RoleSelect)
    ]


def _items(form=twitch.FORM):
    return wait(data().guild_cogs.find_cog_by_guild_id(GUILD_ID, form))[
        "notifications"
    ]["values"]


async def _card_to_review(scenario, streamer, messages=None):
    """The item card: channel, streamer and, when given, the messages; then Done."""
    await twitch.pick_channel(scenario)
    await twitch.submit_streamer(scenario, streamer)
    if messages:
        await scenario.click("customize:messages")
        fields = scenario.pending_modal_fields()
        await scenario.submit_modal(dict(zip(fields, messages)))
    await scenario.click("done")


async def _twitch_card(scenario_factory, deps):
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await twitch.pick_channel(scenario)
    await twitch.submit_streamer(scenario, "gaules")
    return scenario


async def _twitch_panel(scenario_factory, deps, document=twitch.ENABLED):
    twitch.known_streamers(deps)
    deps.twitch.subscribe_to_stream_online_event(GAULES_ID)
    seed_document(deps, twitch.FORM, document)
    return await scenario_factory(locale=LOCALE).start_command(twitch.FORM)


def _only(document=twitch.ENABLED):
    return {
        **document,
        "notifications": {
            "style": "composition",
            "values": [twitch.notification("100", "gaules")],
        },
    }


async def test_the_card_shows_the_picture_of_the_streamer_just_typed(
    scenario_factory, deps
):
    """Lucas: once the streamer is typed, show their picture on the card. The
    picture comes from the same lookup that validates the nick, and it is never
    saved with the item. Since beta.2 (P3) it sits under the streamer section,
    like the welcome banner under its field, and the header keeps Keiko's."""
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await twitch.submit_streamer(scenario, "gaules")

    scenario.expect_message(components_v2=True)
    items = list(scenario.current_message.view.walk_children())
    gallery = [
        entry.media.url
        for item in items
        if isinstance(item, discord.ui.MediaGallery)
        for entry in item.items
    ]
    thumbnails = [
        item.media.url for item in items if isinstance(item, discord.ui.Thumbnail)
    ]
    assert any("gaules.jpg" in url for url in gallery), gallery
    assert thumbnails == [KeikoIcons.IMAGE_01], thumbnails
    await scenario.finish()


async def test_the_message_preview_walks_through_every_text():
    """The preview shows one written message per click, and wraps around."""
    from unittest.mock import AsyncMock, MagicMock

    from app.ui.message_preview import MessagePreviewView

    view = MessagePreviewView(["está ao vivo!", "chegou na live", "bora?"], LOCALE)

    async def press():
        interaction = MagicMock()
        interaction.response.edit_message = AsyncMock()
        await view.next_message.callback(interaction)
        return interaction.response.edit_message.await_args.kwargs["content"]

    assert view.content == "está ao vivo!"
    assert await press() == "chegou na live"
    assert await press() == "bora?"
    assert await press() == "está ao vivo!"


async def test_full_setup_subscribes_streamer(scenario_factory, deps, outside_dev):
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await _card_to_review(scenario, "gaules")

    scenario.expect_step("confirm")
    await scenario.confirm()

    assert GAULES_ID in [call.get("user_id") for call in deps.twitch.subscribe_calls]
    document = scenario.expect_persisted(
        "guild", twitch.FORM, {"guild_id": GUILD_ID}, {"enabled": True}
    )
    streamers = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert streamers == ["gaules"]
    await scenario.finish()


async def test_a_streamer_typed_with_an_at_sign_is_found_and_saved_without_it(
    scenario_factory, deps, outside_dev
):
    """The placeholder reads "your nick (e.g. @shroud)", so admins type the @:
    the lookup, the subscription and the saved item all use the bare nick."""
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await _card_to_review(scenario, " @Gaules ")
    scenario.expect_step("confirm")
    await scenario.confirm()

    assert GAULES_ID in [call.get("user_id") for call in deps.twitch.subscribe_calls]
    document = scenario.expect_persisted(
        "guild", twitch.FORM, {"guild_id": GUILD_ID}, {"enabled": True}
    )
    streamers = [i["streamer"]["value"] for i in document["notifications"]["values"]]
    assert streamers == ["gaules"]
    await scenario.finish()


async def test_an_item_card_saves_the_item_the_notifier_reads(
    scenario_factory, deps, outside_dev
):
    """The notifier reads channel.value, streamer.value and a ;-joined
    notification_messages.value it splits: the item card must save exactly that."""
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await _card_to_review(
        scenario,
        "gaules",
        messages=["{streamer} on! {stream_link}", "Corre, {streamer}!"],
    )
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", twitch.FORM, {"guild_id": GUILD_ID}, {"enabled": True}
    )
    [item] = document["notifications"]["values"]
    assert item["streamer"]["value"] == "gaules"
    assert str(item["channel"]["value"]).isdigit()
    assert item["notification_messages"]["value"] == (
        "{streamer} on! {stream_link};Corre, {streamer}!"
    )
    assert "notification" not in item
    await scenario.finish()


async def test_a_new_notice_starts_on_everyone_and_writes_it_in_front(
    scenario_factory, deps
):
    scenario = await _twitch_card(scenario_factory, deps)

    assert f"📣 **{_section('mention')}**\n> {_option('everyone')}" in _texts(scenario)
    assert not _role_selects(scenario)
    await scenario.click("done")
    await scenario.confirm()

    notice = _items()[0]
    assert notice["mention"]["_raw_value"] == "everyone"
    assert notice["notification_messages"]["value"] == (
        "gaules está ao vivo! https://twitch.tv/gaules"
    ), "the message keeps no @everyone of its own"
    await scenario.finish()


async def test_some_roles_asks_for_a_role_before_it_saves(scenario_factory, deps):
    scenario = await _twitch_card(scenario_factory, deps)

    await scenario.select_option("roles", target="mention")
    assert len(_role_selects(scenario)) == 1
    await scenario.click("done")
    refused = json.dumps(scenario.outputs[-1], ensure_ascii=False)
    assert _section("mention_roles") in refused, "Done names the role select it needs"
    scenario.expect_step("notification")

    role = next(role for role in scenario.guild.roles if role.name == "Member")
    await scenario.select_option("Member", target="mention_roles")
    await scenario.click("done")
    await scenario.confirm()

    notice = _items()[0]
    assert notice["mention"]["_raw_value"] == "roles"
    assert notice["mention_roles"]["value"] == str(role.id)
    await scenario.finish()


@pytest.mark.parametrize("feature", [twitch, youtube], ids=["twitch", "youtube"])
@pytest.mark.parametrize(
    "mention, option, lists_roles",
    [
        ({"value": "@everyone", "_raw_value": "everyone"}, "everyone", False),
        ({"value": "Alguns cargos", "_raw_value": "roles"}, "roles", True),
    ],
    ids=["everyone-with-roles-kept", "some-roles"],
)
async def test_the_panel_lists_the_roles_only_while_the_notice_mentions_them(
    scenario_factory, deps, feature, mention, option, lists_roles
):
    roles_line = _section("mention_roles")
    document = json.loads(json.dumps(feature.ENABLED))
    first = document["notifications"]["values"][0]
    first["mention"] = {**mention, "title": _section("mention")}
    first["mention_roles"] = {"value": ["201"], "title": roles_line, "style": "role"}

    def seed(deps):
        known = getattr(feature, "known_streamers", None) or feature.known_channels
        known(deps)
        seed_document(deps, feature.FORM, document)

    scenario = await open_manager(scenario_factory, deps, LOCALE, feature.FORM, seed)

    panel = "\n".join(_texts(scenario))
    assert f"📣 **{_section('mention')}:** {_option(option)}" in panel
    assert (f"**{roles_line}:** <@&201>" in panel) is lists_roles
    assert (roles_line in panel) is lists_roles
    await scenario.finish()


async def test_editing_an_item_from_its_own_edit_moves_the_subscription(
    scenario_factory, deps, outside_dev
):
    scenario = await _twitch_panel(scenario_factory, deps)

    await scenario.click("section:notifications$0")
    await twitch.submit_streamer(scenario, "shroud")
    await scenario.click("done")

    assert SHROUD_ID in [call.get("user_id") for call in deps.twitch.subscribe_calls]
    assert deps.twitch.unsubscribe_calls, "the old streamer is unsubscribed"
    await scenario.finish()


async def test_editing_the_streamer_of_a_saved_item_rewrites_its_messages(
    scenario_factory, deps
):
    """tw-5 (beta.2): when the streamer changes on a saved item, the old name and
    link written in its messages become the new ones, like in the setup."""
    written = "gaules está ao vivo! https://twitch.tv/gaules"
    notice = {
        **twitch.notification("100", "gaules"),
        "notification_messages": {"value": written, "style": "bullet"},
    }
    document = {
        **twitch.ENABLED,
        "notifications": {"style": "composition", "values": [notice]},
    }
    scenario = await _twitch_panel(scenario_factory, deps, document)

    await scenario.click("section:notifications$0")
    await twitch.submit_streamer(scenario, "cellbit")
    await scenario.click("done")

    item = _items()[0]
    assert item["streamer"]["value"] == "cellbit"
    assert item["notification_messages"]["value"] == (
        "cellbit está ao vivo! https://twitch.tv/cellbit"
    )
    await scenario.finish()


async def test_a_streamer_saved_by_the_old_pause_is_edited_like_any_other(
    scenario_factory, deps, outside_dev
):
    """Round 3 (N1) took the per-item pause off: an item a dev panel saved with
    `enabled: false` loads, notifies and edits like any other."""
    paused = {**twitch.notification("100", "gaules"), "enabled": False}
    document = {
        **twitch.ENABLED,
        "notifications": {
            "style": "composition",
            "values": [paused, twitch.notification("102", "cellbit")],
        },
    }
    scenario = await _twitch_panel(scenario_factory, deps, document)

    await scenario.click("section:notifications$0")
    await twitch.submit_streamer(scenario, "shroud")
    await scenario.click("done")

    assert _items()[0]["streamer"]["value"] == "shroud"
    assert "enabled" not in _items()[0], "the old field is not carried over"
    subscribed = [call.get("user_id") for call in deps.twitch.subscribe_calls]
    assert SHROUD_ID in subscribed, "the new streamer is subscribed as usual"
    await scenario.finish()


async def test_removing_the_only_streamer_asks_and_turns_twitch_off(
    scenario_factory, deps, outside_dev
):
    """N5 (beta.2 round 2): a Remove beside each item; removing the only one warns
    with the item and the feature's name, then turns the feature off."""
    scenario = await _twitch_panel(scenario_factory, deps, _only())
    feature = next(spec for spec in setup_features() if spec.key == twitch.FORM)

    await scenario.click("remove_this:notifications$0")
    scenario.expect_message(
        title_contains=copy_text("commands.responses.remove.overrides.1.title", LOCALE)
    )
    [warning] = scenario.current_message.embeds
    head, tail = copy_text(
        "notifications_twitch.form.steps.notifications.last_item_warning",
        LOCALE,
        feature=command_text(feature, "command.setup.label", LOCALE),
    ).split("$item")
    assert warning.description.startswith(head)
    assert warning.description.endswith(tail)
    assert f"**{_section('streamer')}:** gaules" in warning.description
    await scenario.click("remove_items")

    assert wait(data().guild_cogs.find_cog_by_guild_id(GUILD_ID, twitch.FORM)) is None
    assert deps.twitch.unsubscribe_calls, "the feature off lets gaules go"
    scenario.expect_message(
        title_contains=ml("commands.responses.disabled.title", locale=LOCALE)
    )


async def test_removing_the_only_streamer_after_another_panel_added_one_drops_nothing(
    scenario_factory, deps, outside_dev
):
    scenario = await _twitch_panel(scenario_factory, deps, _only())
    await scenario.click("remove_this:notifications$0")
    deps.mongo_client.guild.cogs.update_one(
        {"_id": f"{GUILD_ID}:{twitch.FORM}"},
        {"$set": {"options.notifications.values": [
            twitch.notification("100", "gaules"),
            twitch.notification("102", "cellbit"),
        ]}},
    )

    await scenario.click("remove_items")

    assert [item["streamer"]["value"] for item in _items()] == ["gaules", "cellbit"]
    assert deps.twitch.unsubscribe_calls == []
    await scenario.finish()


async def test_disable_unsubscribes_streamers(scenario_factory, deps, outside_dev):
    scenario = await _twitch_panel(scenario_factory, deps, _only())

    await scenario.click("disable")
    await scenario.click("delete_all")

    assert deps.twitch.unsubscribe_calls, "disable must unsubscribe streamers"
    scenario.expect_not_persisted("guild", twitch.FORM, {"guild_id": GUILD_ID})
