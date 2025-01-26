from discord import app_commands

from app.bot import DiscordBot
from app.cogs.integrations.stream_elements import StreamElementsCommands
from app.translator import locale_str
from app.types.cogs import GroupCog


@app_commands.guild_only()
@app_commands.default_permissions()
class Integrations(GroupCog, name=locale_str("integrations", type="groups")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()


async def setup(bot: DiscordBot) -> None:
    integrations = Integrations(bot)

    integrations.app_command.add_command(StreamElementsCommands(bot))

    await bot.add_cog(integrations)
