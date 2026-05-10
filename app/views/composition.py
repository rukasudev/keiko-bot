from typing import Any, Callable, Dict, List

import discord

from app import logger
from app.constants import LogTypes as logconstants
from app.services.compositions import merge_composition_item_by_nested_value


class FormComposition(discord.ui.View):
    def __init__(
        self,
        composition: Dict[str, str],
        parent_callback: Callable,
        locale: str,
        cogs: List[Dict[str, Any]] = None,
        index: int = None,
        prefilled_fields: Dict[str, Any] = None,
    ) -> None:
        super().__init__(timeout=1800)
        self.composition = composition
        self.parent_callback = parent_callback
        self.cogs = cogs
        self.locale = locale
        self.index = index
        self.prefilled_fields = prefilled_fields or {}
        self.responses = []

    def get_response(self):
        return self.responses

    def save_response(self) -> None:
        response = self.transform_response(self.form_view.responses)
        unique_by = self.composition.get("unique_by")
        if unique_by:
            return self._save_unique_response(response, unique_by)

        if not self.cogs or self.index is None:
            return self.responses.append(response)

        self.responses = self.cogs[self.composition.get("key")]["values"]
        if self.index is not None and len(self.responses) > self.index:
            self.responses[self.index] = response
        else:
            self.responses.append(response)

    def _save_unique_response(self, response: Dict[str, Any], unique_by: str) -> None:
        if self.cogs and self.index is not None:
            self.responses = self.cogs[self.composition.get("key")]["values"]

        merge_composition_item_by_nested_value(
            self.responses,
            response,
            unique_by,
            int(self.index) if self.index is not None else None,
        )

    def transform_response(self, response: List[Dict[str, str]]) -> Dict[str, str]:
        result = {}

        for item in response:
            key = item.get('key')
            value = {'value': item.get('value')}
            value['title'] = item.get('title')

            if item.get('style'):
                value['style'] = item.get('style')
            if item.get("hidden"):
                value["hidden"] = True

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
        self._apply_prefilled_fields(self.form_view)

        self.form_view._set_after_callback(self.finish)

        await self.form_view._callback(interaction)

    def _apply_prefilled_fields(self, form_view) -> None:
        if not self.prefilled_fields:
            return

        form_view.prefilled_step_keys = set()
        steps_by_key = {
            step.get("key"): step
            for step in self.composition.get("steps", [])
            if step.get("key")
        }

        for key, raw in self.prefilled_fields.items():
            step = steps_by_key.get(key, {})
            if isinstance(raw, dict):
                value = raw.get("value") or raw.get("values")
                title = raw.get("title")
                style = raw.get("style")
                hidden = raw.get("hidden")
            else:
                value = raw
                title = None
                style = None
                hidden = None

            step_title = step.get("title")
            if not title and isinstance(step_title, dict):
                title = step_title.get(self.locale) or step_title.get("en-us")

            response = {
                "key": key,
                "title": title or key,
                "value": value,
                "style": style or step.get("style"),
            }
            if hidden:
                response["hidden"] = True

            form_view._upsert_response(response)
            form_view.prefilled_step_keys.add(key)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        logger.error(
            f"FormComposition error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )
