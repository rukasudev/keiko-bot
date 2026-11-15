"""/admin cogs: the loaded extensions, and loading them again."""

import discord
from discord import app_commands
from discord.app_commands import Choice

from app.bot import DiscordBot
from app.constants import DiscordLimits
from app.core.utils import cogs_manager, extension_names
from app.decorators import keiko_command
from app.types.cogs import Group


async def extension_choices(
    _interaction: discord.Interaction[DiscordBot], current: str
) -> list[Choice[str]]:
    """The core cogs and command keys whose name holds what the admin typed."""
    typed = current.lower()
    return [
        Choice(name=name, value=name) for name in extension_names() if typed in name
    ][: DiscordLimits.AUTOCOMPLETE_CHOICES]


class Cogs(Group, name="cogs"):  # noqa: D101 - discord.py sends a Group's docstring as its description
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name="show",
        description=(
            "Keiko kindly provides you with a list of all the amazing "
            "features and commands available"
        ),
    )
    async def show_cogs(self, interaction: discord.Interaction) -> None:
        """List the loaded extensions and the global commands."""
        commands = self.bot.tree.get_commands()

        extensions = "\n".join(
            [ext.split(".")[-1] for ext in self.bot.extensions.keys()]
        )
        extensions_message = f":point_up: **Extensions Up**:\n{extensions}"

        commands_names = "\n".join([command.name for command in commands])
        commands_message = f":fist: **Global GroupCogs Up**:\n{commands_names}"

        message = f"{extensions_message}\n\n{commands_message}"

        await interaction.response.send_message(message)

    @keiko_command(
        name="load", description="Keiko joyfully loads the specified cog for you"
    )
    @app_commands.autocomplete(cog=extension_choices)
    async def load_cog(self, interaction: discord.Interaction, cog: str) -> None:
        """Load the extensions of `cog`."""
        await cogs_manager(self.bot, "load", [cog], True)
        await interaction.response.send_message(f":point_right: Cog {cog} loaded!")

    @keiko_command(
        name="unload", description="Keiko sweetly unloads the specified cog for you"
    )
    @app_commands.autocomplete(cog=extension_choices)
    async def unload_cog(self, interaction: discord.Interaction, cog: str) -> None:
        """Unload the extensions of `cog`."""
        await cogs_manager(self.bot, "unload", [cog], True)

        await interaction.response.send_message(f":point_left: Cog {cog} unloaded!")

    @keiko_command(
        name="reload", description="Keiko happily reloads the specified cog for you"
    )
    @app_commands.autocomplete(cog=extension_choices)
    async def reload_cog(self, interaction: discord.Interaction, cog: str) -> None:
        """Reload the extensions of `cog`."""
        await cogs_manager(self.bot, "reload", [cog], True)

        await interaction.response.send_message(f":thumbsup: `{cog}` reloaded!")
