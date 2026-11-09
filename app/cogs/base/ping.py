import discord

from app.bot import DiscordBot
from app.decorators import keiko_command
from app.services.help import ping_embed
from app.translator import locale_str
from app.types.cogs import Cog


class Ping(Cog, name="ping"):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("ping", type="name", namespace="ping"),
        description=locale_str("ping", type="desc", namespace="ping"),
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        embed = ping_embed(self.bot, interaction.locale)
        await interaction.response.send_message(embed=embed)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Ping(bot))
