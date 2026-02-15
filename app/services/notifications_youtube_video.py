import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Union

import discord

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.data.notifications_youtube_video import (
    count_youtube_video_subscription_by_guilds,
    find_guilds_by_youtuber,
)
from app.data.reminder import (
    delete_reminder_by_id,
    find_reminder_by_value,
    insert_reminder,
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

def send_youtube_video_notification(video_id: str, channel_id: str) -> None:
    context = ErrorContext(
        flow="youtube_notification",
        extra={
            "video_id": video_id,
            "youtube_channel_id": channel_id,
        }
    )

    try:
        youtuber_info = bot.youtube.get_channel_info(channel_id)
        video_info = bot.youtube.get_video_info(video_id)
        youtuber_user = youtuber_info.get("customUrl").replace("@", "")

        context.extra["youtuber"] = youtuber_user

        guilds_data = find_guilds_by_youtuber(youtuber_user)

        logger.info(
            f"Starting to send notifications for youtube video: **{youtuber_user}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )

        count = 0
        for guild_data in guilds_data:
            guild = bot.get_guild(int(guild_data.get("guild_id")))

            notifications = guild_data.get("notifications").get("values")
            for notification in notifications:
                youtuber = notification.get("youtuber").get("value")
                if youtuber != youtuber_user:
                    continue

                message = compose_notification_message(notification, youtuber, video_id)
                channel = guild.get_channel(int(notification.get("channel").get("value")))

                embed = create_video_notification_embed(video_info, youtuber_info)

                bot.loop.create_task(channel.send(content=message, embed=embed))
                count += 1

        logger.info(
            f"Notifications sent for youtuber **{youtuber_user}** new video in {count} guilds",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
    except Exception as e:
        logger.error(
            f"Failed to send youtube notification: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise

def create_video_notification_embed(video_info: Dict[str, Any], youtuber_info: Dict[str, Any]) -> discord.Embed:
    video_link = f"https://www.youtube.com/watch?v={video_info.get('id')}"
    video_thumbnail = video_info.get("thumbnails").get("maxres") or video_info.get("thumbnails").get("high")

    description = youtuber_info.get("description")
    profile_picture = youtuber_info.get("thumbnails").get("high").get("url")

    embed = discord.Embed(
        title=video_info.get("title"),
        description=description.split("\n")[0],
        url=video_link,
        color=discord.Color.red(),
    )

    video_description = video_info.get("description").split("\n")[0]
    video_tags = video_info.get("tags")

    embed.set_thumbnail(url=profile_picture)
    embed.set_image(url=video_thumbnail.get("url"))

    if video_description:
        embed.add_field(name="Description", value=video_description, inline=True)

    if video_tags:
        video_tags = video_tags[:3] if len(video_tags) > 3 else video_tags
        embed.add_field(name="Tags", value=", ".join(video_tags), inline=True)

    return embed

def compose_notification_message(notification: Dict[str, Any], youtuber: str, video_id: str) -> str:
    messages = notification.get("notification_messages").get("value")
    video_link = f"https://www.youtube.com/watch?v={video_id}"
    random_message = random.choice(messages.split(";")).lstrip()

    return parse_streamer_message(random_message, youtuber, video_link)

def parse_streamer_message(message: str, youtuber: str, video_link: str) -> str:
    if "{video_link}" not in message.lower():
        message += "\n{video_link}"

    message = message.format(youtuber=youtuber, video_link=video_link)

    return message

def handle_subscribe_youtubers_new_video(interaction: discord.Interaction, cogs: Union[List[Dict[str, Any]], Dict[str, Any]]):
    if isinstance(cogs, list):
        for response in cogs[0].get("value"):
            subscribe_youtube_new_video(interaction, response)
    else:
        subscribe_youtube_new_video(interaction, cogs)

def subscribe_youtube_new_video(interaction: discord.Interaction, response: Dict[str, Any]):
    youtuber = response.get("youtuber").get("value")
    channel_id = bot.youtube.get_channel_id_from_username(youtuber)

    guilds_by_youtuber = count_youtube_video_subscription_by_guilds(youtuber)
    if guilds_by_youtuber > 0:
        logger.info(
            f"Youtuber {youtuber} already subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return

    bot.youtube.subscribe_to_new_video_event(channel_id)
    renew_date = datetime.now() + timedelta(days=4)
    reminder = bot.reminder.create_reminder(
        {"title": "youtube_notification", "notes": youtuber, "date_tz": renew_date.date()}
    )

    insert_reminder(reminder["id"], "youtube_notification", youtuber)
    logger.info(
        f"Youtuber {youtuber} subscribed. Reminder created for youtuber {youtuber} to renew subscription. Date: {renew_date}",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

def handle_unsubscribe_youtube_new_video(interaction: discord.Interaction, cogs: Union[List[Dict[str, Any]], Dict[str, Any]]):
    if cogs.get("notifications"):
        for response in cogs.get("notifications").get("values"):
            unsubscribe_youtube_new_video(interaction, response)
    else:
        unsubscribe_youtube_new_video(interaction, cogs)

def unsubscribe_youtube_new_video(interaction: discord.Interaction, notification: Dict[str, Any]):
    youtuber = notification.get("youtuber").get("value")
    channel_id = bot.youtube.get_channel_id_from_username(youtuber)

    guilds_by_youtuber = count_youtube_video_subscription_by_guilds(youtuber)
    if guilds_by_youtuber > 1:
        logger.warn(
            f"Youtuber {youtuber} has more than one subscription and will not be unsubscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    bot.youtube.unsubscribe_from_new_video_event(channel_id)
    logger.info(
        f"Youtuber {youtuber} unsubscribed",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    reminder = find_reminder_by_value(youtuber)
    if not reminder:
        return

    logger.info(
        f"Reminder {reminder.get('reminder_id')} found for youtuber {youtuber}",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    bot.reminder.delete_reminder(reminder.get("reminder_id"))
    logger.info(
        f"Reminder {reminder.get('reminder_id')} deleted from reminder",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    delete_reminder_by_id(reminder.get("id"))
    logger.info(
        f"Reminder {reminder.get('reminder_id')} deleted from database",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
