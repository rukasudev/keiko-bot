import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands
from i18n import t

from app.services import block_links as block_links_service


# TODO: replace command name to use constants variables
class Block(app_commands.Group, name=locale_str("block", namespace="commands")):
    @commands.Cog.listener()
    async def on_message(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        if not interaction.author.bot:
            await block_links_service.check_message(guild_id, interaction)

    @app_commands.command(
        name=locale_str("links", namespace="commands"),
        description=locale_str("linksdesc", namespace="commands"),
    )
    async def _block_links(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                t("errors.command-permission-denied.message"), ephemeral=True
            )

        await block_links_service.manager(ctx=interaction, guild_id=guild_id)
