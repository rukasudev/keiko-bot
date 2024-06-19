import discord
from discord import app_commands
from discord.app_commands import Choice

import app
from app.bot import DiscordBot
from app.services.config import update_activity, update_description, update_status
from app.services.utils import keiko_command
from app.types.cogs import Group, GroupCog


@app_commands.default_permissions()
@app_commands.guilds(discord.Object(app.bot.config.ADMIN_GUILD_ID))
class Config(GroupCog, name="config"):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()

    class Update(Group, name="update"):
        def __init__(self, bot: DiscordBot):
            self.bot = bot
            super().__init__()

        @keiko_command(
            name="status",
            description="Keiko's mood gets a refresh with an updated status",
        )
        @app_commands.choices(
            status=[
                Choice(name=status.name, value=status.value)
                for status in discord.Status
            ]
        )
        async def set_status(
            self, interaction: discord.Interaction, status: Choice[str]
        ):
            await update_status(self.bot, status)
            await interaction.response.send_message(
                f"Keiko's current mood has been uplifted! Status updated to `{status.name}` successfully!"
            )

        @keiko_command(
            name="activity",
            description="Keiko's energy shifts as the activity gets an update",
        )
        @app_commands.choices(
            activity=[
                Choice(name=activity.name, value=activity.value)
                for activity in discord.ActivityType
            ]
        )
        async def set_activity(
            self, interaction: discord.Interaction, activity: Choice[int]
        ):
            await update_activity(self.bot, activity)
            await interaction.response.send_message(
                f"Keiko's energy is buzzing with excitement! Activity updated to `{activity.name}` successfully!"
            )

        @keiko_command(
            name="description",
            description="Keiko's world sparkles with a new description for the activity",
        )
        async def set_description(
            self, interaction: discord.Interaction, description: str
        ):
            await update_description(self.bot, description)
            await interaction.response.send_message(
                f"Keiko's world shines brighter with a new activity description! Description updated to `{description}` successfully!"
            )


async def setup(bot: DiscordBot) -> None:
    config = Config(bot)

    config.app_command.add_command(config.Update(bot))

    await bot.add_cog(config)
