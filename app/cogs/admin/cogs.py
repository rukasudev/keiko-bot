import discord
from discord import app_commands
from discord.app_commands import locale_str

from app.bot import DiscordBot
from app.services.utils import cogs_manager


class Cogs(app_commands.Group, name=locale_str("cogs", namespace="commands")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @app_commands.command(
        name="show",
        description="Keiko kindly provides you with a list of all the amazing features and commands available",
    )
    async def show_cogs(self, interaction: discord.Interaction) -> None:
        commands = self.bot.tree.get_commands()

        extensions = "\n".join(
            [ext.split(".")[-1] for ext in self.bot.extensions.keys()]
        )
        extensions_message = f":point_up: **Extensions Up**:\n{extensions}"

        commands_names = "\n".join([command.name for command in commands])
        commands_message = f":fist: **Global GroupCogs Up**:\n{commands_names}"

        message = f"{extensions_message}\n\n{commands_message}"

        await interaction.response.send_message(message)

    @app_commands.command(
        name="load", description="Keiko joyfully loads the specified cog for you"
    )
    async def load_cog(self, interaction: discord.Interaction, cog: str) -> None:
        await cogs_manager(self.bot, "load", [cog], True)
        await interaction.response.send_message(f":point_right: Cog {cog} loaded!")

    @app_commands.command(
        name="unload", description="Keiko sweetly unloads the specified cog for you"
    )
    async def unload_cog(self, interaction: discord.Interaction, cog: str) -> None:
        await cogs_manager(self.bot, "unload", [cog], True)

        await interaction.response.send_message(f":point_left: Cog {cog} unloaded!")

    @app_commands.command(
        name="reload", description="Keiko happily reloads the specified cog for you"
    )
    async def reload_cog(self, interaction: discord.Interaction, cog: str) -> None:
        await cogs_manager(self.bot, "reload", [cog], True)

        await interaction.response.send_message(f":thumbsup: `{cog}` reloaded!")
