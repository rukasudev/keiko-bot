from typing import Any, Dict

import discord

from app import logger
from app.components.buttons import (
    DisableButton,
    EditButton,
    HistoryButton,
    PauseButton,
    UnpauseButton,
)
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.services.cogs import (
    delete_cog_by_guild,
    find_cog_events_by_guild,
    insert_cog_event,
    update_cog_by_guild,
)
from app.services.manager import parse_history_data, parse_history_desc
from app.services.moderations import (
    pause_moderations_by_guild,
    unpause_moderations_by_guild,
)
from app.services.notifications_twitch import unsubscribe_streamer
from app.services.notifications_youtube_video import unsubscribe_youtube_new_video
from app.services.utils import (
    ml,
    need_confirmation_modal,
    parse_command_event_description,
    parse_locale,
)
from app.views.pagination import PaginationView


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
        self.locale = parse_locale(interaction.locale)
        super().__init__(timeout=1800)
        self.add_item(EditButton(self.update_command, locale=self.locale))
        self.add_item(self.pause_handler())
        self.add_item(DisableButton(callback=self.disable_callback, locale=self.locale))
        self.add_item(HistoryButton(callback=self.history_callback, locale=self.locale))

    async def update_command(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        data = self.edited_form_view._parse_responses_to_cog()

        update_cog_by_guild(interaction.guild_id, self.command_key, data)

        embed = interaction.message.embeds[0]
        embed.clear_fields()

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

        logger.info(
            f"Command edited: **{self.command_key}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        await interaction.followup.edit_message(interaction.message.id, view=self)
        await interaction.followup.send(embed=embed, view=self)

    def pause_handler(self) -> discord.ui.Button:
        if self.cogs.get(constants.ENABLED_KEY):
            return PauseButton(callback=self.pause_callback, locale=self.locale)

        return UnpauseButton(callback=self.unpause_callback, locale=self.locale)

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

        logger.info(
            f"Command unpaused: **{self.command_key}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
            guild_id=guild_id,
            interaction=interaction,
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

        logger.info(
            f"Command paused: **{self.command_key}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
            guild_id=guild_id,
            interaction=interaction,
        )

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed, view=self)

    @need_confirmation_modal
    async def disable_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        guild_id = str(interaction.guild.id)
        embed = interaction.message.embeds[0]

        # TODO: handle this type of logic in a service
        if self.command_key == constants.NOTIFICATIONS_TWITCH_KEY:
            unsubscribe_streamer(interaction, self.cogs)
        if self.command_key == constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY:
            unsubscribe_youtube_new_video(interaction, self.cogs)

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
            interaction.message.created_at,
            str(interaction.user.id),
        )

        logger.info(
            f"Command disabled: **{self.command_key}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
            guild_id=guild_id,
            interaction=interaction,
        )

        await interaction.followup.edit_message(interaction.message.id, view=self)
        await interaction.followup.send(embed=embed, view=self)

    async def history_callback(self, interaction: discord.Interaction):
        raw_data = find_cog_events_by_guild(self.interaction.guild_id, self.command_key)
        data = parse_history_data(raw_data, interaction)

        title = ml("buttons.changes-history.label", locale=self.locale)
        desc = parse_history_desc(interaction, self.command_key)
        pagination_view = PaginationView(interaction, title, desc, data, sep=4)

        await pagination_view.send(ephemeral=True)
