"""/support: how to reach the people behind Keiko."""

import discord

from app.bot import DiscordBot
from app.decorators import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog
from app.ui.embed import response_embed


class Support(Cog, name="support"):
    """The /support slash command."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("support", type="name", namespace="support.slash"),
        description=locale_str(
            "support", type="description", namespace="support.slash"
        ),
    )
    async def support(self, interaction: discord.Interaction) -> None:
        """Send the support server's invitation."""
        embed = response_embed(
            "support.responses.support", interaction.locale, image=True
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: DiscordBot) -> None:
    """Load the /support cog."""
    await bot.add_cog(Support(bot))
