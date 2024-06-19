from typing import Callable

import discord

from app.components.select import Select
from app.services.utils import ml, parse_form_steps_titles, parse_form_yaml_to_dict
from app.views.form import Form


class EditCommand(discord.ui.View):
    def __init__(self, command_key: str, locale: str, callback: Callable):
        self.command_key = command_key
        self.locale = locale
        self.after_callback = callback
        self.form_view = Form(self.command_key, self.locale)
        super().__init__()
        self.add_item(
            Select(
                ml("commands.command-events.edited.placeholder", locale=self.locale),
                self.get_command_options(),
            )
        )

    def get_command_options(self):
        form_steps = parse_form_yaml_to_dict(self.command_key)
        return parse_form_steps_titles(form_steps, self.locale)

    async def callback(self, interaction: discord.Interaction):
        self.form_view.filter_steps(self.selected_options)
        self.form_view._set_after_callback(self.after_callback)
        await self.form_view._callback(interaction)
