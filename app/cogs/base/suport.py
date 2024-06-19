import discord

from app.constants import Style as constants
from app.constants import KeikoIcons as keiko_icons
from app.bot import DiscordBot
from app.services.utils import keiko_command, ml
from app.translator import locale_str
from app.types.cogs import Cog


class Support(Cog, name="support"):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("support", type="name", namespace="support"),
        description=locale_str("support", type="desc", namespace="support"),
    )
    async def support(self, interaction: discord.Interaction) -> None:
        multilang_key = "commands.commands.support.response"

        embed = discord.Embed(
            color=(int(constants.BACKGROUND_COLOR, base=16)),
            title=ml(f"{multilang_key}.title", interaction.locale),
            description=ml(f"{multilang_key}.message", locale=interaction.locale),
        )
        embed.add_field(
            name=ml(f"{multilang_key}.field.title", interaction.locale),
            value=ml(f"{multilang_key}.field.desc", interaction.locale),
            inline=False,
        )
        embed.set_footer(text=f"• {ml(f'{multilang_key}.footer', interaction.locale)}")
        embed.set_thumbnail(url=keiko_icons.IMAGE_01)

        await interaction.response.send_message(embed=embed)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Support(bot))
