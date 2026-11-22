"""Kick lives: a live announced in every server that follows its streamer, edited
back online when it restarts within the hour and marked ended with how long it ran;
one Kick subscription per followed streamer, kept by a daily resync."""

from datetime import datetime, timezone
from functools import partial
from http import HTTPStatus
from typing import Any, Dict, List, Mapping, Optional, Sequence

import discord
from dateutil import parser

from app import bot, logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import lives
from app.core.commands import WebhookEvent, WebhookReply
from app.core.threads import to_thread
from app.core.utils import ml
from app.data import data
from app.data.collection import Document
from app.exceptions import ErrorContext
from app.forms import item_values
from app.forms.features.subscriptions import (
    Account,
    SubscriptionFeature,
    platform_followed,
)
from app.integrations.kick import KickAPIError
from app.ui.embed import announcement_embed

LIVES = lives.LiveNotices(
    key="notifications_kick",
    item_key="streamer",
    platform="kick",
    flow="kick_notification",
    claim_seconds=constants.KICK_NOTIFIED_LIVE_TTL_SECONDS,
    live_link="https://kick.com/{name}",
    link_variable="stream_link",
)


async def count_announcers(streamer: str) -> int:
    """How many servers announce the streamer's lives while notifications are on."""
    return await data().guild_cogs.count_cogs_by_option(
        "notifications_kick",
        "notifications.values.streamer.value",
        streamer,
        enabled_only=True,
    )


async def count_servers_following(streamer: str) -> int:
    """How many servers Keiko is still in follow the streamer's lives, paused or not."""
    following = [
        str(document.get("guild_id"))
        for document in await following_guilds(streamer, enabled_only=False)
    ]
    return len(following) - await data().guild_settings.count_absent(following)


async def following_guilds(streamer: str, enabled_only: bool = True) -> List[Document]:
    """The live notices of every server that follows the streamer, only those on
    unless told otherwise."""
    return list(
        await data().guild_cogs.find_cogs_by_option(
            "notifications_kick",
            "notifications.values.streamer.value",
            streamer,
            enabled_only=enabled_only,
        )
    )


def moment(written: Any) -> Optional[datetime]:
    """A time Kick wrote, in UTC, or None when it wrote none."""
    if not written:
        return None

    try:
        parsed: datetime = parser.isoparse(str(written))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def notify_live(event: WebhookEvent) -> WebhookReply:
    """Hand to the bot the live that started or ended on a streamer's channel,
    naming the streamer while the request still holds its message."""
    from app.integrations.http import schedule_webhook_job

    status = event.name.removeprefix("kick.stream.")
    logger.info(
        f"kick {status} — **{event.subject}**", log_type=logconstants.COMMAND_INFO_TYPE
    )
    work = go_online if status == "online" else go_offline
    schedule_webhook_job(
        work(event.subject, event.payload), f"kick {status} — {event.subject}"
    )
    return WebhookReply(200, "Webhook processed")


