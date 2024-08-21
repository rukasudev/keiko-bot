import discord

from app.bot import DiscordBot
from app.services import notifications_youtube_video
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Group


class Youtube(
    Group,
    name=locale_str("youtube", type="subgroup", namespace="notifications-youtube"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("video", type="name", namespace="notifications-youtube"),
        description=locale_str("desc", type="desc", namespace="notifications-youtube"),
    )
    async def notifications_youtube_video(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await notifications_youtube_video.manager(interaction=interaction, guild_id=guild_id)
