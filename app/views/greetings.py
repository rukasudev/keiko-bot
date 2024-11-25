import discord

from app.components.embed import response_embed
from app.components.select import Select
from app.constants import supported_locales
from app.integrations.google_translate import GoogleTranslate


class GreetingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        placeholder = GoogleTranslate().get_language_with_flag(
            discord.Locale.brazil_portuguese.value
        )
        self.add_item(
            Select(
                placeholder,
                self.parse_language_options(),
                custom_callback=self.callback,
            )
        )

    def parse_language_options(self):
        options = {}
        for locale in supported_locales:
            options[locale] = GoogleTranslate().get_language_with_flag(locale)

        return options

    async def send(self, guild: discord.Guild):
        embed = response_embed(
            "messages.greetings-message",
            discord.Locale.brazil_portuguese.value,
            image=True
        )

        for channel in guild.text_channels:
            if not channel.permissions_for(guild.me).send_messages:
                continue
            return await channel.send(embed=embed, view=self)

    async def callback(self, interaction: discord.Interaction):
        selected = self.selected_options[0]

        new_embed = response_embed(
            "messages.greetings-message",
            selected,
            image=True
        )
        self.clear_items()

        self.add_item(Select(
            GoogleTranslate().get_language_with_flag(selected),
            self.parse_language_options(),
            custom_callback=self.callback,
        ))

        await interaction.response.edit_message(embed=new_embed, view=self)
