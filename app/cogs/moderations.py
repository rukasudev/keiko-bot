import discord

from app.bot import DiscordBot
from app.logger import logger
from app.services import block_links as block_links_service
from discord import app_commands
from discord.ext import commands


# TODO: maybe transfer listeners to events and create events services to make more sense
class ModerationCogs(commands.Cog, name="Moderations"):
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

    @app_commands.command(
        name="sync",
        description="Sincronizar slash commands com o servidor",
    )
    async def _sync(self, interaction: discord.Interaction):
        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild.id))

        await interaction.response.send_message("Sincronizado!")


async def setup(bot):
    await bot.add_cog(ModerationCogs(bot))
