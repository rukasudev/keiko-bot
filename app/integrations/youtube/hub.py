"""YouTube's WebSub hub, followed for every command that follows a YouTube channel:
the channel behind a handle found, a channel subscribed when the first server follows
it, one renewal kept per channel, every followed channel subscribed again at start,
the last one let go unsubscribed."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from http import HTTPStatus
from typing import Any, Mapping, Optional, Tuple

import discord
from requests import RequestException

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core.commands import Reminder
from app.core.threads import to_thread
from app.core.trace import as_utc
from app.data import data, wait
from app.data.collection import Document
from app.data.keiko import schedules
from app.exceptions import ErrorContext
from app.forms.features.subscriptions import (
    Account,
    platform_announcers,
    platform_followed,
    platform_followers,
)
from app.integrations.reminders.client import REMINDER_TIMEZONE, reminder_time


class HubOutcome(Enum):
    """How asking YouTube's hub about one youtuber went."""

    DONE = "done"
    NO_SECRET = "no hub secret"
    NO_CHANNEL = "no channel"
    REFUSED = "refused"
    TRY_LATER = "try later"


@dataclass(frozen=True)
class HubAnswer:
    """The outcome of one request to the hub, in words a log line can carry."""

    outcome: HubOutcome
    detail: str
    status: Optional[int] = None


def ask_the_hub(youtuber: str, mode: str) -> HubAnswer:
    """Find a youtuber's channel and send the hub one (un)subscribe; a failed request is
    an answer too."""
    try:
        channel_id = bot.youtube.get_channel_id_from_username(youtuber)
    except RequestException as error:
        return HubAnswer(HubOutcome.TRY_LATER, f"{type(error).__name__}: {error}")

    if not channel_id:
        return HubAnswer(HubOutcome.NO_CHANNEL, "YouTube has no channel by that name")
    return send_to_hub(channel_id, mode)


def send_to_hub(channel_id: str, mode: str) -> HubAnswer:
    """Send the hub one subscribe or unsubscribe and read its answer, never raising."""
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
    if (
        status >= HTTPStatus.INTERNAL_SERVER_ERROR
        or status == HTTPStatus.TOO_MANY_REQUESTS
    ):
        return HubAnswer(HubOutcome.TRY_LATER, detail, status)
    return HubAnswer(HubOutcome.REFUSED, detail, status)


@dataclass(frozen=True)
class Renewal:
    """One youtuber's hub renewal, as Keiko keeps it in its schedules."""

    youtuber: str
    reminder_id: str
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


_RENEWALS = asyncio.Lock()


def youtube_renewal_id(handle: str) -> str:
    """The schedule of one youtuber's hub renewal."""
    return schedules.schedule_id("youtube_renewal", handle)


async def find_reminder_by_value(youtuber: str) -> Optional[Renewal]:
    """The youtuber's renewal, or None."""
    return _renewal(
        await data().keiko_schedules.find_schedule(youtube_renewal_id(youtuber))
    )


async def find_reminder_by_id(reminder_id: Any) -> Optional[Renewal]:
    """A renewal reminder Keiko created, found by the id the reminders API gave it."""
    schedule = await data().keiko_schedules.find_schedule_by_reminder(reminder_id)
    if not schedule or schedule.get("kind") != "youtube_renewal":
        return None
    return _renewal(schedule)


async def insert_renewal_reminder(
    reminder_id: Any, youtuber: str, due_at: Optional[datetime] = None
) -> None:
    """Schedule a youtuber's renewal reminder, kept until nothing renews it a while."""
    await data().keiko_schedules.upsert_schedule(
        youtube_renewal_id(youtuber),
        "youtube_renewal",
        reminder_id,
        due_at=due_at,
        expires_at=_kept_until(datetime.now(timezone.utc)),
    )


async def delete_reminder_by_id(reminder_id: Any) -> None:
    """Forget the schedule of the renewal the reminders API knows as `reminder_id`."""
    renewal = await find_reminder_by_id(reminder_id)
    if renewal:
        await data().keiko_schedules.delete_schedule(
            youtube_renewal_id(renewal.youtuber)
        )


def _renewal(schedule: Optional[Document]) -> Optional[Renewal]:
    if not schedule:
        return None

    (youtuber,) = schedules.schedule_subject(schedule)
    return Renewal(
        youtuber=youtuber,
        reminder_id=str(schedule.get("reminder_id")),
        created_at=schedule.get("created_at"),
        expires_at=schedule.get("expires_at"),
    )


def _kept_until(renewed_at: datetime) -> datetime:
    return renewed_at + timedelta(seconds=constants.YOUTUBE_RENEWAL_ROW_SECONDS)


async def find_channel(handle: str) -> Account:
    """The YouTube channel behind a handle, and its picture."""
    found = await to_thread(bot.youtube.get_channel_id_from_username, handle)
    image = await to_thread(_channel_picture, found) if found else ""
    return Account(found, image)


def _channel_picture(channel_id: str) -> str:
    try:
        snippet = bot.youtube.get_channel_info(channel_id) or {}
        thumbnails = snippet.get("thumbnails") or {}
        picture = thumbnails.get("medium") or thumbnails.get("default") or {}
        return str(picture.get("url") or "")
    except Exception:
        return ""


