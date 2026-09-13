"""Baseline safety net: every YAML-driven command starts offline and renders
its real first step. Cheap, runs the real entry seam + engine for all 7
forms, so a shared platform change that breaks command startup fails
here before anyone opens Discord.
"""
import pytest

from app.forms.definitions.registry import registry
from app.forms.features import feature_keys

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]


def _intro_title(command_key: str, locale: str) -> str:
    intro = registry.get(command_key).steps[0]
    return intro.title.get(locale)


@pytest.mark.parametrize("command_key", sorted(feature_keys()))
async def test_form_starts_and_renders_real_first_step(command_key, scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start(command_key)

    expected_title = _intro_title(command_key, "pt-br")
    event = scenario.expect_message(kind="send", ephemeral=True)
    embed = event["embed"]
    assert expected_title.split(" ", 1)[-1] in (embed["title"] or ""), (
        f"{command_key}: first embed title {embed['title']!r} does not "
        f"contain YAML title {expected_title!r}"
    )
    assert embed["description"], f"{command_key}: intro embed has no description"
    scenario.expect_component(label_or_action="continue")
    scenario.expect_component(label_or_action="cancel")
    await scenario.finish()


@pytest.mark.parametrize("command_key", ["default_roles", "welcome_messages"])
async def test_form_first_step_renders_in_english_too(command_key, scenario_factory):
    scenario = await scenario_factory(locale="en-us").start(command_key)
    event = scenario.expect_message(kind="send", ephemeral=True)
    assert _intro_title(command_key, "en-us").split(" ", 1)[-1] in (event["embed"]["title"] or "")
    await scenario.finish()
