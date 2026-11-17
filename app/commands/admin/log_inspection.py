"""The /admin inspection of a log message, a server and a user."""

import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

import discord

from app import bot, logger
from app.constants import Commands as constants
from app.constants import Emojis
from app.constants import LogTypes as logconstants
from app.core.cogs import features_on, find_changes
from app.core.dates import parse_if_time
from app.core.manager import change_label, parse_history_data
from app.core.utils import format_relative_time
from app.data import data
from app.forms.features import feature_keys
from app.ui.buttons import GenericButton, HistoryButton
from app.ui.records import RecordsBrowser


class LogInspectionView(discord.ui.View):
    """The inspect action of a log message: the user's page and the guild's, in turn."""

    def __init__(
        self,
        user: Optional[discord.User],
        guild: Optional[discord.Guild],
        guild_id: str,
    ) -> None:
        self.user = user
        self.guild = guild
        self.guild_id = guild_id
        self.actual_page = 0
        self.history_offered = False
        super().__init__(timeout=None)
        self.add_item(
            GenericButton("Next", self.callback, style=discord.ButtonStyle.primary)
        )

    async def get_user_info_embed(self) -> discord.Embed:
        """The page of the user the log names, or a note that the log names none."""
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
        embed.add_field(
            name="Date Created", value=parse_if_time(self.user.created_at), inline=False
        )

        if self.user.avatar:
            embed.set_thumbnail(url=self.user.avatar.url)

        return embed

    async def get_guild_info_embed(self) -> discord.Embed:
        """The page of the guild the log names, from Discord while Keiko is in it."""
        if not self.guild:
            return await self._get_offline_guild_info_embed()

        embed = discord.Embed(
            title=self.guild.name,
            color=discord.Color.green(),
        )

        embed.add_field(name="guild_id", value=self.guild.id, inline=False)
        embed.add_field(
            name="guild_created_at",
            value=parse_if_time(self.guild.created_at),
            inline=False,
        )

        server = await data().guild_settings.find_settings(self.guild.id)
        if server:
            self._add_server_fields(embed, server)
            await self._add_commands_status(embed, server)

        keiko = bot.user.name if bot.user else None
        try:
            integrations = await self.guild.integrations()
            for integration in integrations:
                if not isinstance(integration, discord.BotIntegration):
                    continue

                application_user = integration.application.user
                if application_user is None or application_user.name != keiko:
                    continue

                if integration.user:
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
            color=discord.Color.dark_grey(),
        )

        server = await data().guild_settings.find_settings(self.guild_id)
        if server:
            self._add_server_fields(embed, server)
            await self._add_commands_status(embed, server)

        return embed

    def _add_server_fields(
        self, embed: discord.Embed, server: Mapping[str, Any]
    ) -> None:
        options = {
            key: server[key] for key in constants.SERVER_SETTING_KEYS if key in server
        }
        fields = {
            "owner_id": server.get("owner_id"),
            "added_at": server.get("created_at"),
            "joined_at": server.get("joined_at"),
            "left_at": server.get("left_at"),
            "updated_at": server.get("updated_at"),
            constants.SERVER_SETTINGS_KEY: " · ".join(
                f"{name}: {value}" for name, value in options.items()
            ),
        }

        for key, value in fields.items():
            if value:
                embed.add_field(name=key, value=parse_if_time(value), inline=False)

    async def _add_commands_status(
        self, embed: discord.Embed, server: Mapping[str, Any]
    ) -> None:
        guild_id = self.guild.id if self.guild else self.guild_id
        active = set(await features_on(str(guild_id)))
        status_lines = []
        for command_key in feature_keys():
            is_active = command_key in active
            icon = Emojis.STATUS_ICONS[is_active]
            line = f"{icon} {command_key}"

            if is_active:
                last_enabled = await self._find_last_enabled_event(
                    str(guild_id), command_key
                )
                if last_enabled:
                    line += f" ({format_relative_time(last_enabled)})"

            status_lines.append(line)

        bot_online = bool(server.get("present"))
        bot_icon = Emojis.STATUS_ICONS[bot_online]

        embed.add_field(
            name=f"Bot Online: {bot_icon}",
            value="\n".join(status_lines),
            inline=False,
        )

    @staticmethod
    async def _find_last_enabled_event(guild_id: str, cog: str) -> Optional[datetime]:
        found = await data().events_changes.find_by_guild(
            guild_id, cog=cog, types=["feature.enabled", "feature.unpaused"], limit=1
        )
        moment: Optional[datetime] = found[0]["ts"] if found else None
        return moment

    async def history_callback(self, interaction: discord.Interaction) -> None:
        """Open the guild's history of changes, every feature in one list."""
        guild_id = str(self.guild.id if self.guild else self.guild_id)

        async def fetch(
            _interaction: discord.Interaction, _user: Optional[str]
        ) -> List[Dict[str, Any]]:
            return await find_changes(guild_id)

        browser = RecordsBrowser(
            fetch=fetch,
            to_fields=lambda records, asked: parse_history_data(
                records, asked, label_for=lambda key: change_label(key, asked.locale)
            ),
            copy="commands.records.changes",
        )
        await browser.send(interaction)

    async def send(self, interaction: discord.Interaction) -> None:
        """Answer with the user's page and the Next button."""
        embed = await self.get_user_info_embed()
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Turn to the other page, offering the guild's history from its page on."""
        page_index_to_embed = {
            0: self.get_user_info_embed,
            1: self.get_guild_info_embed,
        }
        self.actual_page = (self.actual_page + 1) % len(page_index_to_embed)
        if self.actual_page == 1 and not self.history_offered:
            locale = str(interaction.locale)
            self.add_item(HistoryButton(callback=self.history_callback, locale=locale))
            self.history_offered = True

        new_embed = await page_index_to_embed[self.actual_page]()

        await interaction.response.edit_message(embed=new_embed, view=self)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item[Any],
    ) -> None:
        """Log what failed on the inspection, with the interaction that reached it."""
        logger.error(
            f"LogInspectionView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


class UserInspectionView(discord.ui.View):
    """The inspection of a user: who it is and the guilds it invited Keiko to."""

    def __init__(self, user: discord.User) -> None:
        self.user = user
        super().__init__(timeout=None)

    async def _get_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.user.name,
            color=discord.Color.green(),
        )

        embed.add_field(name="User ID", value=self.user.id, inline=False)
        embed.add_field(
            name="Date Created",
            value=parse_if_time(self.user.created_at),
            inline=False,
        )
        embed.add_field(name="Display Name", value=self.user.display_name, inline=False)

        total_guilds = await data().guild_settings.count_servers_by_owner(
            str(self.user.id)
        )
        embed.add_field(name="Total Guilds Invited", value=total_guilds, inline=False)

        guilds_value = await self._get_guilds_field()
        embed.add_field(name="Guilds", value=guilds_value, inline=False)

        if self.user.avatar:
            embed.set_thumbnail(url=self.user.avatar.url)

        return embed

    async def _get_guilds_field(self) -> str:
        servers = await data().guild_settings.find_servers_by_owner(str(self.user.id))

        if not servers:
            return "No guilds found"

        lines = []
        for server in servers:
            guild_id = server.get("guild_id", "Unknown")
            guild = bot.get_guild(int(guild_id)) if guild_id != "Unknown" else None
            guild_name = guild.name if guild else "Unknown Guild"

            is_online = bool(server.get("present"))
            status_icon = Emojis.STATUS_ICONS[is_online]
            status_label = "Active" if is_online else "Left"

            created_at = server.get("created_at")
            added_str = (
                f" — added {format_relative_time(created_at)}" if created_at else ""
            )

            left_at = None if is_online else server.get("left_at")
            left_str = f" — left {format_relative_time(left_at)}" if left_at else ""

            lines.append(
                f"{status_icon} {guild_name} (`{guild_id}`) — "
                f"{status_label}{added_str}{left_str}"
            )

        return "\n".join(lines)

    async def send(self, interaction: discord.Interaction) -> None:
        """Answer with the user's inspection."""
        embed = await self._get_embed()
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        _item: discord.ui.Item[Any],
    ) -> None:
        """Log what failed on the inspection, with the interaction that reached it."""
        logger.error(
            f"UserInspectionView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


async def get_user(user_id: int) -> Optional[discord.User]:
    """The user from discord.py's cache, else from the API, or None."""
    cached = bot.get_user(user_id)
    if cached is not None:
        return cached

    try:
        return await bot.fetch_user(user_id)
    except discord.NotFound:
        return None


async def parse_log_message_to_embed(
    interaction: discord.Interaction, message: discord.Message
) -> None:
    """Answer the inspect action with the pages of the user and the guild logged."""
    guild_id = None
    user_id = None

    for item in message.embeds[0].fields:
        if item.name == "Guild ID":
            guild_id = item.value
        elif item.name == "User ID" and item.value is not None:
            user_id = item.value.replace("<@", "").replace(">", "")

    if not user_id and message.embeds[0].description:
        match = re.search(r"by (?:<@)?(\d+)>?", message.embeds[0].description)
        if match:
            user_id = match.group(1)

    if not guild_id:
        await interaction.response.send_message(
            "Guild ID not found in this log.", ephemeral=True
        )
        return

    guild = bot.get_guild(int(guild_id))
    user = await get_user(int(user_id)) if user_id else None

    view = LogInspectionView(user, guild, guild_id)
    await view.send(interaction)
