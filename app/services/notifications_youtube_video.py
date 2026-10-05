import asyncio
import random
from dataclasses import dataclass
from datetime import timedelta, timezone
from enum import Enum
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Union

import discord
from requests import RequestException

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.exceptions import DestinationNotFound, ErrorContext
from app.data.notifications_youtube_video import (
    count_servers_following,
    count_youtube_video_subscription_by_guilds,
    find_followed_youtubers,
    find_guilds_by_youtuber,
)
from app.data.reminder import insert_renewal_reminder, stamp_hub_confirmation
from app.integrations.reminder_webhook import REMINDER_TIMEZONE, reminder_time
from app.settings import open_feature
from app.services import analytics
from app.services.utils import fill_placeholders, ml, values_of
from app.views.message_preview import MessagePreviewView


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)


@dataclass(frozen=True)
class VideoAnnouncement:
    """Everything a new video's announcement needs, read before anything is sent."""

    video: Dict[str, Any]
    channel: Dict[str, Any]
    youtuber: str
    followers: List[Dict[str, Any]]

    @property
    def video_id(self) -> str:
        """The id of the video being announced."""
        return str(self.video.get("id"))

    @property
    def owner(self) -> Optional[str]:
        """The channel YouTube says the video belongs to."""
        return (self.video.get("snippet") or {}).get("channelId")


def prepare_video_announcement(video_id: str, channel_id: str) -> Optional[VideoAnnouncement]:
    """Read the video, its channel and its followers; None while YouTube does not show the video."""
    video = bot.youtube.get_video_info(video_id)
    if not video:
        return None

    channel = bot.youtube.get_channel_info(channel_id)
    youtuber = channel.get("customUrl").replace("@", "")
    return VideoAnnouncement(
        video=video,
        channel=channel,
        youtuber=youtuber,
        followers=list(find_guilds_by_youtuber(youtuber)),
    )


