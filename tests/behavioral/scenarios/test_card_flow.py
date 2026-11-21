"""Behavioral scenarios: what every form's card does, whichever command draws it.

Cancelar asks only when there is something to lose: with nothing answered the
setup closes at once as a discard, otherwise it asks to keep or discard, with the
keep button in grey (PR15 round 5, k03b; round 6, m08).

A list drawn inside the card or the panel adds beside its title, removes several
items after one confirmation that names them, and previews each item on its own;
the birthday panel searches one member and lists the next birthdays as bullets
(P4, tw-8, bd-6 of beta.2).

An @everyone, an @here or a role an admin types in a notice or birthday message
is taken out of the text as the modal is saved (the card's 📣 Mencionar says who
is pinged), and Keiko says so once, on a yellow note that removes itself; a
member's mention stays.

An expired setup or panel keeps one button, the only control left, that opens the
feature again the way its slash command does (the panel when it is configured, its
setup when it is not, for an admin only), even after a restart, since discord.py
routes it by the template the bot registers at startup.
"""
import json

import discord
import pytest

from app.commands.birthdays.service import find_birthdays_by_guild
from app.constants import Style, ViewConstants
from app.core.dates import format_mm_dd_label
from app.core.utils import ml
from app.forms.discord.interactions import encode
from app.forms.discord.views import ReopenButton
from app.forms.form.form_yaml import CardStep, ListStep, registry
from tests.behavioral.flows import birthdays, block_links, twitch
from tests.behavioral.flows.common import GUILD_ID
from tests.behavioral.harness import locators
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

LOCALE = "pt-br"
EXPIRED_LINE = ml("texts.notices.expired", locale=LOCALE)
CONTROLS = (
    discord.ui.Button,
    discord.ui.Select,
    discord.ui.ChannelSelect,
    discord.ui.RoleSelect,
    discord.ui.UserSelect,
)


def _shown(event):
    return json.dumps(event, ensure_ascii=False)


def _sent(scenario):
    return [event for event in scenario.outputs if event.get("kind") == "followup_send"]


def _buttons(scenario):
    return [
        item
        for item in locators.walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.Button)
    ]


def _names(recorded):
    return [event["type"] for event in recorded]


# --------------------------------------------------------------------- cancel


def _confirmations(scenario, locale):
    title = ml("commands.responses.discard.title", locale)
    return [
        event
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title", "").endswith(title)
    ]


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
@pytest.mark.parametrize("on_card", [False, True], ids=["intro", "card"])
async def test_cancel_with_nothing_answered_closes_at_once(
    scenario_factory, analytics_events, locale, on_card
):
    scenario = await scenario_factory(locale=locale).start("block_links")
    if on_card:
        await scenario.confirm()
    session_id = scenario.session.id

    await scenario.cancel()

    assert not _confirmations(scenario, locale), "nothing to lose, nothing asked"
    scenario.expect_status("cancelled", session_id)
    assert not [button for button in _buttons(scenario) if not button.disabled]
    assert "setup.discarded" in _names(analytics_events)
    await scenario.finish()


async def test_cancel_after_an_answer_asks_to_keep_or_discard(
    scenario_factory, analytics_events
):
    scenario = await scenario_factory(locale=LOCALE).start("block_links")
    await scenario.confirm()
    await scenario.select_option("allow_all", target="mode")
    session_id = scenario.session.id

    await scenario.cancel()

    assert _confirmations(scenario, LOCALE)
    scenario.expect_status("awaiting", session_id)
    await scenario.click(ml("buttons.discard.label", LOCALE))
    scenario.expect_status("cancelled", session_id)
    assert "setup.discarded" in _names(analytics_events)
    await scenario.finish()


