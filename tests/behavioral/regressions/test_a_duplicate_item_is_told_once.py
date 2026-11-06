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
`_on_commit_failed`, app/settings/form/manager.py), and the final duplicate
message of the executor (`Executor.finalize`, app/settings/discord/transitions.py),
used by every list keyed by `unique_by`.

Exposed by: reminders_birthday (`unique_by: user`); notifications_twitch
(`unique_by: streamer`) is covered too, where the nick's validator refuses the
streamer on the card before the list does.

Must remain guaranteed: each duplicate attempt sends exactly one error, the
admin stays on the panel (or the card) to try again, and the next addition
that succeeds sends no error at all.
"""
import pytest

from app.services.utils import ml
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths.common import open_manager
from tests.behavioral.golden.paths.reminders_birthday import (
    _complete_member_card,
    _guild,
    _seed_config,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]

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
    await _complete_member_card(scenario, LOCALE)


async def test_each_duplicate_birthday_is_told_once_and_the_next_member_is_not(
    scenario_factory, deps
):
    _seed_config(deps)
    scenario = await scenario_factory(locale=LOCALE, guild=_guild()).start_command(
        "reminders_birthday"
    )
    duplicate = ml("errors.item-already-registered.title", locale=LOCALE)

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
        "reminders", "birthdays", {"user_id": "777"}, {"user_id": "777"}
    )


@pytest.mark.usefixtures("outside_dev")
async def test_each_duplicate_streamer_is_told_once_and_the_next_streamer_is_not(
    scenario_factory, deps
):
    scenario = await open_manager(scenario_factory, deps, LOCALE, twitch.FORM, twitch._seed)
    await scenario.click("add")
    await twitch._channel(scenario)

    for attempt in range(3):
        start = len(scenario.outputs)
        await twitch._submit_streamer(scenario, "gaules")

        errors = _errors_since(scenario, start)
        assert len(errors) == 1, (
            f"attempt {attempt + 1} on a streamer already listed sent "
            f"{len(errors)} errors\n{scenario.transcript}"
        )

    start = len(scenario.outputs)
    await twitch._submit_streamer(scenario, "shroud")
    await twitch._submit_messages(scenario)
    await scenario.click("done")

    assert _errors_since(scenario, start) == [], scenario.transcript
    saved = deps.mongo_client.guild[twitch.FORM].find_one({})
    streamers = [
        item["streamer"]["value"] for item in saved["notifications"]["values"]
    ]
    assert streamers == ["gaules", "cellbit", "shroud"]


@pytest.fixture
def outside_dev(deps):
    """The subscription hooks run only outside dev."""
    deps.bot.config.is_dev = lambda: False
