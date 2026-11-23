"""Reaction roles: the saved menus, the server whose channels show what Keiko posts in
them on the screen, the card's steps (the channel, the roles, the mode) and a member's
click on a posted menu."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

import discord

from app.commands.reaction_roles.service import ReactionRoleButton
from app.core.utils import ml
from tests.behavioral.flows.common import GUILD_ID, seed_document
from tests.mocks.discord import MockChannel, MockRole, create_guild

FORM = "reaction_roles"

GAMES_MENU = "0123456789abcdef0123456789abcdef"
COLOURS_MENU = "fedcba9876543210fedcba9876543210"
ROLES = {"Gamer": 210, "Artista": 211, "Leitor": 212, "Azul": 213, "Verde": 214}
CHANNELS = {"cargos": 103, "cores": 104}


def menu(menu_id: str, channel: int, roles: list, mode: str = "several",
         title: str = "🏷️ Escolha seus cargos",
         text: str = "Clique nos botões para pegar ou largar um cargo!",
         footer: Optional[str] = None, image: Optional[str] = None) -> dict:
    """One menu as the card saves it: `roles` (ids) as buttons in `channel`, in
    `mode`, its message content one line under its title, the text, footer and image
    kept hidden beside it."""
    content = "Conteúdo da mensagem"
    return {
        "channel": {"value": str(channel), "title": "Canal", "style": "channel"},
        "text": {"value": text, "title": content, "hidden": True},
        "image": {"value": image, "title": content, "hidden": True},
        "title": {"value": title, "title": content},
        "footer": {"value": footer, "title": "Rodapé", "hidden": True},
        "mode": {"value": "✅ Vários cargos" if mode == "several" else "🎯 Só um",
                 "title": "Modo", "_raw_value": mode},
        "roles": {"value": [str(role) for role in roles], "title": "Cargos",
                  "style": "role"},
        "state": {"menu_id": menu_id},
    }


ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "menus": {
        "style": "composition",
        "values": [
            menu(GAMES_MENU, CHANNELS["cargos"], [ROLES["Gamer"], ROLES["Artista"]]),
            menu(COLOURS_MENU, CHANNELS["cores"], [ROLES["Azul"], ROLES["Verde"]],
                 mode="one", title="🎨 Escolha sua cor", text="Uma cor por membro!"),
        ],
    },
}
PAUSED = {**ENABLED, "enabled": False}


@dataclass
class MenuChannel(MockChannel):
    """A text channel whose messages land on the scenario's screen, where members
    click them; one deleted is no longer found by its id, as on Discord."""

    scenario: Any = None

    async def _handle_send(self, content=None, *, embed=None, view=None,
                           delete_after=None, **kwargs):
        if "send_messages" in self.denied:
            raise discord.Forbidden(
                SimpleNamespace(status=403, reason="Forbidden"),
                {"code": 50013, "message": "Missing Permissions"},
            )
        message = self.scenario.post(view, embed=embed)
        self._sent_messages.append(message)
        return message

    async def _handle_fetch_message(self, message_id):
        for message in self._sent_messages:
            if str(message.id) == str(message_id) and not message.deleted:
                return message
        raise discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"),
            {"code": 10008, "message": "Unknown Message"},
        )


def menu_server():
    """The mock server with the menus' roles, all below Keiko's."""
    guild = create_guild()
    for name, role_id in ROLES.items():
        guild.roles.append(MockRole(id=role_id, name=name, position=2, guild=guild))
    return guild


def menu_scenario(scenario_factory, locale: str = "pt-br", guild=None):
    """A scenario on the menus' server, whose menu channels post on its screen."""
    guild = guild or menu_server()
    scenario = scenario_factory(locale=locale, guild=guild)
    for name, channel_id in CHANNELS.items():
        guild.add_channel(MenuChannel(id=channel_id, name=name, scenario=scenario))
    return scenario


def channel(scenario, name: str = "cargos") -> MenuChannel:
    """The menu channel `name` of the scenario's server."""
    return scenario.guild.get_channel(CHANNELS[name])


def posted(scenario, name: str = "cargos") -> list:
    """The menus Keiko posted in the channel `name` that are still there."""
    return [message for message in channel(scenario, name).get_all_messages()
            if not message.deleted]


async def open_card(scenario_factory, deps, locale, guild=None):
    """The setup opened and its intro confirmed: a menu's card on screen."""
    scenario = await menu_scenario(scenario_factory, locale, guild).start_command(FORM)
    await scenario.confirm()
    return scenario


