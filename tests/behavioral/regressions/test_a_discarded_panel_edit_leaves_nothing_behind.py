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
import pytest

from app.core.utils import ml
from tests.behavioral.golden.paths import block_links, notifications_twitch
from tests.behavioral.golden.paths import reminders_birthday as birthdays
from tests.behavioral.golden.paths.common import GUILD_ID, open_manager

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]

LOCALE = "pt-br"


async def _block_links(scenario_factory, deps):
    return await open_manager(
        scenario_factory, deps, LOCALE, block_links.FORM, block_links._seed
    )


async def _twitch(scenario_factory, deps):
    return await open_manager(
        scenario_factory, deps, LOCALE, notifications_twitch.FORM,
        notifications_twitch._seed,
    )


async def _birthdays(scenario_factory, deps):
    await birthdays._seed_config(deps)
    return await scenario_factory(locale=LOCALE, guild=birthdays._guild()).start_command(
        birthdays.FORM
    )


EDITS = {
    "a section edit": (block_links.FORM, _block_links, (), "section:link_settings"),
    "an item edit": (
        notifications_twitch.FORM, _twitch, (), "section:notifications$0"
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
