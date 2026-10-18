"""Regressions: manager summary rendering for commands that share the
manager infrastructure.
"""
import pytest
import discord
from tests.behavioral.harness.locators import walk_items
from tests.mocks.discord import create_guild, create_member
from tests.behavioral.golden.paths.common import GUILD_ID, label, seed_document
from tests.behavioral.harness import locators
from types import SimpleNamespace

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]


async def test_manager_form_keeps_rendering_existing_config_for_all_consumers(
        scenario_factory):
    """What broke: while developing the birthday-reminder flow, shared
    summary rendering (then `parse_settings_with_database_values` in
    app/services/utils.py, today `panel_rows` in app/settings/form/actions/manage.py)
    was changed and stopped resolving titles for keys nested inside
    `multi_select.selects[]`. The birthday flow kept
    working; commands like /moderations default roles silently lost their
    saved configuration in the manager panel and the break was only found
    by manually running those commands in Discord.

    Guaranteed behavior: a command whose values live in nested multi_select
    keys must keep showing every saved value, with its localized label and
    proper mention formatting, in the manager summary.
    """
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "default_roles",
        {
            "guild_id": "123456789", "enabled": True,
            "default_roles": {"style": "role", "values": "201"},
            "default_roles_bot": {"style": "role", "values": ["202", "203"]},
        },
    )
    scenario.expect_configuration_values(
        "Cargos para Membros", "<@&201>",
        "Cargos para Bots", "<@&202>", "<@&203>",
    )


async def test_adding_a_new_consumer_must_not_drop_other_forms_saved_values(
        scenario_factory):
    """What broke: same incident as above, seen from the other side — the
    NEW consumer (birthday) worked, so its tests passed, while EXISTING
    consumers regressed unnoticed.

    Guaranteed behavior: the representative consumers of the manager view
    render their saved values side by side in one process, so a change that
    helps one consumer but breaks another fails here immediately.
    """
    from app.services.block_links import normalize_block_links_config

    roles = await scenario_factory(locale="pt-br").start_manager(
        "default_roles",
        {"guild_id": "1", "enabled": True,
         "default_roles": {"style": "role", "values": "201"}},
    )
    links = await scenario_factory(locale="pt-br").start_manager(
        "block_links",
        normalize_block_links_config(
            {"guild_id": "2", "enabled": True,
             "allowed_chats": {"style": "channel", "values": "100"},
             "allowed_links": ["Youtube"]}
        ),
    )
    roles.expect_configuration_values("<@&201>")
    # Legacy labels render as translated domains since the card-first redesign.
    links.expect_configuration_values("<#100>", "youtube.com")


def _texts(message):
    texts = [embed.description or "" for embed in message.embeds or []]
    if message.view is not None:
        texts += [
            item.content
            for item in walk_items(message.view)
            if isinstance(item, discord.ui.TextDisplay)
        ]
    return texts


async def test_a_multi_line_value_is_never_wrapped_in_bold(scenario_factory, deps):
    """A value of several lines on the review is never wrapped in bold.

    On the local bot (2026-09-15) the Twitch review showed "Mensagens de Notificação"
    as `**` around a code block: Discord printed the asterisks. The review wrapped
    every value in bold, and a list of messages formats as a code block.

    Shared behaviour: the review screen of every form. Consumer that exposed it:
    notifications_twitch. Guaranteed: no review text puts `**` around a block.
    """
    deps.twitch.add_user("gaules", user_id="111")
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})
    await scenario.click("customize:2")
    fields = scenario.pending_modal_fields()
    await scenario.submit_modal(
        {fields[0]: "{streamer} on!", fields[1]: "{stream_link}"}
    )
    await scenario.click("done")
    scenario.expect_step("confirm")

    joined = "\n".join(_texts(scenario.current_message))
    assert "gaules" in joined
    assert "**\n```" not in joined and "```**" not in joined
    await scenario.finish()


DAY_FIELD = {"pt-br": "Dia", "en-us": "Day"}


def _guild():
    guild = create_guild()
    create_member(guild, id=777, name="Amiga", roles=["Member"])
    return guild


async def _complete_settings_card(scenario):
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.select_option("America/Sao_Paulo")
    await scenario.click("customize:2")
    await scenario.click("08:00")
    await scenario.click("done")


async def _complete_member_card(scenario, locale, month="05", day="12"):
    await scenario.click("customize:0")
    await scenario.select_option(month)
    await scenario.click("customize:1")
    await scenario.submit_modal({DAY_FIELD[locale]: day})
    await scenario.click("done")


