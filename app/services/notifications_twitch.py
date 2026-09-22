import asyncio
import datetime
import random
import time
from typing import Any, Dict, List, Optional, Union

import discord
from dateutil import parser

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.data.notifications_twitch import (
    count_streamers_guilds,
    find_guilds_by_streamer_name,
    find_last_stream_date,
    find_stream_notification,
    save_stream_notification,
    update_last_stream_date,
)
from app.settings import open_feature
from app.services import analytics, cache
from app.views.message_preview import MessagePreviewView
from app.services.utils import format_datetime_output, ml


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, constants.NOTIFICATIONS_TWITCH_KEY)


async def handle_send_streamer_notification(streamer_name: str) -> None:
    context = ErrorContext(
        flow="twitch_notification",
        extra={
            "streamer_name": streamer_name,
            "event_type": "stream_online",
        }
    )

    try:
        # The second one sleeps 15 seconds between attempts.
        user_info = await asyncio.to_thread(bot.twitch.get_user_info, streamer_name)
        stream_info = await asyncio.to_thread(wait_for_stream_info, streamer_name)

        if not stream_info:
            logger.info(f"Stream info not found for streamer **{streamer_name}**", log_type=logconstants.COMMAND_INFO_TYPE)
            return

        stream_started_at = stream_info.get("started_at")
        last_stream_date = find_last_stream_date(streamer_name)

        if not last_stream_date or is_more_than_one_hour(stream_started_at, last_stream_date):
            await send_streamer_notifications(stream_info, user_info)
            update_last_stream_date(streamer_name, stream_started_at)
        else:
            await edit_streamer_notifications(user_info, status=constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_ONLINE)
    except Exception as e:
        logger.error(
            f"Failed to handle twitch notification: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise

async def send_streamer_notifications(stream_info: Dict[str, Any], user_info: Dict[str, Any]) -> None:
    streamer_name = user_info.get("login")
    guilds_data = find_guilds_by_streamer_name(streamer_name)
    logger.info(f"Sending notifications for **{streamer_name}**", log_type=logconstants.COMMAND_INFO_TYPE)

    count = await process_notifications(guilds_data, streamer_name, stream_info, user_info)
    logger.info(f"Notifications sent for **{streamer_name}** in {count} guilds", log_type=logconstants.COMMAND_INFO_TYPE)

async def edit_streamer_notifications(user_info: Dict[str, Any], status: str) -> None:
    streamer_name = user_info.get("login")
    guilds_data = find_guilds_by_streamer_name(streamer_name)
    logger.info(f"Editing notifications to {status} for **{streamer_name}**", log_type=logconstants.COMMAND_INFO_TYPE)

    count = await update_notification_status(guilds_data, streamer_name, status)
    logger.info(f"Notifications edited to {status} for **{streamer_name}** in {count} guilds", log_type=logconstants.COMMAND_INFO_TYPE)

async def handle_send_streamer_offline_notification(streamer_name: str) -> None:
    context = ErrorContext(
        flow="twitch_notification",
        extra={
            "streamer_name": streamer_name,
            "event_type": "stream_offline",
        }
    )

    try:
        guilds_data = find_guilds_by_streamer_name(streamer_name)
        last_stream_date = find_last_stream_date(streamer_name)
        stream_duration = None

        if last_stream_date:
            last_stream_date = parser.parse(last_stream_date)
            stream_duration = format_datetime_output(datetime.datetime.now(datetime.timezone.utc) - last_stream_date)

        logger.info(f"Editing notifications to offline for **{streamer_name}**", log_type=logconstants.COMMAND_INFO_TYPE)
        count = await update_notification_status(guilds_data, streamer_name, constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_OFFLINE, stream_duration)
        logger.info(f"Notifications edited to offline for **{streamer_name}** in {count} guilds", log_type=logconstants.COMMAND_INFO_TYPE)
    except Exception as e:
        logger.error(
            f"Failed to handle twitch offline notification: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise

async def process_notifications(guilds_data, streamer_name, stream_info, user_info):
    count = 0
    for guild_data in guilds_data:
        guild = bot.get_guild(int(guild_data["guild_id"]))

        for notification in guild_data["notifications"]["values"]:
            if notification["streamer"]["value"].lower() != streamer_name:
                continue

            channel = guild.get_channel(int(notification["channel"]["value"]))
            message = await channel.send(
                content=compose_notification_message(notification, streamer_name),
                embed=create_stream_notification_embed(streamer_name, stream_info, user_info)
            )
            save_stream_notification(guild.id, channel.id, streamer_name, message.id)
            analytics.record_value(guild.id, constants.NOTIFICATIONS_TWITCH_KEY)
            count += 1
    return count

async def update_notification_status(guilds_data: List[Dict[str, Any]], streamer_name: str, status: str, duration: str = None) -> int:
    count = 0
    for guild_data in guilds_data:
        guild = bot.get_guild(int(guild_data["guild_id"]))

        for notification in guild_data["notifications"]["values"]:
            if notification["streamer"]["value"].lower() != streamer_name:
                continue

            channel = guild.get_channel(int(notification["channel"]["value"]))
            message = await fetch_notification_message(guild.id, channel.id, streamer_name)
            status = parse_stream_status(status)
            status = f"{status} | ⌛️ Duration: {duration}" if duration else status

            if message:
                message.embeds[0].set_footer(text=status)
                await message.edit(embed=message.embeds[0])
                count += 1
    return count

def parse_stream_status(status: str) -> str:
    return f"🟢 {status.capitalize()}" if status == constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_ONLINE else f"🔴 {status.capitalize()}"

async def fetch_notification_message(guild_id: str, channel_id: str, streamer_name: str) -> discord.Message:
    stream_notification = find_stream_notification(guild_id, channel_id, streamer_name)
    if not stream_notification:
        return None

    # A guild Keiko was removed from, or a channel that was deleted, both read
    # back as None here. Chaining through them raised AttributeError inside a
    # listener, which is how it reached the error channel 213 times.
    guild = bot.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if not channel:
        return None

    try:
        return await channel.fetch_message(stream_notification["message_id"])
    except (discord.NotFound, discord.Forbidden):
        return None

def is_more_than_one_hour(start_time: str, last_time: str) -> bool:
    return (parser.parse(start_time) - parser.parse(last_time)).total_seconds() > 3600


def wait_for_stream_info(streamer: str) -> Dict[str, Any]:
    stream_info = bot.twitch.get_stream_info(streamer)
    if stream_info:
        return stream_info

    attempts = 0
    while attempts <= 3:
        attempts += 1

        logger.info(f"Checking stream info for {streamer}, attempt {attempts}", log_type=logconstants.COMMAND_INFO_TYPE)
        stream_info = bot.twitch.get_stream_info(streamer)
        if stream_info:
            return stream_info

        if attempts < 3:
            time.sleep(15)

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

    if stream_thumbnail:
        cache_key = f"?{stream_info['id']}" if stream_info.get("id") else ""
        live_thumbnail = f"{stream_thumbnail}{cache_key}"
        embed.set_image(url=live_thumbnail.format(width=1280, height=720))
    embed.add_field(name="Game", value=stream_game, inline=True)
    embed.add_field(name="Streamer", value=streamer, inline=True)
    embed.set_footer(text=parse_stream_status(constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_ONLINE))

    return embed

def handle_subscribe_streamer(interaction: discord.Interaction, cogs: Union[List[Dict[str, Any]], Dict[str, Any]]):
    if isinstance(cogs, list):
        for form_responses in cogs[0].get("value"):
            subscribe_streamer(interaction, form_responses)
    else:
        subscribe_streamer(interaction, cogs)

def subscribe_streamer(interaction: discord.Interaction, response: Dict[str, Any]) -> None:
    streamer = response.get("streamer").get("value")
    streamer_id = bot.twitch.get_user_id_from_login(streamer)

    response = bot.twitch.subscribe_to_stream_online_event(streamer_id)
    if response.status_code == 409:
        logger.warn(
            f"Streamer {streamer} already subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    elif response.status_code != 202:
        logger.error(
            f"Error subscribing online event to streamer {streamer}: {response.json()}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        return

    response = bot.twitch.subscribe_to_stream_offline_event(streamer_id)
    if response.status_code != 202:
        logger.error(
            f"Error subscribing offline event to streamer {streamer}: {response.json()}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        return

    logger.info(
        f"Streamer {streamer} subscribed",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

def handle_unsubscribe_streamer(interaction: discord.Interaction, cogs: Union[List[Dict[str, Any]], Dict[str, Any]]):
    if cogs.get("notifications"):
        for notification in cogs.get("notifications").get("values"):
            unsubscribe_streamer(interaction, notification)
    else:
        unsubscribe_streamer(interaction, cogs)

def unsubscribe_streamer(interaction: discord.Interaction, notification: Dict[str, Any]) -> None:
    streamer = notification.get("streamer").get("value")
    streamer_id = bot.twitch.get_user_id_from_login(streamer)

    guilds_by_streamer = count_streamers_guilds(streamer)
    if guilds_by_streamer > 1:
        logger.warn(
            f"Streamer {streamer} has more than one subscription and will not be unsubscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    subscriptions = bot.twitch.get_subscription_by_user_id(streamer_id)
    if not subscriptions.get("data"):
        return

    for subscription in subscriptions.get("data"):
        response = bot.twitch.unsubscribe_from_stream_event(subscription.get("id"))
        if response.status_code != 204:
            logger.error(
                f"Error unsubscribing from streamer {streamer}: {response.json()}",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )
            return

    logger.info(
        f"Streamer {streamer} unsubscribed",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

async def send_notification_preview(
    interaction: discord.Interaction, responses: List[Dict[str, Any]]
) -> None:
    """The live announcement as it will arrive, one written message per click."""
    values = {
        item["key"]: item.get("_raw_value", item.get("value"))
        for item in responses
        if item.get("key")
    }
    streamer = str(values.get("streamer") or "")
    stream_link = f"https://www.twitch.tv/{streamer}"
    texts = [
        parse_streamer_message(message.lstrip(), streamer, stream_link)
        for message in str(values.get("notification_messages") or "").split(";")
        if message.strip()
    ]
    embed = await build_preview_embed(streamer, interaction.locale)
    await MessagePreviewView(texts, interaction.locale, embed).send(interaction)

async def build_preview_embed(streamer: str, locale: str) -> Optional[discord.Embed]:
    """The very embed a live announcement carries, drawn for this streamer.

    The live's own title and category when there is one, an example of both
    when nobody is streaming right now.
    """
    try:
        user_info = await asyncio.to_thread(bot.twitch.get_user_info, streamer) or {}
        stream_info = await asyncio.to_thread(bot.twitch.get_stream_info, streamer)
    except Exception as error:
        logger.warn(
            f"Could not draw the twitch preview of {streamer}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None

    if not stream_info:
        namespace = "commands.commands.commons.notifications-preview.twitch"
        stream_info = {
            "title": ml(f"{namespace}.title", locale=locale),
            "game_name": ml(f"{namespace}.game", locale=locale),
            "thumbnail_url": await last_stream_thumbnail(user_info),
        }
    return create_stream_notification_embed(streamer, stream_info, user_info)

async def last_stream_thumbnail(user_info: Dict[str, Any]) -> str:
    """The last stream's picture, so a preview shows the space one fills."""
    try:
        found = await asyncio.to_thread(
            bot.twitch.get_last_video_thumbnail, user_info.get("id")
        )
    except Exception:
        found = None
    return str(found or user_info.get("offline_image_url") or "")

def compose_notification_message(notification: Dict[str, Any], streamer: str) -> str:
    messages = notification.get("notification_messages").get("value")
    stream_link = f"https://www.twitch.tv/{streamer}"
    random_message = random.choice(messages.split(";")).lstrip()

    return parse_streamer_message(random_message, streamer, stream_link)

def parse_streamer_message(message: str, streamer: str, stream_link: str) -> str:
    if "{stream_link}" not in message.lower():
        message += "\n{stream_link}"

    message = message.replace("{streamer}", streamer).replace("{stream_link}", stream_link)

    return message
