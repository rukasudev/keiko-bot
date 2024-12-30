from typing import Any, Callable, Dict

import discord


class CustomModal(discord.ui.Modal):
    def __init__(self, config: dict, callback: Callable, locale: str) -> None:
        self.callback = callback
        self.lowercase = config.get("lowercase", False)
        self.locale = locale
        self.validation = config.get("validation", None)
        self.modal_validation = ModalValidations()
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
        for i, field in enumerate(config.get("fields")):
            default = self.get_config_by_locale(field, "default")

            if config.get("enumerate", False):
                label = f"{self.get_config_by_locale(field, 'label')} #{i+1}"
            else:
                label = self.get_config_by_locale(field, "label")

            item = discord.ui.TextInput(
                label=label,
                style=style,
                placeholder=self.get_config_by_locale(field, "placeholder") or default,
                required=field.get("required", True),
                default=default,
                max_length=config.get("max_length", 40),
            )
            self.add_item(item)

    def get_config_by_locale(self, config_dict: Dict[str, Any], key: str) -> Any:
        config = config_dict.get(key, "")
        return config.get(self.locale) if config else ""

    def get_response(self):
        if not hasattr(self, "response"):
            return None
        return self.response

    def parse_response(self) -> str:
        self.response = self.children[0].value if not self.lowercase else self.children[0].value.lower()
        for child in self.children[1:]:
            if len(child.value) == 0:
                continue
            if self.response: self.response += ";"
            self.response += child.value if not self.lowercase else child.value.lower()
        return self.response

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from app.components.embed import response_error_embed

        self.parse_response()

        if self.validation:
            validation = self.modal_validation.validate(self.validation, responses=self.response)
            if not validation["ok"]:
                self.response = None
                embed = response_error_embed(validation["error_key"], self.locale)
                return await interaction.response.send_message(embed=embed, ephemeral=True)

        await self.callback(interaction)


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

class ModalValidations:

    @staticmethod
    def validate(validation_func: str, responses: str) -> bool:
        try:
            return getattr(ModalValidations, validation_func)(responses)
        except AttributeError:
            return False

    @staticmethod
    def validate_streamer_name(response: str) -> bool:
        from app import bot

        ok = bot.twitch.get_user_id_from_login(response) is not None
        return {"ok": ok, "error_key": "streamer-not-found"}
