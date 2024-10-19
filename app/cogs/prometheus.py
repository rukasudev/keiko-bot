
from discord.ext.prometheus import PrometheusCog

from app.bot import DiscordBot


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(PrometheusCog(bot))
