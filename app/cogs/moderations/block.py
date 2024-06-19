import discord

from app.bot import DiscordBot
from app.services import block_links as block_links_service
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Group


class Block(
    Group,
    name=locale_str("block", type="subgroup", namespace="block-links"),
):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("links", type="name", namespace="block-links"),
        description=locale_str("desc", type="desc", namespace="block-links"),
    )
    async def block_links(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await block_links_service.manager(interaction=interaction, guild_id=guild_id)