@pytest.mark.parametrize(
    "locale,title",
    [("pt-br", "🚨 Descartar Configurações?"), ("en-us", "🚨 Discard Settings?")],
)
async def test_the_confirmation_asks_to_discard_and_keeps_editing_in_grey(
    scenario_factory, locale, title
):
    """The confirmation is named after what it does, and continuing is not the green,
    primary-looking choice (PR15 round 6, m08)."""
    scenario = await scenario_factory(locale=locale).start("block_links")
    await scenario.confirm()
    await scenario.select_option("allow_all", target="mode")

    await scenario.cancel()

    assert _confirmations(scenario, locale)
    assert ml("commands.responses.discard.title", locale) == title
    keep = next(b for b in _buttons(scenario) if b.label == ml("buttons.keep.label", locale))
    assert keep.style == discord.ButtonStyle.secondary
    await scenario.click(ml("buttons.discard.label", locale))
    await scenario.finish()


# ---------------------------------------------------------------------- lists


async def test_each_streamer_on_the_panel_previews_its_own_announcement(
    scenario_factory, deps
):
    twitch.seed(deps)
    scenario = await scenario_factory(locale=LOCALE).start_command(twitch.FORM)

    await scenario.click("aside:preview:notifications$1")

    preview = _shown(_sent(scenario)[-1])
    assert "cellbit" in preview and "gaules" not in preview
    assert ml("notifications_twitch.ui.announcement.samples.description", LOCALE) in preview
    scenario.expect_component(label_or_action="aside:preview:notifications$0")
    await scenario.finish()


async def test_each_streamer_of_the_summary_previews_its_own_announcement(
    scenario_factory, deps
):
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    for streamer in ("gaules", "cellbit"):
        await twitch.pick_channel(scenario)
        await twitch.submit_streamer(scenario, streamer)
        await scenario.click("done")
        if streamer == "gaules":
            await scenario.click("add:notifications")

    await scenario.click("aside:preview:notifications$1")

    assert "cellbit" in _shown(_sent(scenario)[-1])
    labels = [
        getattr(item, "label", None)
        for item in scenario.current_message.view.walk_children()
    ]
    preview = ml("buttons.preview.label", LOCALE)
    assert labels.count(preview) == 2, "one beside each Editar, none below"
    await scenario.finish()


async def test_back_on_the_confirmation_keeps_every_item(scenario_factory, deps):
    block_links.seed(deps)
    scenario = await scenario_factory(locale=LOCALE).start_command(block_links.FORM)

    await scenario.click("remove:custom_links")
    await scenario.select_option(["custom_links$0", "custom_links$1"])
    await scenario.go_back()

    saved = scenario.get_persisted("guild", block_links.FORM, {})
    assert len(saved["custom_links"]["values"]) == 2
    scenario.expect_component(label_or_action="remove:custom_links")
    await scenario.finish()


async def test_the_summary_removes_several_items_at_once(scenario_factory):
    """The birthday list this ran on left the setup (bd-3); the block links
    summary keeps the contract: one confirmation names every item chosen.
    Since bl-7 the links are added on the card, and the review removes them."""
    link = block_links.LINK_FIELD[LOCALE]
    scenario = await block_links.open_card(scenario_factory, LOCALE)
    await scenario.click(block_links.EXCEPTIONS)
    await scenario.click(block_links.CUSTOM_LINKS)
    await scenario.submit_modal({link: "meusite.com.br"})
    await scenario.go_back()
    await scenario.click("done")
    await scenario.click("add:custom_links")
    await scenario.submit_modal({link: "docs.example.org"})

    await scenario.click("remove:custom_links")
    await scenario.select_option(["custom_links$0", "custom_links$1"])
    confirmation = _shown(scenario.outputs[-1])
    assert "meusite.com.br" in confirmation and "docs.example.org" in confirmation
    await scenario.click("remove_items")

    assert scenario.answers["custom_links"] == ()
    await scenario.finish()


