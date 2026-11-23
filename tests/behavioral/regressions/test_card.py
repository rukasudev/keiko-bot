"""The card a form step draws, and how it answers the admin: one first answer per click,
every message after it reaching the admin, a refused value told apart, optional selects
that empty, previews drawn from what the card holds, and a setup that ends whole,
discarded or expired, even for a click that lands late.
"""
import ast
import asyncio
import copy
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from app.commands.stream_elements.service import StreamElementsFeature
from app.constants import DiscordLimits, ViewConstants
from app.core import analytics
from app.core.utils import ml
from app.forms.discord import callbacks
from app.forms.features.feature import GenericCogFeature
from tests.behavioral.flows import (
    block_links,
    roles,
    stream_elements,
    twitch,
    welcome,
    youtube,
)
from tests.behavioral.flows.common import open_manager, remove_item, seed_document
from tests.behavioral.harness import locators
from tests.behavioral.harness.copy_keys import copy_text
from tests.behavioral.harness.locators import walk_items
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks.discord import MockRole, create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]


def _interactions(outcome, code=""):
    from prometheus_client import REGISTRY

    labels = {"outcome": outcome, "code": code}
    return REGISTRY.get_sample_value("keiko_interactions_total", labels) or 0.0


def _components(message):
    return [
        item
        for item in locators.walk_items(message.view)
        if isinstance(item, (discord.ui.Button, discord.ui.Select,
                             discord.ui.ChannelSelect, discord.ui.RoleSelect,
                             discord.ui.UserSelect))
    ]


async def _until(condition, tries=200):
    for _ in range(tries):
        if condition():
            return True
        await asyncio.sleep(0.005)
    return condition()


@pytest.fixture
def gated_lookup(monkeypatch):
    released = asyncio.Event()
    started = asyncio.Event()
    original = StreamElementsFeature.prefetch

    async def gated(self, *args):
        started.set()
        await released.wait()
        return await original(self, *args)

    monkeypatch.setattr(StreamElementsFeature, "prefetch", gated)
    return started, released


async def test_a_commit_that_ends_as_the_clock_runs_out_is_answered_once(
    scenario_factory, deps, monkeypatch, caplog
):
    """Every interaction gets one first answer, and a card cannot be clicked while it loads.

    Broke as (beta.2, B5): Discord marks an interaction answered only once the
    answer's round trip returns (discord.py 2.7 sets `is_done()` after the HTTP
    call). The late-answer clock woke at 1.5 s, read `is_done()` while the first
    answer was still on the wire and deferred again, so whichever arrived second
    got 40060 ("Could not answer in time"): a Twitch Disable whose commit took
    2.5 s was counted failed. After the StreamElements streamer modal, the intro's
    buttons stayed clickable during the four lookups; a second click waited for
    the lock, was refused as stale, and its notice raced its own clock and lost
    with 40060, dropping the redraw that should have followed.

    Shared behaviour: the first answer of an interaction (`Answering` in
    `app/forms/discord/transitions.py`, used by the executor and the runtime's
    clock in `callbacks.py`), the hold that disables a card while the adapter
    waits, and the stale-click answer of `settle` (`app/forms/form/form.py`).
    Exposed by: notifications_twitch (Disable), stream_elements_commands (setup).

    Guaranteed: the clock never sends a second first answer, a card's buttons are
    disabled while a lookup runs, and a click that arrived meanwhile is answered
    with the current screen only.
    """
    seed_document(deps, twitch.FORM, twitch.ENABLED)
    twitch.known_streamers(deps)
    scenario = await scenario_factory().start_command(twitch.FORM)
    monkeypatch.setattr(ViewConstants, "DEFER_AFTER_SECONDS", 0.05)
    committing = GenericCogFeature.commit

    async def slow(self, kind, payload, context):
        await asyncio.sleep(0.04)
        return await committing(self, kind, payload, context)

    monkeypatch.setattr(GenericCogFeature, "commit", slow)
    scenario.latency = 0.1
    refused = _interactions("failed", "40060")

    with caplog.at_level(logging.WARNING):
        await scenario.click("disable")
        await scenario.click("delete_all")
        await asyncio.sleep(0.2)

    assert _interactions("failed", "40060") == refused
    assert "Could not answer in time" not in caplog.text
    scenario.expect_message(
        title_contains=ml("commands.responses.disabled.title", locale="pt-br")
    )


async def test_the_card_cannot_be_clicked_while_a_modal_lookup_runs(
    scenario_factory, deps, gated_lookup
):
    started, released = gated_lookup
    stream_elements.known_streamers(deps)
    scenario = await scenario_factory().start_command(stream_elements.FORM)
    await scenario.confirm()
    field = scenario.pending_modal_fields()[0]

    submit = asyncio.ensure_future(scenario.submit_modal({field: "shroud"}))
    await asyncio.wait_for(started.wait(), 1)
    shown = scenario.current_message
    clickable = [item.custom_id for item in _components(shown) if not item.disabled]
    released.set()
    await submit

    assert _components(shown), "the intro shows its buttons"
    assert clickable == [], f"still clickable while looking up: {clickable}"
    scenario.expect_step("confirm")
    assert not any(item.disabled for item in _components(scenario.current_message))


