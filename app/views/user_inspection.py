import discord

from app import bot
from app.data import moderations as moderations_data
from app.services.utils import format_relative_time


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

        total_guilds = moderations_data.count_moderations_by_owner(str(self.user.id))
        embed.add_field(name="Total Guilds Invited", value=total_guilds, inline=False)

        guilds_value = self._get_guilds_field()
        embed.add_field(name="Guilds", value=guilds_value, inline=False)

        if self.user.avatar:
            embed.set_thumbnail(url=self.user.avatar.url)

        return embed

    def _get_guilds_field(self) -> str:
        moderations = moderations_data.find_moderations_by_owner(str(self.user.id))

        if not moderations:
            return "No guilds found"

        lines = []
        for mod in moderations:
            guild_id = mod.get("guild_id", "Unknown")
            guild = bot.get_guild(int(guild_id)) if guild_id != "Unknown" else None
            guild_name = guild.name if guild else "Unknown Guild"

            is_online = mod.get("is_bot_online", False)
            status_icon = BOT_STATUS_ICONS.get(is_online, "\u274c")
            status_label = "Active" if is_online else "Left"

            created_at = mod.get("created_at")
            added_str = f" — added {format_relative_time(created_at)}" if created_at else ""

            left_str = ""
            if not is_online:
                updated_at = mod.get("updated_at")
                if updated_at and updated_at != created_at:
                    left_str = f" — left {format_relative_time(updated_at)}"

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
