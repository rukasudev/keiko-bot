import asyncio

import discord

from app import bot, logger
from app.components.buttons import GenericButton, HistoryButton
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.data.guild import settings as guild_settings
from app.core.cogs import features_on, find_changes
from app.data.events import changes
from app.core.manager import parse_history_data
from app.core.utils import format_relative_time, ml
from app.views.records import RecordsBrowser

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

        server = await asyncio.to_thread(guild_settings.find_settings, self.guild.id)
        if server:
            self._add_server_fields(embed, server)
            await asyncio.to_thread(self._add_commands_status, embed, server)

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

        server = await asyncio.to_thread(guild_settings.find_settings, self.guild_id)
        if server:
            self._add_server_fields(embed, server)
            await asyncio.to_thread(self._add_commands_status, embed, server)

        return embed

    def _add_server_fields(self, embed: discord.Embed, server: dict):
        options = {
            key: server[key] for key in constants.SERVER_SETTING_KEYS if key in server
        }
        fields = {
            "owner_id": server.get("owner_id"),
            "added_at": server.get("created_at"),
            "joined_at": server.get("joined_at"),
            "left_at": server.get("left_at"),
            "updated_at": server.get("updated_at"),
            constants.SERVER_SETTINGS_KEY: self.settings_line(options),
        }
        for key, value in fields.items():
            if value:
                embed.add_field(name=key, value=self.parse_if_time(value), inline=False)

    def _add_commands_status(self, embed: discord.Embed, server: dict):
        guild_id = self.guild.id if self.guild else self.guild_id
        active = set(features_on(str(guild_id)))
        status_lines = []
        for command_key in constants.COMMANDS_LIST:
            is_active = command_key in active
            icon = COMMAND_STATUS_ICONS.get(is_active, "\u274c")
            line = f"{icon} {command_key}"

            if is_active:
                last_enabled = self._find_last_enabled_event(str(guild_id), command_key)
                if last_enabled:
                    line += f" ({format_relative_time(last_enabled)})"

            status_lines.append(line)

        bot_online = bool(server.get("present"))
        bot_icon = COMMAND_STATUS_ICONS.get(bot_online, "\u274c")

        embed.add_field(
            name=f"Bot Online: {bot_icon}",
            value="\n".join(status_lines),
            inline=False,
        )

    @staticmethod
    def _find_last_enabled_event(guild_id: str, cog: str):
        found = changes.find_by_guild(
            guild_id, cog=cog, types=["feature.enabled", "feature.unpaused"], limit=1
        )
        return found[0]["ts"] if found else None

    async def history_callback(self, interaction: discord.Interaction):
        guild_id = self.guild.id if self.guild else self.guild_id

        history = await asyncio.to_thread(find_changes, str(guild_id))
        browser = RecordsBrowser(
            fetch=lambda _interaction, _user: history,
            to_fields=lambda raw, i: parse_history_data(raw, i, with_cog=True),
            title=ml("buttons.changes-history.label", locale=interaction.locale),
        )
        await browser.send(interaction)

    def settings_line(self, settings: dict) -> str:
        return " · ".join(f"{name}: {value}" for name, value in settings.items())

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
