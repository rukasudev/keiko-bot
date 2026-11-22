"""YouTube lives: a live announced in every server that follows its channel when it
starts, a scheduled one read again at its time, and the notice marked ended with how
long it ran once YouTube says it is over."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, List, Mapping, Optional, Sequence

import discord
from dateutil import parser
from requests import RequestException

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import lives
from app.core.commands import WebhookEvent, WebhookReply
from app.core.threads import to_thread
from app.core.utils import ml
from app.data import data, wait
from app.data.collection import Document
from app.data.keiko import schedules
from app.exceptions import ErrorContext
from app.forms import item_values
from app.forms.features.subscriptions import SubscriptionFeature
from app.integrations.youtube import hub
from app.integrations.youtube.client import VideoNotice, notice_event
from app.ui.embed import announcement_embed

LIVES = lives.LiveNotices(
    key="notifications_youtube_live",
    item_key="youtuber",
    platform="youtube",
    flow="youtube_live_notification",
    claim_seconds=constants.YOUTUBE_NOTIFIED_LIVE_TTL_SECONDS,
    live_link="https://www.youtube.com/@{name}/live",
    link_variable="stream_link",
)


async def count_announcers(youtuber: str) -> int:
    """How many servers announce the channel's lives while notifications are on."""
    return await data().guild_cogs.count_cogs_by_option(
        "notifications_youtube_live",
        "notifications.values.youtuber.value",
        youtuber,
        enabled_only=True,
    )


async def count_servers_following(youtuber: str) -> int:
    """How many servers Keiko is still in follow the channel's lives, paused or not."""
    following = [
        str(document.get("guild_id"))
        for document in await following_guilds(youtuber, enabled_only=False)
    ]
    return len(following) - await data().guild_settings.count_absent(following)


async def following_guilds(youtuber: str, enabled_only: bool = True) -> List[Document]:
    """The live notices of every server that follows the channel, only those on
    unless told otherwise."""
    return list(
        await data().guild_cogs.find_cogs_by_option(
            "notifications_youtube_live",
            "notifications.values.youtuber.value",
            youtuber,
            enabled_only=enabled_only,
        )
    )


def live_schedule_id(video_id: str) -> str:
    """The schedule a scheduled live waits under until it is read again."""
    return schedules.schedule_id("youtube_live", video_id)


def moment(video: Mapping[str, Any], name: str) -> Optional[datetime]:
    """A time of the video's live (`scheduledStartTime`, `actualStartTime`,
    `actualEndTime`), in UTC, or None while YouTube gives none."""
    written = (video.get("liveStreamingDetails") or {}).get(name)
    if not written:
        return None

    parsed: datetime = parser.isoparse(str(written))
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class LiveVideo:
    """A live as YouTube describes it, and the channel it airs on: everything its
    notice needs, read before anything is sent."""

    video: Mapping[str, Any]
    channel: Mapping[str, Any]

    @property
    def video_id(self) -> str:
        """The id of the live's video."""
        return str(self.video.get("id") or "")

    @property
    def youtuber(self) -> str:
        """The handle of the channel, as a server saves it."""
        return str(self.channel.get("customUrl") or "").removeprefix("@").lower()

    @property
    def link(self) -> str:
        """Where the live is watched; none for a sample with no video."""
        if not self.video_id:
            return ""
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def channel_link(self) -> str:
        """The page of the channel the live airs on."""
        return f"https://www.youtube.com/@{self.youtuber}" if self.youtuber else ""


def read_live(video: Mapping[str, Any]) -> LiveVideo:
    """The live of a video the hub's notice or the due pass already read, with its
    channel read now."""
    channel_id = str((video.get("snippet") or {}).get("channelId") or "")
    return LiveVideo(
        video=video, channel=bot.youtube.get_channel_info(channel_id) or {}
    )


