from typing import Any, Callable, Dict, List

import discord

from app.components.buttons import GenericButton
from app.components.embed import parse_form_dict_to_embed
from app.constants import Commands as constants
from app.services.utils import ml


class FormComposition(discord.ui.View):
    def __init__(self, composition: Dict[str, str], parent_callback: Callable, locale: str, cogs: Dict[str, Any] = None, index: int = None) -> None:
        super().__init__(timeout=1800)
        self.composition = composition
        self.parent_callback = parent_callback
        self.max_length = constants.COMPOSITION_MAX_LENGTH.get(composition.get("parent_key"), 0)
        self.cogs = cogs
        self.locale = locale
        self.index = index
        self.responses = []

    def get_response(self):
        return self.responses

    def save_response(self) -> None:
        if not self.cogs or self.index is None:
            return self.responses.append(self.transform_response(self.form_view.responses))

        response = self.transform_response(self.form_view.responses)
        self.responses = self.cogs[self.composition.get("key")]["values"]
        self.responses[self.index] = response

    def transform_response(self, response: List[Dict[str, str]]) -> Dict[str, str]:
        result = {}

        for item in response:
            key = item.get('key')
            value = {'value': item.get('value')}
            value['title'] = item.get('title')

            if item.get('style'):
                value['style'] = item.get('style')

            result[key] = value

        return result

    async def interate(self, interaction: discord.Interaction) -> None:
        if self.max_length == 0 or (self.cogs and hasattr(self, "form_view")):
            return await self.finish(interaction)

        if self.max_length == constants.COMPOSITION_MAX_LENGTH.get(self.composition.get("parent_key"), 0):
            self.max_length -= 1
            return await self.send_form(interaction)

        self.max_length -= 1
        self.clear_items()

        embed = parse_form_dict_to_embed(self.composition, self.locale)

        self.add_item(GenericButton(ml("buttons.yes.label", self.locale), self.send_form, style=discord.ButtonStyle.success))
        self.add_item(GenericButton(ml("buttons.no.label", self.locale), self.finish, style=discord.ButtonStyle.danger))

        await interaction.response.edit_message(embed=embed, view=self)

    async def finish(self, interaction: discord.Interaction) -> None:
        self.save_response()
        await self.parent_callback(interaction)

    async def send_form(self, interaction: discord.Interaction) -> None:
        from app.views.form import Form

        if hasattr(self, "form_view"):
            self.save_response()

        cogs = None

        if self.cogs and self.index is not None:
            cogs = self.cogs[self.composition.get("key")]["values"][self.index]

        self.form_view = Form("", self.locale, self.composition.get("steps", {}), cogs=cogs)
        self.form_view._set_after_callback(self.interate)

        await self.form_view._callback(interaction)
