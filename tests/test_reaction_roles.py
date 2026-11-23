"""Reaction roles: a member's click on a posted menu gives or takes its role and answers
with the member's own copy of the menu, blue where they hold a role; a menu of only
one keeps one role; each refusal tells the member why; and every save keeps the posted
menus as the panel shows them."""

import asyncio
import functools
from types import SimpleNamespace

import discord
import pytest

from tests.behavioral.flows import reaction_roles as flows
from tests.behavioral.flows.common import UPLOADED_URL, dump_channel, seed_document
from tests.behavioral.harness.driver import FormScenario
from tests.helpers.components import card_texts
from tests.mocks.discord import clicked_by, create_guild, create_member

pytestmark = pytest.mark.behavioral

BLUE, GREY = discord.ButtonStyle.primary, discord.ButtonStyle.secondary


@pytest.fixture
def factory(deps):
    """Scenarios on a fresh mock server, with no form session left from another test."""

    def build(locale="pt-br", guild=None, user=None):
        guild = guild or create_guild()
        scenario = FormScenario(
            guild=guild, user=user or create_member(guild, id=555, name="Tester"),
            locale=locale, mongo=deps.mongo_client,
        )
        scenario.restart()
        return scenario

    return build


def held(member):
    return sorted(role.name for role in member.roles)


def give(scenario, *names):
    """The member already holds `names`, given outside the menu."""
    scenario.user.roles.extend(scenario.guild.get_role(flows.ROLES[name]) for name in names)


def last_error(scenario):
    """The title of the last error the member read."""
    return next(
        event["embed"]["title"] for event in reversed(scenario.outputs)
        if (event.get("embed") or {}).get("color") == 0xFF0000
    )


async def test_a_click_gives_the_role_and_a_second_click_takes_it(
    factory, deps, analytics_events
):
    scenario = await flows.set_up_menu(factory, deps)
    [menu] = flows.posted(scenario)

    await flows.click_role(scenario, menu, "Gamer")
    assert held(scenario.user) == ["Gamer"]

    await flows.click_role(scenario, menu, "Gamer")
    assert held(scenario.user) == []
    given = [event for event in analytics_events if event["type"] == "value.delivered"]
    assert [event["cog"] for event in given] == ["reaction_roles"], (
        "the role given is the one delivery the statistics count"
    )


async def test_the_private_copy_is_blue_exactly_where_the_member_has_the_role(
    factory, deps
):
    """The public menu stays grey for everyone; the click answers privately with the
    menu painted for the member, and a click there paints that same copy again."""
    scenario = await flows.set_up_menu(factory, deps)
    [menu] = flows.posted(scenario)
    give(scenario, "Artista", "Leitor")

    await flows.click_role(scenario, menu, "Gamer")
    private = scenario.current_message
    assert private.ephemeral
    assert flows.button_styles(private) == {"Gamer": BLUE, "Artista": BLUE}

    await flows.click_role(scenario, private, "Artista")
    assert scenario.current_message is private, "the private copy is drawn again"
    assert flows.button_styles(private) == {"Gamer": BLUE, "Artista": GREY}
    assert flows.button_styles(menu) == {"Gamer": GREY, "Artista": GREY}
    assert held(scenario.user) == ["Gamer", "Leitor"]


async def test_a_menu_of_only_one_swaps_the_role_and_a_click_on_it_leaves_none(
    factory, deps
):
    """Picking a role takes the menu's other roles and no role outside the menu, and
    a click on the role the member holds takes it, so they may end with none."""
    scenario = await flows.set_up_menu(factory, deps, only_one=True)
    [menu] = flows.posted(scenario)
    give(scenario, "Artista", "Leitor")

    await flows.click_role(scenario, menu, "Gamer")
    assert held(scenario.user) == ["Gamer", "Leitor"]
    assert flows.button_styles(scenario.current_message) == {
        "Gamer": BLUE, "Artista": GREY
    }

    await flows.click_role(scenario, menu, "Gamer")
    assert held(scenario.user) == ["Leitor"]


