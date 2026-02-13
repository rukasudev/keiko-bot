from typing import Any, Callable, Dict, List

import discord

from app.components.select import Select
from app.constants import FormConstants as constants
from app.services.utils import ml, parse_form_steps_titles, parse_form_yaml_to_dict
from app.views.form import Form


class EditCommand(discord.ui.View):
    def __init__(self, command_key: str, cogs: Dict[str, Any], locale: str, callback: Callable):
        super().__init__(timeout=1800)
        self.command_key = command_key
        self.locale = locale
        self.after_callback = callback
        self.form_view = Form(command_key, locale, cogs=cogs)
        self.composition = False
        self.initialize_select_component()

    def initialize_select_component(self):
        placeholder_text = ml("commands.command-events.edited.placeholder", locale=self.locale)
        options = self.get_command_options()
        self.add_item(Select(placeholder_text, options, unique=self.composition))

    def get_command_options(self) -> Dict[str, str]:
        form_steps = parse_form_yaml_to_dict(self.command_key)
        self.max_length = self.parse_composition(form_steps)

        steps_titles = parse_form_steps_titles(form_steps, self.locale)
        steps_titles = self.filter_conditional_steps(form_steps, steps_titles)

        return self.generate_options(steps_titles, self.max_length)

    def filter_conditional_steps(self, form_steps: list, steps_titles: dict) -> dict:
        """Remove steps from edit options if their condition is not met."""
        filtered = steps_titles.copy()

        for step in form_steps:
            condition = step.get("condition")
            if not condition:
                continue

            key = condition.get("key")
            not_in = condition.get("not_in", [])

            current_value = self.form_view.cogs.get(key) if self.form_view.cogs else None

            if current_value in not_in:
                step_key = step.get("key")
                if step_key in filtered:
                    del filtered[step_key]

        return filtered

    def parse_composition(self, form_steps: List[Dict[str, Any]]) -> int:
        for item in form_steps:
            if not item.get("action") == constants.COMPOSITION_ACTION_KEY:
                continue

            self.composition = True
            return len(self.form_view.cogs.get(item["key"])["values"])

    def generate_options(self, steps_titles: Dict[str, str], max_length: int):
        if self.composition:
            return {f"{step}${i}": f"{title} #{i + 1}" for i in range(max_length) for step, title in steps_titles.items()}
        return steps_titles

    async def callback(self, interaction: discord.Interaction):
        if self.composition:
            await self.handle_composition_callback(interaction, self.selected_options[0])
        else:
            self.form_view.filter_steps(self.selected_options)
            await self.execute_form_view_callback(interaction)

    async def handle_composition_callback(self, interaction: discord.Interaction, selected_option: List[str]):
        option, index = selected_option.split("$")
        self.form_view.filter_steps([option])
        self.form_view.set_composition_index(index)
        await self.execute_form_view_callback(interaction)

    async def execute_form_view_callback(self, interaction: discord.Interaction):
        self.form_view._set_after_callback(self.after_callback)
        await self.form_view._callback(interaction)
