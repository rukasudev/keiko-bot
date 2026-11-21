"""Behavioral scenarios: /moderações cargos padrão, a card since beta.2.

Guaranteed: after the card, a setup-only question asks, with the server's totals
and the chosen roles, whether to give them to who is already in the server; Sim
gives them after saving to the members already there, Não leaves them for whoever
joins, and the answer is never saved with the settings.
"""

import asyncio
import re

import pytest

from app.core.utils import ml
from tests.behavioral.flows import roles
from tests.behavioral.harness.copy_keys import copy_text
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

QUESTION = "default_roles.form.steps.sync_existing"


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def _asked(scenario_factory, guild):
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(roles.FORM)
    await scenario.confirm()
    await scenario.select_option("Member", target="default_roles")
    await scenario.click("done")
    return scenario


def _member_role(guild):
    return next(role for role in guild.roles if role.name == "Member")


async def test_a_setup_that_says_yes_gives_the_roles_to_who_is_already_there(
    scenario_factory,
):
    """N3 (beta.2 round 2): after the card, a setup step of its own asks with the
    server's totals and the chosen roles; Sim gives them, after saving, to the
    members already in the server."""
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")
    create_member(guild, id=888, name="Robo", bot=True)
    scenario = await _asked(scenario_factory, guild)

    asked = scenario.expect_message()["embed"]
    assert asked["title"].endswith(copy_text(f"{QUESTION}.title", "pt-br"))
    counted = copy_text(
        f"{QUESTION}.description", "pt-br", members_count=2, bots_count=1
    )
    for written in re.split(r"\$\{[^}]*\}", counted):
        assert written in asked["description"], "the members and bots it counted"
    assert f"<@&{_member_role(guild).id}>" in asked["description"]
    assert [button.label for button in scenario.current_message.view.children] == [
        copy_text(f"{QUESTION}.options.0.label", "pt-br"),
        copy_text(f"{QUESTION}.options.1.label", "pt-br"),
        ml("buttons.back.label", "pt-br"),
        ml("buttons.cancel.label", "pt-br"),
    ]

    await scenario.click("pick:True")
    await scenario.confirm()
    await _settle()

    assert _member_role(guild) in friend.roles, "the member already there got the role"
    stored = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})
    assert "sync_existing" not in stored
    await scenario.finish()


async def test_a_setup_left_on_no_gives_the_roles_only_to_whoever_joins(
    scenario_factory,
):
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")
    scenario = await _asked(scenario_factory, guild)

    await scenario.click("pick:False")
    await scenario.confirm()
    await _settle()

    assert _member_role(guild) not in friend.roles
    await scenario.finish()
