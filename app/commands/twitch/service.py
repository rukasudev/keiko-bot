"""Twitch notifications: a live announced, then edited when it ends."""

import datetime
import random
import re
import time
from typing import Any, List, Mapping, Optional, Sequence, Tuple

import discord
from dateutil import parser
from redis.exceptions import RedisError

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import analytics
from app.core.cache import claim_redis_key, release_redis_key
from app.core.commands import WebhookEvent, WebhookReply
from app.core.servers import server_language
from app.core.threads import to_thread
from app.core.trace import Outcome, as_utc, fan_out
from app.core.utils import (
    destination,
    fill,
    fill_placeholders,
    format_datetime_output,
    ml,
    split_messages,
    values_of,
)
from app.data import data
from app.data.collection import Document
from app.exceptions import ErrorContext
from app.forms import open_feature
from app.forms.features.subscriptions import Account, SubscriptionFeature
from app.ui.embed import announcement_embed
from app.ui.message_preview import MessagePreviewView


async def count_streamers_guilds(streamer_name: str) -> int:
    """How many servers announce the streamer's lives while notifications are on."""
    return await data().guild_cogs.count_cogs_by_option(
        "notifications_twitch",
        "notifications.values.streamer.value",
        streamer_name,
        enabled_only=True,
    )


async def find_guilds_by_streamer_name(streamer_name: str) -> List[Document]:
    """The notifications of every server announcing the streamer's lives, while on."""
    return await data().guild_cogs.find_cogs_by_option(
        "notifications_twitch",
        "notifications.values.streamer.value",
        streamer_name,
        enabled_only=True,
    )


async def manager(interaction: discord.Interaction) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, "notifications_twitch")


def notify_stream(event: WebhookEvent) -> WebhookReply:
    """Announce after the request the live that started or ended, naming the streamer
    while the request still holds its message."""
    from app.integrations.http import schedule_webhook_job

    kind = event.name.removeprefix("twitch.")
    logger.info(
        f"{kind} — **{event.subject}**", log_type=logconstants.COMMAND_INFO_TYPE
    )
    handler = (
        handle_send_streamer_notification
        if kind == "stream.online"
        else handle_send_streamer_offline_notification
    )
    schedule_webhook_job(handler(event.subject), f"twitch {kind} — {event.subject}")
    return WebhookReply(200, "Webhook processed")


