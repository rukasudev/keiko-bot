

import discord

from app.constants import Commands as constants
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)


async def manager(interaction: discord.Interaction, guild_id: str):
    cogs = cache.get_cog_data_or_populate(guild_id, constants.NOTIFICATIONS_TWITCH_KEY, manager=True)

    if cogs == None:
        return await send_command_form_message(interaction, constants.NOTIFICATIONS_TWITCH_KEY)

    await send_command_manager_message(
        interaction, constants.NOTIFICATIONS_TWITCH_KEY, cogs
    )
