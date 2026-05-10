from typing import Any, Dict

import discord

from app import logger
from app.constants import FormConstants as form_constants
from app.constants import LogTypes as logconstants
from app.constants import Style as style_constants
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
    parse_form_yaml_to_dict,
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
        self,
        key: str,
        cogs: Dict[str, Any],
        interaction: discord.Interaction,
        enable_composition_controls: bool = True,
        lifecycle_callbacks: Dict[str, Any] = None,
    ):
        self.command_key = key
        self.cogs = cogs
        self.interaction = interaction
        self.enable_composition_controls = enable_composition_controls
        self.lifecycle_callbacks = lifecycle_callbacks or {}
        self.locale = parse_locale(interaction.locale)
        super().__init__(timeout=1800)
        self.add_item(EditButton(
            self.update_command,
            locale=self.locale,
        ))
        self.add_item(self.pause_handler())
        self.add_item(DisableButton(
            callback=self.disable_callback,
            locale=self.locale,
        ))
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

        custom_save = self.lifecycle_callbacks.get(constants.LIFECYCLE_EDIT)
        if custom_save:
            await custom_save(interaction, self, data)
        else:
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

        await interaction.followup.send(embed=embed, view=self, ephemeral=True)

    def pause_handler(self) -> discord.ui.Button:
        if self.cogs.get(constants.ENABLED_KEY):
            return PauseButton(
                callback=self.lifecycle_callbacks.get(constants.LIFECYCLE_PAUSE, self.pause_callback),
                locale=self.locale,
            )

        return UnpauseButton(
            callback=self.lifecycle_callbacks.get(constants.LIFECYCLE_UNPAUSE, self.unpause_callback),
            locale=self.locale,
        )

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
        await interaction.followup.send(embed=embed, view=self, ephemeral=True)

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
        await interaction.followup.send(embed=embed, view=self, ephemeral=True)

    @need_confirmation_modal
    async def disable_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
        else:
            from app.constants import Style as style_constants
            embed = discord.Embed(color=int(style_constants.BACKGROUND_COLOR, base=16))
            footer_text = ml("commands.commands.commons.embed.footer", locale=self.locale)
            embed.set_footer(text=f"• {footer_text}")

        # TODO: handle this type of logic in a service
        if self.command_key == constants.NOTIFICATIONS_TWITCH_KEY:
            handle_unsubscribe_streamer(interaction, self.cogs)
        if self.command_key == constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY:
            handle_unsubscribe_youtube_new_video(interaction, self.cogs)
        custom_disable = self.lifecycle_callbacks.get(constants.LIFECYCLE_DISABLE)
        if custom_disable:
            result = custom_disable(interaction, self.cogs)
            if hasattr(result, "__await__"):
                await result

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

        await interaction.response.send_message(embed=embed, ephemeral=True)
        if interaction.message:
            try:
                await interaction.followup.edit_message(interaction.message.id, view=self)
            except Exception:
                pass

    async def history_callback(self, interaction: discord.Interaction):
        raw_data = find_cog_events_by_guild(self.interaction.guild_id, self.command_key)
        data = parse_history_data(raw_data, interaction)

        title = ml("buttons.changes-history.label", locale=self.locale)
        desc = parse_history_desc(interaction, self.command_key)
        pagination_view = PaginationView(interaction, title, desc, data, sep=4)

        await pagination_view.send(ephemeral=True)

    def handle_add_item_button(self) -> None:
        if not self.enable_composition_controls:
            return
        if self.command_key not in constants.COMPOSITION_COMMANDS_LIST:
            return

        if len(self.cogs[constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]]["values"]) == constants.COMPOSITION_MAX_LENGTH[self.command_key]:
            return

        return self.add_item(AddItemButton(self.add_item_callback, locale=self.locale))

    async def add_item_callback(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        response = self.form_view._parse_responses_to_cog()[constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]].get("values")[0]
        composition_key = constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]
        values = self.cogs[composition_key]["values"]
        unique_by = self._composition_unique_by(composition_key)
        if self._has_composition_value(values, response, unique_by):
            await self._send_duplicate_add_feedback(interaction)
            return

        await self.form_view.pre_finish_step(interaction)

        custom_add_item = self.lifecycle_callbacks.get(constants.LIFECYCLE_ADD_ITEM)
        if custom_add_item:
            result = await custom_add_item(interaction, self, response)
            if result is False:
                await self._send_duplicate_add_feedback(interaction)
                return
        else:
            values.append(response)
            update_cog_by_guild(interaction.guild_id, self.command_key, self.cogs)

        embed = self._response_embed(interaction)
        embed.clear_fields()

        event_date = self._interaction_message_date(interaction)
        embed.title = parse_command_event_description(
            ml("commands.command-events.added.title", locale=self.locale),
            event_date,
            self.interaction,
            self.command_key,
        )
        embed.description = parse_command_event_description(
            ml("commands.command-events.added.description", locale=self.locale),
            event_date,
            self.interaction,
            self.command_key,
        )

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.ADDED_KEY,
            event_date,
            str(interaction.user.id),
        )

        logger.info(
            f"Item added to: **{self.command_key}**",
            log_type=logconstants.BOT_ACTION_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)

    def _composition_unique_by(self, composition_key: str) -> str:
        form_steps = parse_form_yaml_to_dict(self.command_key)
        composition = next(
            (
                step for step in form_steps
                if step.get("action") == form_constants.COMPOSITION_ACTION_KEY
                and step.get("key") == composition_key
            ),
            None,
        )
        return composition.get("unique_by") if composition else None

    def _has_composition_value(self, values: list, response: Dict[str, Any], unique_by: str) -> bool:
        if not unique_by:
            return False
        new_value = self._composition_item_value(response, unique_by)
        return any(
            new_value and self._composition_item_value(item, unique_by) == new_value
            for item in values
        )

    def _composition_item_value(self, item: Dict[str, Any], key: str) -> str:
        field = item.get(key)
        if isinstance(field, dict):
            field = field.get("value") or field.get("values") or field.get("_raw_value")
        if isinstance(field, list):
            field = field[0] if len(field) == 1 else sorted(str(value) for value in field)
        return str(field) if field is not None else None

    async def _send_duplicate_add_feedback(self, interaction: discord.Interaction) -> None:
        message = ml("commands.command-events.added.duplicate", locale=self.locale)
        if not message or message == "commands.command-events.added.duplicate":
            message = "I already have this item saved in the list."
        try:
            await interaction.edit_original_response(content=message, embed=None, view=None)
        except Exception:
            await interaction.followup.send(message, ephemeral=True)

    def _response_embed(self, interaction: discord.Interaction) -> discord.Embed:
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
        else:
            embed = discord.Embed(color=int(style_constants.BACKGROUND_COLOR, base=16))
        footer_text = ml("commands.commands.commons.embed.footer", locale=self.locale)
        if footer_text:
            embed.set_footer(text=f"• {footer_text}")
        embed.set_thumbnail(url=icons.IMAGE_02)
        return embed

    def _interaction_message_date(self, interaction: discord.Interaction):
        if interaction.message:
            return interaction.message.edited_at or interaction.message.created_at or discord.utils.utcnow()
        return discord.utils.utcnow()

    def handle_remove_item_button(self) -> None:
        if not self.enable_composition_controls:
            return
        if self.command_key not in constants.COMPOSITION_COMMANDS_LIST:
            return

        if len(self.cogs[constants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]]["values"]) == 1:
            return

        return self.add_item(RemoveItemButton(self.remove_item_callback, locale=self.locale))

    async def remove_item_callback(self, interaction: discord.Interaction, item_removed: Dict[str, Any],  new_cogs: Dict[str, Any]):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if self.command_key == constants.NOTIFICATIONS_TWITCH_KEY:
            handle_unsubscribe_streamer(interaction, item_removed)
        if self.command_key == constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY:
            handle_unsubscribe_youtube_new_video(interaction, item_removed)
        custom_remove_item = self.lifecycle_callbacks.get(constants.LIFECYCLE_REMOVE_ITEM)
        if custom_remove_item:
            await custom_remove_item(interaction, self, item_removed, new_cogs)
        else:
            update_cog_by_guild(interaction.guild_id, self.command_key, new_cogs)

        embed = self._response_embed(interaction)
        embed.clear_fields()

        event_date = self._interaction_message_date(interaction)
        embed.title = parse_command_event_description(
            ml("commands.command-events.removed.title", locale=self.locale),
            event_date,
            self.interaction,
            self.command_key,
        )
        embed.description = parse_command_event_description(
            ml("commands.command-events.removed.description", locale=self.locale),
            event_date,
            self.interaction,
            self.command_key,
        )

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            constants.REMOVED_KEY,
            event_date,
            str(interaction.user.id),
        )

        logger.info(
            f"Item removed from: **{self.command_key}**",
            log_type=logconstants.BOT_ACTION_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
