import asyncio
import inspect
from typing import Any, Callable, Dict, Generator, List, Optional, Union

import discord
from discord import SelectDefaultValue, SelectDefaultValueType

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
from app.components.select_views import ChannelSelectView, RoleSelectView, MultiSelectView, DesignSelectView, FileUploadModal, UserSelectView, MonthSelectView
from app.components.embed import parse_form_dict_to_embed
from app.components.modals import CustomModal
from app.constants import Commands as commandconstants
from app.constants import FormConstants as constants
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.integrations.stream_elements import StreamElementsClient
from app.services.cogs import insert_cog_by_guild, insert_cog_event
from app.services.compositions import merge_composition_item_by_nested_value
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

    def __init__(
        self,
        command_key: str,
        locale: str,
        steps: List[Dict[str, str]] = None,
        cogs: Dict[str, Any] = None,
        persistence_callback: Callable = None,
    ) -> None:
        self.command_key = command_key
        self.locale = locale
        self.state = FormStateManager(list(self._get_steps(steps)))
        self.cogs = cogs
        self.responses = []
        self.persistence_callback = persistence_callback
        self._using_layout_view = False
        super().__init__(timeout=1800)
        self.add_item(ConfirmButton(callback=self._callback, locale=locale))
        self.add_item(CancelButton(locale=locale))

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        context = ErrorContext(
            flow=f"form_{self.command_key}",
            guild_id=str(interaction.guild.id) if interaction.guild else None,
            user_id=str(interaction.user.id),
            extra={"step": self._step.get("key") if hasattr(self, "_step") else None},
        )
        logger.error(
            f"Form error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )

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
                selects = self._step.get("selects", [])
                select_labels = {
                    s["key"]: s["label"].get(self.locale) or s["label"].get("en-us", s["key"])
                    for s in selects if s.get("label")
                }
                for key, data in response.items():
                    title = select_labels.get(key, key)
                    if isinstance(data, dict):
                        self._upsert_response({
                            "key": key,
                            "title": title,
                            "value": data.get("values"),
                            "style": data.get("style"),
                        })
                    else:
                        self._upsert_response({
                            "key": key,
                            "title": title,
                            "value": data,
                            "style": None,
                        })
            return self.responses

        if self._get_step_item("action") == constants.SUMMARY_CARD_ACTION_KEY:
            if isinstance(response, dict):
                self._save_summary_card_response(response)
            return self.responses

        if isinstance(response, dict):
            transformed_response = self._transform_step_response(response)
            if transformed_response:
                self._upsert_response(transformed_response)
                return self.responses

            fields = self._step.get("fields", [])

            for i, field in enumerate(fields):
                field_key = field.get("key")
                if field_key and field_key in response:
                    value = response[field_key]
                    if value is None:
                        continue
                    label = field.get("label", {})
                    title = label.get(self.locale) if isinstance(label, dict) else field_key
                    self._upsert_response({
                        "key": field_key,
                        "title": title,
                        "value": value,
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

    def _transform_step_response(self, response: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        transform = self._get_step_item("response_transform")
        if transform == "birthday_date_parts" and "day" in response:
            from app.services.dates import parse_date_parts

            month = next((r.get("_raw_value", r["value"]) for r in self.responses if r["key"] == "month"), None)
            return {
                "key": self._get_step_item("key"),
                "title": self._get_step_item("title"),
                "value": parse_date_parts(response["day"], month),
                "style": "birthday_date",
            }
        return None

    def _save_summary_card_response(self, response: Dict[str, Any]) -> None:
        fields = self._step.get("fields", [])
        if not fields:
            fields = [{"key": key, "label": key} for key in response]

        for field in fields:
            key = field.get("key")
            if key in response:
                label = field.get("label")
                title = ml(label, locale=self.locale) if label else key
                self._upsert_response({
                    "key": key,
                    "title": title or key,
                    "value": response.get(key),
                    "style": None,
                })

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

    def filter_steps(self, steps):
        """Filter steps list to only include selected steps and their dependents."""
        if isinstance(steps, str):
            steps = [steps]
        all_steps = set(steps) | self._get_dependent_steps(steps)
        self.state.steps_list = [s for s in self.state.steps_list if s["key"] in all_steps]

    def set_composition_index(self, index: int):
        self.composition_index = int(index)

    def _set_titles_and_descriptions(self, steps: List[Dict[str, str]]):
        self.title_and_desc = {}
        self._collect_titles_and_descriptions(steps)
        return self.title_and_desc

    def _collect_titles_and_descriptions(self, steps: List[Dict[str, str]]):
        for step in steps:
            if step["action"] == constants.COMPOSITION_ACTION_KEY:
                self._collect_titles_and_descriptions(step.get("steps", []))
                continue
            if step["action"] in constants.NO_ACTION_LIST:
                continue
            self.title_and_desc[step["title"][self.locale]] = step["description"][self.locale]

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
        validation_context_keys = self._get_step_item("validation_context_keys")
        if validation_context_keys:
            validation_context = {"responses": self.responses}
        else:
            validation_context = self.get_possible_values()
        self.view = CustomModal(self._step, self._callback, self.locale, validation_context)
        if not self.state.fill_modal(self.view) and self.cogs:
            self.parse_cogs_to_modal()

        await interaction.response.send_modal(self.view)

    def parse_cogs_to_modal(self) -> None:
        if isinstance(self.cogs, list):
            return

        fields = self._step.get("fields", [])
        has_field_keys = any(f.get("key") for f in fields) if fields else False

        if has_field_keys:
            if self._step.get("key") == "date" and "date" in self.cogs:
                value = self.extract_value_from_cogs(self.cogs["date"])
                if isinstance(value, str) and "-" in value:
                    _, day = value.split("-", 1)
                    if len(self.view.children) >= 1:
                        self.view.children[0].default = str(int(day))
                return

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
            options=self._get_options(),
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", False),
            styled_values=self._get_step_item("styled_values", False),
            auto_confirm=self._get_step_item("auto_confirm", False),
        )
        await self._send_view(interaction)

    def _get_options(self):
        options = self._get_step_item("options")
        if not isinstance(options, list):
            return options
        if not options or not isinstance(options[0], dict) or "label" not in options[0]:
            return options
        return {
            (option["label"].get(self.locale) or option["label"].get("en-us")): option.get("value")
            for option in options
        }

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

    async def show_user_select(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = UserSelectView(
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", False),
            unique=self._get_step_item("unique", True),
        )
        await self._send_view(interaction)

    async def show_month_select(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = MonthSelectView(
            callback=self._callback,
            locale=self.locale,
            required=self._get_step_item("required", True),
        )
        await self._send_view(interaction)

    async def show_summary_card(self, interaction: discord.Interaction):
        from app.views.birthday_summary_card import BirthdaySummaryCardView

        await interaction.response.defer()

        user_id = next((r.get("_raw_value", r["value"]) for r in self.responses if r["key"] == "user"), None)
        mm_dd = next((r.get("_raw_value", r["value"]) for r in self.responses if r["key"] == "date"), None)

        member = interaction.guild.get_member(int(user_id)) if user_id and user_id.isdigit() else None
        member_name = member.display_name if member else (user_id or "")

        prior_state = self._get_summary_card_prior_state()

        self.view = BirthdaySummaryCardView(
            callback=self._callback,
            locale=self.locale,
            member_name=member_name,
            member_id=user_id,
            guild_name=interaction.guild.name if interaction.guild else "",
            mm_dd=mm_dd,
            prior_state=prior_state,
            back_callback=self._go_back if self.state.can_go_back else None,
        )
        await self._send_layout_view(interaction)

    def _get_summary_card_prior_state(self):
        """Extract summary card state from current responses (when editing existing item)."""
        keys = ["use_custom_message", "custom_message_title", "custom_message_content",
                "use_custom_image", "custom_image"]
        state = {}
        for key in keys:
            value = next((r.get("value") for r in self.responses if r["key"] == key), None)
            if isinstance(value, dict):
                value = value.get("value")
            if value is None and isinstance(self.cogs, dict):
                cog_entry = self.cogs.get(key)
                if isinstance(cog_entry, dict):
                    value = cog_entry.get("value")
                else:
                    value = cog_entry
            state[key] = value

        if not any(state.values()):
            return None

        if state.get("use_custom_message") not in ("default", "custom"):
            state["use_custom_message"] = "custom" if state.get("custom_message_title") else "default"
        if state.get("use_custom_image") not in ("default", "custom"):
            state["use_custom_image"] = "custom" if state.get("custom_image") else "default"

        return state

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
            composition_response = self._get_composition_response()
            if composition_response is not None:
                if len(composition_response['value']) < commandconstants.COMPOSITION_MAX_LENGTH[self.command_key]:
                    self.add_item(AddItemButton(self.add_item_callback, locale=self.locale))
                if len(composition_response['value']) > 1:
                    self.add_item(RemoveItemButton(self.remove_item_callback, locale=self.locale))

        if self._get_step_item("preview"):
            self.add_item(PreviewButton(custom_callback=send_welcome_message_preview, locale=self.locale, command_key=self.command_key))

        self.add_item(ConfirmButton(callback=self._finish, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))

        await self._transition_from_layout_view(interaction, embed, self)

    async def add_item_callback(self, interaction: discord.Interaction):
        composition_response = self._get_composition_response()
        new_items = self.form_view.responses[0]['value']
        unique_by = self._get_composition_step_item("unique_by")
        if unique_by:
            for item in new_items:
                merge_composition_item_by_nested_value(composition_response['value'], item, unique_by)
        else:
            composition_response['value'].extend(new_items)
        await self.show_resume(interaction)

    async def remove_item_callback(self, interaction: discord.Interaction, item_removed: Dict[str, Any], new_cogs: Dict[str, Any]):
        composition_key = commandconstants.COMMAND_KEY_TO_COMPOSITION_KEY[self.command_key]
        composition_response = self._get_composition_response()
        composition_response['value'] = new_cogs[composition_key]["values"]
        await self.show_resume(interaction)

    def _get_composition_response(self) -> Optional[Dict[str, Any]]:
        composition_key = commandconstants.COMMAND_KEY_TO_COMPOSITION_KEY.get(self.command_key)
        if not composition_key:
            return None
        return next((r for r in self.responses if r["key"] == composition_key), None)

    def _get_composition_step_item(self, key: str, default_value: Any = None) -> Any:
        composition_key = commandconstants.COMMAND_KEY_TO_COMPOSITION_KEY.get(self.command_key)
        step = next(
            (
                item for item in self.state.steps_list
                if item.get("action") == constants.COMPOSITION_ACTION_KEY
                and item.get("key") == composition_key
            ),
            None,
        )
        return step.get(key, default_value) if step else default_value

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

        if self.persistence_callback:
            result = self.persistence_callback(interaction, self.responses, cog_param)
            if inspect.isawaitable(result):
                await result
        else:
            update_moderations_by_guild(
                guild_id=interaction.guild_id, key=self.command_key, value=True
            )
            insert_cog_by_guild(
                guild_id=interaction.guild_id, cog=self.command_key, data=cog_param
            )

        self.clear_items()

        try:
            await interaction.followup.edit_message(interaction.message.id, view=None)
        except Exception as e:
            logger.warn(
                f"Failed to clear command form message: {type(e).__name__}: {e}",
                log_type=logconstants.COMMAND_WARN_TYPE,
                interaction=interaction,
            )

        if interaction.message.embeds:
            embed = interaction.message.embeds[0]
        else:
            from app.constants import Style as style_constants
            embed = discord.Embed(color=int(style_constants.BACKGROUND_COLOR, base=16))
            footer_text = ml("commands.commands.commons.embed.footer", locale=self.locale)
            embed.set_footer(text=f"• {footer_text}")
        embed.title = ml("commands.command-events.enabled.title", locale=self.locale)
        event_date = interaction.message.edited_at or interaction.message.created_at or discord.utils.utcnow()
        embed.description = parse_command_event_description(
            ml("commands.command-events.enabled.description", locale=self.locale),
            event_date,
            interaction,
            self.command_key,
        )

        insert_cog_event(
            str(interaction.guild_id),
            self.command_key,
            commandconstants.ENABLED_KEY,
            event_date,
            str(interaction.user.id),
        )

        logger.info(
            f"Command enabled: **{self.command_key}**",
            log_type=logconstants.BOT_ACTION_TYPE,
            guild_id=str(interaction.guild.id),
            interaction=interaction,
        )

        await interaction.followup.send(embed=embed, view=self, ephemeral=True)

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
        if self._step["key"] == "month" and "date" in self.cogs:
            value = self.extract_value_from_cogs(self.cogs["date"])
            if isinstance(value, str) and "-" in value:
                month = value.split("-", 1)[0]
                self.view.response[month] = month
                if hasattr(self.view, "month_select"):
                    for option in self.view.month_select.options:
                        option.default = option.value == month
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

        if hasattr(self.view, 'user_select') and value:
            values_list = value if isinstance(value, list) else [value]
            self.view.user_select.default_values = [
                SelectDefaultValue(id=int(v), type=SelectDefaultValueType.user)
                for v in values_list if str(v).isdigit()
            ]

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
            if getattr(self.view, "styled_values", False):
                if isinstance(value, list):
                    values = [str(v) for v in value]
                else:
                    values = [str(value)]
                return item.custom_id in values
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
            constants.USER_SELECT_ACTION_KEY: self.show_user_select,
            constants.MONTH_SELECT_ACTION_KEY: self.show_month_select,
            constants.SUMMARY_CARD_ACTION_KEY: self.show_summary_card,
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
            UserSelectView: ('user_select', self._parse_cogs_to_select),
            MonthSelectView: ('select', self._parse_cogs_to_select),
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
        previous_is_layout = (
            self._using_layout_view
            or bool(interaction.message and interaction.message.flags.components_v2)
        )
        if previous_is_layout:
            self._using_layout_view = False
            if not deferred:
                await interaction.response.defer()
            await interaction.followup.delete_message(interaction.message.id)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
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
        from app.views.birthday_summary_card import BirthdaySummaryCardView

        view_config = {
            DesignSelectView: ('design_select', self._parse_cogs_to_design_select),
            BirthdaySummaryCardView: (None, None),
        }

        config = view_config.get(type(self.view))
        if config:
            fill_type, cogs_fallback = config
            if fill_type:
                fill_fn = getattr(self.state, f'fill_{fill_type}', None)
                if fill_fn and not fill_fn(self.view) and self.cogs and cogs_fallback:
                    cogs_fallback()

        await interaction.followup.delete_message(interaction.message.id)
        await interaction.followup.send(view=self.view, ephemeral=True)
        self._using_layout_view = True

    @_update_form_step
    async def _callback(self, interaction: discord.Interaction) -> None:
        self._start_preview_pregeneration(interaction)

        action = self._get_step_item("action")
        if action not in (constants.MODAL_ACTION_KEY, constants.FILE_UPLOAD_ACTION_KEY):
            self.clear_items()

        return await self.get_action_by_type(action, interaction)
