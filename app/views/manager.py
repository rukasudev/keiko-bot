from typing import Any, Dict

import discord

from app.components.buttons import DisableButtom, EditButtom, PauseButtom, UnpauseButtom
from app.components.embed import parse_dict_to_embed
from app.constants import Commands as constants
from app.services.cogs import delete_cog_by_guild, insert_cog_event, update_cog_by_guild
from app.services.moderations import (
    pause_moderations_by_guild,
    unpause_moderations_by_guild,
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

    def __init__(
        self, key: str, cogs: Dict[str, Any], interaction: discord.Interaction
    ):
        self.command_key = key
        self.cogs = cogs
        self.interaction = interaction
        self.locale = interaction.locale
        super().__init__()
        self.add_item(EditButtom(self.update_command, locale=self.locale))
        self.add_item(self.pause_handler())
        self.add_item(DisableButtom(self.disable_callback, locale=self.locale))

    async def update_command(self, interaction: discord.Interaction):
        data = self.edited_form_view._parse_responses_to_cog()

        update_cog_by_guild(interaction.guild_id, self.command_key, data)

        question = list(self.edited_form_view._get_questions())[0]
        embed = parse_dict_to_embed(question)

        embed.title = ml("commands.command-events.edited.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.edited.description", locale=self.locale),
            interaction.message.edited_at,
            self.interaction,
            self.command_key,
        )

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.EDITED_KEY,
            interaction.message.edited_at,
            str(interaction.user.id),
        )

        view = self.edited_form_view.view
        view.clear_items()

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, view=self)

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
            self.interaction,
            self.command_key,
        )
        self.clear_items()

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.UNPAUSED_KEY,
            interaction.message.created_at,
            str(interaction.user.id),
        )

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, view=self)

    @need_confirmation_modal
    async def pause_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        embed = interaction.message.embeds[0]

        pause_moderations_by_guild(guild_id=guild_id, key=self.command_key)

        embed.title = ml("commands.command-events.paused.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.paused.description", locale=self.locale),
            interaction.message.created_at,
            self.interaction,
            self.command_key,
        )
        self.clear_items()

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.PAUSED_KEY,
            interaction.message.created_at,
            str(interaction.user.id),
        )

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, view=self)

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
            self.interaction,
            self.command_key,
        )
        self.clear_items()

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.DISABLED_KEY,
            interaction.message.edited_at,
            str(interaction.user.id),
        )

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, view=self)
