

from typing import Any, Dict, List

import discord

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.data.notifications_twitch import count_streamers_guilds
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

def subscribe_streamer(interaction: discord.Interaction, form_responses: List[Dict[str, Any]]) -> None:
    for response in form_responses[0].get("value"):
        streamer = response.get("streamer").get("value")
        streamer_id = bot.twitch.get_user_id_from_login(streamer)

        response = bot.twitch.subscribe_to_stream_online_event(streamer_id)
        if response.status_code == 409:
            logger.warn(
                f"Streamer {streamer} already subscribed",
                interaction=interaction,
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue

        elif response.status_code != 202:
            logger.error(
                f"Error subscribing to streamer {streamer}: {response.json()}",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )
            continue

        logger.info(
            f"Streamer {streamer} subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )

def unsubscribe_streamer(interaction: discord.Interaction, cogs: Dict[str, Any]) -> None:
    for notification in cogs.get("notifications").get("values"):
        streamer = notification.get("streamer").get("value")
        streamer_id = bot.twitch.get_user_id_from_login(streamer)

        guilds_by_streamer = count_streamers_guilds(streamer)
        if guilds_by_streamer > 1:
            logger.warn(
                f"Streamer {streamer} has more than one subscription and will not be unsubscribed",
                interaction=interaction,
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue

        subscription = bot.twitch.get_subscription_by_user_id(streamer_id)
        if not subscription.get("data"):
            continue

        response = bot.twitch.unsubscribe_from_stream_online_event(subscription.get("data")[0].get("id"))
        if response.status_code != 204:
            logger.error(
                f"Error unsubscribing from streamer {streamer}: {response.json()}",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )
            continue

        logger.info(
            f"Streamer {streamer} unsubscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
