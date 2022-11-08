import discord
from discord import app_commands
from discord.ext import commands

from app.bot import DiscordBot
from app.logger import logger
from app.services import block_links as block_links_service


# TODO: replace command name to use constants variables
class Block(app_commands.Group, name="bloquear"):
    @commands.Cog.listener()
    async def on_message(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        if not interaction.author.bot:
            await block_links_service.check_message(guild_id, interaction)

    @app_commands.command(
        name="links",
        description="Adicionar bloqueador de links ao servidor?",
    )
    async def _block_links(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "Você não tem permissão pra rodar esse comando :/", ephemeral=True
            )

        await block_links_service.manager(ctx=interaction, guild_id=guild_id)
