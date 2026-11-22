"""Twitch notifications: a live announced, then edited when it ends."""

import time
from functools import partial
from typing import Any, List, Mapping, Optional

import discord
from dateutil import parser

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import lives
from app.core.commands import WebhookEvent, WebhookReply
from app.core.threads import to_thread
from app.core.utils import (
    ml,
)
from app.data import data
from app.data.collection import Document
from app.exceptions import ErrorContext
from app.forms import item_values
from app.forms.features.subscriptions import Account, SubscriptionFeature
from app.ui.embed import announcement_embed

LIVES = lives.LiveNotices(
    key="notifications_twitch",
    item_key="streamer",
    platform="twitch",
    flow="twitch_notification",
    claim_seconds=constants.TWITCH_NOTIFIED_STREAM_TTL_SECONDS,
    live_link="https://www.twitch.tv/{name}",
    link_variable="stream_link",
)


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
        await lives.go_live(
            LIVES,
            live_of(stream_info, user_info),
            parser.parse(stream_started_at),
            live_claim(streamer_name, stream_started_at),
            partial(following_guilds, streamer_name),
        )
    except Exception as e:
        logger.error(
            f"Failed to handle twitch notification: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


def live_claim(streamer_name: str, started_at: Any) -> str:
    """The Redis key one live of a streamer is claimed under."""
    return constants.REDIS_TWITCH_LIVE_CLAIM.format(
        streamer=streamer_name, started_at=started_at
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
        await lives.end_live(LIVES, streamer_name, guilds_data)
    except Exception as e:
        logger.error(
            f"Failed to handle twitch offline notification: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def following_guilds(streamer_name: str) -> List[Document]:
    """Every guild document that follows the streamer, read to the end."""
    return list(await find_guilds_by_streamer_name(streamer_name))


def live_of(stream_info: Mapping[str, Any], user_info: Mapping[str, Any]) -> lives.Live:
    """A streamer's live as its notice draws it: the embed in a server's language and
    where it is watched."""
    streamer = str(user_info["login"])
    return lives.Live(
        name=streamer,
        embed=partial(
            create_stream_notification_embed, streamer, stream_info, user_info
        ),
        link=LIVES.link(streamer),
    )


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
        lives.stream_status("online", locale),
        picture,
        copy="notifications_twitch.ui.announcement",
        locale=locale,
        fields={"game": stream_info.get("game_name"), "streamer": streamer},
        url=LIVES.link(streamer),
        colour=discord.Color.purple(),
        thumbnail=str(user_info.get("profile_image_url") or ""),
    )


async def subscribe_streamer(
    interaction: Optional[discord.Interaction], response: Mapping[str, Any]
) -> None:
    """Ask Twitch to tell Keiko when a streamer goes live and when the live ends."""
    streamer = item_values(response)["streamer"]
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
    streamer = item_values(notification)["streamer"]
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
    """The live announcement as it will arrive, under the embed drawn for the
    streamer."""
    await lives.send_preview(LIVES, interaction, responses, build_preview_embed)


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
