"""YouTube notifications: a new video announced in every server that follows its
channel."""

import random
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence

import discord

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import analytics
from app.core.cache import claim_redis_key, release_redis_key
from app.core.commands import WebhookEvent, WebhookReply
from app.core.mentions import mention_of
from app.core.servers import server_language
from app.core.trace import Outcome, fan_out
from app.core.utils import (
    destination,
    fill_placeholders,
    ml,
    split_messages,
    values_of,
)
from app.data import data, wait
from app.data.collection import Document
from app.exceptions import ErrorContext
from app.forms import item_values, items_of
from app.forms.features.subscriptions import SubscriptionFeature
from app.integrations.youtube import hub
from app.integrations.youtube.client import VideoNotice
from app.ui.embed import announcement_embed
from app.ui.message_preview import MessagePreviewView


async def count_youtube_video_subscription_by_guilds(youtuber: str) -> int:
    """How many servers announce the youtuber's videos while notifications are on."""
    return await data().guild_cogs.count_cogs_by_option(
        "notifications_youtube_video",
        "notifications.values.youtuber.value",
        youtuber,
        enabled_only=True,
    )


async def count_servers_following(youtuber: str) -> int:
    """How many servers Keiko is still in follow the youtuber, paused or not."""
    following = [
        str(document.get("guild_id"))
        for document in await data().guild_cogs.find_cogs_by_option(
            "notifications_youtube_video",
            "notifications.values.youtuber.value",
            youtuber,
        )
    ]
    return len(following) - await data().guild_settings.count_absent(following)


async def find_guilds_by_youtuber(youtuber: str) -> List[Document]:
    """The notifications of every server announcing the youtuber's videos, while on."""
    return await data().guild_cogs.find_cogs_by_option(
        "notifications_youtube_video",
        "notifications.values.youtuber.value",
        youtuber,
        enabled_only=True,
    )


@dataclass(frozen=True)
class VideoAnnouncement:
    """Everything a new video's announcement needs, read before anything is sent."""

    video: Mapping[str, Any]
    channel: Mapping[str, Any]
    youtuber: str
    followers: Sequence[Mapping[str, Any]]

    @property
    def video_id(self) -> str:
        """The id of the video being announced."""
        return str(self.video.get("id"))


def prepare_video_announcement(
    video: Mapping[str, Any], channel_id: str
) -> VideoAnnouncement:
    """Read the channel and the followers of the video the hub's notice already read."""
    channel = bot.youtube.get_channel_info(channel_id) or {}
    youtuber = channel["customUrl"].replace("@", "")
    return VideoAnnouncement(
        video=video,
        channel=channel,
        youtuber=youtuber,
        followers=list(wait(find_guilds_by_youtuber(youtuber))),
    )