async def go_online(streamer: str, payload: Mapping[str, Any]) -> None:
    """Announce a streamer's new live everywhere it is followed, its channel read for
    the picture and the category, or mark its notices online again."""
    await bot.wait_until_ready()
    context = ErrorContext(
        flow=LIVES.flow, extra={"streamer_name": streamer, "event_type": "online"}
    )

    try:
        channel = await read_channel(streamer)
        written = str(payload.get("started_at") or "")
        started_at = moment(written) or datetime.now(timezone.utc)
        await lives.go_live(
            LIVES,
            live_of(streamer, payload, channel),
            started_at,
            constants.REDIS_KICK_LIVE_CLAIM.format(
                streamer=streamer, started_at=written or started_at.isoformat()
            ),
            partial(following_guilds, streamer),
        )
    except Exception as error:
        logger.error(
            f"Failed to handle kick live: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def go_offline(streamer: str, payload: Mapping[str, Any]) -> None:
    """Mark every notice of the streamer's live ended, with how long it ran from the
    start and the end Kick wrote, or since the start Keiko recorded."""
    await bot.wait_until_ready()
    context = ErrorContext(
        flow=LIVES.flow, extra={"streamer_name": streamer, "event_type": "offline"}
    )

    try:
        followers = await following_guilds(streamer, enabled_only=False)
        started = moment(payload.get("started_at"))
        ended = moment(payload.get("ended_at"))
        if started and ended and ended >= started:
            await lives.mark_ended(LIVES, streamer, followers, ended - started)
        else:
            await lives.end_live(LIVES, streamer, followers)
    except Exception as error:
        logger.error(
            f"Failed to handle kick live end: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def read_channel(streamer: str) -> Mapping[str, Any]:
    """The streamer's channel as Kick shows it now; empty when Kick does not answer,
    so the live is still announced from what its event says."""
    try:
        found = await to_thread(bot.kick.channels, [streamer])
    except KickAPIError as error:
        logger.warn(
            f"kick channel **{streamer}** not read — {error}; its live is announced "
            "without its picture and category",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return {}
    return dict(found.get(streamer) or {})


def live_of(
    streamer: str, payload: Mapping[str, Any], channel: Mapping[str, Any]
) -> lives.Live:
    """A streamer's live as its notice draws it: the embed in a server's language and
    where it is watched."""
    return lives.Live(
        name=streamer,
        embed=partial(create_live_embed, streamer, payload, channel),
        link=LIVES.link(streamer),
    )


def create_live_embed(
    streamer: str,
    payload: Mapping[str, Any],
    channel: Mapping[str, Any],
    locale: str = "en-us",
) -> discord.Embed:
    """The embed of a live announcement: its title, picture, category and streamer."""
    stream = channel.get("stream") or {}
    broadcaster = payload.get("broadcaster") or {}
    description = str(channel.get("channel_description") or "").split("\n")[0]

    return announcement_embed(
        channel.get("stream_title") or payload.get("title"),
        description or None,
        lives.stream_status("online", locale),
        stream.get("thumbnail") or None,
        copy="notifications_kick.ui.announcement",
        locale=locale,
        fields={
            "category": (channel.get("category") or {}).get("name"),
            "streamer": broadcaster.get("username") or streamer,
        },
        url=LIVES.link(streamer),
        colour=discord.Colour(constants.KICK_COLOR),
        thumbnail=str(broadcaster.get("profile_picture") or ""),
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
        found = await to_thread(bot.kick.channels, [streamer])
    except KickAPIError as error:
        logger.warn(
            f"Could not draw the kick preview of {streamer}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return None

    channel = dict(found.get(streamer) or {})
    stream = channel.get("stream") or {}
    if not stream.get("is_live"):
        samples = "notifications_kick.ui.announcement.samples"
        channel = {
            **channel,
            "stream_title": ml(f"{samples}.title", locale),
            "category": {"name": ml(f"{samples}.category", locale)},
            "stream": {"thumbnail": channel.get("banner_picture")},
        }
    described = {
        **channel,
        "channel_description": ml(
            "notifications_kick.ui.announcement.samples.description", locale
        ),
    }
    return create_live_embed(streamer, {}, described, locale)


async def broadcaster_of(streamer: str) -> int:
    """The id Kick keeps behind a streamer's channel; raises when Kick has none."""
    found = await to_thread(bot.kick.channels, [streamer])
    user_id = (found.get(streamer) or {}).get("broadcaster_user_id")
    if user_id is None:
        raise KickAPIError(f"Kick has no channel named {streamer}")
    return int(user_id)


async def held_by_kick(subscription_id: Any, broadcaster_user_id: int) -> Optional[str]:
    """The id of a live subscription Kick holds for a broadcaster, the one Keiko kept
    when Kick still holds it; None when Kick holds none."""
    held = [
        subscription.id
        for subscription in await to_thread(bot.kick.subscriptions)
        if subscription.broadcaster_user_id == broadcaster_user_id
        and subscription.event == constants.KICK_LIVE_STATUS_EVENT
    ]
    if subscription_id and str(subscription_id) in held:
        return str(subscription_id)
    return held[0] if held else None


async def subscribe_streamer(
    interaction: Optional[discord.Interaction], response: Mapping[str, Any]
) -> None:
    """Ask Kick to tell Keiko when a streamer goes live and when the live ends, unless
    Kick still holds a live subscription for it, whose id Keiko then keeps; a refusal
    is left to the daily resync."""
    streamer = item_values(response)["streamer"]
    subscriptions = data().keiko_subscriptions
    state = await subscriptions.subscription_state("kick", streamer)
    kept = state.get("subscription_id")

    try:
        known = state.get("broadcaster_user_id")
        user_id = int(known) if known is not None else await broadcaster_of(streamer)
        held = await held_by_kick(kept, user_id)
        if held is not None and held == kept:
            logger.info(
                f"Kick streamer {streamer} already subscribed",
                interaction=interaction,
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
            return
        subscription_id = held or await to_thread(
            bot.kick.subscribe_live_status, user_id
        )
    except KickAPIError as error:
        logger.error(
            f"Kick streamer {streamer} not subscribed — {error}; the daily resync "
            "tries again",
            interaction=interaction,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext(
                flow="kick_subscribe",
                extra={"streamer_name": streamer, "status": error.status},
            ),
        )
        return

    await subscriptions.update_subscription_state(
        "kick",
        streamer,
        {"broadcaster_user_id": user_id, "subscription_id": subscription_id},
    )
    logger.info(
        f"Kick streamer {streamer} subscribed",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def unsubscribe_streamer(
    interaction: Optional[discord.Interaction], notification: Mapping[str, Any]
) -> None:
    """Drop the streamer's Kick subscription once no server follows it; one Kick
    refuses is left to the daily resync."""
    streamer = item_values(notification)["streamer"]
    subscriptions = data().keiko_subscriptions
    state = await subscriptions.subscription_state("kick", streamer)
    subscription_id = state.get("subscription_id")
    if not subscription_id:
        return

    try:
        await to_thread(bot.kick.delete_subscription, str(subscription_id))
    except KickAPIError as error:
        if error.status != HTTPStatus.NOT_FOUND:
            logger.error(
                f"Kick streamer {streamer} not unsubscribed — {error}; the daily "
                "resync deletes it",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow="kick_unsubscribe",
                    extra={"streamer_name": streamer, "status": error.status},
                ),
            )
            return

    await subscriptions.update_subscription_state(
        "kick", streamer, {"subscription_id": None}
    )
    logger.info(
        f"Kick streamer {streamer} unsubscribed",
        interaction=interaction,
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def find_streamer(slug: str) -> Account:
    """The Kick channel behind a typed name; Kick's channels carry no picture."""
    found = await to_thread(bot.kick.channels, [slug])
    user_id = (found.get(slug.lower()) or {}).get("broadcaster_user_id")
    return Account(str(user_id) if user_id is not None else None, "")


async def broadcasters(streamers: Sequence[str]) -> Dict[int, str]:
    """The streamer behind each broadcaster id: the id Keiko kept, else read from
    Kick and kept."""
    subscriptions = data().keiko_subscriptions
    found: Dict[int, str] = {}
    unknown: List[str] = []
    for streamer in streamers:
        state = await subscriptions.subscription_state("kick", streamer)
        if state.get("broadcaster_user_id") is None:
            unknown.append(streamer)
        else:
            found[int(state["broadcaster_user_id"])] = streamer

    channels = await to_thread(bot.kick.channels, unknown) if unknown else {}
    for streamer, channel in channels.items():
        user_id = channel.get("broadcaster_user_id")
        if user_id is None:
            continue
        await subscriptions.update_subscription_state(
            "kick", streamer, {"broadcaster_user_id": int(user_id)}
        )
        found[int(user_id)] = streamer
    return found


async def resync_subscriptions() -> None:
    """Leave Kick one live subscription per streamer a server follows and no other,
    keeping the id of each one kept or created and forgetting each one deleted;
    outside dev."""
    if bot.config.is_dev():
        return

    try:
        followed = await broadcasters(await platform_followed(LIVES.platform))
        changed = await to_thread(bot.kick.resync, set(followed))
    except KickAPIError as error:
        logger.warn(
            f"kick resync skipped — {error}; it runs again in "
            f"{constants.KICK_RESYNC_HOURS} hours",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    if changed.deleted:
        await data().keiko_subscriptions.clear_subscription_ids(
            "kick", [subscription.id for subscription in changed.deleted]
        )
    for subscription in (*changed.kept, *changed.created):
        await data().keiko_subscriptions.update_subscription_state(
            "kick",
            followed[subscription.broadcaster_user_id],
            {"subscription_id": subscription.id},
        )
    logger.info(
        f"kick resync — {len(followed)} followed, {len(changed.kept)} kept, "
        f"{len(changed.created)} created, {len(changed.deleted)} deleted",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


class KickFeature(SubscriptionFeature):
    """Kick streamers to announce when they go live."""

    item_key = "streamer"
    external = "kick"
    lookup_result = "user_id"

    def __init__(self) -> None:
        super().__init__("notifications_kick")
        self.subscribe = subscribe_streamer
        self.unsubscribe = unsubscribe_streamer
        self.followers = count_servers_following
        self.announcers = count_announcers
        self.find_account = find_streamer
        self.preview_sender = send_notification_preview


FEATURE = KickFeature()
