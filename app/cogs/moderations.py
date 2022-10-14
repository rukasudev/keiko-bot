from logger import logger
from discord.ext import commands
from discord import app_commands
from services.moderations import Moderation

import discord


class ModerationCogs(commands.Cog, name="Moderations"):
    def __init__(self, bot):
        self.bot = bot
        self.moderations_service = Moderation(bot)

    @commands.Cog.listener()
    async def on_message(self, message):
        guild_id = str(message.guild.id)

        if not message.author.bot:
            await self.moderations_service.block_links_on_message(guild_id, message)

    @app_commands.command(
        name="block_links",
        description="Adicionar bloqueador de links ao servidor?",
    )
    async def _block_links(self, ctx):
        guild_id = str(ctx.guild.id)

        await self.moderations_service.block_links_manager(ctx, guild_id)

    @app_commands.command(
        name="sync",
        description="Sincronizar slash commands com o servidor",
    )
    async def _sync(self, interaction):
        await self.bot.tree.sync(guild=discord.Object(id=interaction.guild.id))

        await interaction.response.send_message("Sincronizado!")


async def setup(bot):
    await bot.add_cog(ModerationCogs(bot))