async def test_a_member_added_from_the_review_is_listed_on_the_review(
    scenario_factory,
):
    """An item added from the review shows up on the review.

    On the local bot (2026-09-16) adding a member with the review's Add button
    left the birthday list on the review unchanged: the member was saved, but the
    review still listed the first one only.

    Shared behaviour: the review screen of every form with a composition.
    Consumer that exposed it: reminders_birthday. Guaranteed: after a child
    session adds an item, the review lists it.
    """
    locale = "pt-br"
    scenario = await scenario_factory(locale=locale, guild=_guild()).start(
        "reminders_birthday"
    )
    await scenario.confirm()
    await _complete_settings_card(scenario)
    await scenario.click("Sim")
    await scenario.select_option("Tester")
    await scenario.confirm()
    await _complete_member_card(scenario, locale)
    scenario.expect_step("confirm")

    await scenario.click("add")
    await scenario.select_option("Amiga")
    await scenario.confirm()
    await _complete_member_card(scenario, locale, month="11", day="03")

    scenario.expect_step("confirm")
    joined = "\n".join(_texts(scenario.current_message))
    assert "<@555>" in joined
    assert "<@777>" in joined


async def test_the_first_member_added_from_the_review_is_listed_on_the_review(
    scenario_factory,
):
    """The list was left for later, so the review has no list block yet."""
    locale = "pt-br"
    scenario = await scenario_factory(locale=locale, guild=_guild()).start(
        "reminders_birthday"
    )
    await scenario.confirm()
    await _complete_settings_card(scenario)
    await scenario.click("Depois")
    scenario.expect_step("confirm")

    await scenario.click("add")
    await scenario.select_option("Amiga")
    await scenario.confirm()
    await _complete_member_card(scenario, locale, month="11", day="03")

    scenario.expect_step("confirm")
    joined = "\n".join(_texts(scenario.current_message))
    assert "<@777>" in joined


FORM = "block_links"


LOCALE = "en-us"


LINK_FIELD = "Type the link or website"


async def _review_with_one_link(scenario_factory, link):
    scenario = await scenario_factory(locale=LOCALE).start_command(FORM)
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click(label("yes", LOCALE))
    await scenario.submit_modal({LINK_FIELD: link})
    await scenario.select_option("general", target="allowed_chats")
    await scenario.confirm()
    scenario.expect_step("confirm")
    return scenario


def _review_text(scenario):
    view = scenario.current_message.view
    return "\n".join(
        item.content
        for item in walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    )


def _saved_links(scenario):
    document = scenario.get_persisted("guild", FORM, {"guild_id": GUILD_ID})
    return [item["link"]["value"] for item in document["custom_links"]["values"]]


@pytest.mark.regression
@pytest.mark.shared_contract("form_engine")
async def test_a_link_added_again_in_a_setup_takes_the_place_of_the_one_listed(
    scenario_factory,
):
    """A list built during a setup holds each item once, by the key that makes it unique.

    Broke as: the form engine's merge of a finished item compared `index != index`, a
    loop variable shadowing the parameter it meant, so the check for an item already
    listed under the same `unique_by` value never matched. Adding a link the setup
    already listed appended it again, and the review and the saved document showed
    it twice.

    Shared behaviour: the merge of a child session's item into its parent's list
    (`merge_item` in the form engine), used by the review's Add and Edit of every
    form whose list declares `unique_by` (block_links by link, reminders_birthday by
    member). Exposed by: block_links, adding the same website twice before
    confirming.

    Guaranteed: during a setup, an item whose unique value is already listed takes
    that item's place instead of being added again; the review lists it once and the
    document the setup saves holds it once. A different value is still added beside
    the first.
    """
    scenario = await _review_with_one_link(scenario_factory, "meusite.com.br")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "meusite.com.br"})

    scenario.expect_step("confirm")
    assert _review_text(scenario).count("meusite.com.br") == 1

    await scenario.confirm()

    assert _saved_links(scenario) == ["meusite.com.br"]


@pytest.mark.regression
@pytest.mark.shared_contract("form_engine")
async def test_a_different_link_added_in_a_setup_is_listed_beside_the_first(
    scenario_factory,
):
    scenario = await _review_with_one_link(scenario_factory, "meusite.com.br")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "docs.example.org"})
    await scenario.confirm()

    assert _saved_links(scenario) == ["meusite.com.br", "docs.example.org"]


MESSAGE = "{streamer} esta ao vivo!"