async def test_the_card_removes_the_links_it_lists_and_stays_on_screen(
    scenario_factory,
):
    """bl-7: the card's own list removes too, and the screen that draws it stays
    in front (the exceptions screen since round 5 E)."""
    link = block_links.LINK_FIELD[LOCALE]
    scenario = await block_links.open_card(scenario_factory, LOCALE)
    await scenario.click(block_links.EXCEPTIONS)
    for typed in ("meusite.com.br", "docs.example.org"):
        await scenario.click(block_links.CUSTOM_LINKS)
        await scenario.submit_modal({link: typed})

    await scenario.click("remove:custom_links")
    await scenario.select_option(["custom_links$1"])
    await scenario.click("remove_items")

    scenario.expect_step("link_settings")
    assert scenario.component("allowed_chats"), "the exceptions screen is in front"
    names = [item["link"] for item in scenario.answers["custom_links"]]
    assert [answer.raw for answer in names] == ["meusite.com.br"]
    await scenario.finish()


async def test_a_birthday_found_by_search_is_removed_from_its_own_screen(
    scenario_factory, deps
):
    scenario = await birthdays.open_manager(
        scenario_factory, deps, LOCALE, members=("555", "777")
    )

    await scenario.click("search")
    await scenario.select_option("Amiga")
    await scenario.click("remove_this:reminders_birthday$1")
    await scenario.click("remove_items")

    assert [item["user_id"] for item in await find_birthdays_by_guild(GUILD_ID)] == [
        "555"
    ]
    await scenario.finish()


async def test_the_birthday_panel_lists_the_next_birthdays_as_bullets(
    scenario_factory, deps
):
    """Broke as: the counts block read "Próximos aniversários: 1. <@555>: 12 de
    maio" and "2. <@777>: 12 de junho" on a bare next line, a numbered text glued
    to its label, while every other list of the panel is bullets under its title
    (P4). The next birthdays are a list of values, drawn like one."""
    scenario = await birthdays.open_manager(
        scenario_factory, deps, LOCALE, members=("555", "777")
    )

    label = ml("reminders_birthday.command.panel.rows.next", LOCALE)
    first, second = (format_mm_dd_label(day, LOCALE) for day in ("05-12", "06-12"))
    panel = _shown(scenario.outputs[-1])
    assert (
        f"**{label}:**\\n- <@555>: {first}\\n- <@777>: {second}" in panel
    ), panel
    await scenario.finish()


async def test_a_birthday_search_opens_the_member_with_picture_and_messages(
    scenario_factory, deps
):
    """N2 and B5 (round 3): picking the member on Pesquisar opens them at once,
    with no Confirmar, under the member's title and a separator: the birthday, the
    messages that will celebrate them, then their picture big below (the screen
    itself is the `reminders_birthday/manager_search` golden). The picture is the
    member's own, read from the server's members."""
    scenario = await birthdays.open_manager(
        scenario_factory, deps, LOCALE, members=("555", "777")
    )

    await scenario.click("search")
    await scenario.select_option("Amiga")

    drawn = list(scenario.current_message.view.children[0].children)
    assert [type(item).__name__ for item in drawn[:4]] == [
        "TextDisplay", "Separator", "TextDisplay", "MediaGallery"
    ]
    assert [item.media.url for item in drawn[3].items] == [
        "https://cdn.discordapp.com/avatars/777/fake.png"
    ], "the picture is the member's own, read from the server's members"
    scenario.expect_component(label_or_action="remove_this:reminders_birthday$1")
    await scenario.finish()


# ---------------------------------------------------- a mention typed in a text


def _told_mention_removed(scenario):
    title = ml("commands.responses.mention_removed.title", locale=LOCALE)
    return [
        event
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == title
    ]


async def _twitch_messages(scenario_factory, deps):
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await twitch.pick_channel(scenario)
    await twitch.submit_streamer(scenario, "gaules")
    return scenario, "customize:messages"


