import discord

from app.components.buttons import DisableButtom, EditButtom
from app.services.moderations import delete_cog_by_guild, update_moderations_by_guild, update_cog_by_guild
from app.components.embed import parse_dict_to_embed
from app.services.utils import (
    parse_command_event_description,
)
from i18n import t


class Manager(discord.ui.View):
    """
    A custom view to create a form message with questions and
    save to database.

    Attributes:
        `command_key` -- the key of the form message from form.json file
        `locale` -- the locale of the interaction (ex: pt-br, en-US)
    """

    def __init__(self, key: str, locale: str):
        self.command_key = key
        self.locale = locale
        super().__init__()
        self.add_item(EditButtom(after_callback=self.update_command, locale=locale))
        self.add_item(DisableButtom(callback=self.disable_callback, locale=locale))

    async def update_command(self, interaction: discord.Interaction):
        data = self.edited_form_view._parse_responses_to_cog()

        await update_cog_by_guild(interaction.guild_id, self.command_key, data)

        question = list(self.edited_form_view._get_questions())[0]
        embed = parse_dict_to_embed(question)

        embed.title = t("commands.command-event.edit.title", locale=self.locale)
        embed.description = parse_command_event_description(
            t("commands.command-event.edit.description", locale=self.locale),
            interaction.message.edited_at,
            interaction.message.interaction.name,
            interaction.user.mention
        )

        view = self.edited_form_view.view
        view.clear_items()

        await interaction.response.send_message(embed=embed, view=view)

    async def disable_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        embed = interaction.message.embeds[0]

        await update_moderations_by_guild(
            guild_id=guild_id, data=self.command_key, value=False
        )

        await delete_cog_by_guild(guild_id, self.command_key)

        embed.title = t("commands.command-event.disabled.title", locale=self.locale)
        embed.description = parse_command_event_description(
            t("commands.command-event.disabled.description", locale=self.locale),
            interaction.message.created_at,
            interaction.message.interaction.name,
            interaction.user.mention
        )
        self.clear_items()

        await interaction.response.send_message(embed=embed, view=self)