async def announce_video(announcement: VideoAnnouncement) -> None:
    """Post a new video in every server that follows its channel, each one on its own."""
    await bot.wait_until_ready()

    targets = [
        (guild_data, notification)
        for guild_data in announcement.followers
        for notification in (guild_data.get("notifications") or {}).get("values") or []
        if (notification.get("youtuber") or {}).get("value") == announcement.youtuber
    ]
    results = await asyncio.gather(
        *(deliver_video(announcement, guild_data, notification) for guild_data, notification in targets),
        return_exceptions=True,
    )

    failures = [
        (str(guild_data.get("guild_id")), result)
        for (guild_data, _), result in zip(targets, results, strict=True)
        if isinstance(result, Exception)
    ]

    for guild_id, failure in failures:
        if isinstance(failure, (discord.Forbidden, DestinationNotFound)):
            logger.warn(
                f"Youtube notification not sent in guild {guild_id}: {type(failure).__name__}: {failure}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue

        logger.error(
            f"Failed to send youtube notification in guild {guild_id}: {type(failure).__name__}: {failure}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow="youtube_notification",
                guild_id=guild_id,
                extra={"video_id": announcement.video_id, "youtuber": announcement.youtuber},
            ),
            exc_info=failure,
        )

    logger.info(
        f"Notifications sent for youtuber **{announcement.youtuber}** new video in "
        f"{len(results) - len(failures)} guilds, {len(failures)} failed",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def deliver_video(
    announcement: VideoAnnouncement, guild_data: Dict[str, Any], notification: Dict[str, Any]
) -> None:
    """Post the announcement in the channel one server chose for it."""
    channel_id = (notification.get("channel") or {}).get("value")
    guild = bot.get_guild(int(guild_data.get("guild_id")))
    channel = guild.get_channel(int(channel_id)) if guild else None

    if channel is None:
        raise DestinationNotFound(f"channel {channel_id} of guild {guild_data.get('guild_id')} not found")

    message = compose_notification_message(notification, announcement.youtuber, announcement.video_id)
    embed = create_video_notification_embed(
        announcement.video_id, announcement.video.get("snippet") or {}, announcement.channel
    )

    try:
        await channel.send(content=message, embed=embed)
    except discord.Forbidden as error:
        analytics.record_permission_failure(guild.id, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY, error)
        raise

    analytics.record_value(guild.id, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)

def create_video_notification_embed(
    video_id: str, video_info: Dict[str, Any], youtuber_info: Dict[str, Any]
) -> discord.Embed:
    video_link = f"https://www.youtube.com/watch?v={video_id}"
    video_thumbnails = video_info.get("thumbnails") or {}
    video_thumbnail = video_thumbnails.get("maxres") or video_thumbnails.get("high")

    description = youtuber_info.get("description") or ""
    channel_thumbnails = youtuber_info.get("thumbnails") or {}
    picture = channel_thumbnails.get("high") or channel_thumbnails.get("default") or {}
    profile_picture = picture.get("url")

    embed = discord.Embed(
        title=video_info.get("title"),
        description=description.split("\n")[0],
        url=video_link,
        color=discord.Color.red(),
    )

    video_description = (video_info.get("description") or "").split("\n")[0]
    video_tags = video_info.get("tags")

    if profile_picture:
        embed.set_thumbnail(url=profile_picture)
    if video_thumbnail and video_thumbnail.get("url"):
        embed.set_image(url=video_thumbnail["url"])

    if video_description:
        embed.add_field(name="Description", value=video_description, inline=True)

    if video_tags:
        video_tags = video_tags[:3] if len(video_tags) > 3 else video_tags
        embed.add_field(name="Tags", value=", ".join(video_tags), inline=True)

    return embed

async def send_notification_preview(
    interaction: discord.Interaction, responses: List[Dict[str, Any]]
) -> None:
    """The video announcement as it will arrive, one written message per click."""

    values = values_of(responses)
    youtuber = str(values.get("youtuber") or "")
    video_link = f"https://www.youtube.com/@{youtuber}"
    texts = [
        parse_streamer_message(message.lstrip(), youtuber, video_link)
        for message in str(values.get("notification_messages") or "").split(";")
        if message.strip()
    ]
    embed = await build_preview_embed(youtuber, interaction.locale)
    await MessagePreviewView(texts, interaction.locale, embed).send(interaction)

async def build_preview_embed(youtuber: str, locale: str) -> Optional[discord.Embed]:
    """The very embed a new video announcement carries, drawn for this channel.

    The channel is the real one; the video lines show an example, since the
    next video does not exist yet.
    """
    try:
        channel_id = await asyncio.to_thread(
            bot.youtube.get_channel_id_from_username, youtuber
        )
        youtuber_info = (
            await asyncio.to_thread(bot.youtube.get_channel_info, channel_id)
            if channel_id
            else None
        )
    except Exception as error:
        logger.warn(
            f"Could not draw the youtube preview of {youtuber}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None

    if not youtuber_info:
        return None

    namespace = "commands.commands.commons.notifications-preview.youtube"
    video_info = {"title": ml(f"{namespace}.title", locale=locale)}
    return create_video_notification_embed("", video_info, youtuber_info)

def compose_notification_message(notification: Dict[str, Any], youtuber: str, video_id: str) -> str:
    messages = notification.get("notification_messages").get("value")
    video_link = f"https://www.youtube.com/watch?v={video_id}"
    random_message = random.choice(messages.split(";")).lstrip()

    return parse_streamer_message(random_message, youtuber, video_link)

def parse_streamer_message(message: str, youtuber: str, video_link: str) -> str:
    return fill_placeholders(
        message, {"youtuber": youtuber, "video_link": video_link}, required="video_link"
    )


class HubOutcome(Enum):
    """How asking YouTube's hub about one youtuber went."""

    DONE = "done"
    NO_SECRET = "no hub secret"
    NO_CHANNEL = "no channel"
    REFUSED = "refused"
    TRY_LATER = "try later"


@dataclass(frozen=True)
class HubAnswer:
    """The outcome of one request to the hub, in words a log line can carry and nothing else."""

    outcome: HubOutcome
    detail: str
    status: Optional[int] = None


def ask_the_hub(youtuber: str, mode: str) -> HubAnswer:
    """Find a youtuber's channel and send the hub one (un)subscribe; a failed request is an answer too."""
    try:
        channel_id = bot.youtube.get_channel_id_from_username(youtuber)
    except RequestException as error:
        return HubAnswer(HubOutcome.TRY_LATER, f"{type(error).__name__}: {error}")

    if not channel_id:
        return HubAnswer(HubOutcome.NO_CHANNEL, "YouTube has no channel by that name")
    return send_to_hub(channel_id, mode)


def send_to_hub(channel_id: str, mode: str) -> HubAnswer:
    """Send the hub one subscribe or unsubscribe of a channel and read its answer, never raising."""
    send = (
        bot.youtube.subscribe_to_new_video_event
        if mode == "subscribe"
        else bot.youtube.unsubscribe_from_new_video_event
    )

    try:
        response = send(channel_id)
    except RequestException as error:
        return HubAnswer(HubOutcome.TRY_LATER, f"{type(error).__name__}: {error}")

    if response is None:
        return HubAnswer(HubOutcome.NO_SECRET, "no hub secret is configured")

    status = response.status_code
    detail = f"the hub answered {status}"
    if 200 <= status < 300:
        return HubAnswer(HubOutcome.DONE, detail, status)
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR or status == HTTPStatus.TOO_MANY_REQUESTS:
        return HubAnswer(HubOutcome.TRY_LATER, detail, status)
    return HubAnswer(HubOutcome.REFUSED, detail, status)


def handle_subscribe_youtubers_new_video(interaction: discord.Interaction, cogs: Union[List[Dict[str, Any]], Dict[str, Any]]):
    if isinstance(cogs, list):
        for response in cogs[0].get("value"):
            subscribe_youtube_new_video(interaction, response)
    else:
        subscribe_youtube_new_video(interaction, cogs)

def subscribe_youtube_new_video(interaction: discord.Interaction, response: Dict[str, Any]):
    """Subscribe a youtuber a server starts following; only a failed lookup keeps it unsaved."""
    youtuber = response.get("youtuber").get("value")
    guilds_by_youtuber = count_youtube_video_subscription_by_guilds(youtuber)

    if guilds_by_youtuber > 0:
        logger.info(
            f"Youtuber **{youtuber}** already subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return

    channel_id = bot.youtube.get_channel_id_from_username(youtuber)
    answer = (
        send_to_hub(channel_id, "subscribe")
        if channel_id
        else HubAnswer(HubOutcome.NO_CHANNEL, "YouTube has no channel by that name")
    )
    wait = constants.YOUTUBE_RENEWAL_INTERVAL_SECONDS

    if answer.outcome is HubOutcome.TRY_LATER:
        wait = constants.YOUTUBE_RENEWAL_RETRY_SECONDS
        logger.warn(
            f"Youtuber **{youtuber}** not subscribed yet — {answer.detail}; "
            "its renewal tries again within the hour",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
    elif answer.outcome in (HubOutcome.REFUSED, HubOutcome.NO_CHANNEL):
        logger.error(
            f"Youtuber **{youtuber}** not subscribed — {answer.detail}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow="youtube_subscribe",
                extra={"youtuber": youtuber, "status": answer.status},
            ),
        )
    elif answer.outcome is HubOutcome.DONE:
        logger.info(
            f"Youtuber **{youtuber}** subscribed — {answer.detail}",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )

    renewal = reminder_time(timedelta(seconds=wait))

    # A refused reminder used to surface as KeyError on the line below, which
    # aborted the subscription that had already succeeded.
    try:
        reminder = bot.reminder.create_reminder({
            "title": "youtube_notification",
            "notes": youtuber,
            "date_tz": renewal.date(),
            "time_tz": f"{renewal:%H:%M}",
        })
        reminder_id = reminder.get("id") if isinstance(reminder, dict) else None
    except Exception as error:
        reminder_id = None
        logger.error(
            f"Failed to create renewal reminder for youtuber {youtuber}: "
            f"{type(error).__name__}: {error}",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )

    if not reminder_id:
        logger.warn(
            f"Youtuber {youtuber} subscribed without a renewal reminder",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    insert_renewal_reminder(reminder_id, youtuber)
    logger.info(
        f"renewal of **{youtuber}** scheduled for {renewal:%Y-%m-%d %H:%M} ({REMINDER_TIMEZONE})",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

def resubscribe_followed_channels() -> int:
    """Subscribe every followed channel again, signed with Keiko's secret; how many were."""
    if bot.config.is_dev():
        return 0
    if not bot.config.YOUTUBE_HUB_SECRET:
        logger.warn(
            "youtube subscriptions not renewed — no hub secret is configured",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 0

    youtubers = find_followed_youtubers()
    renewed = 0

    for youtuber in youtubers:
        try:
            answer = ask_the_hub(youtuber, "subscribe")
        except Exception as error:
            answer = HubAnswer(HubOutcome.TRY_LATER, f"{type(error).__name__}: {error}")

        if answer.outcome is HubOutcome.DONE:
            stamp_hub_confirmation(youtuber, reminder_time().astimezone(timezone.utc))
            renewed += 1
            continue
        logger.warn(
            f"youtuber **{youtuber}** not subscribed again — {answer.detail}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )

    logger.info(
        f"youtube subscriptions renewed — {renewed} of {len(youtubers)}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    return renewed

def handle_unsubscribe_youtube_new_video(interaction: discord.Interaction, cogs: Union[List[Dict[str, Any]], Dict[str, Any]]):
    if cogs.get("notifications"):
        for response in cogs.get("notifications").get("values"):
            unsubscribe_youtube_new_video(interaction, response)
    else:
        unsubscribe_youtube_new_video(interaction, cogs)

def unsubscribe_youtube_new_video(interaction: discord.Interaction, notification: Dict[str, Any]):
    """Unsubscribe a youtuber no other server follows; the renewal ends itself, never here."""
    youtuber = notification.get("youtuber").get("value")
    followers = count_servers_following(youtuber)

    if followers > 1:
        logger.warn(
            f"Youtuber **{youtuber}** is still followed by another server and stays subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    answer = ask_the_hub(youtuber, "unsubscribe")

    if answer.outcome in (HubOutcome.REFUSED, HubOutcome.TRY_LATER):
        logger.error(
            f"Youtuber **{youtuber}** not unsubscribed from the hub — {answer.detail}; "
            "the subscription ends with its lease once its renewal finds no server following",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow="youtube_unsubscribe",
                extra={"youtuber": youtuber, "status": answer.status},
            ),
        )
    elif answer.outcome is HubOutcome.DONE:
        logger.info(
            f"Youtuber **{youtuber}** unsubscribed — {answer.detail}",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
    else:
        logger.info(
            f"Youtuber **{youtuber}** had nothing to unsubscribe — {answer.detail}",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
