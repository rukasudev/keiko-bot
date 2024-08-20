

import random
from typing import Any, Dict, List

import discord

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.data.notifications_twitch import (
    count_streamers_guilds,
    find_guilds_by_streamer_name,
)
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

def send_streamer_notifications(streamer_name: str) -> None:
    guilds_data = find_guilds_by_streamer_name(streamer_name)

    logger.info(
        f"Starting to send notifications for streamer: **{streamer_name}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    count = 0
    for guild_data in guilds_data:
        guild = bot.get_guild(int(guild_data.get("guild_id")))

        notifications = guild_data.get("notifications").get("values")
        for notification in notifications:
            streamer = notification.get("streamer").get("value")
            if streamer != streamer_name:
                continue

            message = compose_notification_message(notification, streamer)
            channel = guild.get_channel(int(notification.get("channel").get("value")))

            stream_info = bot.twitch.get_stream_info(streamer)
            user_info = bot.twitch.get_user_info(streamer)
            embed = create_stream_notification_embed(streamer, stream_info, user_info)

            bot.loop.create_task(channel.send(content=message, embed=embed))
            count += 1


    logger.info(
        f"Notifications sent for streamer **{streamer_name}** in {count} guilds",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

def create_stream_notification_embed(streamer: str, stream_info: Dict[str, Any], user_info: Dict[str, Any]) -> discord.Embed:
    stream_link = f"https://www.twitch.tv/{streamer}"
    stream_title = stream_info.get("title")
    stream_game = stream_info.get("game_name")
    stream_thumbnail = stream_info.get("thumbnail_url")
    streamer_profile_image = user_info.get("profile_image_url")
    description = user_info.get("description")

    embed = discord.Embed(
        title=stream_title,
        description=description,
        url=stream_link,
        color=discord.Color.purple(),
    )

    embed.set_thumbnail(url=streamer_profile_image)

    streame_thumbnail = stream_thumbnail.format(width=1280, height=720)
    embed.set_image(url=streame_thumbnail)
    embed.add_field(name="Game", value=stream_game, inline=True)
    embed.add_field(name="Streamer", value=streamer, inline=True)

    return embed


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

def compose_notification_message(notification: Dict[str, Any], streamer: str) -> str:
    messages = notification.get("notification_messages").get("value")
    stream_link = f"https://www.twitch.tv/{streamer}"
    random_message = random.choice(messages.split(";")).lstrip()

    return parse_streamer_message(random_message, streamer, stream_link)

def parse_streamer_message(message: str, streamer: str, stream_link: str) -> str:
    if "{stream_link}" not in message.lower():
        message += "\n{stream_link}"

    message = message.format(streamer=streamer, stream_link=stream_link)

    return message