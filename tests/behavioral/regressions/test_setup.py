"""The greeting that leads a new server to /configurar (`setup`): what its language
select lets a server pick, and what a pick on one greeting leaves on the others."""
import discord
import pytest

from app.commands.setup.service import GreetingsView
from app.core.utils import ml
from tests.behavioral.harness.locators import walk_items

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

LANGUAGE_SELECT = "greetings:language_select"


def language_select(view):
    return next(
        item
        for item in walk_items(view)
        if isinstance(item, discord.ui.Select) and item.custom_id == LANGUAGE_SELECT
    )


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_the_greeting_lets_a_server_pick_one_language(locale):
    """Broke as: the greeting's language select let a member tick both languages at
    once (its `max_values` was 2), while the greeting redraws in the first one only,
    so the second tick did nothing.

    Shared behaviour: `Select` (app/ui/select.py), which opens every option to one
    pick unless told `unique`. Consumer that exposed it: the greeting a new server
    gets (PR9, G1b).

    Guaranteed: the greeting's language select takes exactly one language.
    """
    select = language_select(GreetingsView(locale))

    assert (select.min_values, select.max_values) == (1, 1)


async def test_a_language_pick_leaves_every_other_greeting_answering(
    scenario_factory, deps
):
    """Broke as (by reading discord.py's ViewStore, PR9): after a restart one
    registered greeting view answers every greeting ever posted, and a language pick
    cleared that view's items to draw the new language on it, so the select and the
    buttons of every other greeting were discarded ("This interaction failed") until
    the next restart.

    Shared behaviour: a persistent view a command registers on ready
    (`Hooks.persistent_views`, added by `Events.on_ready`). Consumer that exposed it:
    the greeting's language select.

    Guaranteed: a pick draws the new card on its own message, and the Help of another
    greeting still opens the command list after it.
    """
    registered = GreetingsView()
    scenario = scenario_factory(locale="pt-br")
    other = scenario.post(registered)
    help_on_other = scenario.component("greetings:commands", on=other)
    scenario.post(registered)

    await scenario.select_option("pt-BR")
    await scenario.click(help_on_other, on=other)

    answer = scenario.expect_message(ephemeral=True)
    assert answer["embed"]["title"] == ml("help.ui.records.commands.title", "pt-br")
