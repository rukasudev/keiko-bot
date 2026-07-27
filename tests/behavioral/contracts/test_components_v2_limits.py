"""Guardrails for Discord's Components V2 hard limits.

Discord rejects a LayoutView message that exceeds 40 components or 4000
characters of combined text. These are exactly the errors an offline suite
would otherwise miss (the fake transport accepts anything), so the real
cards are built through real scenarios and checked against the documented
limits here.
"""
import discord
import pytest

from tests.behavioral.harness.locators import walk_items
from tests.behavioral.scenarios.test_reminders_birthday_flow import (
    YES_LABEL,
    _complete_global_card,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]

MAX_COMPONENTS = 40
MAX_TOTAL_TEXT = 4000


def _assert_within_limits(view, label: str) -> None:
    items = list(walk_items(view))
    assert len(items) <= MAX_COMPONENTS, (
        f"{label}: {len(items)} components exceed Discord's limit of {MAX_COMPONENTS}"
    )
    total_text = sum(
        len(item.content or "")
        for item in items
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert total_text <= MAX_TOTAL_TEXT, (
        f"{label}: {total_text} chars of TextDisplay exceed {MAX_TOTAL_TEXT}"
    )


async def test_birthday_global_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_config card")
    await scenario.finish()


async def test_birthday_member_card_is_within_discord_limits(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)
    await scenario.click(YES_LABEL["pt-br"])
    await scenario.select_option("Tester")
    await scenario.confirm()
    _assert_within_limits(scenario.current_message.view, "birthday_member_config card")
    await scenario.finish()
