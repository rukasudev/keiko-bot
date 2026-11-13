import asyncio
import random
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from http import HTTPStatus
from typing import Any, Dict, List, Optional, Union

import discord
from requests import RequestException

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import analytics
from app.core.servers import server_language
from app.core.trace import Outcome, fan_out
from app.core.utils import (
    destination,
    fill_placeholders,
    ml,
    split_messages,
    values_of,
)
from app.data.guild import cogs as guild_cogs
from app.data.guild import settings as guild_settings
from app.data.keiko import schedules
from app.data.keiko.subscriptions import update_subscription_state
from app.exceptions import ErrorContext
from app.integrations.reminder_webhook import REMINDER_TIMEZONE, reminder_time
from app.settings import open_feature
from app.views.message_preview import MessagePreviewView


def count_youtube_video_subscription_by_guilds(youtuber: str) -> int:
    """How many servers announce the youtuber's videos while their notifications are on."""
    return guild_cogs.count_cogs_by_option(
        constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        "notifications.values.youtuber.value",
        youtuber,
        enabled_only=True,
    )


def count_servers_following(youtuber: str) -> int:
    """How many servers Keiko is still in follow the youtuber, paused or not."""
    following = [
        str(document.get("guild_id"))
        for document in guild_cogs.find_cogs_by_option(
            constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
            "notifications.values.youtuber.value",
            youtuber,
        )
    ]
    return len(following) - guild_settings.count_absent(following)


def find_guilds_by_youtuber(youtuber: str) -> List[Dict[str, Any]]:
    """The notifications of every server announcing the youtuber's videos, while on."""
    return guild_cogs.find_cogs_by_option(
        constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        "notifications.values.youtuber.value",
        youtuber,
        enabled_only=True,
    )


