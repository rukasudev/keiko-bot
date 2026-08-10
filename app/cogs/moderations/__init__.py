import discord
from discord import app_commands

from app.bot import DiscordBot
from app.cogs.moderations.birthdays import Birthdays
from app.cogs.moderations.block import Block
from app.cogs.moderations.roles import Roles
from app.cogs.moderations.welcome import Welcome
from app.translator import locale_str
from app.types.cogs import GroupCog


@app_commands.guild_only()
class Moderations(GroupCog, name=locale_str("moderations", type="groups")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()


async def setup(bot: DiscordBot) -> None:
    moderations = Moderations(bot)
    block = Block(bot)

    moderations.app_command.add_command(Birthdays(bot))
    moderations.app_command.add_command(block)
    moderations.app_command.add_command(Roles(bot))
    moderations.app_command.add_command(Welcome(bot))

    bot.tree.add_command(app_commands.ContextMenu(
        name=locale_str(
            "block-links-check", type="context-menu", namespace="block-links-check"
        ),
        callback=block.validate_block_link,
        type=discord.AppCommandType.message,
    ))

    await bot.add_cog(moderations)
