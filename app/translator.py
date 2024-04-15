from discord import Locale, app_commands
from discord.app_commands import TranslationContext, locale_str

from app.services.utils import ml


class Translator(app_commands.Translator):
    def __init__(self) -> None:
        super().__init__()

    async def load(self) -> None:
        return await super().load()

    async def translate(
        self, string: locale_str, locale: Locale, context: TranslationContext
    ) -> str:
        command = str(string)
        base = "commands.commands"

        tp = string.extras.get("type")
        ns = string.extras.get("namespace")

        # TODO: pass this to constants
        if tp == "groups":
            return ml(f"commands.{tp}.{command}", locale=locale)

        return ml(f"{base}.{ns}.{tp}", locale=locale) if tp else command


class locale_str(app_commands.locale_str):
    def __init__(self, key: str, **kwargs) -> None:
        self.key = key
        super().__init__(key, **kwargs)
