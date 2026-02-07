import re

import discord

from app import bot
from app.views.log_inspection import LogInspectionView


def get_guild(guild_id: int) -> discord.Guild:
    return bot.get_guild(int(guild_id))


def get_user(user_id: int) -> discord.User:
    return bot.get_user(int(user_id))


async def parse_log_message_to_embed(interaction: discord.Interaction, message: discord.Message) -> discord.Embed:
    guild_id = None
    user_id = None

    for item in message.embeds[0].fields:
        if item.name == "Guild ID":
            guild_id = item.value
        elif item.name == "User ID":
            user_id = item.value.replace("<@", "").replace(">", "")

    if not user_id and message.embeds[0].description:
        match = re.search(r"by (?:<@)?(\d+)>?", message.embeds[0].description)
        if match:
            user_id = match.group(1)

    if not guild_id:
        return await interaction.response.send_message(
            "Guild ID not found in this log.", ephemeral=True
        )

    guild = get_guild(guild_id)
    user = get_user(user_id) if user_id else None

    view = LogInspectionView(user, guild, guild_id)
    await view.send(interaction)
