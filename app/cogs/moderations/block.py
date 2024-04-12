import discord
from discord import app_commands

from app.bot import DiscordBot
from app.services import block_links as block_links_service
from app.translator import locale_str


class Block(
    app_commands.Group,
    name=locale_str("block", type="subgroup", namespace="block-links"),
):
    def __init__(self, bot: DiscordBot):
        bot.add_listener(self.on_message)
        super().__init__()

    async def on_message(self, message: discord.Message):
        if not message.author.bot:
            guild_id = str(message.guild.id)

            await block_links_service.check_message(guild_id, message)

    @app_commands.command(
        name=locale_str("links", type="name", namespace="block-links"),
        description=locale_str("desc", type="desc", namespace="block-links"),
    )
    async def block_links(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await block_links_service.manager(interaction=interaction, guild_id=guild_id)