async def _review_with_one_streamer(scenario_factory, deps):
    deps.twitch.add_user("gaules", user_id="111")
    scenario = await scenario_factory(locale="pt-br").start("notifications_twitch")
    await scenario.confirm()
    await scenario.click("customize:0")
    await scenario.select_option("general")
    await scenario.click("customize:1")
    await scenario.submit_modal({scenario.pending_modal_fields()[0]: "gaules"})
    await scenario.click("customize:2")
    fields = scenario.pending_modal_fields()
    await scenario.submit_modal({fields[0]: MESSAGE})
    await scenario.click("done")
    return scenario


async def _press(scenario, message, label):
    button = locators.find_button(message, label, scenario.locale)
    interaction = scenario._mint(message=message, custom_id=button.custom_id)
    await locators.dispatch_click(message.view, button, interaction)


async def test_pressing_preview_on_the_review_announces_the_item_it_lists(
    scenario_factory, deps, monkeypatch
):
    """The Preview on a review shows the announcement, not "nothing yet".

    Setting up a Twitch or YouTube notification ends on a review whose Preview
    button reads the answers through the adapter's side-action seam. From a review
    the answers hold the whole composition as one value, so the senders, which
    read flat keys, found nothing and the preview answered with the empty value.

    Shared behaviour: `Runtime._shown` in the adapter, which expands a card's
    draft with `shown_answers` and then collapses a listed composition with
    `responses_for_aside`. Consumer that exposed it: notifications_twitch.
    Guaranteed: pressing Preview on a review sends the announcement of the item
    the review lists, and never the empty value.

    This test drives the button through the adapter on purpose. Its first version
    called `responses_for_aside` directly and went on passing when the adapter
    stopped calling it.
    """
    from app.settings.features import feature_for

    seen: dict = {}

    async def spy(interaction, responses):
        seen["rows"] = {
            row["key"]: row.get("_raw_value", row.get("value"))
            for row in responses
            if row.get("key")
        }

    monkeypatch.setattr(feature_for("notifications_twitch"), "preview_sender", spy)
    scenario = await _review_with_one_streamer(scenario_factory, deps)

    await _press(scenario, scenario.current_message, "Pré-visualizar")

    assert seen["rows"].get("streamer") == "gaules", seen["rows"]
    assert seen["rows"].get("notification_messages") == MESSAGE, seen["rows"]


IMAGE_LINE = "Imagem personalizada"


UPLOADED = "https://cdn.discordapp.com/novo.png"


SAVED = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "welcome_messages_channel": {"style": "channel", "values": "101"},
    "welcome_custom_image": "https://cdn.example/banner.png",
    "welcome_messages_title": "Oi!",
    "welcome_messages": {"style": "bullet", "values": "A;B"},
}


def _lines(view):
    return "\n".join(
        item.content
        for item in walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    )


async def _panel(scenario_factory, deps, design):
    seed_document(deps, "welcome_messages", {**SAVED, "welcome_design": design})

    async def send(*_args, **_kwargs):
        return SimpleNamespace(attachments=[SimpleNamespace(url=UPLOADED)])

    deps.bot.get_channel = lambda _channel_id: SimpleNamespace(send=send)
    return await scenario_factory(locale="pt-br").start_command("welcome_messages")


async def test_a_custom_design_puts_the_image_on_the_panel_with_its_own_edit(
    scenario_factory, deps
):
    """The panel shows what the card shows, and nothing the card hides.

    A card section can declare `visible-when`, and the card obeys it. The panel
    did not: it listed every field of the card, so a section the card hides kept a
    line on the panel, and a field marked hidden to keep that line away lost its
    Edit with it. On the local bot (2026-09-22) that left the welcome banner's
    custom image impossible to change after setup.

    Shared behaviour: `manager.panel_rows`, read by every manager panel and by
    every setup review. Consumer that exposed it: welcome_messages. Guaranteed: a
    card key is on the panel exactly while the section that owns it is visible,
    and while it is there it carries its own Edit.
    """
    scenario = await _panel(scenario_factory, deps, "custom_only")

    assert IMAGE_LINE in _lines(scenario.current_message.view)
    await scenario.click("section:welcome_config/image")
    await scenario.submit_file_upload(filename="novo.png", content=b"\x89PNG")
    document = deps.mongo_client.guild["welcome_messages"].find_one(
        {"guild_id": GUILD_ID}
    )
    assert document["welcome_custom_image"] == UPLOADED


async def test_the_server_design_leaves_no_image_line_behind(scenario_factory, deps):
    """The saved url outlives a design switch, so the row has to be the one
    that goes."""
    scenario = await _panel(scenario_factory, deps, "server_blur")

    assert IMAGE_LINE not in _lines(scenario.current_message.view)
    await scenario.finish()