def clicking_member(factory):
    """A factory whose scenario's user is a member behind Discord's API, on the menus'
    server: what a click carries is a copy of them, their role changes a round trip."""
    guild = flows.menu_server()
    member = create_member(guild, id=555, name="Tester", round_trip=True)
    return guild, member, functools.partial(factory, user=member)


@pytest.mark.parametrize(
    ("only_one", "clicked", "left"),
    [
        pytest.param(True, ("Gamer", "Artista"), 1, id="two_roles_of_only_one"),
        pytest.param(False, ("Gamer", "Gamer"), 0, id="the_same_role_twice"),
    ],
)
async def test_quick_clicks_on_a_menu_each_see_what_the_one_before_did(
    factory, deps, only_one, clicked, left
):
    """Two clicks of one member that Discord delivers together each act on the roles
    the other left: a menu of only one keeps one role, and a double click on a role
    gives it and takes it back.

    Broke as: a click read the member's roles from the copy its own payload carried,
    so a second click sent before the first one's change landed read the roles as they
    were before it: two quick clicks on an "only one" menu gave both roles, and a
    double click on a role gave it twice and never took it back. Shared behaviour:
    one member's clicks run one at a time (`KeyedLocks`, `app/core/locks.py`).
    Consumer that exposed it: reaction roles. Guaranteed: each click reads the
    member's roles again once the click before it ended, so the roles the menu leaves
    are those of the clicks in order.
    """
    guild, member, build = clicking_member(factory)
    scenario = await flows.set_up_menu(build, deps, only_one=only_one, guild=guild)
    [menu] = flows.posted(scenario)

    await asyncio.gather(*(
        flows.click_role(scenario, menu, role, member=clicked_by(member))
        for role in clicked
    ))

    assert len(held(member)) == left


async def test_a_menu_posted_before_a_restart_still_gives_its_role(factory, deps):
    scenario = await flows.set_up_menu(factory, deps)
    [menu] = flows.posted(scenario)

    scenario.restart()
    await flows.click_role(scenario, menu, "Artista")

    assert held(scenario.user) == ["Artista"]


def _paused(scenario, deps):
    seed_document(deps, flows.FORM, flows.PAUSED)
    return flows.GAMES_MENU, "Gamer"


def _menu_gone(scenario, deps):
    flows.seed(deps)
    return "0" * 32, "Gamer"


def _role_out_of_the_menu(scenario, deps):
    flows.seed(deps)
    return flows.GAMES_MENU, "Leitor"


def _role_deleted(scenario, deps):
    flows.seed(deps)
    scenario.guild.roles.remove(scenario.guild.get_role(flows.ROLES["Gamer"]))
    return flows.GAMES_MENU, "Gamer"


def _no_manage_roles(scenario, deps):
    flows.seed(deps)
    scenario.guild.me.guild_permissions = discord.Permissions.none()
    return flows.GAMES_MENU, "Gamer"


def _role_moved_above_keiko(scenario, deps):
    flows.seed(deps)
    scenario.guild.get_role(flows.ROLES["Gamer"]).position = 9
    return flows.GAMES_MENU, "Gamer"


def _discord_refuses(scenario, deps):
    flows.seed(deps)
    scenario.user._add_roles.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"),
        {"code": 50013, "message": "Missing Permissions"},
    )
    return flows.GAMES_MENU, "Gamer"


