from typing import Any, Dict

import discord

from app import logger
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.components.buttons import (
    AddItemButton,
    DisableButton,
    EditButton,
    HistoryButton,
    PauseButton,
    RemoveItemButton,
    UnpauseButton,
)
from app.constants import Commands as constants
from app.constants import KeikoIcons as icons
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
from app.services.notifications_twitch import handle_unsubscribe_streamer
from app.services.notifications_youtube_video import (
    handle_unsubscribe_youtube_new_video,
)
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
        self.handle_add_item_button()
        self.handle_remove_item_button()
        self.add_item(HistoryButton(callback=self.history_callback, locale=self.locale))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        context = ErrorContext(
            flow=f"manager_{self.command_key}",
            guild_id=str(interaction.guild.id) if interaction.guild else None,
            user_id=str(interaction.user.id),
        )
        logger.error(
            f"Manager error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )

    async def update_command(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        await self.edited_form_view.pre_finish_step(interaction)

        data = self.edited_form_view._parse_responses_to_cog()

        update_cog_by_guild(interaction.guild_id, self.command_key, data)

        if hasattr(self, "_original_embed"):
            embed = self._original_embed
        elif interaction.message.embeds:
            embed = interaction.message.embeds[0]
        else:
            from app.constants import Style as style_constants
            embed = discord.Embed(color=int(style_constants.BACKGROUND_COLOR, base=16))
            footer_text = ml("buttons.footer.report", locale=self.locale) or "Use the command /report to tell me a bug"
            embed.set_footer(text=f"• {footer_text}")

        embed.clear_fields()

        embed.title = ml("commands.command-events.edited.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.edited.description", locale=self.locale),
            interaction.message.edited_at or interaction.message.created_at,
            self.interaction,
            self.command_key,
        )
        embed.set_thumbnail(url=icons.IMAGE_02)

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.EDITED_KEY,
            interaction.message.edited_at,
            str(interaction.user.id),
        )

        view = self.edited_form_view.view
        if view:
            view.clear_items()

        logger.info(
            f"Command edited: **{self.command_key}**",
            log_type=logconstants.BOT_ACTION_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        try:
            await interaction.followup.delete_message(interaction.message.id)
        except Exception:
            pass

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
            log_type=logconstants.BOT_ACTION_TYPE,
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
            log_type=logconstants.BOT_ACTION_TYPE,
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
            handle_unsubscribe_streamer(interaction, self.cogs)
        if self.command_key == constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY:
            handle_unsubscribe_youtube_new_video(interaction, self.cogs)

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
            log_type=logconstants.BOT_ACTION_TYPE,
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

    def handle_add_item_button(self) -> None:
        if self.command_key not in constants.COMPOSITION_COMMANDS_LIST:
            return

        if len(self.cogs[constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]]["values"]) == constants.COMPOSITION_MAX_LENGTH[self.command_key]:
            return

        return self.add_item(AddItemButton(self.add_item_callback, locale=self.locale))

    async def add_item_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        await self.form_view.pre_finish_step(interaction)

        response = self.form_view._parse_responses_to_cog()[constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]].get("values")[0]
        self.cogs[constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]]["values"].append(response)

        update_cog_by_guild(interaction.guild_id, self.command_key, self.cogs)

        embed = interaction.message.embeds[0]
        embed.clear_fields()

        embed.title = parse_command_event_description(
            ml("commands.command-events.added.title", locale=self.locale),
            interaction.message.edited_at,
            self.interaction,
            self.command_key,
        )
        embed.description = parse_command_event_description(
            ml("commands.command-events.added.description", locale=self.locale),
            interaction.message.edited_at,
            self.interaction,
            self.command_key,
        )

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.ADDED_KEY,
            interaction.message.edited_at,
            str(interaction.user.id),
        )

        logger.info(
            f"Item added to: **{self.command_key}**",
            log_type=logconstants.BOT_ACTION_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        await interaction.followup.edit_message(interaction.message.id, view=self)
        await interaction.followup.send(embed=embed, view=self)

    def handle_remove_item_button(self) -> None:
        if self.command_key not in constants.COMPOSITION_COMMANDS_LIST:
            return

        if len(self.cogs[constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]]["values"]) == 1:
            return

        return self.add_item(RemoveItemButton(self.remove_item_callback, locale=self.locale))

    async def remove_item_callback(self, interaction: discord.Interaction, item_removed: Dict[str, Any],  new_cogs: Dict[str, Any]):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if self.command_key == constants.NOTIFICATIONS_TWITCH_KEY:
            handle_unsubscribe_streamer(interaction, item_removed)
        if self.command_key == constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY:
            handle_unsubscribe_youtube_new_video(interaction, item_removed)

        update_cog_by_guild(interaction.guild_id, self.command_key, new_cogs)

        embed = interaction.message.embeds[0]
        embed.clear_fields()

        embed.title = parse_command_event_description(
            ml("commands.command-events.removed.title", locale=self.locale),
            interaction.message.edited_at,
            self.interaction,
            self.command_key,
        )
        embed.description = parse_command_event_description(
            ml("commands.command-events.removed.description", locale=self.locale),
            interaction.message.edited_at,
            self.interaction,
            self.command_key,
        )

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.REMOVED_KEY,
            interaction.message.edited_at,
            str(interaction.user.id),
        )

        logger.info(
            f"Item removed from: **{self.command_key}**",
            log_type=logconstants.BOT_ACTION_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        await interaction.followup.edit_message(interaction.message.id, view=self)
        await interaction.followup.send(embed=embed, view=self)
