import asyncio
from typing import Any, Callable, Dict, Generator, List, Optional, Union

import discord

from app import logger
from app.components.buttons import (
    AddItemButton,
    CancelButton,
    ConfirmButton,
    EditButton,
    FormBackButton,
    PreviewButton,
    RemoveItemButton,
)
from app.components.select_views import ChannelSelectView, RoleSelectView, MultiSelectView, DesignSelectView, FileUploadModal
from app.components.embed import parse_form_dict_to_embed
from app.components.modals import CustomModal
from app.constants import Commands as commandconstants
from app.constants import FormConstants as constants
from app.constants import LogTypes as logconstants
from app.integrations.stream_elements import StreamElementsClient
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
from app.views.form_state import FormStateManager
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
        self.state = FormStateManager(list(self._get_steps(steps)))
        self.cogs = cogs
        self.responses = []
        self._using_layout_view = False
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

            if not self.state.advance():
                return await self._after_callback(args)

            self._step = self.state.current_step

            if self._step.get("action") == constants.FORM_ACTION_KEY:
                if not self.state.advance():
                    return await self._after_callback(args)
                self._step = self.state.current_step

            while self._should_skip_step():
                if not self.state.advance():
                    return await self._after_callback(args)
                self._step = self.state.current_step

            self.step_embed = parse_form_dict_to_embed(self._step, self.locale)
            await func(self, args)

        return update_counter

    def _should_skip_step(self) -> bool:
        """Check if current step should be skipped based on conditions."""
        condition = self._step.get("condition")
        if not condition:
            return False

        key = condition.get("key")
        not_in = condition.get("not_in", [])

        response = next((r for r in self.responses if r["key"] == key), None)
        if response:
            value = response.get("_raw_value", response.get("value"))
        else:
            value = None
        return value in not_in

    def _should_skip_step_on_back(self) -> bool:
        """Check if current step should be skipped when navigating back.

        Pula steps de modal porque não possuem botão de voltar.
        """
        if self._should_skip_step():
            return True
        action = self._step.get("action")
        return action in (constants.MODAL_ACTION_KEY, constants.FILE_UPLOAD_ACTION_KEY)

    async def _after_callback(self, interaction):
        if not hasattr(self, "after_callback"):
            return
        return await self.after_callback(interaction)

    def _start_preview_pregeneration(self, interaction: discord.Interaction) -> None:
        """Inicia pre-geração de design previews em background para welcome_messages."""
        if self.command_key != commandconstants.WELCOME_MESSAGES_KEY:
            return
        if hasattr(self, '_preview_task'):
            return

        self._preview_task = asyncio.create_task(
            self._pregenerate_design_previews(interaction)
        )

    async def _pregenerate_design_previews(
        self, interaction: discord.Interaction
    ) -> Dict[str, str]:
        """Pre-generate design previews em background para fluxo welcome_messages."""
        from app.services.welcome_messages import generate_design_previews

        member = interaction.user
        if not isinstance(member, discord.Member):
            member = interaction.guild.get_member(interaction.user.id)

        if not member:
            return {}

        design_step = next(
            (s for s in self.state.steps_list if s.get("action") == constants.DESIGN_SELECT_ACTION_KEY),
            None
        )
        if not design_step:
            return {}

        designs = design_step.get("designs", [])
        return await generate_design_previews(member, designs)

    def _set_after_callback(self, after_callback: Callable):
        self.after_callback = after_callback

    def _handle_after_step(self):
        if not hasattr(self, "view"):
            return True

        if self._get_step_item("action") == constants.BUTTON_ACTION_KEY:
            return True

        action = self._get_step_item("action")
        if action in (constants.MODAL_ACTION_KEY, constants.FILE_UPLOAD_ACTION_KEY) and not self.view.get_response():
            return

        return self._save_step_response()

    def _upsert_response(self, response_data: Dict[str, Any]) -> None:
        """Atualiza resposta existente ou adiciona nova."""
        key = response_data.get("key")
        for i, existing in enumerate(self.responses):
            if existing.get("key") == key:
                self.responses[i] = response_data
                return
        self.responses.append(response_data)

    def _save_step_response(self):
        response = self.view.get_response()
        self.state.save_response(response, self._step)

        if self._get_step_item("action") == constants.DESIGN_SELECT_ACTION_KEY:
            designs = self._get_step_item("designs", [])
            design = next((d for d in designs if d["key"] == response), None)
            if design:
                label = design["label"].get(self.locale) or design["label"].get("en-us", response)
                self._upsert_response({
                    "key": self._get_step_item("key"),
                    "title": self._get_step_item("title"),
                    "value": label,
                    "style": None,
                    "_raw_value": response,
                })
                return self.responses

        if self._get_step_item("action") == constants.MULTI_SELECT_ACTION_KEY:
            if isinstance(response, dict):
                for key, data in response.items():
                    if isinstance(data, dict):
                        self._upsert_response({
                            "key": key,
                            "title": key,
                            "value": data.get("values"),
                            "style": data.get("style"),
                        })
                    else:
                        self._upsert_response({
                            "key": key,
                            "title": key,
                            "value": data,
                            "style": None,
                        })
            return self.responses

        if isinstance(response, dict):
            fields = self._step.get("fields", [])

            for i, field in enumerate(fields):
                field_key = field.get("key")
                if field_key and field_key in response:
                    label = field.get("label", {})
                    title = label.get(self.locale) if isinstance(label, dict) else field_key
                    self._upsert_response({
                        "key": field_key,
                        "title": title,
                        "value": response[field_key],
                        "style": None,
                    })

            if "__concat__" in response:
                self._upsert_response({
                    "key": self._get_step_item("key"),
                    "title": self._get_step_item("title"),
                    "value": response["__concat__"],
                    "style": self._get_step_item("style"),
                })

            return self.responses

        self._upsert_response({
            "key": self._get_step_item("key"),
            "title": self._get_step_item("title"),
            "value": response,
            "style": self._get_step_item("style"),
        })
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
                    if "_raw_value" in edited_item:
                        item["_raw_value"] = edited_item["_raw_value"]
        return await self.show_resume(interaction)

    def _parse_responses_to_cog(self) -> Dict[str, str]:
        cog_param = {commandconstants.ENABLED_KEY: True}

        for item in self.responses:
            value = item.get("_raw_value", item["value"])
            if not isinstance(value, str):
                value = list(value)
            if item.get("style"):
                cog_param[item["key"]] = {"style": item["style"], "values": value}
            else:
                cog_param[item["key"]] = value
        return cog_param

    def _get_dependent_steps(self, steps: List[str]) -> set:
        """Get steps that have conditions dependent on the given steps."""
        return {
            s["key"] for s in self.state.steps_list
            if s.get("condition", {}).get("key") in steps
        }

    def filter_steps(self, steps: List[str]):
        """Filter steps list to only include selected steps and their dependents."""
        all_steps = set(steps) | self._get_dependent_steps(steps)
        self.state.steps_list = [s for s in self.state.steps_list if s["key"] in all_steps]

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
        return parse_form_dict_to_embed(self.state.steps_list[0], self.locale)

    def get_possible_values(self) -> List[Dict[str, str]]:
        if hasattr(self, "composition_responses") and self.composition_responses:
            return self.composition_responses
        if hasattr(self, "all_cogs") and self.all_cogs:
            return self.all_cogs
        return []

    async def show_modal(self, interaction: discord.Interaction):
        self.view = CustomModal(self._step, self._callback, self.locale, self.get_possible_values())
        if not self.state.fill_modal(self.view) and self.cogs:
            self.parse_cogs_to_modal()

        await interaction.response.send_modal(self.view)

    def parse_cogs_to_modal(self) -> None:
        if isinstance(self.cogs, list):
            return

        fields = self._step.get("fields", [])
        has_field_keys = any(f.get("key") for f in fields) if fields else False

        if has_field_keys:
            for i, field in enumerate(fields):
                field_key = field.get("key")
                if field_key and field_key in self.cogs:
                    cogs = self.cogs[field_key]
                    value = self.extract_value_from_cogs(cogs)
                    if i < len(self.view.children):
                        self.view.children[i].default = value or ""

            step_key = self._step.get("key")
            if step_key in self.cogs:
                cogs = self.cogs[step_key]
                value = self.extract_value_from_cogs(cogs)
                values = value.split(";") if isinstance(value, str) else []
                concat_index = 0
                for i, field in enumerate(fields):
                    if not field.get("key") and i < len(self.view.children):
                        self.view.children[i].default = values[concat_index] if concat_index < len(values) else ""
                        concat_index += 1
            return

        step_key = self._step.get('key')
        if step_key not in self.cogs:
            return
        cogs = self.cogs[step_key]
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
        await self._send_view(interaction)

    async def show_channels(self, interaction: discord.Interaction):
        if self._get_step_item("select"):
            return await self._show_channels_select(interaction)
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
        await self._send_view(interaction)

    async def _show_channels_select(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = ChannelSelectView(
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", True),
        )
        await self._send_view(interaction)

    async def show_roles(self, interaction: discord.Interaction):
        if self._get_step_item("select"):
            return await self._show_roles_select(interaction)
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
        await self._send_view(interaction)

    async def _show_roles_select(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = RoleSelectView(
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", True),
        )
        await self._send_view(interaction)

    async def show_available_roles(self, interaction: discord.Interaction):
        if self._get_step_item("select"):
            return await self._show_roles_select(interaction)
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
        await self._send_view(interaction)

    async def show_resume(self, interaction: discord.Interaction):
        embed = self.step_embed.copy()
        embed.description += get_form_settings_with_database_values(interaction, self.responses)

        self.clear_items()
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

        await self._transition_from_layout_view(interaction, embed, self)

    async def add_item_callback(self, interaction: discord.Interaction):
        self.responses[0]['value'].extend(self.form_view.responses[0]['value'])
        await self.show_resume(interaction)

    async def remove_item_callback(self, interaction: discord.Interaction, item_removed: Dict[str, Any], new_cogs: Dict[str, Any]):
        self.responses[0]['value'] = new_cogs[commandconstants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]]["values"]
        await self.show_resume(interaction)

    async def show_buttons(self, interaction: discord.Interaction):
        self.clear_items()

        fields = self._step.get("fields", {}).get(self.locale, []) if self._step.get("fields") else []
        for field in fields:
            self.step_embed.add_field(name=field["title"], value=field["message"], inline=False)

        self.add_item(ConfirmButton(callback=self._callback, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))
        if self.state.can_go_back:
            self.add_item(FormBackButton(self, self.locale))

        await self._transition_from_layout_view(interaction, self.step_embed, self)

    async def show_composition(self, interaction: discord.Interaction):
        self.view = FormComposition(self._step, self._callback, self.locale, self.cogs, self.composition_index if hasattr(self, "composition_index") else None)

        await self.view.send_form(interaction)

    async def show_multi_select(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = MultiSelectView(
            config=self._step,
            callback=self._callback,
            locale=self.locale,
        )
        await self._send_view(interaction)

    async def show_design_select(self, interaction: discord.Interaction):
        await interaction.response.defer()

        designs = self._get_step_item("designs", [])

        try:
            preview_urls = await self._preview_task
        except Exception as e:
            logger.warn(f"Preview pre-generation failed: {e}")
            preview_urls = {}

        self._design_select_view = DesignSelectView(
            callback=self._design_select_callback,
            locale=self.locale,
            designs=designs,
            back_callback=self._go_back if self.state.can_go_back else None,
            preview_urls=preview_urls,
        )
        self.view = self._design_select_view
        await self._send_layout_view(interaction)

    async def _design_select_callback(self, interaction: discord.Interaction, reselection: bool = False):
        if reselection:
            self.view = self._design_select_view
            for i, step in enumerate(self.state.steps_list):
                if step.get("action") == constants.DESIGN_SELECT_ACTION_KEY:
                    self.state.step_index = i
                    self._step = step
                    break
        await self._callback(interaction)

    async def show_file_upload(self, interaction: discord.Interaction):
        modal_title = self._step.get("modal_title", {}).get(self.locale)
        self.view = FileUploadModal(
            callback=self._callback,
            locale=self.locale,
            title=modal_title,
        )
        await interaction.response.send_modal(self.view)

    async def _finish(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        interaction.locale = parse_valid_locale(interaction.locale)

        await self.pre_finish_step(interaction)

        cog_param = self._parse_responses_to_cog()

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
        from app import bot
        from app.services.notifications_twitch import (
            handle_subscribe_streamer,
            handle_unsubscribe_streamer,
        )
        from app.services.notifications_youtube_video import (
            handle_subscribe_youtubers_new_video,
            handle_unsubscribe_youtube_new_video,
        )

        if bot.config.is_dev():
            return

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

        if self.command_key == commandconstants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY:
            streamer = self.responses[0]["value"]
            channel_info = StreamElementsClient.get_channel_info(streamer)
            self.responses.append({"key": "channel_id", "title": "Channel ID", "value": channel_info["_id"]})


    def _handle_subscription(
        self, interaction: discord.Interaction, subscribe_func, unsubscribe_func, key: str):
        index = getattr(self, "composition_index", 0)

        new_entry = self.responses[0]["value"][index]
        old_entry = self.view.form_view.cogs if hasattr(self.view.form_view, "cogs") else None

        if isinstance(old_entry, list):
            old_entry = None

        if old_entry and old_entry[key]["value"] == new_entry[key]["value"]:
            return

        if old_entry:
            unsubscribe_func(interaction, old_entry)
            subscribe_func(interaction, new_entry)
        else:
            subscribe_func(interaction, self.responses)

    def _parse_cogs_to_select(self) -> None:
        if isinstance(self.cogs, list):
            return
        if self._step['key'] not in self.cogs:
            return
        cogs = self.cogs[self._step['key']]
        value = self.extract_value_from_cogs(cogs)
        if isinstance(value, list):
            for v in value:
                self.view.response[v] = v
        elif value:
            self.view.response[value] = value

    def _parse_cogs_to_design_select(self) -> None:
        if isinstance(self.cogs, list):
            return
        if self._step['key'] not in self.cogs:
            return
        value = self.cogs[self._step['key']]
        if value:
            self.view.response[value] = value

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
            constants.MULTI_SELECT_ACTION_KEY: self.show_multi_select,
            constants.DESIGN_SELECT_ACTION_KEY: self.show_design_select,
            constants.FILE_UPLOAD_ACTION_KEY: self.show_file_upload,
        }

        if action in action_dict:
            return await action_dict[action](interaction)

    async def _go_back(self, interaction: discord.Interaction):
        if not self.state.go_back():
            return

        if self.responses and self._get_step_item("action") != constants.BUTTON_ACTION_KEY:
            self.responses.pop()

        self._step = self.state.current_step

        while self._should_skip_step_on_back():
            if not self.state.go_back():
                return
            if self.responses and self._get_step_item("action") != constants.BUTTON_ACTION_KEY:
                self.responses.pop()
            self._step = self.state.current_step

        self.step_embed = parse_form_dict_to_embed(self._step, self.locale)

        self.clear_items()
        await self.get_action_by_type(self._get_step_item("action"), interaction)
        self.state.clear_previous_response()

    def _add_back_button(self):
        if self.state.can_go_back:
            self.view.add_item(FormBackButton(self, self.locale))

    async def _send_view(self, interaction: discord.Interaction):
        """Prepare and send view with auto-fill and back button."""
        view_config = {
            OptionsView: ('options', self.parse_cogs_to_options_view),
            ChannelSelectView: ('select', self._parse_cogs_to_select),
            RoleSelectView: ('select', self._parse_cogs_to_select),
            MultiSelectView: ('multi_select', None),
        }

        config = view_config.get(type(self.view))
        if config:
            fill_type, cogs_fallback = config
            fill_fn = getattr(self.state, f'fill_{fill_type}')
            if not fill_fn(self.view) and self.cogs and cogs_fallback:
                cogs_fallback()

        self._add_back_button()

        await self._transition_from_layout_view(interaction, self.step_embed, self.view, deferred=True)

    async def _transition_from_layout_view(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        view: discord.ui.View,
        deferred: bool = False
    ):
        """Handle transition from Components V2 LayoutView back to embed-based view."""
        if self._using_layout_view:
            self._using_layout_view = False
            if not deferred:
                await interaction.response.defer()
            await interaction.followup.delete_message(interaction.message.id)
            await interaction.followup.send(embed=embed, view=view)
        else:
            if deferred:
                await interaction.followup.edit_message(
                    message_id=interaction.message.id,
                    embed=embed,
                    view=view
                )
            else:
                await interaction.response.edit_message(embed=embed, view=view)

    async def _send_layout_view(self, interaction: discord.Interaction):
        """Send LayoutView (Components V2) without embed."""
        view_config = {
            DesignSelectView: ('design_select', self._parse_cogs_to_design_select),
        }

        config = view_config.get(type(self.view))
        if config:
            fill_type, cogs_fallback = config
            fill_fn = getattr(self.state, f'fill_{fill_type}', None)
            if fill_fn and not fill_fn(self.view) and self.cogs and cogs_fallback:
                cogs_fallback()

        await interaction.followup.delete_message(interaction.message.id)
        await interaction.followup.send(view=self.view)
        self._using_layout_view = True

    @_update_form_step
    async def _callback(self, interaction: discord.Interaction) -> None:
        self._start_preview_pregeneration(interaction)

        self.clear_items()

        action = self._get_step_item("action")
        return await self.get_action_by_type(action, interaction)