@pytest.mark.parametrize("arrange,error", [
    pytest.param(_paused, "paused", id="paused"),
    pytest.param(_menu_gone, "menu_gone", id="menu-gone"),
    pytest.param(_role_out_of_the_menu, "role_gone", id="role-out-of-the-menu"),
    pytest.param(_role_deleted, "role_gone", id="role-deleted"),
    pytest.param(_no_manage_roles, "no_permission", id="no-manage-roles"),
    pytest.param(_role_moved_above_keiko, "role_above", id="role-above-keiko"),
    pytest.param(_discord_refuses, "role_above", id="discord-refuses"),
])
async def test_a_click_keiko_cannot_answer_tells_the_member_why(
    factory, deps, arrange, error
):
    """Each refusal is a private apology with a way out, and no role changes."""
    scenario = flows.menu_scenario(factory, guild=flows.menu_server())
    menu_id, role = arrange(scenario, deps)

    await flows.click_menu(scenario, menu_id, role)

    assert last_error(scenario) == flows.error_title(error)
    assert scenario.outputs[-1]["ephemeral"] is True
    assert held(scenario.user) == []


async def test_an_edit_draws_the_posted_menu_again_in_its_message(factory, deps):
    scenario = await flows.set_up_menu(factory, deps)
    [before] = flows.posted(scenario)

    await flows.edit_menu(scenario)
    await flows.type_message(scenario, "🎮 Jogos", "Escolha seus jogos!")
    await scenario.click("done")

    [after] = flows.posted(scenario)
    assert after is before, "the same message is edited"
    assert card_texts(after.view) == ["## 🎮 Jogos", "Escolha seus jogos!"]


async def test_the_message_content_takes_an_image_the_posted_menu_shows(factory, deps):
    """The title, the text and an optional image are typed in one modal, and the
    posted menu holds the three in its card, with the role buttons under the card."""
    dump_channel(deps)
    scenario = await flows.set_up_menu(factory, deps)

    await flows.edit_menu(scenario)
    await flows.type_message(scenario, "🎮 Jogos", "Escolha seus jogos!", "jogos.png")
    await scenario.click("done")

    [menu] = flows.posted(scenario)
    [card, *rows] = menu.view.children
    assert isinstance(card, discord.ui.Container)
    gallery = [item for item in card.children if isinstance(item, discord.ui.MediaGallery)]
    assert [entry.media.url for entry in gallery[0].items] == [UPLOADED_URL]
    assert rows and all(isinstance(row, discord.ui.ActionRow) for row in rows)
    assert not any(isinstance(item, discord.ui.ActionRow) for item in card.children)


async def test_the_message_content_takes_a_footer_the_posted_menu_closes_with(
    factory, deps
):
    """The owner's mark (PR10 round 2): the message content modal takes an optional
    footer, which the posted menu draws as the last line of its card, as typed; a menu
    without one draws no footer."""
    scenario = await flows.set_up_menu(factory, deps)
    [menu] = flows.posted(scenario)
    assert not any(text.startswith("-# ") for text in card_texts(menu.view))

    await flows.edit_menu(scenario)
    await flows.type_message(
        scenario, "🎮 Jogos", "Escolha seus jogos!", footer="Troque quando quiser!"
    )
    await scenario.click("done")

    [menu] = flows.posted(scenario)
    assert card_texts(menu.view) == [
        "## 🎮 Jogos",
        "Escolha seus jogos!",
        "-# Troque quando quiser!",
    ]


async def test_an_edit_with_no_new_image_keeps_the_saved_one(factory, deps):
    dump_channel(deps)
    scenario = await flows.set_up_menu(factory, deps)
    await flows.edit_menu(scenario)
    await flows.type_message(scenario, "🎮 Jogos", "Escolha seus jogos!", "jogos.png")
    await scenario.click("done")

    await flows.edit_menu(scenario)
    await flows.type_message(scenario, "🎮 Jogos", "Outro texto!")
    await scenario.click("done")

    [menu] = flows.posted(scenario)
    [card, *_rows] = menu.view.children
    assert any(isinstance(item, discord.ui.MediaGallery) for item in card.children)
    assert card_texts(menu.view) == ["## 🎮 Jogos", "Outro texto!"]


