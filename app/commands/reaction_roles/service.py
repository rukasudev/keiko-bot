"""Reaction roles: the menus of role buttons Keiko posts in their channels, drawn again
on every save and deleted with their menu, and the click that gives or takes a role and
answers with the member's own copy of the menu."""

import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

import discord

from app import logger
from app.constants import Commands as constants
from app.constants import DiscordLimits
from app.constants import LogTypes as logconstants
from app.core import analytics, images
from app.core.cache import read_cog_data
from app.core.cogs import is_feature_on
from app.core.locks import KeyedLocks
from app.core.posted import delete_posted, posted_message
from app.core.servers import interaction_language
from app.core.utils import ensure_list, givable_role_ids, parse_locale
from app.data import data
from app.forms import item_values, items_of
from app.forms.features.feature import CommitContext, CommitResult, GenericCogFeature
from app.forms.form.form_state import Answer
from app.ui.embed import card_footer, card_frame, card_header, response_error_embed


@dataclass(frozen=True)
class Menu:
    """One saved menu: the id its message is kept under, its channel, its title, text,
    image and footer, whether a member keeps only one of its roles, and its roles."""

    id: str
    channel_id: str
    title: str
    text: str
    only_one: bool
    roles: tuple[str, ...]
    image: str = ""
    footer: str = ""


def menu_of(item: Mapping[str, Any]) -> Menu:
    """A saved menu item as its message draws it."""
    values = item_values(item)
    state = item.get("state") or {}
    roles = [str(role) for role in ensure_list(values.get("roles"))]
    return Menu(
        id=str(state.get("menu_id") or ""),
        channel_id=str(values.get("channel") or ""),
        title=str(values.get("title") or ""),
        text=str(values.get("text") or ""),
        only_one=values.get("mode") == "one",
        roles=tuple(roles[: constants.REACTION_ROLES_ROLES_MAX]),
        image=str(values.get("image") or ""),
        footer=str(values.get("footer") or ""),
    )


def menus_of(document: Mapping[str, Any]) -> list[Menu]:
    """The menus a saved document holds, in order, at most the ones a server keeps."""
    items = items_of(document, "menus")[: constants.REACTION_ROLES_MENUS_MAX]
    return [menu_of(item) for item in items]


def with_menu_id(item: Mapping[str, Any]) -> dict[str, Any]:
    """The menu item with a new id, the subject its message is kept under."""
    return {**item, "state": {"menu_id": uuid.uuid4().hex}}


class ReactionRoleButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"reactionrole:(?P<menu>[0-9a-f]{8,32}):(?P<role>\d+)",
):
    """One role's button on a menu, which keeps working after a restart: its custom id
    carries the menu and the role, and one member's clicks run one at a time."""

    clicks: ClassVar[KeyedLocks] = KeyedLocks()

    def __init__(
        self, menu_id: str, role_id: str, label: str = "", held: bool = False
    ) -> None:
        self.menu_id = menu_id
        self.role_id = role_id
        style = discord.ButtonStyle.primary if held else discord.ButtonStyle.secondary
        super().__init__(
            discord.ui.Button(
                label=label[: DiscordLimits.BUTTON_LABEL] or None,
                style=style,
                custom_id=f"reactionrole:{menu_id}:{role_id}",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        _interaction: discord.Interaction[Any],
        _item: discord.ui.Item[Any],
        match: re.Match[str],
    ) -> "ReactionRoleButton":
        """The button rebuilt from its custom id, on a menu no view holds."""
        return cls(match["menu"], match["role"])

    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        """Give or take the button's role, then show the member their own copy of the
        menu."""
        try:
            await toggle_role(interaction, self.menu_id, self.role_id)
        except Exception as error:
            await self.on_error(interaction, error)

    async def on_error(
        self, interaction: discord.Interaction[Any], error: Exception
    ) -> None:
        """Log what failed on the menu and answer the member with the generic error."""
        logger.error(
            f"ReactionRoleButton error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=error,
        )
        embed = response_error_embed("generic", parse_locale(interaction.locale))
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException as answer_error:
            logger.warn(
                "Could not tell a member a role menu failed: "
                f"{type(answer_error).__name__}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )


def reaction_role_buttons() -> tuple[type[discord.ui.DynamicItem[Any]], ...]:
    """The items reaction roles routes by their custom id alone."""
    return (ReactionRoleButton,)


async def menu_view(
    menu: Menu, guild: Any, held: Iterable[str] = ()
) -> discord.ui.LayoutView:
    """The menu as a card holding its title, its text, its image from a link Discord
    still serves and its footer, with a button per role still in the server under it,
    blue where `held` names the role and grey elsewhere."""
    owned = set(held)
    buttons = [
        ReactionRoleButton(menu.id, role_id, role.name, role_id in owned)
        for role_id in menu.roles
        if (role := guild.get_role(int(role_id))) is not None
    ]
    frame = card_frame()
    card_header(frame, menu.title, menu.text)
    if menu.image:
        image = await images.fresh_attachment_url(menu.image)
        frame.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(media=image)))
    if menu.footer:
        card_footer(frame, menu.footer)

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(frame)
    per_row = DiscordLimits.BUTTONS_PER_ROW
    for start in range(0, len(buttons), per_row):
        view.add_item(discord.ui.ActionRow(*buttons[start : start + per_row]))
    return view


