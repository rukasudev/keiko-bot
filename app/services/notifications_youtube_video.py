from typing import Any, Dict

import discord

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.data.notifications_youtube_video import (
    count_youtube_video_subscription_by_guilds,
)
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)


async def manager(interaction: discord.Interaction, guild_id: str):
    cogs = cache.get_cog_data_or_populate(guild_id, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY, manager=True)

    if cogs == None:
        return await send_command_form_message(interaction, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)

    await send_command_manager_message(
        interaction, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY, cogs
    )

def subscribe_youtube_new_video(interaction: discord.Interaction, form_responses: Dict[str, Any]):
    for response in form_responses[0].get("value"):
        youtuber = response.get("youtuber").get("value")
        channel_id = bot.youtube.get_channel_id_from_username(youtuber)

        guilds_by_youtuber = count_youtube_video_subscription_by_guilds(youtuber)
        if guilds_by_youtuber > 0:
            logger.info(
                f"Youtuber {youtuber} already subscribed",
                interaction=interaction,
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
            continue

        bot.youtube.subscribe_to_new_video_event(channel_id)
        logger.info(
            f"Youtuber {youtuber} subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )

def unsubscribe_youtube_new_video(interaction: discord.Interaction, cogs: Dict[str, Any]):
    for notification in cogs.get("notifications").get("values"):
        youtuber = notification.get("youtuber").get("value")
        channel_id = bot.youtube.get_channel_id_from_username(youtuber)

        guilds_by_youtuber = count_youtube_video_subscription_by_guilds(youtuber)
        if guilds_by_youtuber > 1:
            logger.warn(
                f"Youtuber {youtuber} has more than one subscription and will not be unsubscribed",
                interaction=interaction,
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue

        bot.youtube.unsubscribe_from_new_video_event(channel_id)
        logger.info(
            f"Youtuber {youtuber} unsubscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
