from typing import Any, Callable, Dict, Generator, List

import discord

from app import logger
from app.components.buttons import CancelButton, ConfirmButton, EditButton
from app.components.embed import parse_form_dict_to_embed
from app.components.modals import CustomModal
from app.constants import Commands as commandconstants
from app.constants import FormConstants as constants
from app.constants import LogTypes as logconstants
from app.constants import supported_locales
from app.services.cogs import insert_cog_by_guild, insert_cog_event
from app.services.moderations import update_moderations_by_guild
from app.services.utils import (
    get_available_roles_by_guild,
    get_form_settings_with_database_values,
    get_roles_by_guild,
    get_text_channels_by_guild,
    ml,
    parse_command_event_description,
    parse_form_yaml_to_dict,
    parse_valid_locale,
)
from app.views.composition import FormComposition
from app.views.options import OptionsView


class Form(discord.ui.View):
    """
    A custom view to create a form message with steps and
    after save the answers to database.

    Attributes:
        `command_key` -- the key of the command that will be created
        `locale` -- the locale of the interaction (ex: pt-br, en-US)
    """

    def __init__(self, command_key: str, locale: str, steps: List[Dict[str, str]] = None) -> None:
        self.command_key = command_key
        self.locale = locale
        self.steps = self._get_steps(steps)
        super().__init__()
        self.add_item(ConfirmButton(callback=self._callback, locale=locale))
        self.add_item(CancelButton(locale=locale))

    def _get_steps(self, steps: List[Dict[str, str]] = None) -> Generator[Any, Any, Any]:
        if not steps:
            steps = parse_form_yaml_to_dict(self.command_key)
            self._set_titles_and_descriptions(steps)
        yield from steps

    def _update_form_step(func):
        async def update_counter(self, args):
            args.locale = parse_valid_locale(args.locale)

            is_valid_response = self._handle_after_step()
            if not is_valid_response:
                return await func(self, args)

            try:
                self._step = next(self.steps)
            except StopIteration:
                self.pre_finish_step(args)
                return await self._after_callback(args)

            self.step_embed = parse_form_dict_to_embed(self._step, self.locale)
            await func(self, args)

        return update_counter

    async def _after_callback(self, interaction):
        if not hasattr(self, "after_callback"):
            return
        return await self.after_callback(interaction)

    def _set_after_callback(self, after_callback: Callable):
        self.after_callback = after_callback

    def _handle_after_step(self):
        if not hasattr(self, "view"):
            return True

        if not hasattr(self, "responses"):
            self.responses = []

        if self._get_step_item("action") == constants.BUTTON_ACTION_KEY:
            return True

        if self._get_step_item("action") == constants.MODAL_ACTION_KEY and not self.view.get_response():
            return

        return self._save_step_response()

    def _save_step_response(self):
        self.responses.append(
            {
                "key": self._get_step_item("key"),
                "title": self._get_step_item("title"),
                "value": self.view.get_response(),
                "style": self._get_step_item("style"),
            }
        )
        return self.responses

    def _get_step_item(self, key: str, default_value: Any = None) -> Dict[str, Any]:
        multi_lang_keys = ["title", "description", "footer", "fields"]
        if key in multi_lang_keys:
            return self._step[key][self.locale]
        return self._step.get(key, default_value)

    async def update_resume(self, interaction: discord.Interaction):
        for edited_item in self.edited_form_view.responses:
            for item in self.responses:
                if edited_item["key"] == item["key"]:
                    item["value"] = edited_item["value"]
        return await self.show_resume(interaction)

    def _parse_responses_to_cog(self) -> Dict[str, str]:
        cog_param = {commandconstants.ENABLED_KEY: True}

        for item in self.responses:
            value = item["value"]
            if not isinstance(value, str):
                value = list(value)
            if item.get("style"):
                cog_param[item["key"]] = {"style": item["style"], "values": value}
            else:
                cog_param[item["key"]] = value
        return cog_param

    def filter_steps(self, steps: List[str]):
        self.steps = (
            step
            for step in iter(self._get_steps())
            if step["key"] in steps
        )

    def _set_titles_and_descriptions(self, steps: List[Dict[str, str]]):
        for step in steps:
            if step["action"] == constants.COMPOSITION_ACTION_KEY:
                return self._set_titles_and_descriptions(step["steps"])

            self.title_and_desc = {
                step["title"][self.locale]: step["description"][self.locale]
                for step in steps
                if step["action"] not in constants.NO_ACTION_LIST
            }
        return self.title_and_desc

    def get_form_titles_and_descriptions(self) -> List[Dict[str, str]]:
        return self.title_and_desc

    def get_form_embed(self) -> discord.Embed:
        return parse_form_dict_to_embed(next(self.steps), self.locale)

    async def show_modal(self, interaction: discord.Interaction):
        self.view = CustomModal(self._step, self._callback, self.locale)
        await interaction.response.send_modal(self.view)

    async def show_options(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = OptionsView(
            options=self._get_step_item("options"),
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", False),
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.step_embed, view=self.view
        )

    async def show_channels(self, interaction: discord.Interaction):
        channels = get_text_channels_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=channels,
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", False),
            styled_values=True,
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.step_embed, view=self.view
        )

    async def show_roles(self, interaction: discord.Interaction):
        roles = get_roles_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=roles,
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", False),
            styled_values=True,
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.step_embed, view=self.view
        )

    async def show_available_roles(self, interaction: discord.Interaction):
        roles = get_available_roles_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=roles,
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", False),
            styled_values=True,
        )

        if not roles:
            error_message = ml(
                "errors.command-default-roles-low-permissions.message",
                locale=self.locale,
            )
            self.step_embed.description = error_message

        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.step_embed, view=self.view
        )

    async def show_resume(self, interaction: discord.Interaction):
        embed = self.step_embed.copy()
        embed.description += get_form_settings_with_database_values(interaction, self.responses)

        self.add_item(EditButton(after_callback=self.update_resume, locale=self.locale))
        self.add_item(ConfirmButton(callback=self._finish, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))
        await interaction.response.edit_message(embed=embed, view=self)

    async def show_buttons(self, interaction: discord.Interaction):
        self.clear_items()

        embed = interaction.message.embeds[0]
        embed.clear_fields()

        embed.title = f"{self._get_step_item('emoji')} {self._get_step_item('title')}"
        embed.description = self._get_step_item("description")
        embed.set_footer(text=self._get_step_item("footer"))

        for field in self._get_step_item("fields"):
            embed.add_field(name=field["title"], value=field["message"], inline=False)

        self.add_item(ConfirmButton(callback=self._callback, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))
        await interaction.response.edit_message(embed=embed, view=self)

    async def show_composition(self, interaction: discord.Interaction):
        self.view = FormComposition(self._step, self._callback, self.locale)

        await self.view.interate(interaction)

    async def _finish(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        interaction.locale = parse_valid_locale(interaction.locale)
        cog_param = self._parse_responses_to_cog()
        self.pre_finish_step(interaction)

        update_moderations_by_guild(
            guild_id=interaction.guild_id, key=self.command_key, value=True
        )
        insert_cog_by_guild(
            guild_id=interaction.guild_id, cog=self.command_key, data=cog_param
        )

        self.clear_items()

        await interaction.followup.edit_message(interaction.message.id, view=None)

        embed = interaction.message.embeds[0]
        embed.title = ml("commands.command-events.enabled.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.enabled.description", locale=self.locale),
            interaction.message.edited_at,
            interaction,
            self.command_key,
        )

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            commandconstants.ENABLED_KEY,
            interaction.message.edited_at,
            str(interaction.user.id),
        )

        logger.info(
            f"Command enabled: **{self.command_key}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        await interaction.followup.send(embed=embed, view=self)

    def pre_finish_step(self, interaction: discord.Interaction):
        if self.command_key == commandconstants.NOTIFICATIONS_TWITCH_KEY:
            from app.services.notifications_twitch import subscribe_streamer
            subscribe_streamer(interaction, self.responses)
        if self.command_key == commandconstants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY:
            from app.services.notifications_youtube_video import (
                subscribe_youtube_new_video,
            )
            subscribe_youtube_new_video(interaction, self.responses)

    async def get_action_by_type(self, action, interaction) -> None:
        action_dict = {
            constants.MODAL_ACTION_KEY: self.show_modal,
            constants.OPTIONS_ACTION_KEY: self.show_options,
            constants.ROLES_ACTION_KEY: self.show_roles,
            constants.AVAILABLE_ROLES_ACTION_KEY: self.show_available_roles,
            constants.CHANNELS_ACTION_KEY: self.show_channels,
            constants.RESUME_ACTION_KEY: self.show_resume,
            constants.BUTTON_ACTION_KEY: self.show_buttons,
            constants.COMPOSITION_ACTION_KEY: self.show_composition,
        }

        if action in action_dict:
            return await action_dict[action](interaction)

    @_update_form_step
    async def _callback(self, interaction: discord.Interaction) -> None:
        self.clear_items()

        action = self._get_step_item("action")
        return await self.get_action_by_type(action, interaction)
