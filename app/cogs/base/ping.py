import discord

from app.bot import DiscordBot
from app.components.embed import response_embed
from app.services.utils import keiko_command
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
        ping = round(self.bot.latency * 1000)

        embed = response_embed("commands.commands.ping.response", interaction.locale)
        embed.description = embed.description.replace("$ping", str(ping))

        for guild in self.bot.guilds:
            print(f"Guild ({guild.name}): ", guild.id)

        print("Total Guilds: ", len(self.bot.guilds))

        await interaction.response.send_message(embed=embed)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Ping(bot))
