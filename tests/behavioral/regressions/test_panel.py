"""The manager panel every saved feature opens on: what it lists, its buttons staying
alive, the items added and edited from it, and how it ends (saved, paused, deleted,
expired) without leaving a live button or an open story behind.
"""
from types import SimpleNamespace

import discord
import pytest
from prometheus_client import REGISTRY

from app.core import analytics, journey
from app.core.utils import ml
from app.data import data
from app.forms.features import feature_for
from tests.behavioral.flows import birthdays, block_links, kick, module_of, twitch, youtube_live
from tests.behavioral.flows.birthdays import (
    complete_member_card,
)
from tests.behavioral.flows.common import (
    GUILD_ID,
    open_manager,
    seed_document,
    two_panels,
)
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import seed_cog

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]


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
    scenario = await twitch.open_card(scenario_factory, deps, "pt-br")
    await twitch.pick_channel(scenario)
    await twitch.submit_streamer(scenario, "gaules")
    await scenario.click("customize:messages")
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


async def test_an_item_added_from_the_review_is_listed_on_the_review(
    scenario_factory,
):
    """An item added from the review shows up on the review.

    On the local bot (2026-09-16) adding a member with the review's Add button
    left the birthday list on the review unchanged: the member was saved, but the
    review still listed the first one only.

    Shared behaviour: the review screen of every form whose setup builds a list.
    Consumer that exposed it: reminders_birthday, whose list has left the setup
    since beta.2 (bd-3); block links keeps the contract. Guaranteed: after a
    child session adds an item, the review lists it.
    """
    scenario = await _review_with_one_link(scenario_factory, "meusite.com.br")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "docs.example.org"})

    scenario.expect_step("confirm")
    joined = _screen_text(scenario)
    assert "meusite.com.br" in joined and "docs.example.org" in joined
    await scenario.finish()


async def test_the_first_item_added_from_the_review_is_listed_on_the_review(
    scenario_factory,
):
    """The list was left for later, so the review has no list block yet."""
    scenario = await scenario_factory(locale=LINKS_LOCALE).start_command(block_links.FORM)
    await scenario.confirm()
    await scenario.click("done")
    scenario.expect_step("confirm")

    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD: "docs.example.org"})

    scenario.expect_step("confirm")
    assert "docs.example.org" in _screen_text(scenario)
    await scenario.finish()


LINKS_LOCALE = "en-us"


LINK_FIELD = block_links.LINK_FIELD[LINKS_LOCALE]


async def _review_with_one_link(scenario_factory, link):
    scenario = await scenario_factory(locale=LINKS_LOCALE).start_command(block_links.FORM)
    await scenario.confirm()
    await scenario.click("customize:exceptions")
    await scenario.click("customize:own_links")
    await scenario.submit_modal({LINK_FIELD: link})
    scenario.expect_step("link_settings")
    await scenario.click("back")
    await scenario.click("done")
    scenario.expect_step("confirm")
    return scenario


def _screen_text(scenario):
    return "\n".join(
        item.content
        for item in walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.TextDisplay)
    )


def _saved_links(scenario):
    document = scenario.get_persisted("guild", block_links.FORM, {"guild_id": GUILD_ID})
    return [item["link"]["value"] for item in document["custom_links"]["values"]]


