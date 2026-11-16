"""/admin configs: the operator ids Keiko reads and stores."""

from typing import Union

import discord
from discord import app_commands
from discord.app_commands import Choice

from app.bot import DiscordBot
from app.commands.admin.service import get_admin_configs, update_admin_configs
from app.constants import DBConfigs as constants
from app.decorators import keiko_command
from app.types.cogs import Group


class Configs(Group, name="configs"):  # noqa: D101 - discord.py sends a Group's docstring as its description
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
        """Store a new value for an operator id."""
        if not value.isdigit():
            await interaction.response.send_message(
                f":x: `{value}` is not a Discord id, so `{config}` was not changed."
            )
            return

        await update_admin_configs(self.bot, {config: int(value)})
        await interaction.response.send_message(
            f":meta: Config `{config}` was updated successfully!"
        )

    @keiko_command(
        name="show",
        description="Keiko happily provides a list of its current configurations",
    )
    async def show_config(self, interaction: discord.Interaction) -> None:
        """List the stored operator ids beside the ones Keiko runs with."""
        parsed_database_configs = "\n".join(
            [
                f"{key}: {value}"
                for key, value in (await get_admin_configs()).items()
                if key in constants.ADMIN_CONFIGS_LIST
            ]
        )

        parsed_configs = "\n".join(
            f"{key}: {value}"
            for key, value in self.bot.config.get_admin_db_configs().items()
        )

        response = f":card_box: **Configs in database:**\n{parsed_database_configs}\n\n"
        response += f":dog: **Configs in Keiko:**\n{parsed_configs}"

        await interaction.response.send_message(response)
