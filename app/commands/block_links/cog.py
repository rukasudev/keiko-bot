"""The /moderations block links command and the link check context menu."""

import discord
from discord import app_commands

from app.bot import DiscordBot
from app.commands.block_links import service as block_links_service
from app.decorators import keiko_admin_only, keiko_command
from app.translator import locale_str
from app.types.cogs import Cog, Group


class Block(  # noqa: D101 - discord.py sends a Group's docstring as its description
    Group,
    name=locale_str("block", type="subgroup", namespace="block_links.command.slash"),
):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("links", type="name", namespace="block_links.command.slash"),
        description=locale_str(
            "desc", type="description", namespace="block_links.command.slash"
        ),
    )
    @keiko_admin_only
    async def block_links(self, interaction: discord.Interaction) -> None:
        """Open the setup form, or the manager of the blocked links."""
        await block_links_service.manager(interaction)


class LinkCheck(Cog):
    """The message context menu that tells an admin what block links does with it."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.default_permissions(administrator=True)
    @keiko_admin_only
    async def validate_block_link(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Tell the admin what block links does with `message`."""
        await block_links_service.send_link_check_message(interaction, message)


async def setup(bot: DiscordBot) -> None:
    """Add the link check context menu and its cog."""
    check = LinkCheck(bot)
    bot.tree.add_command(
        app_commands.ContextMenu(
            name=locale_str(
                "block-links-check",
                type="context-menu",
                namespace="block_links.command.slash.commands.check",
            ),
            callback=check.validate_block_link,
            type=discord.AppCommandType.message,
        )
    )
    await bot.add_cog(check)
