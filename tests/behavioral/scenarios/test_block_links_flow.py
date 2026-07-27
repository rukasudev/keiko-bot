"""Behavioral scenarios: /moderations block links (classic-View flow).

Proves the harness on the classic path: multi_select -> options grid ->
modal -> resume -> default persistence (insert_cog_by_guild).
"""
import pytest

pytestmark = pytest.mark.behavioral


async def test_full_classic_flow_persists_default_document(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    scenario.expect_step("permissions")

    await scenario.select_option("general", target="allowed_chats")
    await scenario.select_option("Admin", target="allowed_roles")
    await scenario.confirm()
    scenario.expect_step("allowed_links")

    await scenario.click("option:Youtube")
    await scenario.click("option:Spotify")
    await scenario.confirm()
    scenario.expect_step("answer")
    scenario.expect_modal(title_contains="Resposta ao bloquear links")

    await scenario.submit_modal({"Digite minha resposta": "Nada de links aqui! :p"})
    scenario.expect_step("confirm")
    scenario.expect_message(title_contains="Tudo certo?")

    await scenario.confirm()

    guild_id = str(scenario.guild.id)
    channel = scenario.guild.text_channels[0]
    role = next(r for r in scenario.guild.roles if r.name == "Admin")
    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": guild_id}, {"enabled": True}
    )
    assert document["allowed_chats"]["values"] == str(channel.id)
    assert document["allowed_roles"]["values"] == str(role.id)
    assert sorted(document["allowed_links"]["values"]
                  if isinstance(document["allowed_links"], dict)
                  else document["allowed_links"]) == ["Spotify", "Youtube"]
    assert document["answer"] == "Nada de links aqui! :p"

    scenario.expect_persisted(
        "guild", "moderations", {"guild_id": guild_id}, {"block_links": True}
    )
    await scenario.finish()


async def test_back_from_options_returns_to_multi_select(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.select_option("general", target="allowed_chats")
    await scenario.confirm()
    scenario.expect_step("allowed_links")

    await scenario.go_back()
    scenario.expect_step("permissions")
    await scenario.finish()
