from collections import defaultdict
from typing import Dict

import discord
from discord.app_commands.commands import Command

from app.bot import DiscordBot
from app.components.select import HelpSelect
from app.constants import CogsConstants as constants
from app.services.utils import keiko_command, ml
from app.translator import locale_str
from app.types.cogs import Cog
from app.views.pagination import PaginationView


class Help(Cog, name=locale_str("help", type="name", namespace="help")):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

        self.item_emoji = ":flying_disc:"
        self.base_i18n_path = f"commands.commands.help"

    def is_guild_command(
        self, interaction: discord.Interaction, command: Command
    ) -> bool:
        if interaction.guild.id == self.bot.config.ADMIN_GUILD_ID:
            return False

        parent_command = command

        if command.parent:
            parent_command = (
                command.parent.parent if command.parent.parent else command.parent
            )

        return parent_command.name not in constants.INTERACTION_COGS

    def get_command_info(
        self, interaction: discord.Interaction, command: Command
    ) -> Dict[str, str]:
        if not command.extras.get(interaction.locale.value):
            locale = discord.Locale.american_english.value
        else:
            locale = interaction.locale.value

        command_info = {
            "name": (
                command.extras[locale]["locale_qualified_name"]
                if command.extras
                else command.qualified_name
            ),
            "description": (
                command.extras[locale]["locale_qualified_desc"]
                if command.extras
                else command.description
            ),
        }

        return command_info

    def get_title(self, base_title: str, command: Command, locale: str) -> str:
        if command.parent is None:
            return base_title
        elif not command.extras:
            return command.qualified_name.split()[0]
        else:
            return command.extras[locale]["locale_qualified_name"].split()[0]

    def populate_data(self, interaction: discord.Interaction) -> Dict[str, str]:
        base_title = ml(
            f"{self.base_i18n_path}.embed.base-commands", interaction.locale
        )
        data = defaultdict(lambda: {"item": [], "commands": []})

        for command in self.bot.app_commands:
            if self.is_guild_command(interaction, command):
                continue

            command_info = self.get_command_info(interaction, command)
            title = self.get_title(
                base_title, command, interaction.locale.value
            ).capitalize()

            data[title]["item"].append(f"{self.item_emoji} `/{command_info['name']}`")
            data[title]["commands"].append(command_info)

        data = self.order_data(data)

        return data

    def order_data(self, data_dict: Dict[str, str]) -> Dict[str, str]:
        for title in data_dict:
            data_dict[title]["commands"].sort(key=lambda cmd: cmd["name"])
            data_dict[title]["item"].sort()
            data_dict[title]["item"] = "\n".join(data_dict[title]["item"])

        return dict(sorted(data_dict.items()))

    @keiko_command(
        name=locale_str("help", type="name", namespace="help"),
        description=locale_str("help", type="desc", namespace="help"),
    )
    async def help(self, interaction: discord.Interaction) -> None:
        data = self.populate_data(interaction)

        title = ml(f"{self.base_i18n_path}.embed.title", locale=interaction.locale)
        description = ml(f"{self.base_i18n_path}.embed.desc", locale=interaction.locale)
        footer = ml(f"{self.base_i18n_path}.embed.footer", locale=interaction.locale)
        placeholder = ml(
            f"{self.base_i18n_path}.embed.placeholder", locale=interaction.locale
        )

        pagination_view = PaginationView(interaction, title, description, footer, data)
        select = HelpSelect(placeholder)
        pagination_view.add_select(select, first=True)

        await pagination_view.send()


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Help(bot))