EXPIRED = "https://cdn.discordapp.com/attachments/999/1/menu.png?ex=665d566a&is=665c04ea&hm=abc&"
REFRESHED = "https://cdn.discordapp.com/attachments/999/1/menu.png?ex=7fffffff&is=7ffe0000&hm=def&"


def image_of(message):
    """The address of the picture a menu's card shows."""
    [card, *_rows] = message.view.children
    return [
        entry.media.url
        for item in card.children
        if isinstance(item, discord.ui.MediaGallery)
        for entry in item.items
    ]


async def test_a_menu_whose_image_link_expired_shows_it_from_a_fresh_link(
    factory, deps
):
    """Broke as: the menu kept the signed link of its picture in the dump channel and
    drew it as saved on every repost and every member's private copy, so once
    Discord's signature ran out (its `ex=` time, a day later) the menu showed a broken
    picture. Shared behaviour: a signed attachment link Keiko shows is signed again
    once it expires (`refresh_attachment_url`, `app/core/images.py`). Consumer that
    exposed it: reaction roles. Guaranteed: a menu posted again and a member's own
    copy show the picture from a link that has not expired."""
    from unittest.mock import AsyncMock

    deps.bot.http.request = AsyncMock(
        return_value={"refreshed_urls": [{"original": EXPIRED, "refreshed": REFRESHED}]}
    )
    games = flows.menu(
        flows.GAMES_MENU, flows.CHANNELS["cargos"],
        [flows.ROLES["Gamer"], flows.ROLES["Artista"]], image=EXPIRED,
    )
    scenario = await flows.open_panel(
        factory, deps, "pt-br",
        seed_with={**flows.ENABLED, "menus": {"style": "composition", "values": [games]}},
    )

    await flows.edit_menu(scenario)
    await scenario.click("done")
    [menu] = flows.posted(scenario)
    assert image_of(menu) == [REFRESHED]

    await flows.click_role(scenario, menu, "Gamer")
    assert image_of(scenario.current_message) == [REFRESHED]


async def test_an_edit_that_moves_the_menu_posts_it_in_its_new_channel(factory, deps):
    scenario = await flows.set_up_menu(factory, deps)

    await flows.edit_menu(scenario)
    await flows.pick_channel(scenario, "cores")
    await scenario.click("done")

    assert flows.posted(scenario, "cargos") == []
    [moved] = flows.posted(scenario, "cores")
    await flows.click_role(scenario, moved, "Gamer")
    assert held(scenario.user) == ["Gamer"]


async def test_a_save_posts_again_a_menu_someone_deleted(factory, deps):
    scenario = await flows.set_up_menu(factory, deps)
    [deleted] = flows.posted(scenario)
    await deleted.delete()

    await flows.edit_menu(scenario)
    await scenario.click("done")

    [again] = flows.posted(scenario)
    assert again is not deleted
    await flows.click_role(scenario, again, "Gamer")
    assert held(scenario.user) == ["Gamer"]


async def test_removing_a_menu_deletes_its_message_and_keeps_the_others(factory, deps):
    scenario = await flows.set_up_menu(factory, deps)
    await flows.add_menu(scenario)
    assert len(flows.posted(scenario, "cores")) == 1

    await scenario.click("remove_this:menus$1", on=flows.admin_screen(scenario))
    await scenario.click("remove_items")

    assert flows.posted(scenario, "cores") == []
    assert len(flows.posted(scenario, "cargos")) == 1


async def test_turning_the_feature_off_deletes_every_posted_menu(factory, deps):
    scenario = await flows.set_up_menu(factory, deps)
    await flows.add_menu(scenario)

    await scenario.click("disable", on=flows.admin_screen(scenario))
    await scenario.click("delete_all")

    assert flows.posted(scenario, "cargos") == flows.posted(scenario, "cores") == []
    assert scenario.get_persisted("guild", "messages", {"cog": "reaction_roles"}) is None
