from discord import app_commands

from app.bot import DiscordBot
from app.cogs.notifications.twitch import Twitch
from app.translator import locale_str
from app.types.cogs import GroupCog


@app_commands.guild_only()
@app_commands.default_permissions()
class Notifications(GroupCog, name=locale_str("notifications", type="groups")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()


async def setup(bot: DiscordBot) -> None:
    notifications = Notifications(bot)

    notifications.app_command.add_command(Twitch(bot))

    await bot.add_cog(notifications)
