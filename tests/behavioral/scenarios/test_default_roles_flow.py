"""Behavioral scenarios: /moderations default roles, a card since beta.2.

Setup flow: intro -> card (roles for bots, roles for members) -> a setup-only
step asking whether to give them to who is already in the server -> review.
"""

import asyncio

import pytest

from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral


async def _settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_a_setup_that_says_yes_gives_the_roles_to_who_is_already_there(
    scenario_factory,
):
    """N3 (beta.2 round 2): after the card, a setup step of its own asks with the
    server's totals and the chosen roles; Sim gives them, after saving, to the
    members already in the server."""
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")
    create_member(guild, id=888, name="Robo", bot=True)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        "default_roles"
    )
    await scenario.confirm()

    await scenario.select_option("Member", target="default_roles")
    await scenario.click("done")
    asked = scenario.expect_message()["embed"]
    member_role = next(role for role in guild.roles if role.name == "Member")
    assert asked["title"] == "🔄 Dar os cargos para quem já está no servidor hoje?"
    assert "Quantidade: **2**" in asked["description"]
    assert "Quantidade: **1**" in asked["description"]
    assert f"<@&{member_role.id}>" in asked["description"]
    assert [button.label for button in scenario.current_message.view.children] == [
        "Sim",
        "Não",
        "Voltar",
        "Cancelar",
    ]

    await scenario.click("pick:True")
    await scenario.confirm()
    await _settle()

    friend._add_roles.assert_awaited()
    assert member_role in friend._add_roles.await_args.args
    stored = scenario.get_persisted("guild", "default_roles", {"guild_id": str(guild.id)})
    assert "sync_existing" not in stored
    await scenario.finish()


async def test_a_setup_left_on_no_gives_the_roles_only_to_whoever_joins(
    scenario_factory,
):
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        "default_roles"
    )
    await scenario.confirm()
    await scenario.select_option("Member", target="default_roles")
    await scenario.click("done")
    await scenario.click("pick:False")
    await scenario.confirm()
    await _settle()

    friend._add_roles.assert_not_awaited()
    await scenario.finish()