def announce_published_video(event: WebhookEvent) -> WebhookReply:
    """Announce after the request, once, a video the hub said was published and the
    webhook read as the channel's."""
    from app.integrations.http import schedule_webhook_job

    notice: VideoNotice = event.payload
    context = ErrorContext(
        flow="youtube_notification",
        extra={"video_id": notice.video_id, "youtube_channel_id": notice.channel_id},
    )

    try:
        announcement = prepare_video_announcement(notice.video, notice.channel_id)
    except Exception as error:
        logger.error(
            f"youtube video {notice.video_id} could not be read: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return WebhookReply(503, "YouTube did not answer")

    claim = constants.REDIS_YOUTUBE_VIDEO_CLAIM.format(video_id=notice.video_id)
    if not claim_redis_key(claim, constants.YOUTUBE_NOTIFIED_VIDEO_TTL_SECONDS):
        logger.info(
            f"youtube video {notice.video_id} was already announced. No action taken.",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return WebhookReply(204, "Already announced")

    logger.info(
        f"new video {notice.video_id} — **{announcement.youtuber}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    try:
        schedule_webhook_job(
            announce_video(announcement), f"youtube video — {announcement.youtuber}"
        )
    except Exception as error:
        release_redis_key(claim)
        logger.error(
            f"youtube video {notice.video_id} could not be handed to the bot: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return WebhookReply(503, "Announcement not scheduled")

    return WebhookReply(204, "Webhook processed")


async def announce_video(announcement: VideoAnnouncement) -> None:
    """Post a new video in every server that follows its channel, each on its own."""
    await bot.wait_until_ready()

    served = await fan_out(
        "youtube_notification",
        (
            (
                guild_data.get("guild_id"),
                deliver_video(announcement, guild_data, notification),
            )
            for guild_data in announcement.followers
            for notification in items_of(guild_data, "notifications")
            if item_values(notification).get("youtuber") == announcement.youtuber
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
    announcement: VideoAnnouncement,
    guild_data: Mapping[str, Any],
    notification: Mapping[str, Any],
) -> None:
    """Post the announcement in the channel one server chose for it, pinging only who
    the notice mentions."""
    notice = item_values(notification)
    mention = mention_of(notice.get("mention"), notice.get("mention_roles"))
    guild, channel = destination(guild_data.get("guild_id"), notice.get("channel"))
    message = compose_notification_message(
        notice, announcement.youtuber, announcement.video_id
    )
    embed = create_video_notification_embed(
        announcement.video_id,
        announcement.video.get("snippet") or {},
        announcement.channel,
        await server_language(guild.id),
    )

    try:
        await channel.send(
            content=mention.before(message),
            embed=embed,
            allowed_mentions=mention.allowed,
        )
    except discord.Forbidden as error:
        analytics.record_permission_failure(
            guild.id, "notifications_youtube_video", error
        )
        raise

    analytics.record_value(guild.id, "notifications_youtube_video")


def create_video_notification_embed(
    video_id: str,
    video_info: Mapping[str, Any],
    youtuber_info: Mapping[str, Any],
    locale: str = "en-us",
) -> discord.Embed:
    """The embed of a video announcement: its picture, title, tags and channel."""
    video_thumbnails = video_info.get("thumbnails") or {}
    video_thumbnail = video_thumbnails.get("maxres") or video_thumbnails.get("high")
    channel_thumbnails = youtuber_info.get("thumbnails") or {}
    picture = channel_thumbnails.get("high") or channel_thumbnails.get("default") or {}
    description = youtuber_info.get("description") or ""

    return announcement_embed(
        video_info.get("title"),
        description.split("\n")[0],
        image=(video_thumbnail or {}).get("url") or None,
        copy="notifications_youtube_video.ui.announcement",
        locale=locale,
        fields={
            "description": (video_info.get("description") or "").split("\n")[0],
            "tags": ", ".join((video_info.get("tags") or [])[:3]),
        },
        url=f"https://www.youtube.com/watch?v={video_id}",
        colour=discord.Color.red(),
        thumbnail=str(picture.get("url") or ""),
    )


async def send_notification_preview(
    interaction: discord.Interaction, responses: List[Document]
) -> None:
    """The video announcement as it will arrive, one written message per click, its
    mention written but pinging nobody."""
    values = values_of(responses)
    youtuber = str(values.get("youtuber") or "")
    video_link = f"https://www.youtube.com/@{youtuber}"
    mention = mention_of(values.get("mention"), values.get("mention_roles"))
    texts = [
        mention.before(parse_streamer_message(message, youtuber, video_link)) or ""
        for message in split_messages(values.get("notification_messages"))
    ]
    embed = await build_preview_embed(youtuber, str(interaction.locale))
    await MessagePreviewView(texts, interaction.locale, embed).send(interaction)


async def build_preview_embed(youtuber: str, locale: str) -> Optional[discord.Embed]:
    """The very embed a new video announcement carries, drawn for this channel with an
    example video."""
    try:
        channel, latest = await hub.channel_with_latest_upload(youtuber)
    except Exception as error:
        logger.warn(
            f"Could not draw the youtube preview of {youtuber}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None

    youtuber_info = (channel or {}).get("snippet")
    if not youtuber_info:
        return None

    video_info = {
        "title": ml(
            "notifications_youtube_video.ui.announcement.samples.title", locale
        ),
        "description": ml(
            "notifications_youtube_video.ui.announcement.samples.description", locale
        ),
        "thumbnails": (latest or {}).get("thumbnails") or {},
    }
    return create_video_notification_embed("", video_info, youtuber_info, locale)


def compose_notification_message(
    notice: Mapping[str, Any], youtuber: str, video_id: str
) -> str:
    """One of the notice's messages, picked at random, filled for this video."""
    messages = split_messages(notice.get("notification_messages"))
    video_link = f"https://www.youtube.com/watch?v={video_id}"
    random_message = random.choice(messages or [""])

    return parse_streamer_message(random_message, youtuber, video_link)


def parse_streamer_message(message: str, youtuber: str, video_link: str) -> str:
    """A message with the youtuber and video link filled, the link added if missing."""
    return fill_placeholders(
        message, {"youtuber": youtuber, "video_link": video_link}, required="video_link"
    )


async def subscribe_youtube_new_video(
    interaction: Optional[discord.Interaction], response: Mapping[str, Any]
) -> None:
    """Follow on YouTube's hub the youtuber a server starts announcing."""
    await hub.follow_channel(item_values(response)["youtuber"], interaction)


async def unsubscribe_youtube_new_video(
    interaction: Optional[discord.Interaction], notification: Mapping[str, Any]
) -> None:
    """Let go on YouTube's hub of a youtuber no server follows any more."""
    await hub.unfollow_channel(item_values(notification)["youtuber"], interaction)


class YouTubeFeature(SubscriptionFeature):
    """Youtubers to announce when they post a video, followed on YouTube's hub with
    every other YouTube command."""

    item_key = "youtuber"
    external = "youtube"
    lookup_result = "channel_id"
    renewal_title = constants.YOUTUBE_RENEWAL_REMINDER_TITLE

    def __init__(self) -> None:
        super().__init__("notifications_youtube_video")
        self.subscribe = subscribe_youtube_new_video
        self.unsubscribe = unsubscribe_youtube_new_video
        self.followers = count_servers_following
        self.announcers = count_youtube_video_subscription_by_guilds
        self.find_account = hub.find_channel
        self.preview_sender = send_notification_preview

    async def renewals(self) -> Mapping[str, str]:
        """The youtuber of every renewal reminder Keiko keeps, by reminder id."""
        return await hub.renewals()

    async def forget_renewal(self, reminder_id: str) -> None:
        """Delete a renewal reminder on the reminders API, then its schedule."""
        await hub.forget_renewal(reminder_id)


FEATURE = YouTubeFeature()
