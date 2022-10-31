import discord

from app.bot import DiscordBot
from app.logger import logger
from app.services import block_links as block_links_service
from discord import app_commands
from discord.ext import commands


class BlockLinks(commands.Cog, name="Block Links"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        guild_id = str(message.guild.id)

        if not message.author.bot:
            await block_links_service.check_message(guild_id, message)

    @app_commands.command(
        name="block_links",
        description="Adicionar bloqueador de links ao servidor?",
    )
    async def _block_links(self, ctx):
        guild_id = str(ctx.guild.id)

        await block_links_service.manager(ctx=ctx, guild_id=guild_id)


async def setup(bot):
    await bot.add_cog(BlockLinks(bot))