@pytest.mark.regression
@pytest.mark.shared_contract("form_engine")
async def test_a_link_added_again_in_a_setup_takes_the_place_of_the_one_listed(
    scenario_factory,
):
    """A list built during a setup holds each item once, by the key that makes it unique.

    Broke as: the form engine's merge of a finished item compared `index != index`, a
    loop variable shadowing the parameter it meant, so the check for an item already
    listed under the same `unique.key` value never matched. Adding a link the setup
    already listed appended it again, and the review and the saved document showed
    it twice.

    Shared behaviour: the merge of a child session's item into its parent's list
    (`merge_item` in the form engine), used by the review's Add and Edit of every
    form whose list declares `unique.key` (block_links by link, reminders_birthday by
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
    assert _screen_text(scenario).count("meusite.com.br") == 1

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
    scenario = await twitch.open_card(scenario_factory, deps, "pt-br")
    await twitch.pick_channel(scenario)
    await twitch.submit_streamer(scenario, "gaules")
    await scenario.click("customize:messages")
    fields = scenario.pending_modal_fields()
    await scenario.submit_modal({fields[0]: MESSAGE})
    await scenario.click("done")
    return scenario


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

    seen: dict = {}

    async def spy(interaction, responses):
        seen["rows"] = {
            row["key"]: row.get("_raw_value", row.get("value"))
            for row in responses
            if row.get("key")
        }

    monkeypatch.setattr(feature_for("notifications_twitch"), "preview_sender", spy)
    scenario = await _review_with_one_streamer(scenario_factory, deps)

    await scenario.click("preview")

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


async def _welcome_panel(scenario_factory, deps, design):
    seed_document(deps, "welcome_messages", {**SAVED, "welcome_design": design})

    async def send(*_args, **_kwargs):
        return SimpleNamespace(attachments=[SimpleNamespace(url=UPLOADED)])

    deps.bot.get_channel = lambda _channel_id: SimpleNamespace(send=send)
    return await scenario_factory(locale="pt-br").start_command("welcome_messages")


async def test_a_custom_design_puts_the_image_on_the_panel_with_its_own_edit(
    scenario_factory, deps
):
    """The panel shows what the card shows, and nothing the card hides.

    A card section can declare `when`, and the card obeys it. The panel
    did not: it listed every field of the card, so a section the card hides kept a
    line on the panel, and a field marked hidden to keep that line away lost its
    Edit with it. On the local bot (2026-09-22) that left the welcome banner's
    custom image impossible to change after setup.

    Shared behaviour: `manager.panel_rows`, read by every manager panel and by
    every setup review. Consumer that exposed it: welcome_messages. Guaranteed: a
    card key is on the panel exactly while the section that owns it is visible,
    and while it is there it carries its own Edit.
    """
    scenario = await _welcome_panel(scenario_factory, deps, "custom_only")

    assert IMAGE_LINE in _screen_text(scenario)
    await scenario.click("section:welcome_config/image")
    await scenario.submit_file_upload(filename="novo.png", content=b"\x89PNG")
    document = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "welcome_messages")
    assert document["welcome_custom_image"] == UPLOADED


async def test_the_server_design_leaves_no_image_line_behind(scenario_factory, deps):
    """The saved url outlives a design switch, so the row has to be the one
    that goes."""
    scenario = await _welcome_panel(scenario_factory, deps, "server_blur")

    assert IMAGE_LINE not in _screen_text(scenario)
    await scenario.finish()


BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "mode": "block_all",
    "answer": "Nada de links por aqui!",
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "allowed_chats": {"style": "channel", "values": "100"},
    "custom_links": {
        "style": "composition",
        "values": [
            {"link": {"value": "twitch.tv/jway"},
             "match_type": {"value": "domain", "_raw_value": "domain"}},
        ],
    },
}


ADD = ml("buttons.add.label", locale="pt-br")


async def _block_links_panel(scenario_factory, deps):
    from app.commands.block_links.service import normalize_block_links_config

    seed_cog(deps.mongo_client, "block_links", dict(BLOCK_LINKS_COG))
    return await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(dict(BLOCK_LINKS_COG))
    )


async def test_add_works_again_after_the_modal_is_dismissed(scenario_factory, deps):
    """Regression: a message's buttons stay alive until the message itself leaves.

    What broke: discord.py's ViewStore maps message -> item at send time and reads
    `item.view` live at dispatch. Button callbacks on the manager did
    `parent_view.clear_items()` before opening the next screen, which sets
    `item._view = None` on the very objects the store returns. Whenever the next
    screen did NOT replace the message (a modal response leaves it untouched; a
    failed transition leaves it on screen), every button the user could still see
    was dead: each click logged `View interaction referencing unknown view for
    item ... Discarding` and Discord showed "This interaction failed".

    Exposed by: /moderações bloquear links -> Adicionar. Its composition starts
    with a modal, so the panel stays on screen while the modal is open; dismissing
    the modal (Discord tells the bot nothing) and clicking Adicionar again hit the
    cleared view. The HelpButton had the same divorced mutation and also crashed
    on the panel (`interaction.message.embeds[0]` on a message with no embeds).

    Shared behavior affected: `app/ui/buttons.py` (EditButton,
    AddItemButton, RemoveItemButton, HelpButton), consumed by every command's
    manager panel.

    Must remain guaranteed: after any aborted or failed sub-flow (dismissed modal,
    duplicate item, help), every button still visible on screen keeps working.

    The reported bug, exactly: Adicionar opens a modal (the panel stays on
    screen), the user closes the modal without submitting — Discord tells the
    bot nothing — and clicks Adicionar again. The second click must open the
    modal again, not die against a cleared view.
    """
    scenario = await _block_links_panel(scenario_factory, deps)

    await scenario.click(ADD)
    scenario.expect_modal()
    scenario.dismiss_modal()

    await scenario.click(ADD)
    scenario.expect_modal()


async def test_rapid_consecutive_clicks_all_reach_a_live_view(scenario_factory, deps):
    """Users double-click. The first click must not leave the view in a state
    where the second one is silently discarded."""
    scenario = await _block_links_panel(scenario_factory, deps)

    await scenario.click(ADD)
    await scenario.click(ADD)          # no dismissal in between

    scenario.expect_modal()


async def test_duplicate_add_says_item_and_the_manager_still_works(
    scenario_factory, deps
):
    """The user's scenario end to end: add an item that already exists, get
    the (generic, Keiko-voiced) feedback, and go on using the manager."""
    scenario = await _block_links_panel(scenario_factory, deps)

    await scenario.click(ADD)
    await scenario.submit_modal({block_links.LINK_FIELD["pt-br"]: "twitch.tv/jway"})
    await scenario.click(block_links.DOMAIN_OPTION["pt-br"])

    scenario.expect_error(ml("errors.item_already_registered.message", locale="pt-br"))
    assert "membro" not in (scenario.outputs[-1].get("embed") or {}).get(
        "description", ""
    ), "the shared add flow must not talk about 'member': the item can be anything"

    # The panel was legitimately replaced during the flow; reopening the
    # manager must yield a fully working panel again.
    from app.commands.block_links.service import normalize_block_links_config

    await scenario.start_manager(
        "block_links", normalize_block_links_config(dict(BLOCK_LINKS_COG))
    )
    await scenario.click(ADD)
    scenario.expect_modal()


async def test_help_describes_the_buttons_without_killing_the_panel(
    scenario_factory, deps
):
    """Help must be a read-only screen: it lists what each button does in its
    own ephemeral message and leaves the panel exactly as it was. The old
    implementation edited the panel (crashes: a Components V2 message has no
    embeds) and then cleared the view, killing every button on screen."""
    scenario = await _block_links_panel(scenario_factory, deps)

    await scenario.click(ml("buttons.help.label", locale="pt-br"))

    event = scenario.expect_message(kind="send", ephemeral=True)
    fields = (event.get("embed") or {}).get("fields") or []
    labels = [field.get("name", "") for field in fields]
    assert any(ADD in label for label in labels),\
        f"the help embed lists the panel's buttons, got {labels}"

    await scenario.click(ADD)          # the panel is still fully alive
    scenario.expect_modal()


LOCALE = "pt-br"


def _errors_since(scenario, start):
    return [
        event
        for event in scenario.outputs[start:]
        if event.get("actor") == "bot"
        and (event.get("embed") or {}).get("color") == 0xFF0000
    ]


async def _add_birthday(scenario, member):
    await scenario.click("add")
    await scenario.select_option(member, target="user")
    await complete_member_card(scenario, LOCALE)


async def test_each_duplicate_birthday_is_told_once_and_the_next_member_is_not(
    scenario_factory, deps
):
    """Regression: an item the list already holds is refused with one error, once.

    What broke: on the birthday panel, Adicionar for a member already on the list
    answered with two "Item já cadastrado" embeds per attempt: the separate error
    (`ShowError`) and then the session closed with the same error embed in place
    of the card (`Finalize("duplicate")`). Neither went away, so every new attempt
    added two more ephemeral errors to the channel, the admin read them stacking
    up attempt after attempt, and they were still on screen when the next member
    was added successfully. Each attempt also ended the session, so the panel was
    gone and the command had to be run again.

    Shared behavior affected: the manager's answer to an addition its saved list
    already holds (`Manager.item_finished` and the refused commit in
    `_on_commit_failed`, app/forms/form/manager.py), and the final duplicate
    message of the executor (`Executor.finalize`, app/forms/discord/transitions.py),
    used by every list keyed by `unique.key`.

    Exposed by: reminders_birthday (`unique.key: user`); notifications_twitch
    (`unique.key: streamer`) is covered too, where the nick's validator refuses the
    streamer on the card before the list does.

    Must remain guaranteed: each duplicate attempt sends exactly one error, the
    admin stays on the panel (or the card) to try again, and the next addition
    that succeeds sends no error at all.
    """
    scenario = await birthdays.open_manager(scenario_factory, deps, LOCALE)
    duplicate = ml("errors.item_already_registered.title", locale=LOCALE)

    for attempt in range(3):
        start = len(scenario.outputs)
        await _add_birthday(scenario, "Tester")

        errors = _errors_since(scenario, start)
        assert len(errors) == 1, (
            f"attempt {attempt + 1} on a member already listed sent "
            f"{len(errors)} errors\n{scenario.transcript}"
        )
        assert duplicate in errors[0]["embed"]["title"]
        scenario.expect_component(label_or_action="add")

    start = len(scenario.outputs)
    await _add_birthday(scenario, "Amiga")

    assert _errors_since(scenario, start) == [], scenario.transcript
    scenario.expect_persisted(
        "guild", "members", {"user_id": "777"}, {"user_id": "777"}
    )


@pytest.mark.usefixtures("outside_dev")
async def test_each_duplicate_streamer_is_told_once_and_the_next_streamer_is_not(
    scenario_factory, deps
):
    scenario = await open_manager(scenario_factory, deps, LOCALE, twitch.FORM, twitch.seed)
    await scenario.click("add")
    await twitch.pick_channel(scenario)

    for attempt in range(3):
        start = len(scenario.outputs)
        await twitch.submit_streamer(scenario, "gaules")

        errors = _errors_since(scenario, start)
        assert len(errors) == 1, (
            f"attempt {attempt + 1} on a streamer already listed sent "
            f"{len(errors)} errors\n{scenario.transcript}"
        )

    start = len(scenario.outputs)
    await twitch.submit_streamer(scenario, "shroud")
    await twitch.submit_messages(scenario)
    await scenario.click("done")

    assert _errors_since(scenario, start) == [], scenario.transcript
    saved = await data().guild_cogs.find_cog_by_guild_id(str(scenario.guild.id), twitch.FORM)
    streamers = [
        item["streamer"]["value"] for item in saved["notifications"]["values"]
    ]
    assert streamers == ["gaules", "cellbit", "shroud"]


@pytest.fixture
def outside_dev(deps):
    """The subscription hooks run only outside dev."""
    deps.bot.config.is_dev = lambda: False


async def _block_links(scenario_factory, deps):
    return await open_manager(
        scenario_factory, deps, LOCALE, block_links.FORM, block_links.seed
    )


async def _twitch(scenario_factory, deps):
    return await open_manager(
        scenario_factory, deps, LOCALE, twitch.FORM,
        twitch.seed,
    )


async def _birthdays(scenario_factory, deps):
    return await birthdays.open_manager(scenario_factory, deps, LOCALE)


EDITS = {
    "a section edit": (block_links.FORM, _block_links, (), "section:link_settings"),
    "an item edit": (
        twitch.FORM, _twitch, (), "section:notifications$0"
    ),
    "a group's card": (
        birthdays.FORM, _birthdays, ("edit:group:settings",), "section:birthday_config"
    ),
}


def _last_screen(scenario):
    return next(
        event["components"]
        for event in reversed(scenario.outputs)
        if event.get("actor") == "bot" and event.get("components_v2")
    )


def _saved(deps, form):
    return deps.mongo_client.guild.cogs.find_one({"_id": f"{GUILD_ID}:{form}"})


def _changes(deps, form):
    return [
        change["type"]
        for change in deps.mongo_client.events.changes.find({"cog": form})
    ]


@pytest.mark.parametrize("edit", list(EDITS))
async def test_discard_on_a_panel_edit_closes_the_question_and_saves_nothing(
    scenario_factory, deps, analytics_events, edit
):
    """Regression: Discard after Cancel, on an edit opened from the panel, leaves nothing.

    What broke: on a card opened from the manager panel (a card's section, a
    list's item, a group's card), Cancel asked "Discard settings?" on a message
    of its own and Discard drew the panel again under it, while the question
    stayed on screen with live buttons. The child ended with
    `ResumeParent(discarded=True)`, and the Manager read it as an edit with no
    answers: it committed an empty `edit`, which wrote `updated_at` and recorded
    a `config.changed` that History showed as an edit nobody made.

    Shared behavior affected: the Manager's `ChildFinished` (app/forms/form/manager.py)
    and the confirmation message the executor removes (`Dismiss`,
    app/forms/discord/transitions.py).

    Exposed by: block_links (a section edit), notifications_twitch (an item
    edit) and reminders_birthday (a group's card).

    Must remain guaranteed: Discard removes the question, the admin is back on
    the screen the edit was opened from, drawn the same, and nothing is written
    or recorded: the saved document is unchanged and History gains no change.
    """
    form, opened, before, card = EDITS[edit]
    scenario = await opened(scenario_factory, deps)
    for target in before:
        await scenario.click(target)
    left = _last_screen(scenario)
    saved = _saved(deps, form)
    changes = _changes(deps, form)

    await scenario.click(card)
    await scenario.cancel()
    question = scenario.current_message
    await scenario.click(ml("buttons.discard.label", locale=LOCALE))
    await scenario.finish()

    assert question.deleted, "the discard question leaves the screen"
    assert scenario.current_message is not question
    assert _last_screen(scenario) == left, "back on the screen the edit came from"
    assert _saved(deps, form) == saved, "nothing is written"
    assert _changes(deps, form) == changes, "History gains no change"
    assert "config.changed" not in [event["type"] for event in analytics_events]


LEGACY_COG = {
    "guild_id": "123456789",
    "enabled": True,
    "mode": "block_all",
    "answer": "Resposta antiga",
}


async def test_editing_a_never_edited_panel_still_records_a_timestamp(
    scenario_factory, deps
):
    """Regression: an edit must be auditable in time.

    What broke: `Manager.update_command` stamped the audit record with
    `interaction.message.edited_at` alone. A manager panel that had never been
    edited reports `edited_at = None`, so the `edited` event was written with
    `datetime: None`. `find_cog_events_by_guild_id` sorts on that field, so the
    History screen showed the edit out of order — or lost it among the null keys.

    Shared behavior affected: `insert_cog_event` (app/core/cogs.py), consumed
    by the form engine, the manager lifecycle and the History button of every
    configured feature. The five sibling call sites already used the
    `edited_at or created_at` fallback; this one did not.

    What must remain guaranteed: every audit event carries a real timestamp,
    whatever state the message it came from was in.
    """
    from app.commands.block_links.service import normalize_block_links_config

    seed_cog(deps.mongo_client, "block_links", dict(LEGACY_COG))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(LEGACY_COG)
    )

    await scenario.click("section:link_settings")
    await scenario.click("customize:answer")
    await scenario.submit_modal({"resposta": "Resposta nova"})
    await scenario.click("done")

    events = list(deps.mongo_client.events.changes.find({"cog": "block_links"}))
    edited = [event for event in events if event["type"] == "config.changed"]

    assert edited, "the edit must produce an audit record"
    for event in edited:
        assert event["ts"] is not None, (
            "an audit record without a timestamp cannot be ordered in History"
        )

    await scenario.finish()


FEATURE = "reminders_birthday"


def _results(stories):
    return [story[2] for story in stories]


def _sessions(status):
    counted = REGISTRY.get_sample_value(
        "keiko_form_sessions_total", {"feature": FEATURE, "status": status}
    )
    return counted or 0.0


def _names(recorded):
    return [event["type"] for event in recorded]


async def test_a_panel_that_added_a_member_and_expired_ends_completed(
    scenario_factory, deps, analytics_events, stories
):
    """Regression: a manager panel that saved a change ends completed, never abandoned.

    What broke: once a change saved from the panel drew the panel again instead of
    closing the message (ux-341), the session stayed open, and when nobody clicked
    again it expired like a panel nobody used: `setup.abandoned` fired, its story
    closed as `⌛ abandoned` and `keiko_form_sessions_total` counted it `expired`.
    Before, the commit closed the session as `completed` and nothing was abandoned.

    Shared behavior affected: `on_expired` (app/forms/form/form.py), the
    manager's saved-change bookkeeping (`FormSession.changes_saved`, set by
    `_on_commit_succeeded` in app/forms/form/manager.py), and the adapter's
    journey closing (`Runtime.expire_stale`, `Runtime._close`,
    `observability.ended_as`), shared by every manager panel.

    Exposed by: reminders_birthday, a member added from the panel and the panel
    left open.

    Must remain guaranteed: a panel that saved at least one change and then
    expires emits no `setup.abandoned`, counts `completed`, and closes its story
    as its last change; a panel that saved nothing still ends abandoned and
    counts `expired`.
    """
    completed, expired = _sessions("completed"), _sessions("expired")
    scenario = await birthdays.open_manager(scenario_factory, deps, "pt-br")
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await complete_member_card(scenario, "pt-br")

    await scenario.expire()

    assert "setup.abandoned" not in _names(analytics_events)
    assert "feature.item_added" in _names(analytics_events)
    assert _sessions("expired") == expired
    assert _sessions("completed") == completed + 2, "the panel and its member card"
    assert _results(stories) == ["in progress", "added"]


async def test_a_panel_that_saved_nothing_and_expired_ends_abandoned(
    scenario_factory, deps, analytics_events, stories
):
    completed, expired = _sessions("completed"), _sessions("expired")
    scenario = await birthdays.open_manager(scenario_factory, deps, "pt-br")

    await scenario.expire()

    assert _names(analytics_events).count("setup.abandoned") == 1
    assert _sessions("expired") == expired + 1
    assert _sessions("completed") == completed
    assert _results(stories) == ["in progress", "abandoned"]


OUTCOMES = {"pause": "paused", "unpause": "resumed", "disable": "disabled"}


@pytest.mark.parametrize("events_on", [True, False], ids=["events-on", "events-off"])
@pytest.mark.parametrize("action", ["pause", "unpause"])
@pytest.mark.parametrize("form", ["block_links", "notifications_twitch"])
async def test_a_pause_or_resume_is_a_step_its_panel_closes_with(
    scenario_factory, deps, stories, monkeypatch, form, action, events_on
):
    """Regression: a pause, resume or disable from the panel closes the panel's story.

    What broke: the adapter closed the journey of a completed session only when it
    had saved a change before or when a setup turned the feature on. A pause,
    resume or disable from a panel that saved nothing else ended `completed` with
    no close of its own: its story was closed only by the `feature.paused` /
    `feature.unpaused` / `feature.disabled` events reaching the journey observer,
    so with the events off (`ANALYTICS_ENABLED=false`) every such story stayed
    `⏳ in progress` and its journey was kept in memory for the life of the process.

    Shared behavior affected: `Runtime._close` (app/forms/discord/callbacks.py)
    and `observability.ended_as` / `observability.completed_as`, the one place
    every root session's story ends, shared by every manager panel.

    Exposed by: the review of `_close` (beta.2 corrections, K2), on block_links
    and notifications_twitch panels.

    Since PR8 a pause and a resume keep the panel open (the panel is drawn again
    with the feature's new state), so they are steps of the panel's story, which
    ends with the panel; a delete still closes it at once.

    Must remain guaranteed: a delete closes the panel's story as `disabled`; a
    pause or a resume leaves it open and, when the panel ends, the story closes
    named by that last action (`paused`, `resumed`); both hold whether or not the
    product events reach the journey, and no journey is left open behind it.
    """
    paths = module_of(form)
    seed_document(deps, form, paths.PAUSED if action == "unpause" else paths.ENABLED)
    monkeypatch.setattr(analytics, "_ENABLED", events_on)
    scenario = await scenario_factory(locale="pt-br").start_command(form)

    session = scenario.session.id
    await scenario.click(action)

    assert _results(stories) == ["in progress"], f"a panel {action} closed its story"
    assert journey.get(session) is not None

    await scenario.expire()

    assert _results(stories) == ["in progress", OUTCOMES[action]]
    assert journey.get(session) is None, "the journey leaked in memory"


@pytest.mark.parametrize("events_on", [True, False], ids=["events-on", "events-off"])
@pytest.mark.parametrize("form", ["block_links", "notifications_twitch"])
async def test_a_delete_from_the_panel_ends_its_story_at_once(
    scenario_factory, deps, stories, monkeypatch, form, events_on
):
    paths = module_of(form)
    seed_document(deps, form, paths.ENABLED)
    monkeypatch.setattr(analytics, "_ENABLED", events_on)
    scenario = await scenario_factory(locale="pt-br").start_command(form)
    session = scenario.session.id

    await scenario.click("disable")
    await scenario.click("delete_all")

    assert _results(stories) == ["in progress", "disabled"]
    assert journey.get(session) is None, "the journey leaked in memory"


CONTROLS = (discord.ui.Button, discord.ui.Select, discord.ui.ChannelSelect,
            discord.ui.RoleSelect, discord.ui.UserSelect)


def _clickable(message):
    return [item for item in message.registered_items if isinstance(item, CONTROLS)]


def _registered_text(message):
    return "\n".join(
        item.content for item in message.registered_items
        if isinstance(item, discord.ui.TextDisplay)
    )


def _thumbnails(message):
    return [
        item for item in message.registered_items
        if isinstance(item, discord.ui.Thumbnail)
    ]


def _shown_lines(message):
    return set(_registered_text(message).splitlines())


async def test_an_expired_panel_keeps_its_text_and_no_button(scenario_factory, deps):
    """Regression: a closed panel keeps its text and no button anyone can click.

    What broke: once a manager panel closed (it expired, or another panel deleted
    the feature while it stayed open), the message lost its rows of buttons but
    kept every button that sits beside a block of text: the Edit of each heading
    and the Add beside a list's title. Discord still drew them and still sent
    their clicks, so the closed panel looked usable and answered a click with the
    expired notice.

    Shared behavior affected: `finalized_layout` (app/forms/discord/views.py),
    which `closed_payload` and `Executor._strip_controls`
    (app/forms/discord/transitions.py) run on every closed, expired, discarded
    and previewed Components V2 message of every form.

    Exposed by: block_links, whose panel draws an Edit beside each heading and an
    Add beside the custom links.

    Must remain guaranteed: a panel that expires, and a heading's screen closed
    because another panel deleted the feature, keep the text they showed (Keiko's
    picture beside the title included) and register no button and no select, but
    the expired one's button that opens the feature again (`ReopenButton`).
    """
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    scenario = await scenario_factory(locale=LOCALE).start_command(block_links.FORM)
    panel = scenario.current_message
    assert _clickable(panel), "the open panel has its buttons"
    shown = _shown_lines(panel)

    await scenario.expire()

    assert _clickable(panel) == [], "no Edit or Add beside a heading stays live"
    reopens = [
        item.item.label
        for item in panel.registered_items
        if isinstance(item, discord.ui.DynamicItem)
    ]
    assert reopens == [ml("buttons.reopen.label", locale=LOCALE)], (
        "only the button that opens the feature again"
    )
    assert shown <= _shown_lines(panel), "the panel keeps the text it showed"
    assert ml("texts.notices.expired", locale=LOCALE) in _registered_text(panel)
    assert _thumbnails(panel), "Keiko's picture stays beside the title"


async def test_a_screen_closed_by_another_panels_delete_keeps_no_button(
    scenario_factory, deps,
):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    scenario, other = await two_panels(scenario_factory, block_links.FORM, LOCALE)
    await scenario.click("edit:group:exceptions")
    screen = scenario.current_message
    assert _clickable(screen)
    shown = _shown_lines(screen)

    await other.click("disable")
    await other.click("delete_all")
    await scenario.click("Spotify")

    assert _clickable(screen) == [], "the list's Add beside its title is gone too"
    assert shown <= _shown_lines(screen), "the screen keeps the text it showed"
    assert ml("texts.notices.closed", locale=LOCALE) in _registered_text(screen)


@pytest.mark.parametrize("flow", [twitch, youtube_live, kick], ids=lambda flow: flow.FORM)
async def test_a_panel_lists_an_items_entries_in_the_forms_order(scenario_factory, deps, flow):
    """A saved item reads in the order its card declares, whatever order it was stored in.

    Broke as: the panel and the last-item warning listed an item's entries in the order
    of its stored keys, so a notice whose mention was added after its messages (the
    0008 notice-mentions migration did that to every saved notice) showed "Mensagens de
    Notificação" before "Mencionar", while a notice set up through the card showed them
    the other way (s09, s10, s22 and s23 on the PR12 test page).

    Shared behaviour: `titled_items` (`app/forms/form/responses/summary.py`), which
    every panel, last-item warning and search result reads a stored item through.
    Consumers: every list feature's panel (Twitch, YouTube, Kick, birthdays, block links).

    Guaranteed: "Mencionar" comes before "Mensagens de Notificação" on the panel.
    """
    scenario = await open_manager(scenario_factory, deps, "pt-br", flow.FORM, flow.seed)

    text = _screen_text(scenario)
    assert text.index("Mencionar") < text.index("Mensagens de Notificação")
    await scenario.finish()
