from app.bot import DiscordBot
from app.cogs.events import Events
from app.cogs.moderations import Moderations
from app.cogs.moderations.block import Block
from app.cogs.notifications import Notifications
from app.cogs.notifications.twitch import Twitch


async def setup(bot: DiscordBot) -> None:
    moderations = Moderations(bot)
    notifications = Notifications(bot)

    moderations.app_command.add_command(Block())
    notifications.app_command.add_command(Twitch())

    await bot.add_cog(moderations)
    await bot.add_cog(notifications)
    await bot.add_cog(Events(bot))