async def post_menu(menu: Menu, guild: Any) -> None:
    """Draw the menu's message again where it still is, or post it in its channel and
    keep it."""
    channel = guild.get_channel(int(menu.channel_id)) if menu.channel_id else None
    if channel is None:
        logger.warn(
            f"The role menu {menu.id} of guild {guild.id} has no channel to post in",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    view = await menu_view(menu, guild)
    message = await posted_message("reaction_roles", guild, channel, menu.id)
    if message is not None:
        await message.edit(view=view)
        return

    try:
        sent = await channel.send(
            view=view, allowed_mentions=discord.AllowedMentions.none()
        )
    except discord.Forbidden as error:
        analytics.record_permission_failure(guild.id, "reaction_roles", error)
        raise
    await data().guild_messages.save_message(
        guild.id, channel.id, "reaction_roles", menu.id, sent.id
    )


async def delete_menu(menu: Menu, guild: Any) -> None:
    """Delete the menu's message from its channel, and forget it."""
    await delete_posted("reaction_roles", guild, menu.channel_id, menu.id)


async def toggle_role(
    interaction: discord.Interaction[Any], menu_id: str, role_id: str
) -> None:
    """Take the role from a member who holds it, else give it, and answer with their
    own copy of the menu; a menu paused or gone, a role gone and a role Keiko cannot
    give are refused."""
    guild = interaction.guild
    if guild is None:
        return

    private = on_private_copy(interaction)
    if private:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)

    document = await read_cog_data(guild.id, "reaction_roles", True)
    menu = next(
        (saved for saved in menus_of(document or {}) if saved.id == menu_id), None
    )
    if menu is None:
        await refuse(interaction, "reaction_roles.ui.errors.menu_gone")
        return
    refused = refusal(document, menu, guild, role_id)
    if refused is not None:
        await refuse(interaction, refused)
        return

    try:
        async with ReactionRoleButton.clicks.hold((guild.id, interaction.user.id)):
            member = await guild.fetch_member(interaction.user.id)
            held = await toggled(member, guild, menu, role_id)
    except discord.Forbidden as error:
        analytics.record_permission_failure(guild.id, "reaction_roles", error)
        await refuse(interaction, "reaction_roles.ui.errors.role_above")
        return

    view = await menu_view(menu, guild, held)
    if private:
        await interaction.edit_original_response(view=view)
        return
    await interaction.followup.send(view=view, ephemeral=True)


def on_private_copy(interaction: discord.Interaction[Any]) -> bool:
    """Whether the click landed on the member's own copy of the menu."""
    message = interaction.message
    return message is not None and message.flags.ephemeral


