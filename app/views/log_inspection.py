import discord

from app import bot, logger
from app.components.buttons import GenericButton, HistoryButton
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.data import moderations as moderations_data
from app.services.cogs import find_cog_events_by_guild
from app.services.manager import parse_history_data
from app.services.utils import format_relative_time, ml
from app.views.pagination import PaginationView

COMMAND_STATUS_ICONS = {
    True: "\u2705",
    False: "\u274c",
}


class LogInspectionView(discord.ui.View):
    def __init__(self, user: discord.User, guild: discord.Guild, guild_id: str):
        self.user = user
        self.guild = guild
        self.guild_id = guild_id
        self.actual_page = 0
        super().__init__(timeout=None)
        self.add_item(GenericButton("Next", self.callback, style=discord.ButtonStyle.primary))

    async def get_user_info_embed(self) -> discord.Embed:
        if not self.user:
            return discord.Embed(
                title="Unknown User",
                description="User info not available in this log.",
                color=discord.Color.greyple(),
            )

        embed = discord.Embed(
            title=self.user.name,
            description=f"User ID: {self.user.id}",
            color=discord.Color.green(),
        )

        embed.add_field(name="Display Name", value=self.user.display_name, inline=False)
        embed.add_field(name="Date Created", value=self.parse_if_time(self.user.created_at), inline=False)

        if self.user.avatar:
            embed.set_thumbnail(url=self.user.avatar.url)

        return embed

    async def get_guild_info_embed(self) -> discord.Embed:
        if not self.guild:
            return await self._get_offline_guild_info_embed()

        embed = discord.Embed(
            title=self.guild.name,
            color=discord.Color.green(),
        )

        embed.add_field(name="guild_id", value=self.guild.id, inline=False)
        embed.add_field(name="guild_created_at", value=self.parse_if_time(self.guild.created_at), inline=False)

        moderations = moderations_data.find_moderations_by_guild(self.guild.id)
        if moderations:
            self._add_moderations_fields(embed, moderations)
            self._add_commands_status(embed, moderations)

        try:
            integrations = await self.guild.integrations()
            for integration in integrations:
                if not isinstance(integration, discord.BotIntegration):
                    continue

                if integration.application.user.name != bot.user.name:
                    continue

                embed.add_field(name="invited_by", value=integration.user.name)
        except (discord.Forbidden, discord.HTTPException):
            pass

        if self.guild.icon:
            embed.set_thumbnail(url=self.guild.icon.url)

        return embed

    async def _get_offline_guild_info_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"Guild {self.guild_id}",
            description="Bot is not in this guild.",
            color=discord.Color.dark_gray(),
        )

        moderations = moderations_data.find_moderations_by_guild(self.guild_id)
        if moderations:
            self._add_moderations_fields(embed, moderations)
            self._add_commands_status(embed, moderations)

        return embed

    def _add_moderations_fields(self, embed: discord.Embed, moderations: dict):
        for key, value in moderations.items():
            if key in ["guild_id", "_id"] + constants.COMMANDS_LIST + ["is_bot_online"]:
                continue

            if not value:
                continue

            if key == "created_at":
                key = "added_at"

            embed.add_field(name=key, value=self.parse_if_time(value), inline=False)

    def _add_commands_status(self, embed: discord.Embed, moderations: dict):
        guild_id = self.guild.id if self.guild else self.guild_id
        status_lines = []
        for command_key in constants.COMMANDS_LIST:
            is_active = moderations.get(command_key, False)
            icon = COMMAND_STATUS_ICONS.get(is_active, "\u274c")
            line = f"{icon} {command_key}"

            if is_active:
                events = find_cog_events_by_guild(guild_id, command_key)
                last_enabled = self._find_last_enabled_event(events)
                if last_enabled:
                    line += f" ({format_relative_time(last_enabled)})"

            status_lines.append(line)

        bot_online = moderations.get("is_bot_online", False)
        bot_icon = COMMAND_STATUS_ICONS.get(bot_online, "\u274c")

        embed.add_field(
            name=f"Bot Online: {bot_icon}",
            value="\n".join(status_lines),
            inline=False,
        )

    @staticmethod
    def _find_last_enabled_event(events):
        last = None
        for event in events:
            if event.get("event") in ("enabled", "unpaused"):
                last = event.get("datetime")
        return last

    async def history_callback(self, interaction: discord.Interaction):
        guild_id = self.guild.id if self.guild else self.guild_id
        raw_data = []

        for cog_key in constants.COMMANDS_LIST:
            raw_data += find_cog_events_by_guild(guild_id, cog_key)

        data = parse_history_data(raw_data, interaction, guild=self.guild, with_cog=True)

        title = ml("buttons.changes-history.label", locale=interaction.locale)
        pagination_view = PaginationView(interaction, title, "", data, sep=4)

        await pagination_view.send(ephemeral=True)

    def parse_if_time(self, value):
        try:
            formatted = value.strftime("%Y-%m-%d %H:%M:%S")
            return f"{formatted} ({format_relative_time(value)})"
        except AttributeError:
            return value

    async def send(self, interaction: discord.Interaction):
        embed = await self.get_user_info_embed()
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def callback(self, interaction: discord.Interaction):
        self.interaction = interaction

        page_index_to_embed = {
            0: self.get_user_info_embed,
            1: self.get_guild_info_embed,
        }
        self.actual_page = (self.actual_page + 1) % len(page_index_to_embed)
        if self.actual_page == 1:
            self.add_item(HistoryButton(callback=self.history_callback, locale=interaction.locale))

        new_embed = await page_index_to_embed[self.actual_page]()

        await interaction.response.edit_message(embed=new_embed, view=self)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"LogInspectionView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )
