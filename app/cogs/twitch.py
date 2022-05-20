from discord.ext import commands


class TwitchCogs(commands.Cog, name="Twitch"):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(TwitchCogs(bot))
