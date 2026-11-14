"""Behavioral scenarios: the panel a setup sends below its activation embed.

Every setup ends like this (round 7 J3; the contract over every form is
`tests/behavioral/contracts/test_a_setup_ends_with_the_activation_and_its_panel.py`).
The birthdays are the form whose list is filled from that panel: its Adicionar
opens the member's card and the add returns to the panel. The panel left
untouched is no abandoned setup, a change saved from it tells a story of its
own, and when the feature cannot be read again only the activation embed stays.
"""
import pytest

from app.core.utils import ml
from app.settings.features import feature_for
from app.settings.form.form_state import Manage
from tests.behavioral.golden.paths.reminders_birthday import (
    _complete_member_card,
    _complete_settings_card,
)
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

FORM = "reminders_birthday"
LOCALE = "pt-br"
ACTIVATED = ml("commands.command-events.enabled.title", locale=LOCALE)
PANEL_INTRO = "Comemoro o aniversário dos membros com as configurações abaixo."



def _guild(guild_id=123456789):
    guild = create_guild(id=guild_id, channels=["general"], roles=["Member"])
    create_member(guild, id=777, name="Amiga", roles=["Member"])
    return guild


def _screens(scenario):
    return [
        event
        for event in scenario.outputs
        if event.get("actor") == "bot" and event.get("components_v2")
    ]


def _activation_embeds(scenario):
    return [
        event
        for event in scenario.outputs
        if ((event.get("embed") or {}).get("title") or "") == ACTIVATED
    ]


async def _set_up(scenario_factory, guild):
    scenario = await scenario_factory(locale=LOCALE, guild=guild).start_command(FORM)
    await scenario.confirm()
    await _complete_settings_card(scenario)
    summary = _screens(scenario)[-1]["message"]
    await scenario.confirm()
    return scenario, summary


def _deleted(scenario):
    return [
        event["message"] for event in scenario.outputs if event.get("kind") == "delete"
    ]


async def test_the_birthday_setup_sends_the_panel_below_its_activation(
    scenario_factory, deps, analytics_events
):
    scenario, summary = await _set_up(scenario_factory, _guild())

    [activation] = _activation_embeds(scenario)
    panel = _screens(scenario)[-1]
    assert summary in _deleted(scenario), "the activation replaces the summary"
    assert not activation.get("components"), "the activation keeps no controls"
    assert panel["kind"] == "followup_send", "the panel is a new message"
    assert panel["message"] not in (summary, activation["message"])
    assert panel["seq"] > activation["seq"], "the panel comes right below it"
    assert activation["message"] not in _deleted(scenario), "the record stays"
    assert PANEL_INTRO in str(panel), "the screen is the manager panel"
    assert isinstance(scenario.session.mode, Manage)

    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    card = _screens(scenario)[-1]["message"]
    await _complete_member_card(scenario, LOCALE)

    after = _screens(scenario)[-1]
    assert after["message"] == card, "the add returns to the panel on its message"
    assert PANEL_INTRO in str(after) and "<@777>" in str(after)
    scenario.expect_component(label_or_action="add")
    scenario.expect_persisted(
        "guild",
        "members",
        {"guild_id": str(scenario.guild.id), "user_id": "777", "options.reminders_birthday.date": "05-12"},
        {},
    )
    names = [event["type"] for event in analytics_events]
    assert names.count("feature.enabled") == 1
    assert "feature.item_added" in names
    assert "feature.manager_opened" not in names
    await scenario.finish()


async def test_the_panel_left_untouched_is_not_an_abandoned_setup(
    scenario_factory, deps, analytics_events, stories
):
    """The panel carries the save of the setup it follows: its expiry adds nothing."""
    scenario, _ = await _set_up(scenario_factory, _guild())
    told = list(stories)
    recorded = len(analytics_events)

    await scenario.expire()

    assert [event["type"] for event in analytics_events[recorded:]] == []
    assert stories == told
    await scenario.finish()


async def test_a_member_added_from_that_panel_tells_a_story_of_its_own(
    scenario_factory, deps, stories
):
    scenario, _ = await _set_up(scenario_factory, _guild())
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await _complete_member_card(scenario, LOCALE)
    await scenario.expire()

    results = [(story[2], story[3]) for story in stories]
    assert [result for result, _ in results[:2]] == ["in progress", "saved"]
    assert results[2:] == [("in progress", ()), ("added", ("➕ item added",))], (
        "the panel's story opens with its first save and ends as that save"
    )
    await scenario.finish()


async def test_a_panel_that_cannot_be_read_leaves_the_activation_embed(
    scenario_factory, deps, stories, monkeypatch
):
    """The settings are saved either way: when the feature cannot be read again,
    the activation embed stays and no panel follows it."""
    feature = feature_for(FORM)
    reads = {"count": 0}
    original = feature.open

    async def open_once(context):
        reads["count"] += 1
        if reads["count"] > 1:
            raise RuntimeError("the database is away")
        return await original(context)

    monkeypatch.setattr(feature, "open", open_once)
    scenario, _ = await _set_up(scenario_factory, _guild())

    assert _activation_embeds(scenario)
    assert PANEL_INTRO not in str(_screens(scenario)), "no panel is sent"
    assert [story[2] for story in stories] == ["in progress", "saved"]
    await scenario.finish()
