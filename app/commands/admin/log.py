"""The log inspection context menus of the admin server."""

import discord
from discord import app_commands

from app.bot import DiscordBot
from app.commands.admin.log_inspection import (
    UserInspectionView,
    parse_log_message_to_embed,
)
from app.types.cogs import Cog


class Log(Cog):
    """The context menus that inspect a log message and a user."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def log_inspection(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Send the inspection of the guild and user a log message names."""
        await parse_log_message_to_embed(interaction, message)

    async def user_inspection(
        self, interaction: discord.Interaction, user: discord.User
    ) -> None:
        """Send the inspection of `user`."""
        view = UserInspectionView(user)
        await view.send(interaction)


async def setup(bot: DiscordBot) -> None:
    """Register the context menus on the admin server."""
    log = Log(bot)

    context_menu = app_commands.ContextMenu(
        name="Log Inspection",
        callback=log.log_inspection,
        type=discord.AppCommandType.message,
        guild_ids=[bot.config.ADMIN_GUILD_ID],
    )
    bot.tree.add_command(context_menu)

    user_context_menu = app_commands.ContextMenu(
        name="User Inspect",
        callback=log.user_inspection,
        type=discord.AppCommandType.user,
        guild_ids=[bot.config.ADMIN_GUILD_ID],
    )
    bot.tree.add_command(user_context_menu)

    await bot.add_cog(log)
