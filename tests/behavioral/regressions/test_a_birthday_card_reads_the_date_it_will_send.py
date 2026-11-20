"""Regression: a birthday card's message reads the date it will post, before Done.

What broke: the member card writes a custom message such as "Parabéns, {user}!
Hoje, {date}, é o seu dia!". The card draws that message with its variables
filled, but `{date}` was read from the answer `date`, which only the card's
transform writes, on Done: a new member's card read "Hoje, , é o seu dia!"
while the month and the day were already picked, and an edit kept showing the
old date after the admin picked another month.

Shared behavior affected: `_template_vars` (app/forms/form/actions/
configuration_card.py), which fills the `{name}` tokens of a card's preview
lines from its draft, for every card whose `transform` derives a key from
other keys of the draft.

Exposed by: reminders_birthday, the member card's custom message (owner's
round of 2026-11-17).

Must remain guaranteed: while the card is open, the message preview shows the
date the transform will save from the month and the day on the draft, and a
new month or day changes it at once.
"""
import pytest

from tests.behavioral.golden.paths.reminders_birthday import (
    _guild,
    _seed_config,
    _type_day,
)
from tests.behavioral.harness.locators import walk_items
from tests.generators.birthdays import saved_birthday

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("form_engine"),
]

FORM = "reminders_birthday"
MESSAGE = "Parabéns, {user}! Hoje, {date}, é o seu dia!"
CUSTOMIZE_MESSAGE = "customize:3"


def _message_line(scenario):
    lines = [
        line
        for item in walk_items(scenario.current_message.view.children[0])
        for line in (getattr(item, "content", "") or "").splitlines()
        if line.startswith("> Parabéns")
    ]
    assert lines, "the card draws the custom message"
    return lines[-1]


async def _panel(scenario_factory, deps):
    await _seed_config(deps)
    return await scenario_factory(locale="pt-br", guild=_guild()).start_command(FORM)


async def test_a_new_members_card_reads_the_date_before_done(scenario_factory, deps):
    scenario = await _panel(scenario_factory, deps)
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await scenario.select_option("02", target="month")
    await _type_day(scenario, "28")
    await scenario.click(CUSTOMIZE_MESSAGE)
    await scenario.submit_modal({"Título": "🎂 Dia de festa", "Conteúdo": MESSAGE})

    assert _message_line(scenario) == (
        "> Parabéns, <@777>! Hoje, 28 de fevereiro, é o seu dia!"
    )
    await scenario.finish()


async def test_an_edited_month_changes_the_date_on_the_card_at_once(
    scenario_factory, deps
):
    await _seed_config(deps)
    saved_birthday(
        "123456789", "777", "05-12",
        message={"mode": "custom", "title": "🎂 Dia de festa", "content": MESSAGE},
    )
    scenario = await scenario_factory(locale="pt-br", guild=_guild()).start_command(FORM)
    await scenario.click("search")
    await scenario.select_option("Amiga")
    await scenario.click("edit:reminders_birthday$1")
    assert _message_line(scenario) == "> Parabéns, <@777>! Hoje, 12 de maio, é o seu dia!"

    await scenario.select_option("06", target="month")

    assert _message_line(scenario) == (
        "> Parabéns, <@777>! Hoje, 12 de junho, é o seu dia!"
    )
    await scenario.finish()