def announce_started_live(event: WebhookEvent) -> WebhookReply:
    """Announce after the request, once, a live the hub said started on a channel
    the webhook read it from."""
    from app.integrations.http import schedule_webhook_job

    notice: VideoNotice = event.payload
    context = ErrorContext(
        flow=LIVES.flow,
        extra={"video_id": notice.video_id, "youtube_channel_id": notice.channel_id},
    )

    try:
        live = read_live(notice.video)
    except Exception as error:
        logger.error(
            f"youtube live {notice.video_id} could not be read: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return WebhookReply(503, "YouTube did not answer")

    logger.info(
        f"live {notice.video_id} started — **{live.youtuber}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    try:
        schedule_webhook_job(go_live(live), f"youtube live — {live.youtuber}")
    except Exception as error:
        logger.error(
            f"youtube live {notice.video_id} could not be handed to the bot: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return WebhookReply(503, "Announcement not scheduled")

    return WebhookReply(204, "Webhook processed")


async def go_live(live: LiveVideo) -> None:
    """Announce a live in every server that follows its channel, once, then forget
    the time it was scheduled for."""
    await bot.wait_until_ready()
    claim = constants.REDIS_YOUTUBE_LIVE_CLAIM.format(video_id=live.video_id)

    if not live.youtuber:
        logger.info(
            f"The live {live.video_id} is on a channel with no handle. "
            "No action taken.",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
    elif await data().guild_messages.count_messages(LIVES.key, live.video_id):
        logger.info(
            f"The live {live.video_id} of **{live.youtuber}** is already posted",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
    elif await to_thread(
        lives.claim_the_live, claim, LIVES.claim_seconds, live.youtuber
    ):
        try:
            followers = await following_guilds(live.youtuber)
        except Exception:
            await to_thread(lives.give_back_the_live, claim, live.youtuber)
            raise
        await lives.send_notifications(LIVES, live_notice(live), followers)
    else:
        logger.info(
            f"The live {live.video_id} of **{live.youtuber}** was already announced",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )

    await data().keiko_schedules.delete_schedule(live_schedule_id(live.video_id))


def live_notice(live: LiveVideo) -> lives.Live:
    """A live as its notice draws it, saved under its video so its end edits it."""
    return lives.Live(
        name=live.youtuber,
        embed=partial(create_live_embed, live),
        link=live.link,
        subject=live.video_id,
    )


def schedule_live(event: WebhookEvent) -> WebhookReply:
    """Keep a scheduled live to read again when it is set to start; a later notice
    of the same live moves the time."""
    notice: VideoNotice = event.payload
    due_at = moment(notice.video, "scheduledStartTime") or datetime.now(timezone.utc)

    try:
        wait(save_schedule(notice.video_id, due_at, attempt=0))
    except Exception as error:
        logger.error(
            f"youtube live {notice.video_id} could not be scheduled: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow=LIVES.flow,
                extra={
                    "video_id": notice.video_id,
                    "youtube_channel_id": notice.channel_id,
                },
            ),
            exc_info=True,
        )
        return WebhookReply(503, "Live not scheduled")

    logger.info(
        f"live {notice.video_id} scheduled for {due_at:%Y-%m-%d %H:%M} UTC",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    return WebhookReply(204, "Live scheduled")


async def save_schedule(video_id: str, due_at: datetime, attempt: int) -> None:
    """Keep the live due at `due_at`, read again for the `attempt`-th time then."""
    await data().keiko_schedules.upsert_schedule(
        live_schedule_id(video_id), "youtube_live", None, due_at=due_at, attempt=attempt
    )


def end_live(event: WebhookEvent) -> WebhookReply:
    """Mark ended, after the request and once, the notices of a live Keiko announced;
    a notice about any other finished live, or about one already ended, changes
    nothing."""
    from app.integrations.http import schedule_webhook_job

    notice: VideoNotice = event.payload
    claim = constants.REDIS_YOUTUBE_LIVE_END_CLAIM.format(video_id=notice.video_id)
    context = ErrorContext(
        flow=LIVES.flow,
        extra={"video_id": notice.video_id, "youtube_channel_id": notice.channel_id},
    )

    try:
        wait(data().keiko_schedules.delete_schedule(live_schedule_id(notice.video_id)))
        announced = wait(
            data().guild_messages.count_messages(LIVES.key, notice.video_id)
        )
    except Exception as error:
        logger.error(
            f"youtube live {notice.video_id} could not be ended: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return WebhookReply(503, "Live not ended")

    if not announced:
        return WebhookReply(204, "No notice to end")
    if not lives.claim_the_live(claim, LIVES.claim_seconds, notice.video_id):
        return WebhookReply(204, "Live already ended")

    try:
        live = read_live(notice.video)
    except Exception as error:
        lives.give_back_the_live(claim, notice.video_id)
        logger.error(
            f"youtube live {notice.video_id} could not be ended: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return WebhookReply(503, "Live not ended")

    logger.info(
        f"live {notice.video_id} ended — **{live.youtuber}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    try:
        schedule_webhook_job(
            mark_live_ended(live, claim), f"youtube live end — {live.youtuber}"
        )
    except Exception as error:
        lives.give_back_the_live(claim, notice.video_id)
        logger.error(
            f"youtube live {notice.video_id} end could not be handed to the bot: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        return WebhookReply(503, "End not scheduled")

    return WebhookReply(204, "Webhook processed")


async def mark_live_ended(live: LiveVideo, claim: str) -> None:
    """Mark every notice of the live ended, with how long it ran; an end that fails
    gives its claim back for the next notice."""
    await bot.wait_until_ready()
    started = moment(live.video, "actualStartTime")
    ended = moment(live.video, "actualEndTime")
    duration = ended - started if started and ended else None

    try:
        followers = await following_guilds(live.youtuber, enabled_only=False)
        await lives.mark_ended(LIVES, live.youtuber, followers, duration, live.video_id)
    except Exception:
        await to_thread(lives.give_back_the_live, claim, live.video_id)
        raise


async def read_due_lives(due: Sequence[Mapping[str, Any]]) -> None:
    """Read every scheduled live now due in one request per 50, then announce, move
    or drop each one; a live YouTube could not read or handle is read again a step
    later."""
    video_ids = [schedules.schedule_subject(dict(entry))[0] for entry in due]
    now = datetime.now(timezone.utc)

    try:
        videos = await to_thread(bot.youtube.get_videos_info, video_ids)
    except Exception as error:
        logger.warn(
            f"{len(video_ids)} scheduled youtube lives not read — "
            f"{type(error).__name__}: {error}; each is read again a step later",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        for entry, video_id in zip(due, video_ids):
            await step_later(entry, video_id, now, "YouTube still not answering")
        return

    found = {str(video.get("id")): video for video in videos}
    for entry, video_id in zip(due, video_ids):
        try:
            await follow_up(entry, video_id, found.get(video_id), now)
        except Exception as error:
            logger.error(
                f"scheduled youtube live {video_id} failed: "
                f"{type(error).__name__}: {error}; it is read again a step later",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(flow=LIVES.flow, extra={"video_id": video_id}),
                exc_info=True,
            )
            await step_later(entry, video_id, now, "still failing")


async def follow_up(
    entry: Mapping[str, Any],
    video_id: str,
    video: Optional[Mapping[str, Any]],
    now: datetime,
) -> None:
    """Announce a due live that is on air, wait again for one still to come, and
    drop one that is over, gone or no live."""
    if video is None:
        reason = "gone"
    else:
        event = notice_event(video)
        if event == "youtube.live.started":
            await announce_due_live(entry, video_id, video, now)
            return
        if event == "youtube.live.scheduled":
            await wait_again(entry, video_id, video, now)
            return
        reason = event.removeprefix("youtube.")

    await data().keiko_schedules.delete_schedule(live_schedule_id(video_id))
    logger.info(
        f"scheduled live {video_id} dropped — {reason}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def announce_due_live(
    entry: Mapping[str, Any], video_id: str, video: Mapping[str, Any], now: datetime
) -> None:
    """Announce a due live now on air; one whose channel YouTube does not describe now
    is read again a step later."""
    try:
        live = await to_thread(read_live, video)
    except RequestException as error:
        logger.warn(
            f"scheduled youtube live {video_id} is on air but its channel was not "
            f"read — {type(error).__name__}: {error}; it is read again a step later",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        await step_later(entry, video_id, now, "its channel still not read")
        return

    await go_live(live)


async def wait_again(
    entry: Mapping[str, Any], video_id: str, video: Mapping[str, Any], now: datetime
) -> None:
    """Wait for a live still to come: at its new time when YouTube moved it later,
    else a step later past its time."""
    scheduled = moment(video, "scheduledStartTime")
    if scheduled and scheduled > now:
        await save_schedule(video_id, scheduled, attempt=0)
        return

    await step_later(entry, video_id, scheduled or now, "still not on air")


async def step_later(
    entry: Mapping[str, Any], video_id: str, after: datetime, reason: str
) -> None:
    """Read the live again the next step of `YOUTUBE_LIVE_RETRY_MINUTES` after
    `after`, or give it up past the last step."""
    attempt = int(entry.get("attempt") or 0)
    steps = constants.YOUTUBE_LIVE_RETRY_MINUTES
    if attempt >= len(steps):
        await data().keiko_schedules.delete_schedule(live_schedule_id(video_id))
        logger.info(
            f"scheduled live {video_id} dropped — {reason} after the last of "
            f"{len(steps)} steps",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return

    due_at = after + timedelta(minutes=steps[attempt])
    await save_schedule(video_id, due_at, attempt=attempt + 1)


def create_live_embed(live: LiveVideo, locale: str = "en-us") -> discord.Embed:
    """The embed of a live announcement: its channel above its title, its picture,
    and when it started, which Discord shows in each reader's own time."""
    snippet = live.video.get("snippet") or {}
    thumbnails = snippet.get("thumbnails") or {}
    picture = thumbnails.get("maxres") or thumbnails.get("high") or {}
    avatars = live.channel.get("thumbnails") or {}
    avatar = avatars.get("high") or avatars.get("default") or {}
    description = str(live.channel.get("description") or "")

    embed = announcement_embed(
        snippet.get("title"),
        description.split("\n")[0],
        lives.stream_status("online", locale),
        picture.get("url") or None,
        copy="notifications_youtube_live.ui.announcement",
        locale=locale,
        url=live.link or None,
        colour=discord.Color.red(),
        thumbnail=str(avatar.get("url") or ""),
    )
    embed.set_author(name=live.channel.get("title"), url=live.channel_link)
    embed.timestamp = moment(live.video, "actualStartTime")
    return embed


async def send_notification_preview(
    interaction: discord.Interaction, responses: List[Document]
) -> None:
    """The live announcement as it will arrive, under the embed drawn for the
    channel."""
    await lives.send_preview(LIVES, interaction, responses, build_preview_embed)


async def build_preview_embed(youtuber: str, locale: str) -> Optional[discord.Embed]:
    """The very embed a live announcement carries, drawn for this channel with an
    example live."""
    try:
        channel, latest = await hub.channel_with_latest_upload(youtuber)
    except Exception as error:
        logger.warn(
            f"Could not draw the youtube live preview of {youtuber}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None

    snippet = (channel or {}).get("snippet")
    if not snippet:
        return None

    sample = {
        "snippet": {
            "title": ml(
                "notifications_youtube_live.ui.announcement.samples.title", locale
            ),
            "thumbnails": (latest or {}).get("thumbnails") or {},
        }
    }
    return create_live_embed(LiveVideo(video=sample, channel=snippet), locale)


async def subscribe_channel(
    interaction: Optional[discord.Interaction], response: Mapping[str, Any]
) -> None:
    """Follow on YouTube's hub the channel a server starts announcing the lives of."""
    await hub.follow_channel(item_values(response)["youtuber"], interaction)


async def unsubscribe_channel(
    interaction: Optional[discord.Interaction], notification: Mapping[str, Any]
) -> None:
    """Let go on YouTube's hub of a channel no server follows any more."""
    await hub.unfollow_channel(item_values(notification)["youtuber"], interaction)


class YouTubeLiveFeature(SubscriptionFeature):
    """YouTube channels to announce when they go live, followed on YouTube's hub with
    every other YouTube command."""

    item_key = "youtuber"
    external = "youtube"
    lookup_result = "channel_id"

    def __init__(self) -> None:
        super().__init__("notifications_youtube_live")
        self.subscribe = subscribe_channel
        self.unsubscribe = unsubscribe_channel
        self.followers = count_servers_following
        self.announcers = count_announcers
        self.find_account = hub.find_channel
        self.preview_sender = send_notification_preview


FEATURE = YouTubeLiveFeature()
