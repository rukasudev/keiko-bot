"""Baseline safety net: every YAML-driven command starts offline and renders
its real first step. Cheap, runs the real entry seam + engine for all 7
forms, so a shared form-engine change that breaks command startup fails
here before anyone opens Discord.
"""
import pytest

from app.services.utils import parse_form_yaml_to_dict
from tests.behavioral.test_form_yaml_contracts import ALL_FORMS

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("form_engine")]


@pytest.mark.parametrize("command_key", ALL_FORMS)
async def test_form_starts_and_renders_real_first_step(command_key, scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start(command_key)

    intro = parse_form_yaml_to_dict(command_key)[0]
    expected_title = intro["title"]["pt-br"]

    event = scenario.expect_message(kind="send", ephemeral=True)
    embed = event["embed"]
    # parse_form_dict_to_embed may prefix an emoji; the YAML title must appear.
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
    intro = parse_form_yaml_to_dict(command_key)[0]
    event = scenario.expect_message(kind="send", ephemeral=True)
    assert intro["title"]["en-us"].split(" ", 1)[-1] in (event["embed"]["title"] or "")
    await scenario.finish()
