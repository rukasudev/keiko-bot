"""A birthday panel left open after the feature was deleted elsewhere saves nothing.

Broke as: the birthday commits (`BirthdayFeature` in app/commands/birthdays/service.py)
wrote straight to `guild.members` and `guild.cogs`, never asking whether the feature
was still saved, while every generic commit starts with `saved_document`. A panel
opened before another panel deleted the birthdays added a member's birthday (and its
reminder) to a server with no birthday feature, and an edit of the settings wrote the
config document back, half a feature nobody turned on.

Shared behaviour: the stale-commit rule of the manager (`saved_document`, the refusal
it raises and the `Finalize("closed")` it ends on). Exposed by: the birthday panel,
the one feature with commits of its own for every change.

Guaranteed: after the feature is deleted, an Add, an edit of the settings, an edit of
a member and a removal from a panel opened before write nothing, record nothing, and
close that panel in place with the closed notice.
"""

import json
from datetime import datetime, timezone

import pytest

from app.commands.birthdays.service import FEATURE, birthday_config, find_birthday
from app.core.utils import ml
from app.forms.features.feature import CommitContext
from tests.behavioral.golden.paths.common import GUILD_ID
from tests.behavioral.golden.paths.reminders_birthday import (
    FORM,
    _guild,
    _seed_config,
    _type_day,
)
from tests.generators.events import lifecycle

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

LOCALE = "pt-br"


async def _add(panel):
    await panel.click("add")
    await panel.select_option("Amiga", target="user")
    await panel.select_option("02", target="month")
    await _type_day(panel, "28", LOCALE)
    await panel.click("done")


async def _edit_settings(panel):
    await panel.click("edit:group:settings")
    await panel.click("section:birthday_config")
    await panel.select_option("12:00", target="notification_time")
    await panel.click("done")


async def _edit_member(panel):
    await panel.click("search")
    await panel.select_option("Tester")
    await panel.click("edit:reminders_birthday$0")
    await panel.select_option("03", target="month")
    await panel.click("done")


async def _remove_member(panel):
    await panel.click("search")
    await panel.select_option("Tester")
    await panel.click("remove_this:reminders_birthday$0")
    await panel.click("remove_items")


def _closed_in_place(scenario):
    drawn = [
        event
        for event in scenario.outputs
        if event.get("actor") == "bot"
        and (event.get("components_v2") or event.get("embed"))
    ]
    last = json.dumps(drawn[-1], ensure_ascii=False)
    return ml("texts.notices.closed", locale=LOCALE) in last and '"actionrow"' not in last


async def _deleted_elsewhere():
    context = CommitContext(
        GUILD_ID, "556", LOCALE, {}, datetime.now(timezone.utc), "manager", "s2"
    )
    await FEATURE.commit("disable", {}, context)


@pytest.mark.shared_contract("manager_form")
@pytest.mark.parametrize(
    "change",
    [_add, _edit_settings, _edit_member, _remove_member],
    ids=["add", "edit_settings", "edit_member", "remove"],
)
async def test_a_birthday_change_after_the_feature_was_deleted_writes_nothing(
    scenario_factory, deps, change
):
    await _seed_config(deps, members=("555",))
    guild = _guild()
    panel = await scenario_factory(locale=LOCALE, guild=guild).start_command(FORM)

    await _deleted_elsewhere()
    await change(panel)

    assert await birthday_config(GUILD_ID) is None, "the config came back"
    assert await find_birthday(GUILD_ID, "777") is None, "a birthday was added"
    assert await find_birthday(GUILD_ID, "555") is None, "a birthday was written back"
    assert lifecycle(deps.mongo_client, GUILD_ID, FORM) == ["disabled"]
    assert _closed_in_place(panel)
