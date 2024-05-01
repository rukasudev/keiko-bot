from typing import Any, Dict

import discord

from app.bot import DiscordBot
from app.data import admin as configs_data


async def send_log_file_from_channel_by_date(
    date: str, interaction: discord.Interaction
) -> None:
    bot = interaction.client
    channel = bot.get_channel(bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)

    async for message in channel.history(limit=None):
        if date not in message.content:
            continue

        attachment = message.attachments[0].url
        return await interaction.followup.send(
            f":page_facing_up: Here is my log file for: **{date}**! {attachment}",
        )

    await interaction.followup.send(f":pensive: Log file not found for **{date}**")


def update_admin_configs(bot: DiscordBot, data: Dict[str, Any]):
    configs_data.update_admin_configs(data)
    key, value = next(iter(data.items()))
    return setattr(bot.config, key, value)


def get_admin_configs():
    return configs_data.find_admin_configs()
