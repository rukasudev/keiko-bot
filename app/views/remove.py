from typing import Any, Callable, Dict, List

import discord

from app import logger
from app.components.select import Select
from app.constants import LogTypes as logconstants
from app.constants import FormConstants as constants
from app.services.utils import ml, parse_form_steps_titles, parse_form_yaml_to_dict


class RemoveItem(discord.ui.View):
    def __init__(self, command_key: str, cogs: List[Dict[str, Any]], locale: str, callback: Callable):
        super().__init__(timeout=1800)
        self.command_key = command_key
        self.locale = locale
        self.after_callback = callback
        self.cogs = cogs
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
            return len(self.cogs.get(item["key"])["values"])

    def generate_options(self, steps_titles: Dict[str, str], max_length: int):
        if self.composition:
            return {f"{step}${i}": f"{title} #{i + 1}" for i in range(max_length) for step, title in steps_titles.items()}
        return steps_titles

    async def callback(self, interaction: discord.Interaction):
        option, index = self.selected_options[0].split("$")
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
