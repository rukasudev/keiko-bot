from typing import Any, Callable, Dict, List

import discord

from app import logger
from app.components.embed import parse_form_dict_to_embed
from app.components.select import Select
from app.components.select_views import UserSelectView
from app.constants import LogTypes as logconstants
from app.constants import FormConstants as constants
from app.services.utils import ml, parse_form_steps_titles, parse_form_yaml_to_dict


class RemoveItem(discord.ui.View):
    def __init__(self, command_key: str, cogs: Dict[str, Any], locale: str, callback: Callable):
        super().__init__(timeout=1800)
        self.command_key = command_key
        self.locale = locale
        self.after_callback = callback
        self.cogs = cogs
        self.composition = False
        self.composition_picker_step = None
        self.composition_picker_view = None
        self.initialize_select_component()

    def initialize_select_component(self):
        placeholder_text = ml("commands.command-events.removed.placeholder", locale=self.locale)
        options = self.get_command_options()
        self.add_item(Select(placeholder_text, options, unique=self.composition))

    def get_command_options(self) -> Dict[str, str]:
        form_steps = parse_form_yaml_to_dict(self.command_key)
        self.max_length = self.parse_composition(form_steps)

        steps_titles = parse_form_steps_titles(form_steps, self.locale)
        return self.generate_options(steps_titles, self.max_length)

    def parse_composition(self, form_steps: List[Dict[str, Any]]) -> int:
        for item in form_steps:
            if not item.get("action") == constants.COMPOSITION_ACTION_KEY:
                continue

            self.composition = True
            self.composition_key = item["key"]
            self.composition_unique_by = item.get("unique_by")
            self.composition_picker_step = self._find_composition_step(item, self.composition_unique_by)
            return len(self.cogs.get(item["key"])["values"])

    def _find_composition_step(self, composition: Dict[str, Any], key: str) -> Dict[str, Any]:
        if not key:
            return None
        return next((step for step in composition.get("steps", []) if step.get("key") == key), None)

    def generate_options(self, steps_titles: Dict[str, str], max_length: int):
        if not self.composition:
            return steps_titles

        title = steps_titles.get(self.composition_key, self.composition_key)
        if self._uses_member_picker():
            return {self.composition_key: title}
        return {f"{self.composition_key}${i}": f"{title} #{i + 1}" for i in range(max_length)}

    def _uses_member_picker(self) -> bool:
        return bool(
            self.composition
            and self.composition_unique_by
            and self.composition_picker_step
            and self.composition_picker_step.get("action") == constants.USER_SELECT_ACTION_KEY
        )

    async def callback(self, interaction: discord.Interaction):
        selected = self.selected_options[0]
        if self._uses_member_picker() and selected == self.composition_key:
            await self.show_composition_member_picker(interaction)
            return
        await self.remove_selected_item(interaction, selected)

    async def show_composition_member_picker(self, interaction: discord.Interaction):
        self.composition_picker_view = UserSelectView(
            callback=self.handle_composition_member_selection,
            locale=self.locale,
            required=True,
            unique=True,
        )
        embed = parse_form_dict_to_embed(self.composition_picker_step, self.locale)
        embed.title = ml("commands.command-events.removed.member-picker.title", locale=self.locale)
        embed.description = ml("commands.command-events.removed.member-picker.description", locale=self.locale)
        await interaction.response.edit_message(embed=embed, view=self.composition_picker_view)

    async def handle_composition_member_selection(self, interaction: discord.Interaction):
        selected_user = self.composition_picker_view.get_response() if self.composition_picker_view else None
        items = (self.cogs.get(self.composition_key) or {}).get("values") or []
        index = next(
            (
                i for i, item in enumerate(items)
                if self._composition_item_value(item, self.composition_unique_by) == str(selected_user)
            ),
            None,
        )
        if index is None:
            message = ml("commands.command-events.removed.member-picker.not-found", locale=self.locale)
            if not message or message == "commands.command-events.removed.member-picker.not-found":
                message = "This member does not have an item configured."
            await interaction.response.send_message(message, ephemeral=True)
            return
        await self.remove_selected_item(interaction, f"{self.composition_key}${index}")

    def _composition_item_value(self, item: Dict[str, Any], key: str) -> Any:
        field = item.get(key)
        if isinstance(field, dict):
            return field.get("value") or field.get("values")
        return field

    async def remove_selected_item(self, interaction: discord.Interaction, selected_option: str):
        option, index = selected_option.split("$")
        item = self.cogs[option]["values"][int(index)]
        del self.cogs[option]["values"][int(index)]
        await self.after_callback(interaction, item, self.cogs)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"RemoveItem error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )
