import discord
from discord import Locale, app_commands
from discord.app_commands import TranslationContext, locale_str

from app.services.utils import ml


class Translator(app_commands.Translator):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.supported_locales = [
            Locale.american_english.value,
            Locale.brazil_portuguese.value,
        ]
        super().__init__()

    async def load(self) -> None:
        return await super().load()

    async def get_translated_qualified_name(
        self, command: discord.app_commands.commands.Command, locale: str
    ) -> str:
        name = await self.translate(command._locale_name, locale, None)

        if command.parent is None:
            return name

        parent_name = await self.translate(command.parent._locale_name, locale, None)

        names = [name, parent_name]

        if command.parent.parent is not None:
            grandparent_name = await self.translate(
                command.parent.parent._locale_name, locale, None
            )
            names.append(grandparent_name)

        return " ".join(reversed(names))

    async def translate(
        self, string: locale_str, locale: Locale, context: TranslationContext
    ) -> str:
        command = str(string)
        base = "commands.commands"

        tp = string.extras.get("type")
        ns = string.extras.get("namespace")

        if locale.value not in self.supported_locales:
            if tp == "desc":
                desc = context.data.extras.get(Locale.american_english.value).get(
                    "locale_qualified_desc"
                )
                return desc
            else:
                return str(string)

        # TODO: pass this to constants
        if tp == "groups":
            return ml(f"commands.{tp}.{command}", locale=locale)

        translated = ml(f"{base}.{ns}.{tp}", locale=locale) if tp else command

        if tp == "name" and context is not None:
            locale_qualified_name = await self.get_translated_qualified_name(
                context.data, locale
            )

            locale_qualified_desc = await self.translate(
                context.data._locale_description, locale, None
            )

            if locale.value not in context.data.extras:
                context.data.extras[locale.value] = {}

            context.data.extras[locale.value] = {
                "locale_qualified_name": locale_qualified_name,
                "locale_qualified_desc": locale_qualified_desc,
            }

            if locale.value not in self.bot.all_commands:
                self.bot.all_commands[locale.value] = []

            self.bot.all_commands[locale.value].append(
                {
                    "key": context.data._attr,
                    "name": locale_qualified_name,
                    "desc": locale_qualified_desc,
                }
            )

        return translated


class locale_str(app_commands.locale_str):
    def __init__(self, key: str, **kwargs) -> None:
        self.key = key
        super().__init__(key, **kwargs)