async def test_a_click_that_arrived_during_the_lookup_is_answered_quietly(
    scenario_factory, deps, gated_lookup, monkeypatch
):
    started, released = gated_lookup
    stream_elements.known_streamers(deps)
    scenario = await scenario_factory().start_command(stream_elements.FORM)
    intro = scenario.current_message
    confirm = scenario.component("continue")
    await scenario.confirm()
    monkeypatch.setattr(ViewConstants, "DEFER_AFTER_SECONDS", 0.05)
    scenario.latency = 0.02
    field = scenario.pending_modal_fields()[0]

    submit = asyncio.ensure_future(scenario.submit_modal({field: "shroud"}))
    await asyncio.wait_for(started.wait(), 1)
    click = asyncio.ensure_future(scenario.click(confirm, on=intro))
    await asyncio.sleep(0.1)
    released.set()
    await asyncio.gather(submit, click)

    stale = ml("texts.notices.stale", locale="pt-br")
    notices = [event for event in scenario.outputs if event.get("content") == stale]
    assert notices == [], "a click that arrived while loading gets no notice"
    scenario.expect_step("confirm")
    assert scenario.current_message.flags.components_v2
    assert scenario.outputs[-1]["kind"] in ("edit", "followup_edit")


async def test_a_notice_sent_after_the_click_was_answered_reaches_the_admin(
    scenario_factory, deps,
):
    """A message sent after the click was already answered reaches the admin.

    Broke as: an error or a notice sent once the interaction was already answered
    (a typed name looked up on Twitch or YouTube defers the click first, an edit
    defers before it saves) went out through the followup webhook with
    `delete_after`, which discord.py's `Webhook.send` does not take. It raised
    TypeError, the admin never read why the name was refused, and the click was
    logged as a failure. The offline fake accepted the argument, so every test and
    golden stayed green.

    Shared behaviour: `Executor._send` (`app/forms/discord/transitions.py`), the
    one way every effect sends a new ephemeral message. Exposed by:
    notifications_twitch, a streamer name Twitch does not know (beta.2 drew that
    refusal inside the card for a while, so the out-of-date notice of an item edit
    that saves, after another panel removed the item, pinned the contract too; since
    an edit from the panel no longer defers before it saves, that notice is the
    click's first answer and only its self-deletion is pinned here).

    Guaranteed: a self-deleting message sent after the first answer is sent
    without `delete_after` and deleted after the same delay; the fake followup
    refuses `delete_after` the way discord.py does.
    """
    deps.bot.config.is_dev = lambda: False
    first, second = await twitch.two_panels(scenario_factory, deps, "en-us")
    await remove_item(first, "notifications$1")

    await second.click("section:notifications$1")
    await twitch.pick_channel(second, "announcements")
    await second.click("done")

    stale = ml("texts.notices.stale", locale="en-us")
    notices = [event for event in second.outputs if event.get("content") == stale]
    assert notices, "the admin never read why the edit was not saved"
    assert notices[-1]["delete_after"] == ViewConstants.ACTION_NOTICE_SECONDS


async def test_a_streamer_name_refused_after_its_lookup_reaches_the_admin(
    scenario_factory, deps,
):
    scenario = await twitch.open_card(scenario_factory, deps, "en-us")

    await twitch.submit_streamer(scenario, "naoexiste")

    title = ml("errors.streamer_not_found.title", locale="en-us")
    refusals = [
        event for event in scenario.outputs
        if title in ((event.get("embed") or {}).get("title") or "")
    ]
    assert refusals, "the admin never read why the name was refused"
    assert refusals[-1]["kind"] == "followup_send"
    assert refusals[-1]["delete_after"] == 10


