import i18n
from discord import Locale, app_commands
from discord.app_commands import TranslationContext, locale_str

from app.services.utils import parse_locale


class Translator(app_commands.Translator):
    def __init__(self) -> None:
        super().__init__()

    async def load(self) -> None:
        return await super().load()

    async def translate(
        self, string: locale_str, locale: Locale, context: TranslationContext
    ) -> str:
        i18n.set("locale", parse_locale(locale))
        ns = string.extras.get("namespace")

        return i18n.t(f"{ns}.{str(string)}") if ns is not None else str(string)
