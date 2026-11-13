import discord

from app import bot, logger
from app.constants import LogTypes as logconstants
from app.data.guild import settings as guild_settings
from app.core.utils import format_relative_time


BOT_STATUS_ICONS = {
    True: "\u2705",
    False: "\u274c",
}


class UserInspectionView(discord.ui.View):
    def __init__(self, user: discord.User):
        self.user = user
        super().__init__(timeout=None)

    def _get_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.user.name,
            color=discord.Color.green(),
        )

        embed.add_field(name="User ID", value=self.user.id, inline=False)
        embed.add_field(
            name="Date Created",
            value=self._parse_if_time(self.user.created_at),
            inline=False,
        )
        embed.add_field(name="Display Name", value=self.user.display_name, inline=False)

        total_guilds = guild_settings.count_servers_by_owner(str(self.user.id))
        embed.add_field(name="Total Guilds Invited", value=total_guilds, inline=False)

        guilds_value = self._get_guilds_field()
        embed.add_field(name="Guilds", value=guilds_value, inline=False)

        if self.user.avatar:
            embed.set_thumbnail(url=self.user.avatar.url)

        return embed

    def _get_guilds_field(self) -> str:
        servers = guild_settings.find_servers_by_owner(str(self.user.id))

        if not servers:
            return "No guilds found"

        lines = []
        for server in servers:
            guild_id = server.get("guild_id", "Unknown")
            guild = bot.get_guild(int(guild_id)) if guild_id != "Unknown" else None
            guild_name = guild.name if guild else "Unknown Guild"

            is_online = bool(server.get("present"))
            status_icon = BOT_STATUS_ICONS.get(is_online, "\u274c")
            status_label = "Active" if is_online else "Left"

            created_at = server.get("created_at")
            added_str = f" — added {format_relative_time(created_at)}" if created_at else ""

            left_at = None if is_online else server.get("left_at")
            left_str = f" — left {format_relative_time(left_at)}" if left_at else ""

            lines.append(f"{status_icon} {guild_name} (`{guild_id}`) — {status_label}{added_str}{left_str}")

        return "\n".join(lines)

    @staticmethod
    def _parse_if_time(value):
        try:
            formatted = value.strftime("%Y-%m-%d %H:%M:%S")
            return f"{formatted} ({format_relative_time(value)})"
        except AttributeError:
            return value

    async def send(self, interaction: discord.Interaction):
        embed = self._get_embed()
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"UserInspectionView error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )
