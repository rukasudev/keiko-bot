import discord

from app.bot import DiscordBot


async def send_log_file_by_date(
    date: str, interaction: discord.Interaction, bot: DiscordBot
) -> None:
    channel = bot.get_channel(int(bot.config.ADMIN_LOGS_FILES_CHANNEL_ID))

    async for message in channel.history(limit=None):
        if date not in message.content:
            continue

        attachment = message.attachments[0].url
        return await interaction.followup.send(
            f":page_facing_up: Here is my log file for: **{date}**! {attachment}",
        )

    await interaction.followup.send(f":pensive: Log file not found for **{date}**")
