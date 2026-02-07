import discord
from discord import app_commands

from app import bot
from app.bot import DiscordBot
from app.services.log_inspection import parse_log_message_to_embed
from app.types.cogs import Cog
from app.views.user_inspection import UserInspectionView


class Log(Cog):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def log_inspection(self, interaction: discord.Interaction, message: discord.Message) -> None:
        await parse_log_message_to_embed(interaction, message)

    async def user_inspection(self, interaction: discord.Interaction, user: discord.User) -> None:
        view = UserInspectionView(user)
        await view.send(interaction)


async def setup(bot: DiscordBot) -> None:
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