def _screen_texts(scenario) -> list[str]:
    message = scenario.current_message
    return [
        item.content or ""
        for item in locators.walk_items(message.view)
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _sent(scenario, wanted: str) -> list[dict]:
    found = []
    for event in scenario.outputs:
        embed = event.get("embed") or {}
        text = " ".join(
            filter(None, [event.get("content"), embed.get("title"), embed.get("description")])
        )
        if wanted in text:
            found.append(event)
    return found


def _typed(scenario) -> list:
    return [field.default for field in scenario.pending_modal.children]


async def test_an_unknown_streamer_is_told_apart_from_the_card(scenario_factory, deps):
    """A refused value is told on a message of its own, below the screen.

    Broke as (beta.2 round 2, R3): round 1 drew every validation error inside the
    screen that asked for it, a `🚨` line under the card section, under the card's
    header or on the one-field card of a modal step. On a phone the admin had to
    scroll up to find why the value was refused ("ficou ruim, volte pra mensagem
    separada"). The error is a separate ephemeral message again, as before beta.2,
    and what round 1 got right stays: the modal opens again with what was typed.

    Shared behaviour: `Form.refuse` (`app/forms/form/form.py`), the answer of
    every form to a refusal, and the card and modal renderers that no longer draw
    it. Exposed by: notifications_twitch (a streamer Twitch does not know),
    stream_elements_commands (a modal step), reminders_birthday (Done with fields
    missing).

    Guaranteed: the refusal reaches the admin as a separate message, the screen
    in front of them carries no `🚨` line, and the retry modal is prefilled.
    """
    scenario = await twitch.open_card(scenario_factory, deps, "pt-br")

    await twitch.submit_streamer(scenario, "naoexiste")

    message = ml("errors.streamer_not_found.message", locale="pt-br")
    errors = _sent(scenario, message)
    assert errors, "the admin never read why the name was refused"
    assert (errors[-1].get("embed") or {}).get("color") == 0xFF0000
    assert errors[-1]["delete_after"] == 10
    assert not any("🚨" in text for text in _screen_texts(scenario))

    await scenario.click("customize:streamer")
    assert _typed(scenario) == ["naoexiste"], "the modal keeps what was typed"


async def test_a_refused_modal_step_is_told_apart_and_reopens_prefilled(
    scenario_factory, deps,
):
    stream_elements.known_streamers(deps)
    scenario = await scenario_factory(locale="pt-br").start_command(
        stream_elements.FORM
    )
    await scenario.confirm()
    await stream_elements.submit_streamer(scenario, "naoexiste")

    assert _sent(scenario, ml("errors.streamer_not_found.message", locale="pt-br"))
    texts = _screen_texts(scenario)
    assert any("naoexiste" in text for text in texts), texts
    assert not any("🚨" in text for text in texts), texts

    await scenario.click("customize:streamer")
    assert _typed(scenario) == ["naoexiste"]


async def test_done_with_fields_missing_is_told_apart_from_the_card(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await scenario.click("done")

    required = ml("errors.required_fields.message", "pt-br").split("$")[0]
    errors = _sent(scenario, required)
    assert errors and errors[-1].get("content"), "a plain sentence of its own"
    assert not any("🚨" in text for text in _screen_texts(scenario))


def _selects(scenario):
    return [
        item
        for item in walk_items(scenario.current_message.view)
        if isinstance(item, (discord.ui.ChannelSelect, discord.ui.RoleSelect))
    ]


async def test_the_exempt_channels_can_be_emptied_again(scenario_factory):
    """An optional select on a card can be emptied again.

    Broke as: the exempt channels and roles of block links sit on one picker
    (the `selects` card section), and every select it drew asked for at least
    one value (`min_values=1`), because the renderer read "several values" as
    "at least one". An admin who had freed a channel could never free none again.

    Shared behaviour: the native selects the renderer draws for a `Picker`
    (`app/forms/discord/views.py`, `_select_item`) and the pickers of the
    `selects` section (`app/forms/form/actions/configuration_card.py`).
    Exposed by block links' "Canais e cargos liberados".

    Guaranteed: a select whose field the card does not require takes zero
    values, and emptying it saves the field empty; a required one still asks
    for one.
    """
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:exceptions")           # Configurar the exceptions
    await scenario.select_option("general", target="allowed_chats")

    assert [select.min_values for select in _selects(scenario)] == [0, 0]
    await scenario.select_option([], target="allowed_chats")

    assert scenario.card_state.get("allowed_chats") in ([], None)
    await scenario.finish()


REFUSED = "commands.responses.roles_not_assignable"


@pytest.mark.parametrize(
    ("picked", "kept"),
    [(["Member", "Admin"], "202"), (["Admin"], [])],
    ids=["beside_a_role_it_can_give", "alone"],
)
async def test_a_role_keiko_cannot_give_is_refused_with_a_notice(
    scenario_factory, picked, kept
):
    """A role Keiko cannot give never lands on the card, and the admin is told which.

    Broke as: a select that offers only the roles Keiko can give took any role the
    native select returned, a role above Keiko's own included, and the card saved it;
    Keiko left that role out only when it gave the roles to a member, so the admin
    learned it from the panel's warning after the save, or never.

    Shared behaviour: the `available_only` a `Picker` carries for an
    `assignable_roles` select (`app/forms/form/actions/selects.py`), read by the
    role select the Discord adapter draws (`app/forms/discord/views.py`) and decided
    on the card (`app/forms/form/form.py`). Exposed by default roles' members roles.

    Guaranteed: of the roles picked, the card keeps the ones Keiko can give, a pick
    of only roles it cannot give leaves the card as it was, and a private message
    names every role it refused, one line each, on a warning embed.
    """
    scenario = await roles.open_card(scenario_factory, "pt-br")

    await scenario.select_option(picked, target="default_roles")

    assert scenario.card_state.get("default_roles") == kept
    scenario.expect_warning(
        copy_text(f"{REFUSED}.lines.above", "pt-br", role="200")
    )


async def test_roles_that_share_a_name_are_each_given_or_refused_on_their_own(
    scenario_factory,
):
    """Two roles named alike are each kept or refused by their own place below Keiko.

    Broke as: the roles Keiko can give were read as a map keyed by their name, so of
    two roles Keiko could give under one name only the last one listed counted, and
    the card refused the other as a role Keiko cannot give. Shared behaviour:
    `givable_role_ids` (`app/core/utils.py`), read by the role select the Discord
    adapter draws, reaction roles' click and the roles by time pass. Exposed by
    default roles' members roles. Guaranteed: of roles that share a name, the card
    keeps every one Keiko can give and names only the one it cannot.
    """
    guild = create_guild()
    guild.roles.append(MockRole(id=212, name="Member", position=2, guild=guild))
    guild.roles.append(MockRole(id=220, name="Member", position=20, guild=guild))
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()

    await scenario.select_option(["202", "212", "220"], target="default_roles")

    assert scenario.card_state.get("default_roles") == ["202", "212"]
    scenario.expect_warning(
        copy_text(f"{REFUSED}.lines.above", "pt-br", role="220")
    )


def _warnings(scenario):
    """The bodies of the warning embeds Keiko sent, oldest first."""
    from app.constants import Style

    yellow = int(Style.WARNING_COLOR, 16)
    return [
        event["embed"]["description"]
        for event in scenario.outputs
        if (event.get("embed") or {}).get("color") == yellow
    ]


@pytest.mark.parametrize(
    "refused",
    [{"230": "managed"}, {"230": "managed", "200": "above"}],
    ids=["managed_alone", "managed_and_above"],
)
async def test_a_role_a_bot_manages_is_refused_for_what_it_is(scenario_factory, refused):
    """Round 2 of PR10: every refused role read "is above mine", a role below Keiko
    that a bot or an integration manages included, which sent the admin to drag
    Keiko's role for nothing. Shared behaviour: the role select the Discord adapter
    draws names why it refused each role (`why_not_givable`, `app/core/utils.py`) and
    the card's warning picks that role's line.

    Broke as: once each line said why its role was refused, the warning repeated ⚠️
    on every line under its ⚠️ title and no longer told the admin how to fix a role
    above Keiko's. Shared behaviour: the card's warning about the roles it refused
    (`commands.responses.roles_not_assignable`, read in `app/forms/form/form.py`).
    Consumer that exposed it: auto roles' members roles. Guaranteed: a managed role
    reads the managed line, a role above Keiko the line about its place, each a plain
    list line, and only a role above Keiko closes the warning with how to drag
    Keiko's role above it."""
    guild = create_guild()
    guild.roles.append(
        MockRole(id=230, name="Bot Role", position=0, guild=guild, managed=True)
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()

    await scenario.select_option(list(refused), target="default_roles")

    lines = "\n".join(
        copy_text(f"{REFUSED}.lines.{why}", "pt-br", role=role)
        for role, why in refused.items()
    )
    hint = copy_text(f"{REFUSED}.overrides.0.description", "pt-br", warning="")
    told = _warnings(scenario)[-1]
    assert told.startswith(lines)
    assert "⚠️" not in told, "the title alone carries the warning sign"
    assert (hint.strip() in told) == ("above" in refused.values())
    assert scenario.card_state.get("default_roles") in ([], None)


TWITCH_ERROR = "notifications_twitch.ui.errors.preview_needs_streamer.message"


YOUTUBE_ERROR = "notifications_youtube_video.ui.errors.preview_needs_youtuber.message"


def _after(scenario, seen):
    return [
        event for event in scenario.outputs[seen:]
        if event["actor"] == "bot" and event["kind"] != "defer"
    ]


def _shown(events):
    return json.dumps(events, ensure_ascii=False)


async def _press_preview(scenario):
    message, state = scenario.current_message, dict(scenario.card_state)
    seen = len(scenario.outputs)
    await scenario.click("preview")
    return message, state, _after(scenario, seen)


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_twitch_card_with_no_streamer_answers_instead_of_previewing(
    scenario_factory, deps, locale
):
    """A preview is drawn only once the card holds the answer it is drawn from.

    Lucas, on beta.2 (2026-11-06): Pré-visualizar on the Twitch card with no
    streamer typed drew a notification for nobody, and the YouTube card did the
    same with no youtuber. It must answer "você precisa escolher o streamer"
    instead, on a message of its own like every other refusal, and draw nothing.

    Shared behaviour: the card's Preview aside (`aside:preview`, the engine's
    `Aside` handler in `app/forms/form/form.py`) and the `required` keys of the
    card's `preview` declaration. Consumers that exposed it: notifications_twitch
    and notifications_youtube_video, whose item card previews the announcement.
    Guaranteed: while a key the preview needs is empty, Preview sends that
    declared error alone and the card on screen stays as it was; once it is
    filled, in the setup or in an item edit, Preview draws the announcement.
    """
    scenario = await twitch.open_card(scenario_factory, deps, locale)
    await twitch.pick_channel(scenario)

    message, state, events = await _press_preview(scenario)

    assert len(events) == 1, scenario.transcript
    assert events[0]["kind"] in ("send", "followup_send"), scenario.transcript
    assert events[0].get("content") == ml(TWITCH_ERROR, locale), scenario.transcript
    assert "embed" not in events[0], "no preview drawn"
    assert scenario.current_message is message, "the card stays as it is"
    assert dict(scenario.card_state) == state
    await scenario.finish()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_youtube_card_with_no_youtuber_answers_instead_of_previewing(
    scenario_factory, deps, locale
):
    scenario = await youtube.open_card(scenario_factory, deps, locale)

    message, state, events = await _press_preview(scenario)

    assert len(events) == 1, scenario.transcript
    assert events[0].get("content") == ml(YOUTUBE_ERROR, locale), scenario.transcript
    assert "embed" not in events[0], "no preview drawn"
    assert scenario.current_message is message, "the card stays as it is"
    assert dict(scenario.card_state) == state
    await scenario.finish()


async def test_the_twitch_card_previews_once_the_streamer_is_typed(
    scenario_factory, deps
):
    scenario = await twitch.open_card(scenario_factory, deps, "pt-br")
    await twitch.submit_streamer(scenario, "gaules")

    _, _, events = await _press_preview(scenario)

    assert ml(TWITCH_ERROR, "pt-br") not in _shown(events)
    sample = ml("notifications_twitch.ui.announcement.samples.description", "pt-br")
    assert sample in _shown(events), scenario.transcript
    await scenario.finish()


async def test_an_item_edit_previews_the_youtuber_it_already_holds(
    scenario_factory, deps
):
    scenario = await open_manager(
        scenario_factory, deps, "pt-br", youtube.FORM, youtube.seed
    )
    await scenario.click("section:notifications$0")

    _, _, events = await _press_preview(scenario)

    assert ml(YOUTUBE_ERROR, "pt-br") not in _shown(events)
    sample = ml("notifications_youtube_video.ui.announcement.samples.description", "pt-br")
    assert sample in _shown(events), scenario.transcript
    await scenario.finish()


YOUTUBE_PREVIEW = (
    "@everyone pewdiepie acabou de postar um novo vídeo!\n"
    "https://www.youtube.com/@pewdiepie"
)


TWITCH_PREVIEW = "@everyone gaules está ao vivo! https://www.twitch.tv/gaules"


CASES = [
    pytest.param(youtube, YOUTUBE_PREVIEW, id="youtube"),
    pytest.param(twitch, TWITCH_PREVIEW, id="twitch"),
]


async def _preview_content(scenario, target):
    seen = len(scenario.outputs)
    await scenario.click(target)
    sent = [
        event for event in scenario.outputs[seen:]
        if event["actor"] == "bot" and event["kind"] in ("send", "followup_send")
    ]
    assert len(sent) == 1, scenario.transcript
    return sent[0].get("content")


@pytest.mark.parametrize("feature, expected", CASES)
async def test_an_item_edit_previews_the_saved_messages_as_text(
    scenario_factory, deps, feature, expected
):
    """Saved notification messages read as the messages they are, in any stored shape.

    Found in the round-6 prints (2026-11-06): Editar on a saved YouTube channel, then
    Pré-visualizar, sent "['@everyone! pewdiepie acabou de postar um novo vídeo!']" with
    the link below it. The item held its messages as a list, and the preview read them
    with `str(...).split(";")`, which only knows the `;`-joined string the card saves.
    The Twitch preview and both real notifiers read the same value the same way: a list
    became its Python repr in the preview and an AttributeError in the notifier.

    Shared behaviour: the one helper that turns a saved message value into the messages
    a sender picks from (`split_messages` in `app/core/utils.py`), read by the
    Twitch and YouTube previews (the card's and the panel's Pré-visualizar) and by the
    live and new video announcements. Consumers that exposed it: notifications_youtube_video
    and notifications_twitch, whose saved items may hold a list or a `;`-joined string.

    Welcome messages read the same value with their own `split_welcome_messages`
    (`str.split(";")` again), so a welcome saved as a list crashed both the welcome a
    new member gets and the panel's Pré-visualizar with an AttributeError (review
    fixes K2, item 5); they now read it through `split_messages` too.

    Guaranteed: a saved list and a `;`-joined string both preview as one message per
    entry, filled, with no brackets or quotes, two entries as two pages, and a real
    announcement or welcome of either shape sends one of those messages.
    """
    scenario = await open_manager(
        scenario_factory, deps, "pt-br", feature.FORM, feature.seed
    )
    await scenario.click("section:notifications$0")

    content = await _preview_content(scenario, "preview")

    assert content == expected, scenario.transcript
    await scenario.finish()


@pytest.mark.parametrize("feature, expected", CASES)
async def test_the_panel_previews_a_saved_item_as_text(
    scenario_factory, deps, feature, expected
):
    scenario = await open_manager(
        scenario_factory, deps, "pt-br", feature.FORM, feature.seed
    )

    content = await _preview_content(scenario, "aside:preview:notifications$0")

    assert content == expected, scenario.transcript
    await scenario.finish()


TWO_MESSAGES = [
    pytest.param(
        twitch, twitch.known_streamers, "streamer",
        "gaules", "https://www.twitch.tv/gaules", id="twitch",
    ),
    pytest.param(
        youtube, youtube.known_channels, "youtuber",
        "pewdiepie", "https://www.youtube.com/@pewdiepie", id="youtube",
    ),
]


def _stored(shape, entries):
    return list(entries) if shape == "list" else "; ".join(entries)


@pytest.mark.parametrize("shape", ["list", "joined"])
@pytest.mark.parametrize("feature, known, placeholder, name, link", TWO_MESSAGES)
async def test_two_saved_messages_preview_as_two_pages(
    scenario_factory, deps, shape, feature, known, placeholder, name, link
):
    entries = (f"Primeira de {{{placeholder}}}!", f"Segunda de {{{placeholder}}}!")
    document = copy.deepcopy(feature.ENABLED)
    item = document["notifications"]["values"][0]
    item["notification_messages"]["value"] = _stored(shape, entries)

    def seed(deps):
        known(deps)
        seed_document(deps, feature.FORM, document)

    scenario = await open_manager(scenario_factory, deps, "pt-br", feature.FORM, seed)

    content = await _preview_content(scenario, "aside:preview:notifications$0")

    expected = [
        f"@everyone Primeira de {name}!\n{link}",
        f"@everyone Segunda de {name}!\n{link}",
    ]
    assert content == expected[0], scenario.transcript
    preview = scenario.current_message
    assert [item.label for item in preview.view.children] == [
        ml("buttons.next_message.label", locale="pt-br")
    ]
    assert preview.view.pages == expected
    await scenario.finish()


def _destination(deps, name):
    guild = create_guild(id=5001, channels=[name])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    recorded_server(deps.mongo_client, guild.id)
    return guild, guild.text_channels[0]


def _picking_the_last(monkeypatch):
    offered = []

    def last(entries):
        offered.append(list(entries))
        return entries[-1]

    monkeypatch.setattr(random, "choice", last)
    return offered


VIDEO_MESSAGES = [
    pytest.param(["{youtuber} postou {video_link}"], 1, id="list"),
    pytest.param("{youtuber} postou {video_link}", 1, id="joined"),
    pytest.param(["{youtuber} chegou", "{youtuber} postou {video_link}"], 2, id="list-of-two"),
    pytest.param("{youtuber} chegou; {youtuber} postou {video_link}", 2, id="joined-two"),
]


LIVE_MESSAGES = [
    pytest.param(["{streamer} on! {stream_link}"], 1, id="list"),
    pytest.param("{streamer} on! {stream_link}", 1, id="joined"),
    pytest.param(["{streamer} chegou", "{streamer} on! {stream_link}"], 2, id="list-of-two"),
    pytest.param("{streamer} chegou; {streamer} on! {stream_link}", 2, id="joined-two"),
]


@pytest.mark.parametrize("stored, count", VIDEO_MESSAGES)
async def test_a_new_video_is_announced_with_one_saved_message(
    deps, monkeypatch, stored, count
):
    from app.commands.youtube.service import VideoAnnouncement, deliver_video

    guild, channel = _destination(deps, "videos")
    announcement = VideoAnnouncement(
        video={"id": "video-1", "snippet": {"title": "A new video"}},
        channel={"title": "PewDiePie"},
        youtuber="pewdiepie",
        followers=[],
    )
    notification = {
        "youtuber": {"value": "pewdiepie"},
        "channel": {"value": str(channel.id)},
        "notification_messages": {"value": stored, "style": "bullet"},
    }

    offered = _picking_the_last(monkeypatch)

    await deliver_video(announcement, {"guild_id": str(guild.id)}, notification)

    assert [len(entries) for entries in offered] == [count]
    assert channel.get_last_message().content == (
        "pewdiepie postou https://www.youtube.com/watch?v=video-1"
    )


@pytest.mark.parametrize("stored, count", LIVE_MESSAGES)
async def test_a_live_is_announced_with_one_saved_message(
    deps, monkeypatch, stored, count
):
    from app.commands.twitch.service import handle_send_streamer_notification

    guild, channel = _destination(deps, "lives")
    deps.twitch.add_user("gaules", user_id="123")
    deps.twitch.set_stream_online("gaules")
    seed_cog(deps.mongo_client, "notifications_twitch", {
        "guild_id": str(guild.id),
        "enabled": True,
        "notifications": {"values": [{
            "streamer": {"value": "gaules"},
            "channel": {"value": str(channel.id)},
            "notification_messages": {"value": stored, "style": "bullet"},
        }]},
    })

    offered = _picking_the_last(monkeypatch)

    await handle_send_streamer_notification("gaules")

    assert [len(entries) for entries in offered] == [count]
    assert channel.get_last_message().content == "gaules on! https://www.twitch.tv/gaules"


WELCOMES = ["{user} chegou ao {server}!", "Olha quem chegou! {user}"]


WELCOME_SHAPES = pytest.mark.parametrize(
    "stored", [WELCOMES, ";".join(WELCOMES)], ids=["list", "joined"]
)


def _welcome_seed(stored):
    document = {
        **welcome.ENABLED,
        "welcome_messages": {"style": "bullet", "values": stored},
    }
    return lambda deps: seed_cog(deps.mongo_client, "welcome_messages", document)


@WELCOME_SHAPES
async def test_the_welcome_panel_previews_each_saved_message(
    scenario_factory, deps, stored
):
    with welcome.banner_stub():
        scenario = await open_manager(
            scenario_factory, deps, "pt-br", welcome.FORM, _welcome_seed(stored)
        )
        scenario.user._user = scenario.user
        seen = len(scenario.outputs)
        await scenario.click("aside:preview")

    sent = [
        event for event in scenario.outputs[seen:]
        if event["actor"] == "bot" and event["kind"] in ("send", "followup_send")
    ]
    assert len(sent) == 1, scenario.transcript
    assert sent[0]["embed"]["description"] == "<@!555> chegou ao Test Server!"
    pages = [item["label"] for item in sent[0]["components"]]
    assert pages == [ml("buttons.next_message.label", locale="pt-br")], pages
    await scenario.finish()


@WELCOME_SHAPES
async def test_a_new_member_is_welcomed_with_one_saved_message(deps, stored):
    from app.commands.welcome.service import send_welcome_message

    guild, channel = _destination(deps, "boas-vindas")
    member = create_member(guild, id=999, name="Novata")
    member._user = member
    seed_cog(deps.mongo_client, "welcome_messages", {
        **welcome.ENABLED,
        "guild_id": str(guild.id),
        "welcome_messages_channel": {"style": "channel", "values": str(channel.id)},
        "welcome_messages": {"style": "bullet", "values": stored},
    })

    with welcome.banner_stub():
        await send_welcome_message(member)

    description = channel.get_last_embed().description
    assert description in (
        f"<@!999> chegou ao {guild.name}!",
        "Olha quem chegou! <@!999>",
    )


@pytest.mark.parametrize(
    "form", ["notifications_twitch", "notifications_youtube_video"]
)
async def test_cancel_on_an_item_closes_its_setup_as_discarded(
    scenario_factory, stories, monkeypatch, form
):
    """Regression: discarding an item's own screens closes the whole setup as discarded.

    What broke: Cancel on the first screen of an item (the streamer card of
    Twitch or YouTube, a child session under the setup) closed only the child.
    The setup above it stayed waiting for the child, so the message was gone but
    the setup expired later: `setup.abandoned` fired after `setup.discarded` and
    the story closed as abandoned. Once Cancel with nothing answered stopped
    asking (ux-552), this became one click.

    Shared behavior affected: `_discard` and the child ending
    (`ResumeParent`, `ChildFinished`, app/forms/form/form.py) and the
    adapter's closing of a root session (`Runtime._run`, `Runtime._close`).

    Exposed by: notifications_twitch and notifications_youtube_video, Cancel
    on the first streamer card.

    Must remain guaranteed: the setup and its item both end cancelled, the
    setup's story ends discarded, `setup.discarded` fires once and
    nothing is abandoned afterwards.
    """
    events = []
    monkeypatch.setattr(analytics, "record", lambda name, **_: events.append(name))
    scenario = await scenario_factory(locale="pt-br").start(form)
    setup = scenario.session.id
    await scenario.confirm()
    item = scenario.session.id
    assert item != setup

    await scenario.cancel()
    await scenario.expire()

    scenario.expect_status("cancelled", item)
    scenario.expect_status("cancelled", setup)
    endings = [story[2] for story in stories]
    assert endings[-1] == "discarded"
    assert "abandoned" not in endings
    assert events.count("setup.discarded") == 1
    assert "setup.abandoned" not in events
    await scenario.finish()


LOCALE = "pt-br"


EXPIRED = ml("texts.notices.expired", locale=LOCALE)


CONTROLS = (
    discord.ui.Button,
    discord.ui.Select,
    discord.ui.ChannelSelect,
    discord.ui.RoleSelect,
    discord.ui.UserSelect,
)


PAST_EVERY_DEADLINE = datetime.max.replace(tzinfo=timezone.utc)


def _bot_answers(scenario):
    return [event for event in scenario.outputs if event.get("actor") == "bot"]


def _shows_the_expired_message(message, feature):
    shown = "\n".join(
        item.content
        for item in message.registered_items
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert shown.endswith(EXPIRED), shown[-200:]
    assert [i for i in message.registered_items if isinstance(i, CONTROLS)] == []
    reopens = [
        item
        for item in message.registered_items
        if type(item).__name__ == "ReopenButton"
    ]
    assert [button.feature for button in reopens] == [feature]


async def _panel(scenario_factory, deps):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    return await scenario_factory(locale=LOCALE).start_command(block_links.FORM)


def _token_long_gone(monkeypatch):
    """The sweep runs past Discord's interaction token, as it always does live."""
    monkeypatch.setattr(DiscordLimits, "INTERACTION_TOKEN_SECONDS", 0)


async def _twitch_card(scenario_factory, deps):
    scenario = await twitch.open_card(scenario_factory, deps, LOCALE)
    await twitch.pick_channel(scenario)
    return scenario


async def test_a_button_after_a_restart_answers_with_the_expired_message(
    scenario_factory, deps
):
    """Regression: a click on a form whose view is gone answers with the expired message.

    What broke: a form's buttons and selects reach the bot only through the view
    discord.py holds for the message, and that view goes when it times out with the
    session (`ViewConstants.LONG_TIMEOUT_SECONDS`) or when the bot restarts. The
    sweep cannot draw the expiry either, past Discord's 15-minute interaction token.
    So a click on a form left open got no answer at all ("Esta interação falhou"),
    and a modal submitted after its form expired was dropped the same way.

    Shared behavior affected: the routing of every component and modal of the form
    platform (`app/forms/discord/views.py`, `callbacks.py`, the modal listener in
    `app/core/events.py`), for every form.

    Exposed by: the owner's test of s13 (a panel left open past its expiry).

    Must remain guaranteed: a button, a select, a modal-opening button or a modal
    submit that lands after the form expired, after its view timed out or after a
    restart edits that message, with that click, to the expired line and the
    "Abrir de novo" button; a click while the session is alive is left to its live
    view, and gets one answer.
    """
    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    scenario.restart()

    interaction = await scenario.click_after_restart("pause")

    assert interaction.response.is_done(), "the click got an answer"
    _shows_the_expired_message(panel, block_links.FORM)


async def test_a_select_after_its_form_expired_answers_with_the_expired_message(
    scenario_factory, deps, monkeypatch
):
    _token_long_gone(monkeypatch)
    scenario = await _twitch_card(scenario_factory, deps)
    card = scenario.current_message
    await scenario.sweep()
    assert scenario.component("channel", on=card), "the sweep could not draw it"

    await scenario.click_after_restart("channel", on=card)

    _shows_the_expired_message(card, twitch.FORM)


async def test_a_button_that_opens_a_modal_answers_without_the_modal(
    scenario_factory, deps
):
    scenario = await _twitch_card(scenario_factory, deps)
    card = scenario.current_message
    streamer = scenario.component("customize:streamer")
    scenario.restart()

    await scenario.click_after_restart(streamer)

    assert scenario.pending_modal is None, "no modal opens on a dead form"
    _shows_the_expired_message(card, twitch.FORM)


async def test_a_click_past_the_deadline_before_the_sweep_answers_the_same(
    scenario_factory, deps, monkeypatch
):
    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    monkeypatch.setattr(callbacks, "_now", lambda: PAST_EVERY_DEADLINE)

    await scenario.click_after_restart("pause")

    _shows_the_expired_message(panel, block_links.FORM)


async def test_a_modal_submitted_after_its_form_expired_answers_the_same(
    scenario_factory, deps, monkeypatch
):
    _token_long_gone(monkeypatch)
    scenario = await _twitch_card(scenario_factory, deps)
    card = scenario.current_message
    await scenario.click("customize:streamer")
    await scenario.sweep()

    submitted = await scenario.submit_modal_after_restart()

    assert submitted.response.is_done(), "the submit got an answer"
    _shows_the_expired_message(card, twitch.FORM)


async def test_a_click_on_a_live_form_is_left_to_its_view(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    interaction = scenario.interaction(on=panel, target="pause")
    before = len(_bot_answers(scenario))

    await scenario.click_after_restart("pause", interaction=interaction)

    assert len(_bot_answers(scenario)) == before, "the late route answers nothing"
    await scenario.click("pause", on=panel, interaction=interaction)
    assert scenario.current_message is panel
    assert f"({ml('texts.paused_suffix', locale=LOCALE)})" in str(scenario.outputs)


async def test_a_click_both_routes_reach_is_answered_once(scenario_factory, deps):
    """discord.py hands a click to every dynamic item its custom id matches and to
    the live view that holds it: the first route claims the click, the other
    leaves it, so a form past its deadline is never answered twice."""
    import asyncio

    scenario = await _panel(scenario_factory, deps)
    panel = scenario.current_message
    pause = scenario.component("pause")
    interaction = scenario.interaction(on=panel, target=pause)
    scenario.restart()

    await asyncio.gather(
        scenario.click_after_restart(pause, interaction=interaction),
        scenario.click(pause, on=panel, interaction=interaction),
    )

    answers = _bot_answers(scenario)
    assert answers[-1]["kind"] == "edit" and answers[-2]["kind"] != "edit"
    _shows_the_expired_message(panel, block_links.FORM)


def _dynamic_items_declared():
    forms = Path(__file__).resolve().parents[3] / "app" / "forms"
    return {
        node.name
        for path in sorted(forms.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
        and any("DynamicItem" in ast.unparse(base) for base in node.bases)
    }


def test_the_bot_routes_every_dynamic_item_of_the_forms():
    import inspect

    from app.bot import DiscordBot, registered_items
    from app.forms.discord.views import routed_items

    declared = _dynamic_items_declared()

    assert declared, "the forms declare their late and reopen items"
    assert {item.__name__ for item in routed_items()} == declared
    assert set(routed_items()) <= set(registered_items())
    assert "registered_items()" in inspect.getsource(DiscordBot.setup_hook)


async def test_an_expired_message_never_drops_the_reopen_route():
    """discord.py unregisters the template of every dynamic item a removed view
    holds (`ViewStore.remove_view`, run when a view times out), so one expired
    message whose view timed out would stop every Abrir de novo from routing.
    The expired message's view never times out, and holds no control discord.py
    keeps: nothing of it stays in memory."""
    from discord.ui.view import ViewStore

    from app.forms.discord.transitions import Surface, closed_payload
    from app.forms.discord.views import Ids, ReopenButton, reopen_button, routed_items

    ids = Ids("block_links-0123456789abcdef", 3)
    embed_surface = Surface(embed=discord.Embed(description="painel"))
    expired = closed_payload(embed_surface, "expired", LOCALE, "block_links", ids)
    view = expired["view"]

    assert view.timeout is None
    assert [
        item
        for item in view.walk_children()
        if item.is_dispatchable() and not isinstance(item, discord.ui.DynamicItem)
    ] == []

    store = ViewStore(SimpleNamespace())
    store.add_dynamic_items(*routed_items())
    live = discord.ui.View()
    live.add_item(discord.ui.Button(label="Pausar", custom_id=ids("lifecycle", "pause")))
    timing_out = discord.ui.View(timeout=1)
    timing_out.add_item(reopen_button("block_links", ids, "Abrir de novo"))
    store.add_view(live, 1)
    store.add_view(timing_out, 1)

    store.remove_view(timing_out)

    template = ReopenButton.__discord_ui_compiled_template__
    assert template not in store._dynamic_items, "why the expired view never times out"
