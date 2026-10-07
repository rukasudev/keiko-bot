import asyncio
import datetime
import random
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import discord
from dateutil import parser
from redis.exceptions import RedisError

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
from app.services import analytics
from app.services.cache import claim_redis_key
from app.services.work import Outcome, destination, fan_out, guild_locale
from app.views.message_preview import MessagePreviewView
from app.services.utils import fill, fill_placeholders, format_datetime_output, ml, values_of


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
        last_stream_date = await asyncio.to_thread(find_last_stream_date, streamer_name)

        if not last_stream_date or is_more_than_one_hour(stream_started_at, last_stream_date):
            guilds_data = await asyncio.to_thread(following_guilds, streamer_name)
            if not await asyncio.to_thread(claim_the_live, streamer_name, stream_started_at):
                logger.info(
                    f"The live of **{streamer_name}** started at {stream_started_at} was already announced",
                    log_type=logconstants.COMMAND_INFO_TYPE,
                )
                return
            await send_streamer_notifications(stream_info, user_info, guilds_data)
            await asyncio.to_thread(update_last_stream_date, streamer_name, stream_started_at)
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

def claim_the_live(streamer_name: str, started_at: Any) -> bool:
    """Whether this delivery of a live is the one that announces it; True when Redis cannot say."""
    claim = constants.REDIS_TWITCH_NOTIFIED_STREAM.format(
        streamer=streamer_name, started_at=started_at
    )
    try:
        return claim_redis_key(claim, constants.TWITCH_NOTIFIED_STREAM_TTL_SECONDS)
    except RedisError as error:
        logger.warn(
            f"The live of **{streamer_name}** is announced without its claim — Redis: "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return True

async def send_streamer_notifications(
    stream_info: Dict[str, Any], user_info: Dict[str, Any], guilds_data: List[Dict[str, Any]]
) -> None:
    streamer_name = user_info.get("login")
    logger.info(f"Sending notifications for **{streamer_name}**", log_type=logconstants.COMMAND_INFO_TYPE)

    served = await fan_out(
        "twitch_notification",
        (
            (guild_data.get("guild_id"), announce_live(guild_data, notification, stream_info, user_info))
            for guild_data, notification in notices_of(guilds_data, streamer_name)
        ),
        streamer_name=streamer_name,
    )
    count = sum(1 for share in served if share.outcome is Outcome.DELIVERED)
    logger.info(f"Notifications sent for **{streamer_name}** in {count} guilds", log_type=logconstants.COMMAND_INFO_TYPE)

async def edit_streamer_notifications(user_info: Dict[str, Any], status: str) -> None:
    streamer_name = user_info.get("login")
    guilds_data = await asyncio.to_thread(following_guilds, streamer_name)
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
        guilds_data = await asyncio.to_thread(following_guilds, streamer_name)
        last_stream_date = await asyncio.to_thread(find_last_stream_date, streamer_name)
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

async def announce_live(
    guild_data: Dict[str, Any],
    notification: Dict[str, Any],
    stream_info: Dict[str, Any],
    user_info: Dict[str, Any],
) -> None:
    """Post a live in the channel one server chose for it, in that server's language."""
    streamer_name = user_info.get("login")
    guild, channel = destination(
        guild_data.get("guild_id"), (notification.get("channel") or {}).get("value")
    )
    embed = create_stream_notification_embed(
        streamer_name, stream_info, user_info, guild_locale(guild)
    )

    try:
        message = await channel.send(
            content=compose_notification_message(notification, streamer_name),
            embed=embed,
            allowed_mentions=discord.AllowedMentions.all(),
        )
    except discord.Forbidden as error:
        analytics.record_permission_failure(guild.id, constants.NOTIFICATIONS_TWITCH_KEY, error)
        raise

    await asyncio.to_thread(
        save_stream_notification, guild.id, channel.id, streamer_name, message.id
    )
    analytics.record_value(guild.id, constants.NOTIFICATIONS_TWITCH_KEY)


async def update_notification_status(
    guilds_data: List[Dict[str, Any]], streamer_name: str, status: str, duration: str = None
) -> int:
    """Write the stream's status on every notice it has, each server on its own."""
    served = await fan_out(
        "twitch_notification",
        (
            (guild_data.get("guild_id"), show_status(guild_data, notification, streamer_name, status, duration))
            for guild_data, notification in notices_of(guilds_data, streamer_name)
        ),
        streamer_name=streamer_name,
    )
    return sum(1 for share in served if share.value)


async def show_status(
    guild_data: Dict[str, Any],
    notification: Dict[str, Any],
    streamer_name: str,
    status: str,
    duration: Optional[str],
) -> bool:
    """Write the status on the notice one server got; False when that notice is gone."""
    guild, channel = destination(
        guild_data.get("guild_id"), (notification.get("channel") or {}).get("value")
    )
    message = await fetch_notification_message(guild, channel, streamer_name)
    if not message:
        return False

    message.embeds[0].set_footer(text=stream_status(status, guild_locale(guild), duration))
    await message.edit(embed=message.embeds[0])
    return True


def notices_of(
    guilds_data: List[Dict[str, Any]], streamer_name: str
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Every notice a server set for the streamer, with the server's document."""
    return [
        (guild_data, notification)
        for guild_data in guilds_data
        for notification in (guild_data.get("notifications") or {}).get("values") or []
        if str((notification.get("streamer") or {}).get("value") or "").lower() == streamer_name
    ]


def stream_status(status: str, locale: str, duration: Optional[str] = None) -> str:
    """The footer of a live notice: whether the stream is on and, once over, how long it ran."""
    namespace = "commands.commands.commons.notifications-status.twitch"
    parts = [ml(f"{namespace}.{status}", locale)]

    if duration:
        parts.append(fill(ml(f"{namespace}.duration", locale), duration=duration))
    return "• " + " | ".join(parts)


async def fetch_notification_message(
    guild: Any, channel: Any, streamer_name: str
) -> Optional[discord.Message]:
    stream_notification = await asyncio.to_thread(
        find_stream_notification, guild.id, channel.id, streamer_name
    )
    if not stream_notification:
        return None

    try:
        return await channel.fetch_message(stream_notification["message_id"])
    except (discord.NotFound, discord.Forbidden):
        return None


def following_guilds(streamer_name: str) -> List[Dict[str, Any]]:
    """Every guild document that follows the streamer, read to the end."""
    return list(find_guilds_by_streamer_name(streamer_name))


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

def create_stream_notification_embed(
    streamer: str, stream_info: Dict[str, Any], user_info: Dict[str, Any], locale: str = "en-us"
) -> discord.Embed:
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
    fields = "commands.commands.commons.notifications-fields.twitch"
    embed.add_field(name=ml(f"{fields}.game", locale), value=stream_game, inline=True)
    embed.add_field(name=ml(f"{fields}.streamer", locale), value=streamer, inline=True)
    embed.set_footer(text=stream_status(constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_ONLINE, locale))

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
    values = values_of(responses)
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
    return create_stream_notification_embed(streamer, stream_info, user_info, locale)

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
    return fill_placeholders(
        message, {"streamer": streamer, "stream_link": stream_link}, required="stream_link"
    )