def refusal(
    document: Mapping[str, Any] | None, menu: Menu, guild: Any, role_id: str
) -> str | None:
    """The error a click on the role of a saved menu is refused with, None while Keiko
    can give and take it."""
    if not is_feature_on(document):
        return "reaction_roles.ui.errors.paused"
    if role_id not in menu.roles or guild.get_role(int(role_id)) is None:
        return "reaction_roles.ui.errors.role_gone"
    if not guild.me.guild_permissions.manage_roles:
        return "reaction_roles.ui.errors.no_permission"
    if role_id not in givable_role_ids(guild):
        return "reaction_roles.ui.errors.role_above"
    return None


async def toggled(member: Any, guild: Any, menu: Menu, role_id: str) -> set[str]:
    """Take the role from a member who holds it, else give it, taking the menu's other
    roles first in a menu of only one; the ids of the roles the member holds after."""
    role = guild.get_role(int(role_id))
    held = {str(owned.id) for owned in member.roles}
    if role_id in held:
        await member.remove_roles(role)
        return held - {role_id}

    givable = givable_role_ids(guild)
    taken = set(menu.roles) & givable if menu.only_one else set()
    others = [owned for owned in member.roles if str(owned.id) in taken]
    if others:
        await member.remove_roles(*others)
    await member.add_roles(role)
    analytics.record_value(guild.id, "reaction_roles")
    return (held - {str(other.id) for other in others}) | {role_id}


async def refuse(interaction: discord.Interaction[Any], error: str) -> None:
    """Answer the member privately with the error a click was refused with."""
    locale = await interaction_language(interaction)
    embed = response_error_embed(error, locale)
    await interaction.followup.send(embed=embed, ephemeral=True)


class ReactionRolesFeature(GenericCogFeature):
    """Role menus kept posted in their channels: posted on a save, drawn again on an
    edit, deleted with their menu and with the feature."""

    def __init__(self) -> None:
        super().__init__("reaction_roles")

    def item_document(self, item: Mapping[str, Answer], locale: str) -> dict[str, Any]:
        """A menu as the document stores it, with a new id its message is kept under."""
        return with_menu_id(super().item_document(item, locale))

    async def before_setup(
        self,
        document: dict[str, Any],
        _answers: Mapping[str, Answer],
        _context: CommitContext,
    ) -> dict[str, Any]:
        """Every menu of a fresh setup gets the id its message is kept under."""
        key = self.composition.key
        menus = [with_menu_id(item) for item in items_of(document, key)]
        return {**document, key: {"style": "composition", "values": menus}}

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Save the menus, then post each one in its channel."""
        result = await super().commit_setup(payload, context)
        await self.post(menus_of(result.document or {}), context)
        return result

    async def commit_add_item(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Save the new menu, then post it in its channel."""
        result = await super().commit_add_item(payload, context)
        await self.post(menus_of(result.document or {})[-1:], context)
        return result

    async def after_item_replaced(
        self, old: Mapping[str, Any], new: Mapping[str, Any], context: CommitContext
    ) -> None:
        """Draw the edited menu's message again, in its new channel when it moved."""
        if context.guild is None:
            return
        before, after = menu_of(old), menu_of(new)
        if before.channel_id != after.channel_id:
            await delete_menu(before, context.guild)
        await post_menu(after, context.guild)

    async def after_item_removed(
        self, item: Mapping[str, Any], context: CommitContext
    ) -> None:
        """Delete the removed menu's message."""
        if context.guild is not None:
            await delete_menu(menu_of(item), context.guild)

    async def after_disable(
        self, document: Mapping[str, Any], context: CommitContext
    ) -> None:
        """Delete the message of every menu the dropped document held."""
        if context.guild is None:
            return
        for menu in menus_of(document):
            await self.best_effort(delete_menu(menu, context.guild), context)

    async def post(self, menus: Iterable[Menu], context: CommitContext) -> None:
        """Post each menu in its channel, one failure never stopping the next."""
        if context.guild is None:
            return
        for menu in menus:
            await self.best_effort(post_menu(menu, context.guild), context)


FEATURE = ReactionRolesFeature()
