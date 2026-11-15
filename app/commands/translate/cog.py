"""The translate context menu: a message in the reader's own language."""

import discord
from discord import app_commands

from app.bot import DiscordBot, reader_locale
from app.commands.translate.service import translate_embed
from app.core.threads import to_thread
from app.integrations.google_translate import GoogleTranslate
from app.translator import locale_str
from app.types.cogs import Cog
from app.ui.embed import response_error_embed


class Translate(Cog):
    """The context menu that translates a message."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def translate_message(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Translate a message into the reader's own language, told in the server's."""
        reader = reader_locale(interaction)
        if message.content.strip() == "":
            embed = response_error_embed(
                "translate.errors.invalid_message", interaction.locale
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if GoogleTranslate.is_only_url(message.content):
            embed = response_error_embed(
                "translate.errors.only_link", interaction.locale
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        response = await to_thread(GoogleTranslate.translate, message.content, reader)
        if not response:
            embed = response_error_embed("translate.errors.generic", interaction.locale)
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        embed = translate_embed(interaction.locale, response, message.jump_url)

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: DiscordBot) -> None:
    """Register the translate context menu."""
    translate = Translate(bot)

    context_menu = app_commands.ContextMenu(
        name=locale_str(
            "translate-message", type="context-menu", namespace="translate.slash"
        ),
        callback=translate.translate_message,
        type=discord.AppCommandType.message,
    )
    bot.tree.add_command(context_menu)

    await bot.add_cog(translate)
