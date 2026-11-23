"""Behavioral scenarios: /moderações cargos reação, from the setup to the panel.

Guaranteed: the menu's card comes with its title, text and mode already set and asks
for the channel and the roles before it saves; the mode is told in the choice layout,
one line per mode led by its emoji; the role select takes at most ten roles; the setup
posts the menu in its channel as a card of grey buttons, one per role, kept under the
id the saved menu carries; and the panel adds menus, each posted at once, up to three.
"""

import discord
import pytest

from app.constants import Commands, Style
from tests.behavioral.flows import reaction_roles as flows
from tests.behavioral.harness.copy_keys import copy_text
from tests.helpers.components import card_texts

pytestmark = pytest.mark.behavioral

CARD = "reaction_roles.form.steps.menus.steps.menu"


def _card(path, locale):
    return copy_text(f"{CARD}.{path}", locale)


def _select(scenario, key):
    return next(
        item
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.Select | discord.ui.RoleSelect)
        and item.custom_id.endswith(f":{key}")
    )


async def test_the_card_comes_with_its_message_and_mode_and_asks_for_the_rest(
    scenario_factory, deps, each_locale
):
    scenario = await flows.open_card(scenario_factory, deps, each_locale)

    assert {key: scenario.card_state.get(key) for key in ("title", "text", "mode")} == {
        "title": _card("defaults.title", each_locale),
        "text": _card("defaults.text", each_locale),
        "mode": "several",
    }
    await scenario.click("done")
    scenario.expect_step("menu")
    await scenario.finish()


async def test_the_mode_asks_in_one_sentence_and_each_option_explains_itself(
    scenario_factory, deps, each_locale
):
    """The mode's section asks in one sentence, and each option of its select carries
    its own line under its label, so the card explains no option twice."""
    scenario = await flows.open_card(scenario_factory, deps, each_locale)

    description = _card("sections.mode.description", each_locale)
    options = _select(scenario, "mode").options
    assert "\n" not in description
    assert all(option.description for option in options) and len(options) == 2
    assert not any(option.label in description for option in options)
    assert description in "\n".join(card_texts(scenario.current_message.view))
    await scenario.finish()


async def test_the_role_select_takes_at_most_the_roles_a_menu_holds(
    scenario_factory, deps
):
    scenario = await flows.open_card(scenario_factory, deps, "pt-br")

    assert _select(scenario, "roles").max_values == Commands.REACTION_ROLES_ROLES_MAX
    await scenario.finish()


async def test_the_setup_posts_the_menu_as_a_card_of_grey_role_buttons(
    scenario_factory, deps, each_locale
):
    scenario = await flows.set_up_menu(scenario_factory, deps, each_locale)

    [menu] = flows.posted(scenario)
    assert not menu.ephemeral
    assert card_texts(menu.view) == [
        f"## {_card('defaults.title', each_locale)}",
        _card("defaults.text", each_locale),
    ]
    assert flows.button_styles(menu) == {
        "Gamer": discord.ButtonStyle.secondary,
        "Artista": discord.ButtonStyle.secondary,
    }
    [saved] = scenario.get_persisted("guild", "reaction_roles", {})["menus"]["values"]
    kept = scenario.get_persisted("guild", "messages", {"cog": "reaction_roles"})
    assert (kept["subject"], kept["channel_id"], kept["message_id"]) == (
        saved["state"]["menu_id"],
        str(flows.CHANNELS["cargos"]),
        str(menu.id),
    )
    await scenario.finish()


async def test_the_panel_adds_menus_each_posted_at_once_up_to_three(
    scenario_factory, deps
):
    scenario = await flows.set_up_menu(scenario_factory, deps)

    await flows.add_menu(scenario, "cores", ("Azul", "Verde"))
    await flows.add_menu(scenario, "cargos", ("Leitor",))

    assert len(flows.posted(scenario, "cargos")) == 2
    assert len(flows.posted(scenario, "cores")) == 1
    assert scenario.component("add", on=flows.admin_screen(scenario)).disabled
    await scenario.finish()


@pytest.mark.parametrize(
    ("power", "warned"),
    [
        pytest.param("administrator", True, id="administrator"),
        pytest.param("manage_guild", True, id="manage_server"),
        pytest.param("manage_roles", True, id="manage_roles"),
        pytest.param("ban_members", True, id="ban_members"),
        pytest.param("send_messages", False, id="a_plain_role"),
    ],
)
async def test_a_role_that_moderates_the_server_is_kept_with_a_warning(
    scenario_factory, deps, power, warned
):
    """A role with a moderator's power stays on the menu, and the admin is told on a
    warning embed that any member will be able to take it; a plain role is picked
    without a word."""
    guild = flows.menu_server()
    gamer = guild.get_role(flows.ROLES["Gamer"])
    gamer.permissions = discord.Permissions(**{power: True})
    scenario = await flows.open_card(scenario_factory, deps, "pt-br", guild)
    seen = len(scenario.outputs)

    await flows.pick_roles(scenario, "Gamer", "Artista")

    assert scenario.card_state.get("roles") == [
        str(gamer.id),
        str(flows.ROLES["Artista"]),
    ]
    warning = copy_text(
        f"{CARD}.sections.roles.selects.roles.powerful_roles_warning",
        "pt-br",
        roles=gamer.mention,
    )
    told = [
        (event.get("embed") or {}).get("description")
        for event in scenario.outputs[seen:]
        if (event.get("embed") or {}).get("color") == int(Style.WARNING_COLOR, 16)
    ]
    assert [warning in description for description in told] == (
        [True] if warned else []
    )
    await scenario.finish()


async def test_the_slash_command_opens_the_reaction_roles_setup(scenario_factory, deps):
    """`/moderações cargos reação` opens the same first screen of the Cargos por
    Reação setup that `/configurar` opens."""
    from app.commands.reaction_roles.cog import ReactionRoles

    by_command = scenario_factory(locale="pt-br")
    by_command.user.guild_permissions = discord.Permissions(administrator=True)
    await by_command.run_command(ReactionRoles.reaction_roles, ReactionRoles(deps.bot))
    by_setup = await scenario_factory(locale="pt-br").start_command(flows.FORM)

    opened = by_command.expect_message()["embed"]
    assert opened == by_setup.expect_message()["embed"]
    assert opened["title"].endswith(
        copy_text("reaction_roles.form.steps.form.title", "pt-br")
    )
