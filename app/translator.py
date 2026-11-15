"""The slash translator: every slash name and description read from copy, and the
copy block it reads."""

from typing import Any

import discord
from discord import Locale, app_commands
from discord.app_commands import TranslationContext
from pydantic import Field

from app.constants import supported_locales
from app.core.commands import Open
from app.core.i18n import CopyBlock, CopyText
from app.core.utils import ml


class SlashParam(CopyBlock):
    """One parameter of a command's slash commands, shared by every command of the
    folder that takes it."""

    name: CopyText = Field(
        description="The parameter's name Discord shows: lowercase, no spaces.",
        examples=[{"en-us": "member", "pt-br": "membro"}],
        json_schema_extra={"role": "copy"},
    )
    description: CopyText = Field(
        description="The parameter's caption Discord shows.",
        json_schema_extra={"role": "copy"},
    )


class SlashCommand(CopyBlock):
    """One more app command of the folder, besides the one `slash` names."""

    name: CopyText = Field(
        description="The command's name Discord shows: lowercase, no spaces, or the "
        "label of a context menu.",
        examples=[{"en-us": "add", "pt-br": "adicionar"}],
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The command's caption, in the third person; a context menu has "
        "none.",
        json_schema_extra={"role": "copy"},
    )


class Slash(CopyBlock):
    """The names and captions of a command's app commands, read by the `Translator`
    through `locale_str(namespace="<key>.slash")`."""

    subgroup: CopyText | None = Field(
        None,
        description="The subgroup under the parent group, written when the command "
        "declares a `group`; /setup quotes it on the row.",
        examples=[{"en-us": "birthdays", "pt-br": "aniversarios"}],
        json_schema_extra={"role": "copy"},
    )
    name: CopyText = Field(
        description="The slash command's name, or the label of a context menu.",
        json_schema_extra={"role": "copy"},
    )
    description: CopyText | None = Field(
        None,
        description="The slash command's caption, in the third person; a context menu "
        "has none.",
        json_schema_extra={"role": "copy"},
    )
    params: Open[SlashParam] = Field(
        default_factory=dict,
        description="The parameters, keyed by the parameter name in Python; read "
        "under `<key>.slash` with the type `params.<name>.name`.",
        json_schema_extra={"role": "structure"},
    )
    commands: Open[SlashCommand] = Field(
        default_factory=dict,
        description="The folder's other app commands, keyed by what each does; read "
        "with the namespace `<key>.slash.commands.<name>`.",
        json_schema_extra={"role": "structure"},
    )


class Translator(app_commands.Translator):
    def __init__(self, bot) -> None:
        self.bot = bot
        super().__init__()

    async def load(self) -> None:
        return await super().load()

    async def get_translated_qualified_name(
        self, command: discord.app_commands.commands.Command, locale: str
    ) -> str:
        name = await self.translate(command._locale_name, locale, None)

        if not hasattr(command, "parent") or command.parent is None:
            return name

        parent_name = await self.translate(command.parent._locale_name, locale, None)

        names = [name, parent_name]

        if command.parent.parent is not None:
            grandparent_name = await self.translate(
                command.parent.parent._locale_name, locale, None
            )
            names.append(grandparent_name)

        return " ".join(reversed(names))

    async def translate(self, string: app_commands.locale_str, locale: Locale, context: TranslationContext) -> str:
        """The copy of a command string: `namespace` is the block its `type` is read under."""
        command = str(string)

        tp = string.extras.get("type")
        ns = string.extras.get("namespace")

        if locale.value not in supported_locales:
            if tp == "description":
                return context.data.extras.get(Locale.american_english.value).get("locale_qualified_desc", "")
            return command

        if tp == "groups":
            return ml(f"messages.groups.{command}", locale=locale)

        if tp == "context-menu":
            return ml(f"{ns}.name", locale=locale)

        translated = ml(f"{ns}.{tp}", locale=locale) if tp else command

        if tp == "name" and context:
            translated = await self.process_name_translation(tp, context, locale, command, ns)

        return translated

    async def process_name_translation(self, tp, context, locale, command, ns):
        locale_qualified_name = await self.get_translated_qualified_name(context.data, locale)
        locale_qualified_desc = await self.translate(context.data._locale_description, locale, None)

        if not context.data.extras.get(locale.value):
            context.data.extras[locale.value] = {}

        context.data.extras[locale.value]["locale_qualified_name"] = locale_qualified_name
        context.data.extras[locale.value]["locale_qualified_desc"] = locale_qualified_desc

        if not self.bot.all_cogs.get(locale.value):
            self.bot.all_cogs[locale.value] = []

        self.bot.all_cogs[locale.value].append({
            "key": context.data._attr,
            "name": locale_qualified_name,
            "desc": locale_qualified_desc
        })

        return ml(f"{ns}.{tp}", locale=locale)




class locale_str(app_commands.locale_str):
    """A command string that keeps its copy key beside the message Discord shows."""

    def __init__(self, key: str, **kwargs: Any) -> None:
        self.key = key
        super().__init__(key, **kwargs)
