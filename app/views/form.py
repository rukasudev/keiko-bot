from typing import Any, Callable, Dict, Generator, List, Union

import discord

from app import logger
from app.components.buttons import (
    AddItemButton,
    CancelButton,
    ConfirmButton,
    EditButton,
    PreviewButton,
    RemoveItemButton,
)
from app.components.embed import parse_form_dict_to_embed
from app.components.modals import CustomModal
from app.constants import Commands as commandconstants
from app.constants import FormConstants as constants
from app.constants import LogTypes as logconstants
from app.services.cogs import insert_cog_by_guild, insert_cog_event
from app.services.moderations import update_moderations_by_guild
from app.services.notifications_twitch import unsubscribe_streamer
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
from app.services.welcome_messages import send_welcome_message_preview
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

    def __init__(self, command_key: str, locale: str, steps: List[Dict[str, str]] = None, cogs: Dict[str, Any] = None) -> None:
        self.command_key = command_key
        self.locale = locale
        self.steps = self._get_steps(steps)
        self.cogs = cogs
        super().__init__(timeout=1800)
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

    def set_composition_index(self, index: int):
        self.composition_index = int(index)

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

    def get_possible_values(self) -> List[Dict[str, str]]:
        if hasattr(self, "composition_responses") and self.composition_responses:
            return self.composition_responses
        if hasattr(self, "all_cogs") and self.all_cogs:
            return self.all_cogs
        return []

    async def show_modal(self, interaction: discord.Interaction):
        self.view = CustomModal(self._step, self._callback, self.locale, self.get_possible_values())
        if self.cogs: self.parse_cogs_to_modal()
        await interaction.response.send_modal(self.view)

    def parse_cogs_to_modal(self) -> None:
        if isinstance(self.cogs, list):
            return
        cogs = self.cogs[self._step['key']]
        value = self.extract_value_from_cogs(cogs)

        values = value.split(";") if isinstance(value, str) else []

        for i, item in enumerate(self.view.children):
            item.default = values[i] if i < len(values) else ""

    async def show_options(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = OptionsView(
            options=self._get_step_item("options"),
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", False),
        )
        if self.cogs: self.parse_cogs_to_options_view()
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
        if self.cogs: self.parse_cogs_to_options_view()
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
        if self.cogs: self.parse_cogs_to_options_view()
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

        self.step_embed.description += f"\n\n**{ml('commands.commands.default-roles.warning', locale=self.locale)}**\n\n"
        if self.cogs: self.parse_cogs_to_options_view()
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.step_embed, view=self.view
        )

    async def show_resume(self, interaction: discord.Interaction):
        embed = self.step_embed.copy()
        embed.description += get_form_settings_with_database_values(interaction, self.responses)

        self.add_item(EditButton(after_callback=self.update_resume, locale=self.locale))

        if self.command_key in commandconstants.COMPOSITION_COMMANDS_LIST:
            if len(self.responses[0]['value']) < commandconstants.COMPOSITION_MAX_LENGTH[self.command_key]:
                self.add_item(AddItemButton(self.add_item_callback, locale=self.locale))
            if len(self.responses[0]['value']) > 1:
                self.add_item(RemoveItemButton(self.remove_item_callback, locale=self.locale))

        if self._get_step_item("preview"):
            self.add_item(PreviewButton(custom_callback=send_welcome_message_preview, locale=self.locale, command_key=self.command_key))

        self.add_item(ConfirmButton(callback=self._finish, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))
        await interaction.response.edit_message(embed=embed, view=self)

    async def add_item_callback(self, interaction: discord.Interaction):
        self.responses[0]['value'].extend(self.form_view.responses[0]['value'])
        await self.show_resume(interaction)

    async def remove_item_callback(self, interaction: discord.Interaction, item_removed: Dict[str, Any], new_cogs: Dict[str, Any]):
        self.responses[0]['value'] = new_cogs[commandconstants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]]["values"]
        await self.show_resume(interaction)

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
        self.view = FormComposition(self._step, self._callback, self.locale, self.cogs, self.composition_index if hasattr(self, "composition_index") else None)

        await self.view.send_form(interaction)

    async def _finish(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        interaction.locale = parse_valid_locale(interaction.locale)
        cog_param = self._parse_responses_to_cog()
        await self.pre_finish_step(interaction)

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

    async def pre_finish_step(self, interaction: discord.Interaction):
        from app.services.notifications_twitch import (
            handle_subscribe_streamer,
            handle_unsubscribe_streamer,
        )
        from app.services.notifications_youtube_video import (
            handle_subscribe_youtubers_new_video,
            handle_unsubscribe_youtube_new_video,
        )

        subscriptions = {
            commandconstants.NOTIFICATIONS_TWITCH_KEY: {
                "key": "streamer",
                "handler": self._handle_subscription,
                "subscribe": handle_subscribe_streamer,
                "unsubscribe": handle_unsubscribe_streamer,
            },
            commandconstants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: {
                "key": "youtuber",
                "handler": self._handle_subscription,
                "subscribe": handle_subscribe_youtubers_new_video,
                "unsubscribe": handle_unsubscribe_youtube_new_video,
            }
        }

        if self.command_key in subscriptions:
            sub = subscriptions[self.command_key]
            sub["handler"](interaction, sub["subscribe"], sub["unsubscribe"], sub["key"])


    def _handle_subscription(
        self, interaction: discord.Interaction, subscribe_func, unsubscribe_func, key: str):
        index = getattr(self, "composition_index", 0)

        new_entry = self.responses[0]["value"][index]
        old_entry = self.view.form_view.cogs if hasattr(self.view.form_view, "cogs") else None

        if old_entry and old_entry[key]["value"] == new_entry[key]["value"]:
            return

        if old_entry:
            unsubscribe_func(interaction, old_entry)
            subscribe_func(interaction, new_entry)
        else:
            subscribe_func(interaction, self.responses)

    def parse_cogs_to_options_view(self) -> None:
        if isinstance(self.cogs, list):
            return
        cogs = self.cogs[self._step['key']]
        value = self.extract_value_from_cogs(cogs)
        selected_label = ml("buttons.selected.label", locale=self.locale)

        index = 0
        for item in self.view.children:
            index = self.process_item_if_applicable(item, value, selected_label, index)

    def extract_value_from_cogs(self, cogs: Union[dict, list]) -> Union[List[Any], Any]:
        if isinstance(cogs, dict):
            return cogs.get('value') or cogs.get('values')
        return cogs

    def is_value_applicable(self, item: Any, value: Union[List[Any], Any]) -> bool:
        if self._get_step_item("action") == constants.OPTIONS_ACTION_KEY:
            return item.label in value
        if isinstance(value, list):
            return item.custom_id in value
        elif isinstance(value, str):
            return item.custom_id == value
        return False

    def process_item_if_applicable(self, item: Any, value: Union[List[Any], Any], selected_label: str, index: int) -> int:
        if self.is_value_applicable(item, value):
            item.style = discord.ButtonStyle.primary
            self.view.response[item.custom_id] = item.label
            self.step_embed.add_field(
                name=f":flying_disc: {selected_label} #{index + 1}",
                value=item.label,
                inline=False
            )
            return index + 1
        return index

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
