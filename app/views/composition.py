from typing import Any, Callable, Dict, List

import discord


class FormComposition(discord.ui.View):
    def __init__(self, composition: Dict[str, str], parent_callback: Callable, locale: str, cogs: List[Dict[str, Any]] = None, index: int = None) -> None:
        super().__init__(timeout=1800)
        self.composition = composition
        self.parent_callback = parent_callback
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
        if self.index is not None and len(self.responses) > self.index:
            self.responses[self.index] = response
        else:
            self.responses.append(response)

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

    async def finish(self, interaction: discord.Interaction) -> None:
        self.save_response()
        await self.parent_callback(interaction)

    async def send_form(self, interaction: discord.Interaction) -> None:
        from app.views.form import Form

        if hasattr(self, "form_view"):
            self.save_response()

        cogs = None
        current_cog = None

        if self.cogs:
            cogs = self.cogs[self.composition.get("key")]["values"].copy()

            if self.index is not None:
                current_cog = cogs[self.index]
                del cogs[self.index]

        self.form_view = Form("", self.locale, self.composition.get("steps", {}), cogs=current_cog or cogs)
        self.form_view.all_cogs = cogs
        self.form_view.composition_responses = self.responses

        self.form_view._set_after_callback(self.finish)

        await self.form_view._callback(interaction)
