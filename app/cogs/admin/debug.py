import discord

from app.bot import DiscordBot
from app.services.utils import keiko_command
from app.services.welcome_messages import send_welcome_message
from app.types.cogs import Group


class Debug(Group, name="debug"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    @keiko_command(
        name="test-welcome",
        description="Trigger a welcome message for testing purposes",
    )
    async def test_welcome_message(self, interaction: discord.Interaction, member: discord.Member = None) -> None:
        target = member or interaction.user
        await interaction.response.defer(ephemeral=True)

        try:
            await send_welcome_message(target)
            await interaction.followup.send(
                f":white_check_mark: Welcome message sent for **{target.display_name}**!",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f":x: Failed to send welcome message: `{e}`",
                ephemeral=True
            )
