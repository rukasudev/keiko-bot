from typing import Any, Callable, Dict, List, Union

import discord

from app import logger
from app.constants import LogTypes as logconstants


class CustomModal(discord.ui.Modal):
    def __init__(self, config: dict, callback: Callable, locale: str, cogs: Dict[str, Any]) -> None:
        self.config = config
        self.callback = callback
        self.lowercase = config.get("lowercase", False)
        self.locale = locale
        self.validation = config.get("validation", None)
        self.modal_validation = ModalValidations(cogs=cogs)
        self.field_keys = []
        super().__init__(title=config.get("title").get(locale), timeout=300)
        self.add_inputs(config, locale)

    def add_inputs(self, config: Dict[str, Any], locale: str) -> None:
        if config.get("multiline", False):
            style = discord.TextStyle.long
        else:
            style = discord.TextStyle.short

        if not config.get("fields"):
            default = self.get_config_by_locale(config, "default")
            item = discord.ui.TextInput(
                label=self.get_config_by_locale(config, "label"),
                style=style,
                required=config.get("required", True),
                default=default,
                max_length=config.get("max_length", 40),
                placeholder=self.get_config_by_locale(config, "placeholder") or default,
            )
            return self.add_item(item)

        self.add_fields(config, style)

    def add_fields(self, config: Dict[str, Any], style: Any) -> None:
        fields = config.get("fields")

        label_counts = {}
        if config.get("enumerate", False):
            for field in fields:
                label = self.get_config_by_locale(field, "label")
                label_counts[label] = label_counts.get(label, 0) + 1

        label_indices = {}

        for field in fields:
            default = self.get_config_by_locale(field, "default")

            if field.get("key"):
                self.field_keys.append(field.get("key"))

            label = self.get_config_by_locale(field, "label")

            if config.get("enumerate", False) and label_counts.get(label, 0) > 1:
                label_indices[label] = label_indices.get(label, 0) + 1
                label_text = f"{label} #{label_indices[label]}"
            else:
                label_text = label

            description = self.get_config_by_locale(field, "description")
            placeholder = self.get_config_by_locale(field, "placeholder") or default

            if description and not placeholder:
                placeholder = description

            item = discord.ui.TextInput(
                label=label_text,
                style=style,
                placeholder=placeholder,
                required=field.get("required", True),
                default=default,
                max_length=field.get("max_length") or config.get("max_length", 40),
            )
            self.add_item(item)

    def get_config_by_locale(self, config_dict: Dict[str, Any], key: str) -> Any:
        config = config_dict.get(key, "")
        return config.get(self.locale) if config else ""

    def get_response(self):
        if not hasattr(self, "response"):
            return None
        return self.response

    def _get_text_inputs(self) -> List[discord.ui.TextInput]:
        return [child for child in self.children if isinstance(child, discord.ui.TextInput)]

    def parse_response(self) -> Union[str, Dict[str, str]]:
        text_inputs = self._get_text_inputs()
        fields = self.config.get("fields", [])

        keyed_response = {}
        concat_values = []

        for i, text_input in enumerate(text_inputs):
            field = fields[i] if i < len(fields) else {}
            value = text_input.value if not self.lowercase else text_input.value.lower()

            if field.get("key"):
                keyed_response[field["key"]] = value
            elif value:
                concat_values.append(value)

        if keyed_response:
            if concat_values:
                keyed_response["__concat__"] = ";".join(concat_values)
            self.response = keyed_response
        else:
            self.response = ";".join(concat_values) if concat_values else (text_inputs[0].value if text_inputs else "")

        return self.response

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from app.components.embed import response_error_embed

        self.parse_response()

        if self.validation:
            validation = self.modal_validation.validate(self.validation, responses=self.response)
            if not validation["ok"]:
                self.response = None
                embed = response_error_embed(validation["error_key"], self.locale)
                return await interaction.response.send_message(embed=embed, ephemeral=True, delete_after=10)

        await self.callback(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(
            f"Modal error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


class ConfirmationModal(discord.ui.Modal):
    def __init__(self, action: str, locale: str, callback: Callable) -> None:
        from app.services.utils import parse_confirmation_desc, parse_confirmation_title

        self.action = action
        self.callback = callback
        super().__init__(title=parse_confirmation_title(action, locale))
        self.add_item(
            discord.ui.TextInput(
                style=discord.TextStyle.short,
                label=parse_confirmation_desc(action, locale),
                required=True,
                max_length=len(action),
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if str(self.children[0].value).lower() == self.action.lower():
            return await self.callback(interaction)

        await interaction.response.defer()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(
            f"ConfirmationModal error: {type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            exc_info=True,
        )


class ModalValidations:
    def __init__(self, cogs: List[Dict[str, Any]]) -> None:
        self.cogs = cogs

    def validate(self, validation_func: str, responses: str) -> bool:
        try:
            return getattr(self, validation_func)(responses)
        except AttributeError:
            return False

    def validate_streamer_name(self, response: str) -> bool:
        from app import bot

        for item in self.cogs:
            if response.lower() == item.get("streamer").get("value").lower():
                return {"ok": False, "error_key": "streamer-already-registered"}

        ok = bot.twitch.get_user_id_from_login(response) is not None
        return {"ok": ok, "error_key": "streamer-not-found"}

    def validate_youtube_channel(self, response: str) -> bool:
        from app import bot

        for item in self.cogs:
            if response.lower() == item.get("youtuber").get("value").lower():
                return {"ok": False, "error_key": "youtuber-already-registered"}

        ok = bot.youtube.get_channel_id_from_username(response) is not None
        return {"ok": ok, "error_key": "youtuber-not-found"}
