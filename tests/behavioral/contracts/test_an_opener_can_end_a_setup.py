"""Contract: whoever opens a feature may end its setup and take it back.

`open_feature` takes two optional things from its caller, and the platform never
learns who the caller is (no source string, no command, no screen): an `ending`
and a `back`, both the same `Ending` type.

A setup opened with an ending draws over the message the click came from and,
once saved, gives that message back drawn by the ending, instead of the
activation embed and the panel below it. The setup records exactly what a setup
without an ending records: the same product events, the same story in the admin
log and the same audit record.

A setup opened with a `back` shows a grey Voltar where its first screen had
Cancelar; that Voltar closes the setup quietly, as a discard (no keep-or-discard
question, the same `setup.discarded` and the same discarded story), and draws the
view `back` returns on the setup's message. Every later screen keeps its own Back
and Cancelar, and a setup opened without a `back` keeps Cancelar on its first
screen (PR15 round 5, k03).

A manager opened with either ignores it. The open path once also took a lifecycle
`action`, which only /configurar's Despausar used; that button went away (PR15
round 2), so the open path takes the feature, its source, its ending and its
`back`, nothing else.
"""
import inspect

import discord
import pytest

from app.core.utils import ml
from app.forms import open_feature, run_feature_command
from app.forms.discord.callbacks import Runtime
from app.forms.form.form_yaml import DiskSource
from tests.behavioral.flows.common import GUILD_ID, seed_document
from tests.behavioral.flows.roles import ENABLED
from tests.behavioral.harness.locators import walk_items
from tests.helpers.activation import SETUPS, activation_embeds, in_guild, records

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

ENDED = "ended by its caller"
CAME_FROM = "the screen the setup was opened from"
FORMS = DiskSource().list()


def _card(text):
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(text)))
    return view


async def _ending():
    return _card(ENDED)


async def _back():
    return _card(CAME_FROM)


def _opened_from_a_card(factory, *, back=None):
    """`factory`'s scenarios open every command from a card, as /setup's rows do."""
    def opened(locale="pt-br", guild=None, user=None):
        return factory(locale=locale, guild=guild, user=user).open_from(
            _card("launcher"), source="setup_dashboard", ending=_ending, back=back
        )

    return opened


def _texts(message):
    return [
        item.content
        for item in walk_items(message.view)
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _labels(scenario):
    return [
        item.label
        for item in walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.Button)
    ]


@pytest.mark.parametrize("path,locale", SETUPS)
async def test_a_setup_with_an_ending_gives_its_message_back_drawn_by_it(
    path, locale, scenario_factory, deps
):
    opened = _opened_from_a_card(in_guild(scenario_factory, 1003))
    scenario = await path.run(opened, deps, locale)
    await scenario.finish()

    assert _texts(scenario.current_message) == [ENDED]
    assert not activation_embeds(scenario, locale), "no activation embed"
    assert not [
        session for session in scenario.sessions if session.mode.kind == "manage"
    ], "no panel is opened below"
    first = next(event for event in scenario.outputs if event.get("actor") == "bot")
    assert first["message"] == scenario.alias(scenario.launcher), (
        "the setup starts from the message the click came from"
    )


@pytest.mark.parametrize("path", [param.values[0] for param in SETUPS[::2]])
async def test_a_setup_with_an_ending_records_what_any_setup_records(
    path, scenario_factory, deps, analytics_events, stories  # noqa: F811
):
    form = path.form
    ended = await path.run(
        _opened_from_a_card(in_guild(scenario_factory, 1004)), deps, "pt-br"
    )
    plain = await path.run(in_guild(scenario_factory, 1005), deps, "pt-br")

    on_ending = records("1004", form, analytics_events, stories, deps)
    on_panel = records("1005", form, analytics_events, stories, deps)
    assert on_ending[0] and on_ending[1] and on_ending[2]
    sources = {event[1] for event in on_ending[0] if event[1] is not None}
    assert sources == {"setup_dashboard"}, "an event that keeps its source lost it"
    assert [event[0] for event in on_ending[0]] == [event[0] for event in on_panel[0]]
    assert [event[2] for event in on_ending[0]] == [event[2] for event in on_panel[0]]
    assert on_ending[1:] == on_panel[1:]
    await ended.finish()
    await plain.finish()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
