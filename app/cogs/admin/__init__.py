import os
import shutil
from datetime import datetime

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

import app
from app.bot import DiscordBot
from app.cogs.admin.cogs import Cogs
from app.cogs.admin.configs import Configs
from app.cogs.admin.sync import Sync
from app.logger import DiscordLogsHandler
from app.services.admin import send_log_file_from_channel_by_date
from app.services.utils import format_datetime_output, parse_log_filename_with_date


@app_commands.default_permissions()
@app_commands.guilds(discord.Object(app.bot.config.ADMIN_GUILD_ID))
class Admin(commands.GroupCog, name="admin"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        DiscordLogsHandler(bot)
        super().__init__()

    @app_commands.command(
        name="logs",
        description="Keiko generously provides access to the log file for a specific date",
    )
    @app_commands.choices(
        month=[
            Choice(name=datetime(1, i, 1).strftime("%B"), value=i) for i in range(1, 13)
        ]
    )
    async def show_bot_logs(
        self,
        interaction: discord.Interaction,
        year: app_commands.Range[
            int, datetime.now().year - 99, datetime.now().year
        ] = None,
        month: int = None,
        day: app_commands.Range[int, 1, 31] = None,
    ) -> None:
        await interaction.response.defer()

        filename, date = parse_log_filename_with_date("keiko_log", year, month, day)
        if date:
            return await send_log_file_from_channel_by_date(date, interaction, self.bot)

        logs_file = os.path.join(os.getcwd(), "logs", f"{filename}.log")
        logs_copy = os.path.join(os.getcwd(), "logs", f"{filename}.txt")

        shutil.copy(logs_file, logs_copy)
        try:
            await interaction.followup.send(
                f":page_facing_up: Here is my log file for **today**!",
                file=discord.File(logs_copy),
            )
        finally:
            os.remove(logs_copy)

        # TODO: the following code is temporary
        logs_folder = "logs"
        logs_files = os.listdir(logs_folder)
        formatted_logs_files = "\n".join(logs_files)

        await interaction.followup.send(
            f"**Current Logs folder:**\n{formatted_logs_files}"
        )

    @app_commands.command(
        name="uptime",
        description="Check how long Keiko has been active and working non-stop since it started",
    )
    async def show_uptime(self, interaction: discord.Interaction) -> None:
        uptime = datetime.now() - self.bot.ready_time
        formatted_uptime = format_datetime_output(uptime)

        await interaction.response.send_message(
            f":clock1: I have been eating cake for: **{formatted_uptime}**"
        )

    @app_commands.command(
        name="shutdown", description="Keiko will take a little nap...zzz..zz"
    )
    async def shutdown_structure(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f":wave: `{self.bot.user}` It's time for Keiko to take a break and go into sleep mode..."
        )

        await self.bot.close()


async def setup(bot: DiscordBot) -> None:
    admin = Admin(bot)

    admin.app_command.add_command(Cogs(bot))
    admin.app_command.add_command(Sync(bot))
    admin.app_command.add_command(Configs(bot))

    await bot.add_cog(admin)
