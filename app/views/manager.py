from typing import Any, Dict

import discord

from app.components.buttons import DisableButtom, EditButtom, PauseButtom, UnpauseButtom
from app.components.embed import parse_dict_to_embed
from app.constants import Commands as constants
from app.services.moderations import (
    delete_cog_by_guild,
    pause_moderations_by_guild,
    unpause_moderations_by_guild,
    update_cog_by_guild,
)
from app.services.utils import (
    ml,
    need_confirmation_modal,
    parse_command_event_description,
)


class Manager(discord.ui.View):
    """
    A custom view to create a form message with questions and
    save to database.

    Attributes:
        `command_key` -- the key of the form message from form.json file
        `locale` -- the locale of the interaction (ex: pt-br, en-US)
    """

    def __init__(self, key: str, cogs: Dict[str, Any], locale: str, guild_id: str):
        self.command_key = key
        self.cogs = cogs
        self.locale = locale
        self.guild_id = guild_id
        super().__init__()
        self.add_item(EditButtom(after_callback=self.update_command, locale=locale))
        self.add_item(self.pause_handler())
        self.add_item(DisableButtom(callback=self.disable_callback, locale=locale))

    async def update_command(self, interaction: discord.Interaction):
        data = self.edited_form_view._parse_responses_to_cog()

        update_cog_by_guild(interaction.guild_id, self.command_key, data)

        question = list(self.edited_form_view._get_questions())[0]
        embed = parse_dict_to_embed(question)

        embed.title = ml("commands.command-events.edited.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.edited.description", locale=self.locale),
            interaction.message.edited_at,
            interaction.message.interaction.name,
            interaction.user.mention,
        )

        view = self.edited_form_view.view
        view.clear_items()

        await interaction.response.send_message(embed=embed, view=view)

    def pause_handler(self) -> discord.ui.Button:
        if self.cogs.get(constants.ENABLED_KEY):
            return PauseButtom(callback=self.pause_callback, locale=self.locale)

        return UnpauseButtom(callback=self.unpause_callback, locale=self.locale)

    @need_confirmation_modal
    async def unpause_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        embed = interaction.message.embeds[0]

        unpause_moderations_by_guild(guild_id=guild_id, key=self.command_key)

        embed.title = ml("commands.command-events.unpaused.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.unpaused.description", locale=self.locale),
            interaction.message.created_at,
            interaction.message.interaction.name,
            interaction.user.mention,
        )
        self.clear_items()

        await interaction.response.send_message(embed=embed, view=self)

    @need_confirmation_modal
    async def pause_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        embed = interaction.message.embeds[0]

        pause_moderations_by_guild(guild_id=guild_id, key=self.command_key)

        embed.title = ml("commands.command-events.paused.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.paused.description", locale=self.locale),
            interaction.message.created_at,
            interaction.message.interaction.name,
            interaction.user.mention,
        )
        self.clear_items()

        await interaction.response.send_message(embed=embed, view=self)

    @need_confirmation_modal
    async def disable_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        embed = interaction.message.embeds[0]

        unpause_moderations_by_guild(guild_id=guild_id, key=self.command_key)

        delete_cog_by_guild(guild_id, self.command_key)

        embed.title = ml("commands.command-events.disabled.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.disabled.description", locale=self.locale),
            interaction.message.created_at,
            interaction.message.interaction.name,
            interaction.user.mention,
        )
        self.clear_items()

        await interaction.response.send_message(embed=embed, view=self)