async def channel_with_latest_upload(
    handle: str,
) -> Tuple[Optional[Mapping[str, Any]], Optional[Mapping[str, Any]]]:
    """The channel behind a handle, with its snippet, and the snippet of its newest
    upload, as a preview draws them; raises when YouTube does not answer."""
    channel = await to_thread(bot.youtube.get_channel_by_handle, handle)
    uploads = ((channel or {}).get("contentDetails") or {}).get(
        "relatedPlaylists"
    ) or {}
    latest = (
        await to_thread(bot.youtube.get_latest_upload, uploads["uploads"])
        if uploads.get("uploads")
        else None
    )
    return channel, latest


async def follow_channel(
    youtuber: str, interaction: Optional[discord.Interaction] = None
) -> None:
    """Subscribe on the hub a channel a server starts following, with its one renewal,
    unless a YouTube command already announces it; only a failed lookup raises."""
    async with _RENEWALS:
        subscribed = await platform_announcers("youtube", youtuber) > 0 and bool(
            await find_reminder_by_value(youtuber)
        )

    if subscribed:
        logger.info(
            f"Youtuber **{youtuber}** already subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return

    channel_id = await to_thread(bot.youtube.get_channel_id_from_username, youtuber)
    answer = (
        await to_thread(send_to_hub, channel_id, "subscribe")
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

    await ensure_renewal(youtuber, answer, interaction)


async def ensure_renewal(
    youtuber: str, answer: HubAnswer, interaction: Optional[discord.Interaction] = None
) -> None:
    """Schedule a youtuber's renewal unless it has one, within the hour when the hub
    could not take the subscription now."""
    delay = (
        constants.YOUTUBE_RENEWAL_RETRY_SECONDS
        if answer.outcome is HubOutcome.TRY_LATER
        else constants.YOUTUBE_RENEWAL_INTERVAL_SECONDS
    )
    renewal = reminder_time(timedelta(seconds=delay))

    async with _RENEWALS:
        if await find_reminder_by_value(youtuber):
            return

        try:
            reminder = await to_thread(
                bot.reminder.create_reminder,
                {
                    "title": constants.YOUTUBE_RENEWAL_REMINDER_TITLE,
                    "notes": youtuber,
                    "date_tz": renewal.date(),
                    "time_tz": f"{renewal:%H:%M}",
                },
            )
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

        await insert_renewal_reminder(
            reminder_id, youtuber, renewal.astimezone(timezone.utc)
        )

    logger.info(
        f"renewal of **{youtuber}** scheduled for "
        f"{renewal:%Y-%m-%d %H:%M} ({REMINDER_TIMEZONE})",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def drop_unfollowed_renewal(renewal: Renewal) -> bool:
    """Forget a renewal no server Keiko is in follows under the subscribe's lock, then
    delete its reminder outside it."""
    async with _RENEWALS:
        if await platform_followers("youtube", renewal.youtuber):
            return False

        await delete_reminder_by_id(renewal.reminder_id)

    await to_thread(bot.reminder.delete_reminder, renewal.reminder_id)
    return True


async def record_hub_confirmation(youtuber: str, moment: datetime) -> None:
    """Record that the hub took the youtuber's subscription at `moment`, keeping its
    renewal a while after."""
    await data().keiko_subscriptions.update_subscription_state(
        "youtube", youtuber, {"hub_confirmed_at": moment, "lapse_reported_at": None}
    )
    await data().keiko_schedules.extend_schedule_expiry(
        youtube_renewal_id(youtuber), _kept_until(moment)
    )


async def resubscribe_followed_channels() -> int:
    """Subscribe every followed channel again, giving one without a renewal its renewal;
    how many the hub took."""
    if bot.config.is_dev():
        return 0
    if not bot.config.YOUTUBE_HUB_SECRET:
        logger.warn(
            "youtube subscriptions not renewed — no hub secret is configured",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return 0

    youtubers = await platform_followed("youtube")
    renewed = 0

    for youtuber in youtubers:
        try:
            answer = await to_thread(ask_the_hub, youtuber, "subscribe")
        except Exception as error:
            answer = HubAnswer(HubOutcome.TRY_LATER, f"{type(error).__name__}: {error}")

        try:
            await ensure_renewal(youtuber, answer)
        except Exception as error:
            logger.warn(
                f"youtuber **{youtuber}** still has no renewal "
                f"— {type(error).__name__}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

        if answer.outcome is HubOutcome.DONE:
            await record_hub_confirmation(
                youtuber, reminder_time().astimezone(timezone.utc)
            )
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


async def unfollow_channel(
    youtuber: str, interaction: Optional[discord.Interaction] = None
) -> None:
    """Unsubscribe from the hub a channel no other server follows on any YouTube
    command; the renewal ends on its own."""
    followers = await platform_followers("youtube", youtuber)

    if followers > 1:
        logger.warn(
            f"Youtuber **{youtuber}** is still followed by another server "
            "and stays subscribed",
            interaction=interaction,
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    answer = await to_thread(ask_the_hub, youtuber, "unsubscribe")

    if answer.outcome in (HubOutcome.REFUSED, HubOutcome.TRY_LATER):
        logger.error(
            f"Youtuber **{youtuber}** not unsubscribed from the hub — {answer.detail}; "
            "the subscription ends with its lease once its renewal "
            "finds no server following",
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


async def renewals() -> Mapping[str, str]:
    """The youtuber of every renewal reminder Keiko keeps, by reminder id."""
    return {
        schedule["reminder_id"]: schedules.schedule_subject(schedule)[0]
        for schedule in await data().keiko_schedules.find_schedules_by_kind(
            "youtube_renewal"
        )
    }


async def forget_renewal(reminder_id: str) -> None:
    """Delete a renewal reminder on the reminders API, then its schedule."""
    await to_thread(bot.reminder.delete_reminder, reminder_id)
    await delete_reminder_by_id(reminder_id)


def renew_on_reminder(fired: Reminder) -> None:
    """Renew the hub subscription a renewal reminder fired for."""
    renew_youtube_subscription(fired.id)


def renew_youtube_subscription(reminder_id: Any) -> None:
    """Subscribe the youtuber of a renewal reminder again and move the reminder to the
    next renewal, or to a retry when the hub asks to try later."""
    renewal = wait(find_reminder_by_id(reminder_id))

    if not renewal:
        logger.warn(
            f"youtube reminder {reminder_id} — not one of **Keiko**'s, ignored",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    youtuber = renewal.youtuber

    if wait(drop_unfollowed_renewal(renewal)):
        logger.info(
            f"renewal of **{youtuber}** dropped — no server follows them any more",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return

    logger.info(
        f"renewing subscription — **{youtuber}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    answer = ask_the_hub(youtuber, "subscribe")
    now = reminder_time()

    if answer.outcome is HubOutcome.TRY_LATER:
        retry = now + timedelta(seconds=constants.YOUTUBE_RENEWAL_RETRY_SECONDS)
        logger.warn(
            f"renewal of **{youtuber}** failed — {answer.detail}; "
            f"trying again at {retry:%H:%M} ({REMINDER_TIMEZONE})",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        report_a_coming_lapse(renewal, reminder_id, now)
        bot.reminder.update_reminder(
            renewal.reminder_id, str(retry.date()), time_tz=f"{retry:%H:%M}"
        )
        record_renewal_due(youtuber, retry)
        return

    if answer.outcome is HubOutcome.DONE:
        wait(record_hub_confirmation(youtuber, now.astimezone(timezone.utc)))
    elif answer.outcome in (HubOutcome.NO_CHANNEL, HubOutcome.REFUSED):
        logger.error(
            f"renewal of **{youtuber}** failed — {answer.detail}; "
            "the next renewal tries again",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow="youtube_renewal",
                extra={
                    "reminder_id": reminder_id,
                    "youtuber": youtuber,
                    "status": answer.status,
                },
            ),
        )

    next_renewal = now + timedelta(seconds=constants.YOUTUBE_RENEWAL_INTERVAL_SECONDS)
    bot.reminder.update_reminder(
        renewal.reminder_id, str(next_renewal.date()), time_tz=f"{next_renewal:%H:%M}"
    )
    record_renewal_due(youtuber, next_renewal)
    logger.info(
        f"renewal scheduled for {next_renewal:%Y-%m-%d %H:%M} ({REMINDER_TIMEZONE})",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


def record_renewal_due(youtuber: str, moment: datetime) -> None:
    """Keep on the renewal's schedule when its reminder fires next."""
    due_at = moment.astimezone(timezone.utc)
    wait(data().keiko_schedules.set_schedule_due(youtube_renewal_id(youtuber), due_at))


def report_a_coming_lapse(renewal: Renewal, reminder_id: Any, now: datetime) -> None:
    """Tell the error channel once that a renewal failing for days is about to let its
    lease end."""
    subscriptions = data().keiko_subscriptions
    youtuber = renewal.youtuber
    hub = wait(subscriptions.subscription_state("youtube", youtuber))
    confirmed = as_utc(hub.get("hub_confirmed_at") or renewal.created_at)
    lease = timedelta(seconds=constants.YOUTUBE_HUB_LEASE_SECONDS)
    lapses_at = (confirmed + lease if confirmed else now).astimezone(timezone.utc)
    margin = timedelta(seconds=constants.YOUTUBE_HUB_LEASE_MARGIN_SECONDS)

    if hub.get("lapse_reported_at") or now < lapses_at - margin:
        return

    logger.error(
        f"renewal of **{youtuber}** keeps failing — its hub subscription lapses at "
        f"{lapses_at:%Y-%m-%d %H:%M} UTC",
        log_type=logconstants.COMMAND_ERROR_TYPE,
        context=ErrorContext(
            flow="youtube_renewal",
            extra={
                "reminder_id": reminder_id,
                "youtuber": youtuber,
                "lease_lapses_at": lapses_at.isoformat(),
            },
        ),
    )
    wait(
        subscriptions.update_subscription_state(
            "youtube", youtuber, {"lapse_reported_at": now.astimezone(timezone.utc)}
        )
    )
