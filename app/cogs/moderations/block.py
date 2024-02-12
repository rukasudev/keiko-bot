import discord

from discord import app_commands
from discord.app_commands import locale_str
from app.services.utils import parse_locale

from i18n import t
from app.bot import DiscordBot
from app.services import block_links as block_links_service


class Block(app_commands.Group, name=locale_str("block", namespace="commands")):
    def __init__(self, bot: DiscordBot):
        bot.add_listener(self.on_message)
        super().__init__()

    async def on_message(self, message: discord.Message):
        if not message.author.bot:
            guild_id = str(message.guild.id)

            await block_links_service.check_message(guild_id, message)

    @app_commands.command(
        name=locale_str("links", namespace="commands"),
        description=locale_str("linksdesc", namespace="commands"),
    )
    async def _block_links(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        if not interaction.user.guild_permissions.administrator:
            locale = parse_locale(interaction.locale)
            return await interaction.response.send_message(
                t("errors.command-permission-denied.message", locale=locale), ephemeral=True
            )

        await block_links_service.manager(ctx=interaction, guild_id=guild_id)