@pytest.mark.parametrize("form", FORMS)
async def test_the_first_screen_offers_a_grey_voltar_in_place_of_cancelar(
    scenario_factory, form, locale
):
    opened = _opened_from_a_card(in_guild(scenario_factory, int(GUILD_ID)), back=_back)
    scenario = await opened(locale).start_command(form)

    labels = _labels(scenario)
    assert labels[-1] == ml("buttons.back.label", locale)
    assert ml("buttons.cancel.label", locale) not in labels
    back = [
        item for item in walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.Button) and item.label == labels[-1]
    ]
    assert [item.style for item in back] == [discord.ButtonStyle.secondary]
    await scenario.finish()


@pytest.mark.parametrize("form", FORMS)
async def test_voltar_draws_the_screen_it_came_from_and_closes_the_setup_quietly(
    scenario_factory, form, analytics_events, stories  # noqa: F811
):
    opened = _opened_from_a_card(in_guild(scenario_factory, int(GUILD_ID)), back=_back)
    scenario = await opened().start_command(form)
    session_id = scenario.session.id

    await scenario.click("back")

    assert _texts(scenario.current_message) == [CAME_FROM]
    assert scenario.live_messages == [scenario.current_message], (
        "one message, the one it came from"
    )
    discard = ml("commands.responses.discard.title", "pt-br")
    assert not [
        event for event in scenario.outputs
        if (event.get("embed") or {}).get("title", "").endswith(discard)
    ], "nothing to keep, nothing asked"
    scenario.expect_status("cancelled", session_id)
    assert "setup.discarded" in [event["type"] for event in analytics_events]
    assert stories[-1][2] == "discarded", "the story ends as a discard"
    await scenario.finish()


async def test_the_later_screens_keep_their_cancelar(scenario_factory):
    opened = _opened_from_a_card(in_guild(scenario_factory, int(GUILD_ID)), back=_back)
    scenario = await opened().start_command("block_links")

    await scenario.confirm()

    assert _labels(scenario)[-1] == ml("buttons.cancel.label", "pt-br")
    assert ml("buttons.back.label", "pt-br") not in _labels(scenario)
    await scenario.finish()


async def test_a_setup_from_its_own_command_keeps_cancelar(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")

    assert _labels(scenario)[-1] == ml("buttons.cancel.label", "pt-br")
    assert ml("buttons.back.label", "pt-br") not in _labels(scenario)
    await scenario.finish()


@pytest.mark.parametrize("back", [None, _back], ids=["ending", "ending_and_back"])
async def test_a_manager_opened_from_a_card_draws_its_panel_as_ever(
    scenario_factory, deps, back
):
    seed_document(deps, "default_roles", ENABLED)
    opened = _opened_from_a_card(in_guild(scenario_factory, int(GUILD_ID)), back=back)
    scenario = opened()

    await scenario.start_command("default_roles")

    scenario.expect_mode("manage")
    assert not scenario.launcher.deleted
    assert scenario.current_message is not scenario.launcher, "a message of its own"
    await scenario.finish()


@pytest.mark.parametrize("opener,expected", [
    (open_feature, ["interaction", "key", "source", "ending", "back"]),
    (Runtime.open_feature, ["self", "interaction", "key", "source", "ending", "back"]),
    (run_feature_command, ["interaction", "command_key", "source", "ending", "back"]),
])
def test_the_open_path_takes_the_feature_its_source_its_ending_and_its_back_only(
    opener, expected
):
    assert list(inspect.signature(opener).parameters) == expected
