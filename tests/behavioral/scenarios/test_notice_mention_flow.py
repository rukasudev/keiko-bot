"""Behavioral scenarios: who a Twitch or YouTube notice mentions (PR8, #51).

Each notice chooses on its own card who it pings: nobody, @everyone, @here or some
roles, a new one starting on @everyone. The roles select shows only for some roles,
and Done asks for a role there; the panel lists the roles only while the notice
mentions them, however many the card kept from an earlier pick.
"""

import json

import discord
import pytest

from app.data import data
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths import notifications_youtube_video as youtube
from tests.behavioral.golden.paths.common import open_manager, seed_document

pytestmark = pytest.mark.behavioral

LOCALE = "pt-br"
ROLES_LINE = "Cargos que eu menciono"


def _texts(scenario):
    return [
        item.content
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _role_selects(scenario):
    return [
        item
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.RoleSelect)
    ]


async def _notices(scenario, form):
    document = await data().guild_cogs.find_cog_by_guild_id(str(scenario.guild.id), form)
    return document["notifications"]["values"]


def _member_role(scenario):
    return next(role for role in scenario.guild.roles if role.name == "Member")


async def _twitch_card(scenario_factory, deps):
    scenario = await twitch._card(scenario_factory, deps, LOCALE)
    await twitch._channel(scenario)
    await twitch._submit_streamer(scenario, "gaules")
    return scenario


async def test_a_new_notice_starts_on_everyone_and_writes_it_in_front(
    scenario_factory, deps
):
    scenario = await _twitch_card(scenario_factory, deps)

    assert "📣 **Mencionar**\n> @everyone" in _texts(scenario)
    assert not _role_selects(scenario)
    await scenario.click("done")
    await scenario.confirm()

    notice = (await _notices(scenario, twitch.FORM))[0]
    assert notice["mention"]["_raw_value"] == "everyone"
    assert notice["notification_messages"]["value"] == (
        "gaules está ao vivo! https://twitch.tv/gaules"
    ), "the message keeps no @everyone of its own"
    await scenario.finish()


async def test_some_roles_asks_for_a_role_before_it_saves(scenario_factory, deps):
    scenario = await _twitch_card(scenario_factory, deps)

    await scenario.select_option("roles", target="mention")
    assert len(_role_selects(scenario)) == 1
    await scenario.click("done")
    refused = json.dumps(scenario.outputs[-1], ensure_ascii=False)
    assert ROLES_LINE in refused, "Done names the role select it still needs"
    scenario.expect_step("notification")

    role = _member_role(scenario)
    await scenario.select_option("Member", target="mention_roles")
    await scenario.click("done")
    await scenario.confirm()

    notice = (await _notices(scenario, twitch.FORM))[0]
    assert notice["mention"]["_raw_value"] == "roles"
    assert notice["mention_roles"]["value"] == str(role.id)
    await scenario.finish()


@pytest.mark.parametrize("feature", [twitch, youtube], ids=["twitch", "youtube"])
@pytest.mark.parametrize(
    "mention, shown, lists_roles",
    [
        ({"value": "@everyone", "_raw_value": "everyone"}, "@everyone", False),
        ({"value": "Alguns cargos", "_raw_value": "roles"}, "Alguns cargos", True),
    ],
    ids=["everyone-with-roles-kept", "some-roles"],
)
async def test_the_panel_lists_the_roles_only_while_the_notice_mentions_them(
    scenario_factory, deps, feature, mention, shown, lists_roles
):
    document = json.loads(json.dumps(feature.ENABLED))
    first = document["notifications"]["values"][0]
    first["mention"] = {**mention, "title": "Mencionar"}
    first["mention_roles"] = {"value": ["201"], "title": ROLES_LINE, "style": "role"}

    def seed(deps):
        known = getattr(feature, "_known_streamers", None) or feature._known_channels
        known(deps)
        seed_document(deps, feature.FORM, document)

    scenario = await open_manager(scenario_factory, deps, LOCALE, feature.FORM, seed)

    panel = "\n".join(_texts(scenario))
    assert f"📣 **Mencionar:** {shown}" in panel
    assert (f"**{ROLES_LINE}:** <@&201>" in panel) is lists_roles
    assert (ROLES_LINE in panel) is lists_roles
    await scenario.finish()