def find_followed_youtubers() -> List[str]:
    """Every youtuber an enabled guild follows, once each."""
    documents = guild_cogs.find_enabled_cogs(constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)
    return sorted({
        str(notification["youtuber"]["value"])
        for document in documents
        for notification in (document.get("notifications") or {}).get("values") or []
        if (notification.get("youtuber") or {}).get("value")
    })



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

    served = await fan_out(
        "youtube_notification",
        (
            (guild_data.get("guild_id"), deliver_video(announcement, guild_data, notification))
            for guild_data in announcement.followers
            for notification in (guild_data.get("notifications") or {}).get("values") or []
            if (notification.get("youtuber") or {}).get("value") == announcement.youtuber
        ),
        video_id=announcement.video_id,
        youtuber=announcement.youtuber,
    )

    delivered = sum(1 for share in served if share.outcome is Outcome.DELIVERED)
    logger.info(
        f"Notifications sent for youtuber **{announcement.youtuber}** new video in "
        f"{delivered} guilds, {len(served) - delivered} failed",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def deliver_video(
    announcement: VideoAnnouncement, guild_data: Dict[str, Any], notification: Dict[str, Any]
) -> None:
    """Post the announcement in the channel one server chose for it."""
    guild, channel = destination(
        guild_data.get("guild_id"), (notification.get("channel") or {}).get("value")
    )
    message = compose_notification_message(notification, announcement.youtuber, announcement.video_id)
    embed = create_video_notification_embed(
        announcement.video_id,
        announcement.video.get("snippet") or {},
        announcement.channel,
        await server_language(guild.id),
    )

    try:
        await channel.send(
            content=message, embed=embed, allowed_mentions=discord.AllowedMentions.all()
        )
    except discord.Forbidden as error:
        analytics.record_permission_failure(guild.id, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY, error)
        raise

    analytics.record_value(guild.id, constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)

def create_video_notification_embed(
    video_id: str,
    video_info: Dict[str, Any],
    youtuber_info: Dict[str, Any],
    locale: str = "en-us",
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

    fields = "commands.commands.commons.notifications-fields.youtube"
    if video_description:
        embed.add_field(
            name=ml(f"{fields}.description", locale), value=video_description, inline=True
        )

    if video_tags:
        video_tags = video_tags[:3] if len(video_tags) > 3 else video_tags
        embed.add_field(name=ml(f"{fields}.tags", locale), value=", ".join(video_tags), inline=True)

    return embed

async def send_notification_preview(
    interaction: discord.Interaction, responses: List[Dict[str, Any]]
) -> None:
    """The video announcement as it will arrive, one written message per click."""

    values = values_of(responses)
    youtuber = str(values.get("youtuber") or "")
    video_link = f"https://www.youtube.com/@{youtuber}"
    texts = [
        parse_streamer_message(message, youtuber, video_link)
        for message in split_messages(values.get("notification_messages"))
    ]
    embed = await build_preview_embed(youtuber, interaction.locale)
    await MessagePreviewView(texts, interaction.locale, embed).send(interaction)

async def build_preview_embed(youtuber: str, locale: str) -> Optional[discord.Embed]:
    """The very embed a new video announcement carries, drawn for this channel.

    The channel is the real one, with the picture of its latest upload; the
    title and description show an example, since the next video does not
    exist yet. Two quota units: the channel with its uploads playlist, then
    the newest item of that playlist.
    """
    try:
        channel = await asyncio.to_thread(bot.youtube.get_channel_by_handle, youtuber)
        uploads = ((channel or {}).get("contentDetails") or {}).get("relatedPlaylists") or {}
        latest = (
            await asyncio.to_thread(bot.youtube.get_latest_upload, uploads["uploads"])
            if uploads.get("uploads")
            else None
        )
    except Exception as error:
        logger.warn(
            f"Could not draw the youtube preview of {youtuber}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None

    youtuber_info = (channel or {}).get("snippet")
    if not youtuber_info:
        return None

    namespace = "commands.commands.commons.notifications-preview.youtube"
    video_info = {
        "title": ml(f"{namespace}.title", locale=locale),
        "description": ml(f"{namespace}.description", locale=locale),
        "thumbnails": (latest or {}).get("thumbnails") or {},
    }
    return create_video_notification_embed("", video_info, youtuber_info, locale)

def compose_notification_message(notification: Dict[str, Any], youtuber: str, video_id: str) -> str:
    messages = split_messages(notification.get("notification_messages").get("value"))
    video_link = f"https://www.youtube.com/watch?v={video_id}"
    random_message = random.choice(messages or [""])

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

_RENEWALS = threading.Lock()


def youtube_renewal_id(handle: str) -> str:
    """The schedule of one youtuber's hub renewal."""
    return schedules.schedule_id("youtube_renewal", handle)


def find_reminder_by_value(youtuber: str) -> Optional[Dict[str, Any]]:
    """The youtuber's renewal, or None."""
    return _renewal(schedules.find_schedule(youtube_renewal_id(youtuber)))


def find_reminder_by_id(reminder_id: Any) -> Optional[Dict[str, Any]]:
    """A renewal reminder Keiko created, found by the id the reminders API gave it."""
    schedule = schedules.find_schedule_by_reminder(reminder_id)
    if not schedule or schedule.get("kind") != "youtube_renewal":
        return None
    return _renewal(schedule)


def insert_renewal_reminder(
    reminder_id: Any, youtuber: str, due_at: Optional[datetime] = None
) -> None:
    """Schedule a youtuber's renewal reminder, kept until nothing renews it for a while."""
    schedules.upsert_schedule(
        youtube_renewal_id(youtuber),
        "youtube_renewal",
        reminder_id,
        due_at=due_at,
        expires_at=_kept_until(datetime.now(timezone.utc)),
    )


def delete_reminder_by_id(reminder_id: Any) -> None:
    """Forget the schedule of the renewal the reminders API knows as `reminder_id`."""
    renewal = find_reminder_by_id(reminder_id)
    if renewal:
        schedules.delete_schedule(youtube_renewal_id(renewal["value"]))


def _renewal(schedule: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not schedule:
        return None

    (youtuber,) = schedules.schedule_subject(schedule)
    kept = {
        key: schedule[key]
        for key in ("created_at", "due_at", "expires_at")
        if key in schedule
    }
    return {**kept, "reminder_id": schedule.get("reminder_id"), "value": youtuber}


def _kept_until(renewed_at: datetime) -> datetime:
    return renewed_at + timedelta(seconds=constants.YOUTUBE_RENEWAL_ROW_SECONDS)


def subscribe_youtube_new_video(interaction: discord.Interaction, response: Dict[str, Any]):
    """Subscribe a youtuber a server starts following, with its one renewal; only a failed
    lookup keeps it unsaved."""
    youtuber = response.get("youtuber").get("value")

    with _RENEWALS:
        subscribed = count_youtube_video_subscription_by_guilds(youtuber) > 0 and bool(
            find_reminder_by_value(youtuber)
        )
    if subscribed:
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

    if answer.outcome is HubOutcome.TRY_LATER:
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

    ensure_renewal(youtuber, answer, interaction)


def ensure_renewal(
    youtuber: str, answer: HubAnswer, interaction: Optional[discord.Interaction] = None
) -> None:
    """Schedule a youtuber's renewal unless it has one, so however many ask at once it has
    one; it runs within the hour when the hub could not take the subscription now."""
    wait = (
        constants.YOUTUBE_RENEWAL_RETRY_SECONDS
        if answer.outcome is HubOutcome.TRY_LATER
        else constants.YOUTUBE_RENEWAL_INTERVAL_SECONDS
    )
    renewal = reminder_time(timedelta(seconds=wait))

    with _RENEWALS:
        if find_reminder_by_value(youtuber):
            return

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

        insert_renewal_reminder(reminder_id, youtuber, renewal.astimezone(timezone.utc))

    logger.info(
        f"renewal of **{youtuber}** scheduled for {renewal:%Y-%m-%d %H:%M} ({REMINDER_TIMEZONE})",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

def drop_unfollowed_renewal(renewal: Dict[str, Any]) -> bool:
    """Delete a renewal no server Keiko is in follows, counted and deleted under the lock a
    subscribe decides under, so a server adding the youtuber meanwhile makes its own."""
    with _RENEWALS:
        if count_servers_following(renewal.get("value")):
            return False
        bot.reminder.delete_reminder(renewal.get("reminder_id"))
        delete_reminder_by_id(renewal.get("reminder_id"))
    return True


def record_hub_confirmation(youtuber: str, moment: datetime) -> None:
    """Record that the hub took the youtuber's subscription at `moment`, clearing any lapse
    report, and keep its renewal for a while after."""
    update_subscription_state(
        "youtube", youtuber, {"hub_confirmed_at": moment, "lapse_reported_at": None}
    )
    schedules.extend_schedule_expiry(
        youtube_renewal_id(youtuber), _kept_until(moment)
    )


def resubscribe_followed_channels() -> int:
    """Subscribe every followed channel again, signed with Keiko's secret, giving one without
    a renewal its renewal; how many the hub took."""
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

        try:
            ensure_renewal(youtuber, answer)
        except Exception as error:
            logger.warn(
                f"youtuber **{youtuber}** still has no renewal — {type(error).__name__}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

        if answer.outcome is HubOutcome.DONE:
            record_hub_confirmation(youtuber, reminder_time().astimezone(timezone.utc))
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