async def _birthday_message(scenario_factory, deps):
    scenario = await birthdays.open_setup(scenario_factory, LOCALE)
    await scenario.confirm()
    return scenario, "customize:default_message"


@pytest.mark.parametrize("card,typed,saved", [
    pytest.param(
        _twitch_messages,
        (
            "@everyone {streamer} ao vivo <@&201>",
            "<@777>, olha o {streamer}: {stream_link}",
            "@here",
        ),
        {
            "notification_messages":
                "{streamer} ao vivo;<@777>, olha o {streamer}: {stream_link}",
        },
        id="twitch_notice",
    ),
    pytest.param(
        _birthday_message,
        ("@here Parabéns, {user}!", "Feliz dia! <@&201> @everyone"),
        {
            "default_message_title": "Parabéns, {user}!",
            "default_message_content": "Feliz dia!",
        },
        id="birthday_message",
    ),
])
async def test_a_message_saves_without_the_mentions_typed_in_it(
    scenario_factory, deps, card, typed, saved
):
    """@everyone, @here and a role go, a member's mention stays, and one yellow
    note for the whole modal says so, then removes itself."""
    scenario, section = await card(scenario_factory, deps)

    await scenario.click(section)
    fields = scenario.pending_modal_fields()
    await scenario.submit_modal(dict(zip(fields, typed)))

    state = scenario.card_state
    assert {key: state[key] for key in saved} == saved
    [told] = _told_mention_removed(scenario)
    assert told["embed"]["color"] == int(Style.WARNING_COLOR, 16), "Keiko's yellow"
    assert told["embed"]["description"].startswith(
        ml("commands.responses.mention_removed.description", locale=LOCALE)
    )
    assert told["delete_after"] == ViewConstants.ACTION_NOTICE_SECONDS
    assert told["ephemeral"] is True
    await scenario.finish()


async def test_a_message_without_a_typed_mention_is_saved_quietly(
    scenario_factory, deps
):
    scenario, section = await _twitch_messages(scenario_factory, deps)

    await scenario.click(section)
    await scenario.submit_modal(
        {scenario.pending_modal_fields()[0]: "{streamer} ao vivo! {stream_link}"}
    )

    assert scenario.card_state["notification_messages"] == (
        "{streamer} ao vivo! {stream_link}"
    )
    assert _told_mention_removed(scenario) == []
    await scenario.finish()


def _message_normalizers(step):
    for section in step.sections:
        if section.key in ("messages", "default_message", "message"):
            yield section.key, [rule.name if rule else None for rule in section.normalizers()]


@pytest.mark.parametrize(
    "form",
    ["notifications_twitch", "notifications_youtube_video", "reminders_birthday"],
)
def test_every_message_a_mention_field_covers_declares_the_normalizer(form):
    cards = []
    for step in registry.get(form).steps:
        cards += [step] if isinstance(step, CardStep) else []
        if isinstance(step, ListStep):
            cards += [child for child in step.steps if isinstance(child, CardStep)]

    found = [found for card in cards for found in _message_normalizers(card)]

    assert found, "the form writes a message"
    for key, rules in found:
        assert rules and set(rules) == {"without_group_mentions"}, key


# -------------------------------------------------------------------- expired


def _admin(scenario_factory, admin=True):
    guild = create_guild()
    member = create_member(guild, id=555, name="Tester")
    member.guild_permissions = discord.Permissions(administrator=admin)
    return scenario_factory(locale=LOCALE, guild=guild, user=member)


def _reopen_of(message) -> ReopenButton:
    items = list(locators.walk_items(message.view))
    reopens = [item for item in items if isinstance(item, ReopenButton)]
    others = [item for item in items if isinstance(item, CONTROLS)]
    assert len(reopens) == 1 and others == [], "the button is the only control left"
    return reopens[0]


