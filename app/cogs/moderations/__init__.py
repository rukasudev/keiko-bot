from discord import app_commands
from discord.ext import commands

from app.bot import DiscordBot
from app.cogs.moderations.block import Block
from app.cogs.moderations.roles import Roles
from app.translator import locale_str


@app_commands.guild_only()
@app_commands.default_permissions()
class Moderations(commands.GroupCog, name=locale_str("moderations", type="groups")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()


async def setup(bot: DiscordBot) -> None:
    moderations = Moderations(bot)

    moderations.app_command.add_command(Block(bot))
    moderations.app_command.add_command(Roles(bot))

    await bot.add_cog(moderations)
