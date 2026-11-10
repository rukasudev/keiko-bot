"""Contract: whoever opens a feature may end its setup.

`open_feature` takes one optional thing from its caller, and the platform
never learns who the caller is (no source string, no command, no screen): an
`ending`. A setup opened with one draws over the message the click came from
and, once saved, gives that message back drawn by the ending, instead of the
activation embed and the panel below it. The setup records exactly what a
setup without an ending records: the same product events, the same story in
the admin log and the same audit record. A manager opened with an ending
ignores it.

The open path once also took a lifecycle `action`, which only /configurar's
Despausar used; that button went away (PR15 round 2), so the open path takes
the feature, its source, its ending and its `back` (round 5, k03: the first
screen's way back to the opener, `test_an_opener_can_take_a_setup_back.py`),
nothing else.
"""
import inspect

import discord
import pytest

from app.components.buttons import run_feature_command
from app.constants import Commands
from app.settings import open_feature
from app.settings.discord.callbacks import RUNTIME
from app.settings.form.form_state import Manage
from tests.behavioral.contracts.test_a_setup_ends_with_the_activation_and_its_panel import (  # noqa: E501
    SETUPS,
    _activation_embeds,
    _in_guild,
    _records,
)
from tests.behavioral.golden.paths.common import GUILD_ID
from tests.behavioral.harness.driver import FormScenario
from tests.behavioral.harness.locators import walk_items
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

ENDED = "ended by its caller"

def _card(text):
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(text)))
    return view


async def _ending():
    return _card(ENDED)


class Launched(FormScenario):
    """A scenario whose feature opens from a button on a card of its caller."""

    ending = staticmethod(_ending)

    async def _open_feature(self, interaction, command_key):
        launcher = self.store.create([], _card("launcher"), True)
        self.store.record(
            "send", message=launcher.id, view=launcher.view, ephemeral=True
        )
        self.launcher = launcher
        await RUNTIME.open_feature(
            self._mint(message=launcher), command_key, "setup_dashboard", self.ending
        )
        self.store.step_provider = self._current_step_key


def _launched(deps, guild_id):
    def factory(locale="pt-br", guild=None, user=None):
        guild = guild or create_guild()
        guild.id = guild_id
        user = user or create_member(guild, id=555, name="Tester")
        return Launched(guild=guild, user=user, locale=locale, mongo=deps.mongo_client)

    return factory


def _texts(message):
    return [
        item.content
        for item in walk_items(message.view)
        if isinstance(item, discord.ui.TextDisplay)
    ]


@pytest.mark.parametrize("path,locale", SETUPS)
async def test_a_setup_with_an_ending_gives_its_message_back_drawn_by_it(
    path, locale, deps
):
    scenario = await path.run(_launched(deps, 1003), deps, locale)
    await scenario.finish()

    assert _texts(scenario.current_message) == [ENDED]
    assert not _activation_embeds(scenario, locale), "no activation embed"
    sessions = [RUNTIME.store.get(session_id) for session_id in RUNTIME.sessions]
    assert not [
        session for session in sessions if session and isinstance(session.mode, Manage)
    ], "no panel is opened below"
    launcher = scenario.store.alias(scenario.launcher.id)
    first = next(
        event
        for event in scenario.outputs
        if event.get("actor") == "bot" and event.get("message") not in (None,)
        and event["seq"] > 1
    )
    assert first["message"] == launcher, (
        "the setup draws over the message the click came from"
    )


@pytest.mark.parametrize("path", [param.values[0] for param in SETUPS[::2]])
async def test_a_setup_with_an_ending_records_what_any_setup_records(
    path, scenario_factory, deps, analytics_events, stories  # noqa: F811
):
    form = path.form
    ended = await path.run(_launched(deps, 1004), deps, "pt-br")
    plain = await path.run(_in_guild(scenario_factory, 1005), deps, "pt-br")

    on_ending = _records("1004", form, analytics_events, stories, deps)
    on_panel = _records("1005", form, analytics_events, stories, deps)
    assert on_ending[0] and on_ending[1] and on_ending[2]
    sources = {event[1] for event in on_ending[0]}
    assert sources == {"setup_dashboard"}
    assert [event[0] for event in on_ending[0]] == [event[0] for event in on_panel[0]]
    assert [event[2] for event in on_ending[0]] == [event[2] for event in on_panel[0]]
    assert on_ending[1:] == on_panel[1:]
    await ended.finish()
    await plain.finish()


async def test_a_manager_opened_with_an_ending_draws_its_panel_as_ever(deps):
    from tests.behavioral.golden.paths.default_roles import ENABLED
    from tests.behavioral.golden.paths.common import seed_document

    seed_document(deps, Commands.DEFAULT_ROLES_KEY, ENABLED)
    scenario = _launched(deps, int(GUILD_ID))()

    await scenario.start_command(Commands.DEFAULT_ROLES_KEY)

    assert isinstance(scenario.session.mode, Manage)
    assert not scenario.launcher.deleted
    assert scenario.current_message is not scenario.launcher, "a message of its own"
    await scenario.finish()


@pytest.mark.parametrize("opener,expected", [
    (open_feature, ["interaction", "key", "source", "ending", "back"]),
    (RUNTIME.open_feature, ["interaction", "key", "source", "ending", "back"]),
    (run_feature_command, ["interaction", "command_key", "source", "ending", "back"]),
])
def test_the_open_path_takes_the_feature_its_source_its_ending_and_its_back_only(
    opener, expected
):
    assert list(inspect.signature(opener).parameters) == expected