async def test_an_expired_setup_opens_its_setup_again(scenario_factory, deps):
    scenario = await _admin(scenario_factory).start_command("default_roles")
    message = scenario.current_message

    await scenario.expire()

    assert EXPIRED_LINE in message.embeds[0].description
    reopen = _reopen_of(message)
    assert reopen.item.label == ml("buttons.reopen.label", locale=LOCALE)
    assert reopen.feature == "default_roles"

    scenario.restart()
    await scenario.click_after_restart(reopen, on=message)

    assert scenario.current_message is not message, "a new message, the setup's"
    assert scenario.session is not None and scenario.session.key == "default_roles"
    scenario.expect_mode("setup")


async def test_an_expired_panel_opens_its_panel_again(scenario_factory, deps):
    block_links.seed(deps)
    scenario = await _admin(scenario_factory).start_command(block_links.FORM)
    panel = scenario.current_message

    await scenario.expire()

    shown = "\n".join(
        item.content
        for item in panel.registered_items
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert shown.endswith(EXPIRED_LINE)
    reopen = _reopen_of(panel)

    scenario.restart()
    await scenario.click_after_restart(reopen, on=panel)

    assert scenario.current_message is not panel
    assert scenario.session.key == block_links.FORM
    scenario.expect_mode("manage")


async def test_only_an_admin_opens_an_expired_form_again(scenario_factory, deps):
    scenario = await _admin(scenario_factory, admin=False).start_command(
        "default_roles"
    )
    message = scenario.current_message
    await scenario.expire()
    reopen = _reopen_of(message)
    scenario.restart()

    await scenario.click_after_restart(reopen, on=message)

    told = scenario.outputs[-1]
    assert told["embed"]["title"] == ml("commands.responses.admin_only.title", LOCALE)
    assert scenario.sessions == [], "nothing opened"


async def test_after_a_restart_only_the_reopen_button_opens_its_feature(
    scenario_factory, deps
):
    """The template the bot registers matches the reopen button alone: a late click
    on any other control of the same message answers the expired line and opens
    nothing."""
    block_links.seed(deps)
    scenario = await _admin(scenario_factory).start_command(block_links.FORM)
    panel = scenario.current_message
    scenario.restart()

    await scenario.click_after_restart("pause")

    assert scenario.sessions == [], "a late Pausar opens nothing"
    reopen = _reopen_of(panel)
    assert reopen.feature == block_links.FORM
    await scenario.click_after_restart(reopen, on=panel)
    assert [session.key for session in scenario.sessions] == [block_links.FORM]


async def test_a_feature_that_no_longer_exists_answers_closed(scenario_factory, deps):
    """A message drawn before a feature was removed keeps its button: the click
    answers that the form is closed instead of failing."""
    scenario = _admin(scenario_factory)
    view = discord.ui.View(timeout=None)
    reopen = ReopenButton(
        encode("gone_feature-0123456789abcdef", 1, "reopen", "gone_feature"),
        ml("buttons.reopen.label", locale=LOCALE),
    )
    view.add_item(reopen)
    message = scenario.post(view)
    scenario.restart()

    await scenario.click_after_restart(reopen, on=message)

    told = scenario.outputs[-1]
    assert told["content"] == ml("texts.notices.closed", locale=LOCALE)
    assert told["ephemeral"] is True


async def test_a_late_answer_with_no_message_sends_the_button_with_its_line(
    scenario_factory, deps
):
    """With no message to edit, the expired line goes out on a message of its own,
    with the button it names."""
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await twitch.pick_channel(scenario)
    await scenario.click("customize:streamer")
    scenario.restart()
    scenario.dismiss()

    await scenario.submit_modal_after_restart()

    sent = scenario.outputs[-1]
    assert sent["ephemeral"] is True
    assert sent["embed"]["description"].endswith(EXPIRED_LINE)
    reopens = [
        item for item in scenario.current_message.registered_items
        if isinstance(item, ReopenButton)
    ]
    assert [button.feature for button in reopens] == [twitch.FORM]