async def set_up_menu(scenario_factory, deps, locale="pt-br", roles=("Gamer", "Artista"),
                      only_one=False, guild=None, channel_name="cargos"):
    """The setup walked with one menu of `roles` in `channel_name`, saved: the menu
    posted there and the panel on screen (`general` keeps what it posts off the
    screen, as a channel the admin is not looking at)."""
    scenario = await open_card(scenario_factory, deps, locale, guild)
    await pick_channel(scenario, channel_name)
    await pick_roles(scenario, *roles)
    if only_one:
        await pick_only_one(scenario)
    await scenario.click("done")
    await scenario.confirm()
    return scenario


async def add_menu(scenario, channel_name="cores", roles=("Azul", "Verde")):
    """Add on the panel, a menu of `roles` in `channel_name` saved."""
    await scenario.click("add", on=admin_screen(scenario))
    await pick_channel(scenario, channel_name)
    await pick_roles(scenario, *roles)
    await scenario.click("done")


async def pick_channel(scenario, name: str = "cargos"):
    """The menu's channel picked on the card."""
    await scenario.select_option(name, target="channel")


async def pick_roles(scenario, *names: str):
    """The menu's roles picked on the card."""
    await scenario.select_option(list(names or ("Gamer", "Artista")), target="roles")


async def pick_only_one(scenario):
    """The menu's mode set to only one role."""
    await scenario.select_option("one", target="mode")


async def type_message(
    scenario, title: str, text: str, image: str = "", footer: str = ""
):
    """The menu's message content opened, its title and text typed and, when given,
    its footer typed and the picture `image` names uploaded in the same modal."""
    await scenario.click("customize:message")
    title_label, text_label, footer_label, image_label = scenario.pending_modal_fields()
    typed = {title_label: title, text_label: text, footer_label: footer}
    if image:
        typed[image_label] = image
    await scenario.submit_modal(typed)


async def open_panel(scenario_factory, deps, locale, seed_with=None, guild=None):
    """The menus saved (`seed_with` the document, ENABLED by default), then the panel
    opened on the menus' server."""
    seed_document(deps, FORM, seed_with or ENABLED)
    return await menu_scenario(scenario_factory, locale, guild).start_command(FORM)


def admin_screen(scenario):
    """The admin's own screen, the panel or a card, past the menus Keiko posted since."""
    return [message for message in scenario.live_messages
            if message.ephemeral and message.registered_items][-1]


async def edit_menu(scenario, index: int = 0):
    """Edit on the panel's menu at `index`: its card on screen."""
    await scenario.click(f"section:menus${index}", on=admin_screen(scenario))


def role_button(message, role: str) -> discord.ui.DynamicItem:
    """The button of the role `role` on a menu's message."""
    return next(
        item for item in message.registered_items
        if isinstance(item, discord.ui.DynamicItem)
        and item.custom_id.endswith(f":{ROLES[role]}")
    )


async def click_menu(scenario, menu_id: str, role: str):
    """The scenario's user clicks the role's button of the saved menu `menu_id`, as
    Discord delivers a click on a posted menu: by its custom id alone. Returns the
    interaction."""
    custom_id = ReactionRoleButton(menu_id, str(ROLES[role])).custom_id
    return await scenario.click_custom_id(custom_id)


async def click_role(scenario, message, role: str, member: Optional[Any] = None):
    """`member` (the scenario's user by default) clicks the role's button on a menu's
    message, as Discord routes it to a message no view holds. Returns the
    interaction."""
    button = role_button(message, role)
    click = scenario.interaction(on=message, target=button)
    if member is not None:
        click.user = member
    return await scenario.click_after_restart(button, on=message, interaction=click)


def button_styles(message) -> dict:
    """The colour of each role's button on a menu's message, by role name."""
    names = {str(role_id): name for name, role_id in ROLES.items()}
    return {
        names[item.custom_id.rsplit(":", 1)[1]]: item.item.style
        for item in message.registered_items
        if isinstance(item, discord.ui.DynamicItem)
    }


def error_title(name: str, locale: str = "pt-br") -> str:
    """The title of a refusal as the member reads it."""
    return f"🚨 {ml(f'reaction_roles.ui.errors.{name}.title', locale)}"


def seed(deps):
    """The enabled menus saved."""
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The paused menus saved."""
    seed_document(deps, FORM, PAUSED)
