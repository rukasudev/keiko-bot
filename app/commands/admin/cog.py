"""/admin: the operator commands of the admin server."""

import os
import shutil
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.app_commands import Choice

import app
from app.bot import DiscordBot
from app.commands.admin import analytics as admin_analytics
from app.commands.admin.groups.cogs import Cogs
from app.commands.admin.groups.configs import Configs
from app.commands.admin.groups.debug import Debug
from app.commands.admin.groups.subscriptions import Subscriptions
from app.commands.admin.groups.sync import Sync
from app.commands.admin.log_inspection import UserInspectionView
from app.commands.admin.service import (
    build_overview_embed,
    forget_guild,
    get_overview_data,
    ready_since,
    send_log_file_from_channel_by_date,
)
from app.core.threads import to_thread
from app.core.utils import (
    format_datetime_output,
    parse_log_filename_with_date,
)
from app.decorators import keiko_command
from app.logger import DiscordLogsHandler
from app.types.cogs import GroupCog
from app.ui.report_browser import ReportBrowser


@app_commands.default_permissions()
@app_commands.guilds(discord.Object(app.bot.config.ADMIN_GUILD_ID))
class Admin(GroupCog, name="admin"):  # noqa: D101 - discord.py sends a Group's docstring as its description
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        DiscordLogsHandler(bot)
        super().__init__()

    @keiko_command(
        name="overview",
        description=(
            "Keiko proudly presents a complete overview of its health and metrics"
        ),
    )
    async def show_overview(self, interaction: discord.Interaction) -> None:
        """Send the health and metrics overview."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        data = await get_overview_data(self.bot)
        embed = await to_thread(build_overview_embed, self.bot, data)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @keiko_command(
        name="inspect",
        description=(
            "Keiko inspects a user by their ID and reveals all known information"
        ),
    )
    async def inspect_user(
        self, interaction: discord.Interaction, user_id: str
    ) -> None:
        """Send the inspection of the user with `user_id`."""
        try:
            user = await self.bot.fetch_user(int(user_id))
        except (ValueError, discord.NotFound):
            await interaction.response.send_message(
                f"User with ID `{user_id}` not found.", ephemeral=True
            )
            return

        view = UserInspectionView(user)
        await view.send(interaction)

    @keiko_command(
        name="insights",
        description="Keiko opens every product report it keeps, in one place",
    )
    async def show_insights(self, interaction: discord.Interaction) -> None:
        """Open the product reports."""
        browser = ReportBrowser(
            sections=admin_analytics.insight_sections(),
            placeholder="Pick a report...",
        )
        await browser.send(interaction)

    @keiko_command(
        name="guild",
        description="Keiko retells the whole story of one guild, from arrival to today",
    )
    async def show_guild_journey(
        self, interaction: discord.Interaction, guild_id: str
    ) -> None:
        """Send the story of the guild with `guild_id`."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        embed = await admin_analytics.build_guild_journey_embed(guild_id)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @keiko_command(
        name="forget",
        description="Keiko erases every analytics record it holds about a guild",
    )
    async def forget_guild(
        self, interaction: discord.Interaction, guild_id: str
    ) -> None:
        """Erase every analytics record of the guild with `guild_id`."""
        await interaction.response.defer(thinking=True, ephemeral=True)
        deleted = await forget_guild(guild_id)
        await interaction.followup.send(
            f"Erased analytics for guild `{guild_id}` — "
            f"**{deleted['events']}** events, **{deleted['months']}** monthly buckets, "
            f"**{deleted['profile']}** profile, **{deleted['traces']}** traces.",
            ephemeral=True,
        )

    @keiko_command(
        name="logs",
        description=(
            "Keiko generously provides access to the log file for a specific date"
        ),
    )
    @app_commands.choices(
        month=[
            Choice(name=datetime(1, i, 1).strftime("%B"), value=i) for i in range(1, 13)
        ]
    )
    async def show_bot_logs(
        self,
        interaction: discord.Interaction[DiscordBot],
        year: Optional[
            app_commands.Range[int, datetime.now().year - 99, datetime.now().year]
        ] = None,
        month: Optional[int] = None,
        day: Optional[app_commands.Range[int, 1, 31]] = None,
    ) -> None:
        """Send the log file of a date, today's by default."""
        await interaction.response.defer()

        filename, date = parse_log_filename_with_date("keiko_log", year, month, day)
        if date:
            await send_log_file_from_channel_by_date(date, interaction)
            return

        logs_file = os.path.join(os.getcwd(), "logs", f"{filename}.log")
        logs_copy = os.path.join(os.getcwd(), "logs", f"{filename}.txt")

        shutil.copy(logs_file, logs_copy)
        try:
            await interaction.followup.send(
                ":page_facing_up: Here is my log file for **today**!",
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

    @keiko_command(
        name="uptime",
        description=(
            "Check how long Keiko has been active and working non-stop since it started"
        ),
    )
    async def show_uptime(self, interaction: discord.Interaction) -> None:
        """Say how long Keiko has been up."""
        uptime = datetime.now() - ready_since(self.bot)
        formatted_uptime = format_datetime_output(uptime)

        await interaction.response.send_message(
            f":clock1: I have been eating cake for: **{formatted_uptime}**"
        )

    @keiko_command(
        name="shutdown", description="Keiko will take a little nap...zzz..zz"
    )
    async def shutdown_structure(self, interaction: discord.Interaction) -> None:
        """Close the bot."""
        await interaction.response.send_message(
            (
                f":wave: `{self.bot.user}` It's time for Keiko to take a break "
                "and go into sleep mode..."
            )
        )

        await self.bot.close()


async def setup(bot: DiscordBot) -> None:
    """Load the /admin cog with its subgroups."""
    admin = Admin(bot)
    group = admin.app_command
    if group is None:
        raise RuntimeError("the admin cog is a group cog, so it holds its group")

    group.add_command(Cogs(bot))
    group.add_command(Debug(bot))
    group.add_command(Sync(bot))
    group.add_command(Configs(bot))
    group.add_command(Subscriptions(bot))

    await bot.add_cog(admin)
