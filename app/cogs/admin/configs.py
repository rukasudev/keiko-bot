from typing import Union

import discord
from discord import app_commands
from discord.app_commands import Choice

from app.bot import DiscordBot
from app.constants import DBConfigs as constants
from app.services.admin import get_admin_configs, update_admin_configs
from app.services.utils import keiko_command


class Configs(app_commands.Group, name="configs"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name="update",
        description="Keiko cheerfully allows you to update and customize its settings",
    )
    @app_commands.choices(
        config=[Choice(name=i, value=i) for i in constants.ADMIN_CONFIGS_LIST]
    )
    async def update_config(
        self,
        interaction: discord.Interaction,
        config: str,
        value: Union[str],
    ) -> None:
        update_admin_configs(
            self.bot, {config: int(value) if "ID" in config else value}
        )
        await interaction.response.send_message(
            f":meta: Config `{config}` was updated successfully!"
        )

    @keiko_command(
        name="show",
        description="Keiko happily provides a list of its current configurations",
    )
    async def show_config(self, interaction: discord.Interaction) -> None:
        parsed_database_configs = "\n".join(
            [
                f"{key}: {value}"
                for key, value in get_admin_configs().items()
                if key in constants.ADMIN_CONFIGS_LIST
            ]
        )

        parsed_configs = "\n".join(
            [
                f"{key}: {value}"
                for config in self.bot.config.get_db_configs()
                for key, value in config.items()
            ]
        )

        response = f":card_box: **Configs in database:**\n{parsed_database_configs}\n\n"
        response += f":dog: **Configs in Keiko:**\n{parsed_configs}"

        await interaction.response.send_message(response)