async def handle_send_streamer_notification(streamer_name: str) -> None:
    """Announce a streamer's new live everywhere it is followed, or mark the notices
    online again."""
    context = ErrorContext(
        flow="twitch_notification",
        extra={
            "streamer_name": streamer_name,
            "event_type": "stream_online",
        },
    )

    try:
        # The second one sleeps 15 seconds between attempts.
        user_info = await to_thread(bot.twitch.get_user_info, streamer_name)
        stream_info = await to_thread(wait_for_stream_info, streamer_name)

        if not stream_info:
            logger.info(
                f"Stream info not found for streamer **{streamer_name}**",
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
            return

        stream_started_at = stream_info.get("started_at")
        started_at = parser.parse(stream_started_at)
        last_live = await last_live_of(streamer_name)

        if not last_live or is_more_than_one_hour(started_at, last_live):
            if not await to_thread(claim_the_live, streamer_name, stream_started_at):
                logger.info(
                    f"The live of **{streamer_name}** started at {stream_started_at} "
                    "was already announced",
                    log_type=logconstants.COMMAND_INFO_TYPE,
                )
                return
            try:
                guilds_data = await following_guilds(streamer_name)
            except Exception:
                await to_thread(give_back_the_live, streamer_name, stream_started_at)
                raise
            await send_streamer_notifications(stream_info, user_info, guilds_data)
            await data().keiko_subscriptions.update_subscription_state(
                "twitch", streamer_name, {"last_live_started_at": started_at}
            )
        else:
            await edit_streamer_notifications(
                user_info, status=constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_ONLINE
            )
    except Exception as e:
        logger.error(
            f"Failed to handle twitch notification: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


def claim_the_live(streamer_name: str, started_at: Any) -> bool:
    """Whether this delivery of a live announces it; True when Redis cannot say."""
    try:
        return claim_redis_key(
            live_claim(streamer_name, started_at),
            constants.TWITCH_NOTIFIED_STREAM_TTL_SECONDS,
        )
    except RedisError as error:
        logger.warn(
            f"The live of **{streamer_name}** is announced without its claim — Redis: "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return True


def give_back_the_live(streamer_name: str, started_at: Any) -> None:
    """Free the claim of a live this delivery could not announce, for the next one."""
    try:
        release_redis_key(live_claim(streamer_name, started_at))
    except RedisError as error:
        logger.warn(
            f"The claim of the live of **{streamer_name}** stays until it expires "
            f"— Redis: {type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def live_claim(streamer_name: str, started_at: Any) -> str:
    """The Redis key one live of a streamer is claimed under."""
    return constants.REDIS_TWITCH_LIVE_CLAIM.format(
        streamer=streamer_name, started_at=started_at
    )


async def send_streamer_notifications(
    stream_info: Mapping[str, Any],
    user_info: Mapping[str, Any],
    guilds_data: Sequence[Mapping[str, Any]],
) -> None:
    """Post the live in every server that follows the streamer, each one on its own."""
    streamer_name = user_info["login"]
    logger.info(
        f"Sending notifications for **{streamer_name}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    served = await fan_out(
        "twitch_notification",
        (
            (
                guild_data.get("guild_id"),
                announce_live(guild_data, notification, stream_info, user_info),
            )
            for guild_data, notification in notices_of(guilds_data, streamer_name)
        ),
        streamer_name=streamer_name,
    )
    count = sum(1 for share in served if share.outcome is Outcome.DELIVERED)
    logger.info(
        f"Notifications sent for **{streamer_name}** in {count} guilds",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def edit_streamer_notifications(
    user_info: Mapping[str, Any], status: str
) -> None:
    """Write the stream's status on every notice the streamer's live has."""
    streamer_name = user_info["login"]
    guilds_data = await following_guilds(streamer_name)
    logger.info(
        f"Editing notifications to {status} for **{streamer_name}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    count = await update_notification_status(guilds_data, streamer_name, status)
    logger.info(
        f"Notifications edited to {status} for **{streamer_name}** in {count} guilds",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def handle_send_streamer_offline_notification(streamer_name: str) -> None:
    """Mark every notice of the streamer's live offline, with how long it ran."""
    context = ErrorContext(
        flow="twitch_notification",
        extra={
            "streamer_name": streamer_name,
            "event_type": "stream_offline",
        },
    )

    try:
        guilds_data = await following_guilds(streamer_name)
        last_live = await last_live_of(streamer_name)
        stream_duration = None

        if last_live:
            stream_duration = format_datetime_output(
                datetime.datetime.now(datetime.timezone.utc) - last_live
            )

        logger.info(
            f"Editing notifications to offline for **{streamer_name}**",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        count = await update_notification_status(
            guilds_data,
            streamer_name,
            constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_OFFLINE,
            stream_duration,
        )
        logger.info(
            f"Notifications edited to offline for **{streamer_name}** "
            f"in {count} guilds",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
    except Exception as e:
        logger.error(
            f"Failed to handle twitch offline notification: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def announce_live(
    guild_data: Mapping[str, Any],
    notification: Mapping[str, Any],
    stream_info: Mapping[str, Any],
    user_info: Mapping[str, Any],
) -> None:
    """Post a live in the channel one server chose for it, in that server's language."""
    streamer_name = user_info["login"]
    guild, channel = destination(
        guild_data.get("guild_id"), (notification.get("channel") or {}).get("value")
    )
    embed = create_stream_notification_embed(
        streamer_name, stream_info, user_info, await server_language(guild.id)
    )

    try:
        message = await channel.send(
            content=compose_notification_message(notification, streamer_name),
            embed=embed,
            allowed_mentions=discord.AllowedMentions.all(),
        )
    except discord.Forbidden as error:
        analytics.record_permission_failure(guild.id, "notifications_twitch", error)
        raise

    await data().guild_messages.save_message(
        guild.id, channel.id, "notifications_twitch", streamer_name, message.id
    )
    analytics.record_value(guild.id, "notifications_twitch")


async def update_notification_status(
    guilds_data: Sequence[Mapping[str, Any]],
    streamer_name: str,
    status: str,
    duration: Optional[str] = None,
) -> int:
    """Write the stream's status on every notice it has, each server on its own."""
    served = await fan_out(
        "twitch_notification",
        (
            (
                guild_data.get("guild_id"),
                show_status(guild_data, notification, streamer_name, status, duration),
            )
            for guild_data, notification in notices_of(guilds_data, streamer_name)
        ),
        streamer_name=streamer_name,
    )
    return sum(1 for share in served if share.value)


async def show_status(
    guild_data: Mapping[str, Any],
    notification: Mapping[str, Any],
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

    locale = await server_language(guild.id)
    message.embeds[0].set_footer(text=f"• {stream_status(status, locale, duration)}")
    await message.edit(embed=message.embeds[0])
    return True


def notices_of(
    guilds_data: Sequence[Mapping[str, Any]], streamer_name: str
) -> List[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """Every notice a server set for the streamer, with the server's document."""
    return [
        (guild_data, notification)
        for guild_data in guilds_data
        for notification in (guild_data.get("notifications") or {}).get("values") or []
        if str((notification.get("streamer") or {}).get("value") or "").lower()
        == streamer_name
    ]


def stream_status(status: str, locale: str, duration: Optional[str] = None) -> str:
    """The footer of a live notice: whether it is on and, once over, how long it ran."""
    parts = [ml(f"notifications_twitch.ui.announcement.states.{status}", locale)]

    if duration:
        line = ml("notifications_twitch.ui.announcement.states.duration", locale)
        parts.append(fill(line, duration=duration))
    return " | ".join(parts)


async def fetch_notification_message(
    guild: Any, channel: Any, streamer_name: str
) -> Optional[discord.Message]:
    """The notice Keiko posted for the streamer's live in a channel, None once gone."""
    stream_notification = await data().guild_messages.find_message(
        guild.id, channel.id, "notifications_twitch", streamer_name
    )
    if not stream_notification:
        return None

    try:
        message: discord.Message = await channel.fetch_message(
            stream_notification["message_id"]
        )
    except (discord.NotFound, discord.Forbidden):
        return None
    return message


async def following_guilds(streamer_name: str) -> List[Document]:
    """Every guild document that follows the streamer, read to the end."""
    return list(await find_guilds_by_streamer_name(streamer_name))


async def last_live_of(streamer_name: str) -> Optional[datetime.datetime]:
    """When the streamer's last announced live started, as Keiko recorded it."""
    state = await data().keiko_subscriptions.subscription_state("twitch", streamer_name)
    return as_utc(state.get("last_live_started_at"))


def is_more_than_one_hour(
    start_time: datetime.datetime, last_time: datetime.datetime
) -> bool:
    """Whether a live started over an hour after the last one, so it is a new live."""
    return (start_time - last_time).total_seconds() > 3600


def wait_for_stream_info(streamer: str) -> Optional[Mapping[str, Any]]:
    """The streamer's live as Twitch shows it, asked a few times until it does."""
    stream_info: Optional[Mapping[str, Any]] = bot.twitch.get_stream_info(streamer)
    if stream_info:
        return stream_info

    attempts = 0
    while attempts <= 3:
        attempts += 1

        logger.info(
            f"Checking stream info for {streamer}, attempt {attempts}",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        stream_info = bot.twitch.get_stream_info(streamer)
        if stream_info:
            return stream_info

        if attempts < 3:
            time.sleep(15)

    return None


def create_stream_notification_embed(
    streamer: str,
    stream_info: Mapping[str, Any],
    user_info: Mapping[str, Any],
    locale: str = "en-us",
) -> discord.Embed:
    """The embed of a live announcement: its title, picture, game and streamer."""
    stream_thumbnail = stream_info.get("thumbnail_url")
    picture = None
    if stream_thumbnail:
        cache_key = f"?{stream_info['id']}" if stream_info.get("id") else ""
        picture = f"{stream_thumbnail}{cache_key}".format(width=1280, height=720)

    return announcement_embed(
        stream_info.get("title"),
        user_info.get("description"),
        stream_status(constants.NOTIFICATIONS_TWITCH_STREAM_STATUS_ONLINE, locale),
        picture,
        copy="notifications_twitch.ui.announcement",
        locale=locale,
        fields={"game": stream_info.get("game_name"), "streamer": streamer},
        url=f"https://www.twitch.tv/{streamer}",
        colour=discord.Color.purple(),
        thumbnail=str(user_info.get("profile_image_url") or ""),
    )


async def subscribe_streamer(
    interaction: Optional[discord.Interaction], response: Mapping[str, Any]
) -> None:
    """Ask Twitch to tell Keiko when a streamer goes live and when the live ends."""
    streamer = response["streamer"]["value"]
    streamer_id = await to_thread(bot.twitch.get_user_id_from_login, streamer)

    online = await to_thread(bot.twitch.subscribe_to_stream_online_event, streamer_id)
    if online.status_code == 409:
        logger.warn(
            f"Streamer {streamer} already subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    if online.status_code != 202:
        logger.error(
            f"Error subscribing online event to streamer {streamer}: {online.json()}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        return

    offline = await to_thread(bot.twitch.subscribe_to_stream_offline_event, streamer_id)
    if offline.status_code != 202:
        logger.error(
            f"Error subscribing offline event to streamer {streamer}: {offline.json()}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        return

    logger.info(
        f"Streamer {streamer} subscribed",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def unsubscribe_streamer(
    interaction: Optional[discord.Interaction], notification: Mapping[str, Any]
) -> None:
    """Drop the streamer's Twitch subscriptions once no other server follows it."""
    streamer = notification["streamer"]["value"]
    streamer_id = await to_thread(bot.twitch.get_user_id_from_login, streamer)

    guilds_by_streamer = await count_streamers_guilds(streamer)
    if guilds_by_streamer > 1:
        logger.warn(
            f"Streamer {streamer} has more than one subscription "
            "and will not be unsubscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    subscriptions = await to_thread(bot.twitch.get_subscription_by_user_id, streamer_id)
    if not subscriptions.get("data"):
        return

    for subscription in subscriptions.get("data"):
        response = await to_thread(
            bot.twitch.unsubscribe_from_stream_event, subscription.get("id")
        )
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
    interaction: discord.Interaction, responses: List[Document]
) -> None:
    """The live announcement as it will arrive, one written message per click."""
    values = values_of(responses)
    streamer = str(values.get("streamer") or "")
    stream_link = f"https://www.twitch.tv/{streamer}"
    texts = [
        parse_streamer_message(message, streamer, stream_link)
        for message in split_messages(values.get("notification_messages"))
    ]
    embed = await build_preview_embed(streamer, str(interaction.locale))
    await MessagePreviewView(texts, interaction.locale, embed).send(interaction)


async def build_preview_embed(streamer: str, locale: str) -> Optional[discord.Embed]:
    """The very embed a live announcement carries, drawn for this streamer, with an
    example live while nobody streams."""
    try:
        user_info = await to_thread(bot.twitch.get_user_info, streamer) or {}
        stream_info = await to_thread(bot.twitch.get_stream_info, streamer)
    except Exception as error:
        logger.warn(
            f"Could not draw the twitch preview of {streamer}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None

    if not stream_info:
        stream_info = {
            "title": ml("notifications_twitch.ui.announcement.samples.title", locale),
            "game_name": ml(
                "notifications_twitch.ui.announcement.samples.game", locale
            ),
            "thumbnail_url": await last_stream_thumbnail(user_info),
        }
    described = {
        **user_info,
        "description": ml(
            "notifications_twitch.ui.announcement.samples.description", locale
        ),
    }
    return create_stream_notification_embed(streamer, stream_info, described, locale)


async def last_stream_thumbnail(user_info: Mapping[str, Any]) -> str:
    """The last stream's picture, so a preview shows the space one fills."""
    try:
        found = await to_thread(
            bot.twitch.get_last_video_thumbnail, user_info.get("id")
        )
    except Exception:
        found = None
    return str(found or user_info.get("offline_image_url") or "")


def compose_notification_message(notification: Mapping[str, Any], streamer: str) -> str:
    """One of the server's messages, picked at random, filled for this streamer."""
    messages = split_messages(notification["notification_messages"]["value"])
    stream_link = f"https://www.twitch.tv/{streamer}"
    random_message = random.choice(messages or [""])

    return parse_streamer_message(random_message, streamer, stream_link)


def parse_streamer_message(message: str, streamer: str, stream_link: str) -> str:
    """A message with the streamer and the live link filled in, the link added unless
    already written."""
    linked = bool(streamer) and re.search(
        rf"twitch\.tv/{re.escape(streamer)}(?!\w)", message, flags=re.IGNORECASE
    )
    return fill_placeholders(
        message,
        {"streamer": streamer, "stream_link": stream_link},
        required=None if linked else "stream_link",
    )


async def find_streamer(login: str) -> Account:
    """The Twitch account behind a login, id and picture in one request."""
    info = await to_thread(_twitch_user, login)
    return Account(info.get("id"), str(info.get("profile_image_url") or ""))


def _twitch_user(login: str) -> Mapping[str, Any]:
    try:
        found: Mapping[str, Any] = bot.twitch.get_user_info(login) or {}
        return found
    except Exception:
        return {}


class TwitchFeature(SubscriptionFeature):
    """Streamers to announce when they go live."""

    item_key = "streamer"
    external = "twitch"
    lookup_result = "user_id"

    def __init__(self) -> None:
        super().__init__("notifications_twitch")
        self.subscribe = subscribe_streamer
        self.unsubscribe = unsubscribe_streamer
        self.followers = count_streamers_guilds
        self.announcers = count_streamers_guilds
        self.find_account = find_streamer
        self.preview_sender = send_notification_preview


FEATURE = TwitchFeature()
