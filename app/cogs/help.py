from typing import Dict, List, Union

import discord
from discord import app_commands
from discord.app_commands import AppCommand
from discord.ext import commands

from app.bot import DiscordBot
from app.services.utils import ml
from app.translator import locale_str
from app.views.pagination import PaginationView


class Help(commands.Cog, name=locale_str("help", type="name", namespace="help")):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.commands_select = []

        self.group_emoji = "🍰"
        self.item_emoji = ":flying_disc:"
        self.base_i18n_path = f"commands.commands.help"

    async def get_translated_command_name(
        self, command: AppCommand, locale: str
    ) -> str:
        return await self.bot.tree.translator.translate(
            command._locale_name, locale, None
        )

    async def get_translated_command_description(
        self, command: AppCommand, locale: str
    ) -> str:
        return await self.bot.tree.translator.translate(
            command._locale_description, locale, None
        )

    async def get_commands_recursively(
        self,
        cog_name: str,
        command: AppCommand,
        command_list: List[str],
        locale: str,
        parent_list: List[str],
    ) -> None:
        translated_command_name = await self.get_translated_command_name(
            command, locale
        )
        translated_command_description = await self.get_translated_command_description(
            command, locale
        )
        parent_list.append(translated_command_name)

        if not hasattr(command, "commands"):
            command_list.append(
                {
                    "name": f"{cog_name} {' '.join(parent_list)}",
                    "description": translated_command_description,
                }
            )
            return True

        for subcommand in command.commands:
            if await self.get_commands_recursively(
                cog_name, subcommand, command_list, locale, parent_list
            ):
                parent_list.pop()

    async def add_commands_to_embed(
        self, cog_name: str, cog_object, commands_list: List[str], locale: str
    ) -> None:
        if not cog_object.app_command:
            return

        for command in cog_object.app_command.commands:
            await self.get_commands_recursively(
                cog_name, command, commands_list, locale, []
            )

    @staticmethod
    async def get_cog_name(
        cog_name: Union[str, locale_str], locale: str, bot: DiscordBot
    ) -> str:
        if isinstance(cog_name, str):
            return cog_name
        return await bot.tree.translator.translate(cog_name, locale, None)

    async def get_commands_list(self, cog_name, cog_object, locale: str) -> List[str]:
        commands_list = []
        await self.add_commands_to_embed(cog_name, cog_object, commands_list, locale)

        return commands_list

    async def populate_data(self, interaction) -> Dict[str, str]:
        base_commands_title = ml(
            f"{self.base_i18n_path}.embed.base-commands", interaction.locale
        )
        data = [{"label": base_commands_title, "item": "", "commands": []}]

        for cog_name, cog_object in self.bot.cogs.items():
            cog_name = await self.get_cog_name(cog_name, interaction.locale, self.bot)
            commands_list = await self.get_commands_list(
                cog_name, cog_object, interaction.locale
            )

            if not commands_list:
                self.commands_select.append({cog_name: cog_name})
                data[0]["item"] += f"\n{self.item_emoji} `/{cog_name}`"
                data[0]["commands"].append(
                    {"name": cog_name, "description": cog_object.description}
                )
                continue

            data.append(
                {
                    "label": f"{cog_name.capitalize()}",
                    "commands": commands_list,
                    "item": "\n".join(
                        [
                            f"{self.item_emoji} `/{command['name']}`"
                            for command in commands_list
                        ]
                    ),
                }
            )

        return data

    @app_commands.command(
        name=locale_str("help", type="name", namespace="help"),
        description=locale_str("help", type="desc", namespace="help"),
    )
    async def help(self, interaction: discord.Interaction) -> None:
        data = await self.populate_data(interaction)

        title = ml(f"{self.base_i18n_path}.embed.title", locale=interaction.locale)
        description = ml(f"{self.base_i18n_path}.embed.desc", locale=interaction.locale)
        footer = ml(f"{self.base_i18n_path}.embed.footer", locale=interaction.locale)
        placeholder = ml(
            f"{self.base_i18n_path}.embed.placeholder", locale=interaction.locale
        )

        pagination_view = PaginationView(interaction, title, description, footer, data)
        pagination_view.add_select(placeholder, first=True)

        await pagination_view.send()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Help(bot))