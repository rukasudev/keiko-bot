"""Contract: whoever opens a feature may take its setup back from the first screen.

Beside its `ending`, `open_feature` takes one more optional thing from its
caller, and the platform still never learns who the caller is: a `back`, the
same `Ending` type. A setup opened with one shows a grey Voltar where its first
screen had Cancelar; that Voltar closes the setup quietly, as a discard (no
keep-or-discard question, the same `setup.discarded` and the same discarded
story), and draws the view `back` returns on the setup's message. Every later
screen keeps its own Back and Cancelar, a setup opened without a `back` keeps
Cancelar on its first screen, and a manager opened with one ignores it
(PR15 round 5, k03).
"""
import discord
import pytest

from app.core import analytics
from app.core.utils import ml
from app.settings.discord.callbacks import RUNTIME
from app.settings.form.form_state import Manage, Status
from app.settings.form.form_yaml import DiskSource
from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
from tests.behavioral.golden.paths.default_roles import ENABLED
from tests.behavioral.harness.driver import FormScenario
from tests.behavioral.harness.locators import walk_items
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

CAME_FROM = "the screen the setup was opened from"
FORMS = DiskSource().list()


def _card(text):
    view = discord.ui.LayoutView()
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(text)))
    return view


async def _back():
    return _card(CAME_FROM)


async def _ending():
    return _card("ended")


class Launched(FormScenario):
    """A scenario whose feature opens from a button on a card of its caller."""

    async def _open_feature(self, interaction, command_key):
        launcher = self.store.create([], _card("launcher"), True)
        self.store.record(
            "send", message=launcher.id, view=launcher.view, ephemeral=True
        )
        self.launcher = launcher
        await RUNTIME.open_feature(
            self._mint(message=launcher),
            command_key,
            "setup_dashboard",
            ending=_ending,
            back=_back,
        )
        self.store.step_provider = self._current_step_key


@pytest.fixture
def launched(deps):
    def factory(locale="pt-br"):
        guild = create_guild()
        guild.id = int(GUILD_ID)
        user = create_member(guild, id=555, name="Tester")
        return Launched(guild=guild, user=user, locale=locale, mongo=deps.mongo_client)

    return factory


@pytest.fixture
def analytics_events(monkeypatch):
    recorded = []
    monkeypatch.setattr(analytics, "record", lambda name, **fields: recorded.append(name))
    return recorded


def _labels(scenario):
    return [
        item.label
        for item in walk_items(scenario.current_message.view)
        if isinstance(item, discord.ui.Button)
    ]


def _texts(message):
    return [
        item.content
        for item in walk_items(message.view)
        if isinstance(item, discord.ui.TextDisplay)
    ]


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
@pytest.mark.parametrize("form", FORMS)
async def test_the_first_screen_offers_a_grey_voltar_in_place_of_cancelar(
    launched, form, locale
):
    scenario = await launched(locale).start_command(form)

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
    launched, form, analytics_events, stories  # noqa: F811
):
    scenario = await launched().start_command(form)
    session_id = scenario.session.id

    await scenario.click("back")

    assert _texts(scenario.current_message) == [CAME_FROM]
    live = [message for message in scenario.store.messages.values() if not message.deleted]
    assert live == [scenario.current_message], "one message, the one it came from"
    discard = ml("errors.discard-settings-confirmation.title", "pt-br")
    assert not [
        event for event in scenario.outputs
        if (event.get("embed") or {}).get("title", "").endswith(discard)
    ], "nothing to keep, nothing asked"
    assert RUNTIME.store.get(session_id).status is Status.CANCELLED
    assert "setup.discarded" in analytics_events
    assert stories[-1][2] == "discarded", "the story ends as a discard"
    await scenario.finish()


async def test_the_later_screens_keep_their_cancelar(launched):
    scenario = await launched().start_command("block_links")

    await scenario.confirm()

    assert _labels(scenario)[-1] == "Cancelar"
    assert "Voltar" not in _labels(scenario)
    await scenario.finish()


async def test_a_setup_from_its_own_command_keeps_cancelar(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")

    assert _labels(scenario)[-1] == "Cancelar"
    assert "Voltar" not in _labels(scenario)
    await scenario.finish()


async def test_a_manager_opened_with_a_back_draws_its_panel_as_ever(launched, deps):
    seed_document(deps, "default_roles", ENABLED)

    scenario = await launched().start_command("default_roles")

    assert isinstance(scenario.session.mode, Manage)
    assert scenario.current_message is not scenario.launcher, "a message of its own"
    await scenario.finish()
