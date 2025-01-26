import discord

from app.bot import DiscordBot
from app.services import stream_elements as stream_elements_service
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Group


class StreamElementsCommands(
    Group,
    name=locale_str("streamelements", type="subgroup", namespace="stream-elements"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("commands", type="name", namespace="stream-elements"),
        description=locale_str("desc", type="desc", namespace="stream-elements"),
    )
    async def stream_elements_commands(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await stream_elements_service.manager(interaction=interaction, guild_id=guild_id)
