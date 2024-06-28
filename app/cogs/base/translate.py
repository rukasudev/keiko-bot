import discord
from discord import app_commands

from app.bot import DiscordBot
from app.components.embed import response_error_embed, translate_embed
from app.integrations.google_translate import GoogleTranslate
from app.translator import locale_str
from app.types.cogs import Cog


class Translate(Cog):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def translate_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        """
        Translate a message to a specified locale
        """
        if message.content.strip() == "":
            embed = response_error_embed("translate-message.translation-invalid-message", interaction.locale)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        if GoogleTranslate.is_only_url(message.content):
            embed = response_error_embed("translate-message.translation-only-link", interaction.locale)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        response = GoogleTranslate.translate(message.content, interaction.locale.value)
        if not response:
            embed = response_error_embed("translate-message.translation-generic-error", interaction.locale)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        embed = translate_embed("commands.commands.translate-message.response", interaction, response, message.jump_url)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: DiscordBot) -> None:
    translate = Translate(bot)

    context_menu = app_commands.ContextMenu(
        name=locale_str("translate-message", type="context-menu", namespace="translate-message"),
        callback=translate.translate_message,
        type=discord.AppCommandType.message,
    )
    bot.tree.add_command(context_menu)

    await bot.add_cog(translate)
