"""The live notice every streaming command posts and edits: announced once in every
server that follows the account, back online when the live restarts within the hour,
and marked ended with how long it ran; its messages and its preview."""

import random
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import discord
from pydantic import Field
from redis.exceptions import RedisError

from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.core import analytics, i18n
from app.core.cache import claim_redis_key, release_redis_key
from app.core.commands import GLOBAL
from app.core.conditions import Override
from app.core.facts import facts
from app.core.i18n import CopyBlock, CopyText
from app.core.mentions import mention_of
from app.core.posted import posted_message
from app.core.servers import server_language
from app.core.threads import to_thread
from app.core.trace import Outcome, as_utc, fan_out
from app.core.utils import (
    destination,
    fill_placeholders,
    format_datetime_output,
    split_messages,
    values_of,
)
from app.data import data
from app.forms import item_values, items_of
from app.ui.message_preview import MessagePreviewView

Status = Literal["online", "offline"]
Followers = Sequence[Mapping[str, Any]]
Notice = tuple[Mapping[str, Any], Mapping[str, Any]]
PreviewEmbed = Callable[[str, str], Awaitable[discord.Embed | None]]


class LiveNoticeOverride(Override):
    """The footer a live notice draws while a rule holds, in place of its own."""

    footer: CopyText | None = Field(
        None,
        description="The footer drawn while the rule holds; `$duration` marks how "
        "long the live ran.",
        json_schema_extra={"role": "copy"},
    )


class LiveNoticeCopy(CopyBlock):
    """The footer every streaming command's live notice carries: whether the live is
    on and, once it is over, how long it ran."""

    footer: CopyText = Field(
        description="The footer of a live notice while the live is on.",
        examples=[{"en-us": "🟢 Online", "pt-br": "🟢 Ao vivo"}],
        json_schema_extra={"role": "copy"},
    )
    overrides: tuple[LiveNoticeOverride, ...] = Field(
        (),
        description="The footer drawn while a rule on the live's facts holds (the "
        "live over, with how long it ran once known), the first item whose rule "
        "holds winning.",
        json_schema_extra={"role": "conditions"},
    )


@dataclass(frozen=True)
class LiveNotices:
    """Where one streaming command keeps its live notices: its key, the item key that
    names the followed account (and the variable its messages name it with), the
    platform its last live is kept under, the flow its logs carry, how long a live
    stays claimed, where an account's lives are watched (`{name}` the account) and
    the variable its messages name that link with."""

    key: str
    item_key: str
    platform: str
    flow: str
    claim_seconds: int
    live_link: str
    link_variable: str

    def link(self, name: str) -> str:
        """Where the lives of the account `name` are watched."""
        return self.live_link.format(name=name)


@dataclass(frozen=True)
class Live:
    """One live as its command read it: the account, the embed of its notice in a
    language, where it is watched and the subject its notice is saved under, the
    account unless given."""

    name: str
    embed: Callable[[str], discord.Embed]
    link: str
    subject: str = ""

    @property
    def saved_as(self) -> str:
        """The subject the notice of this live is saved under in `guild.messages`."""
        return self.subject or self.name


def claim_the_live(claim: str, seconds: int, name: str) -> bool:
    """Whether this delivery of a live announces it; True when Redis cannot say."""
    try:
        return claim_redis_key(claim, seconds)
    except RedisError as error:
        logger.warn(
            f"The live of **{name}** is announced without its claim — Redis: "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return True


def give_back_the_live(claim: str, name: str) -> None:
    """Free the claim of a live this delivery could not announce, for the next one."""
    try:
        release_redis_key(claim)
    except RedisError as error:
        logger.warn(
            f"The claim of the live of **{name}** stays until it expires "
            f"— Redis: {type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


async def last_live_of(platform: str, name: str) -> datetime | None:
    """When the account's last announced live started, as Keiko recorded it."""
    state = await data().keiko_subscriptions.subscription_state(platform, name)
    return as_utc(state.get("last_live_started_at"))


def is_more_than_one_hour(start_time: datetime, last_time: datetime) -> bool:
    """Whether a live started over an hour after the last one, so it is a new live."""
    return (start_time - last_time).total_seconds() > 3600


async def go_live(
    notices: LiveNotices,
    live: Live,
    started_at: datetime,
    claim: str,
    followers: Callable[[], Awaitable[Followers]],
) -> None:
    """Announce a new live everywhere the account is followed, once, or mark its
    notices online again when it starts within the hour of the last one."""
    last_live = await last_live_of(notices.platform, live.name)
    if last_live and not is_more_than_one_hour(started_at, last_live):
        await edit_notifications(notices, live.name, await followers(), "online")
        return

    if not await to_thread(claim_the_live, claim, notices.claim_seconds, live.name):
        logger.info(
            f"The live of **{live.name}** started at {started_at} "
            "was already announced",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )
        return

    try:
        followed = await followers()
    except Exception:
        await to_thread(give_back_the_live, claim, live.name)
        raise

    await send_notifications(notices, live, followed)
    await data().keiko_subscriptions.update_subscription_state(
        notices.platform, live.name, {"last_live_started_at": started_at}
    )


async def send_notifications(
    notices: LiveNotices, live: Live, followers: Followers
) -> int:
    """Post the live in every server that follows the account, each one on its own;
    how many servers got it."""
    logger.info(
        f"Sending notifications for **{live.name}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    served = await fan_out(
        notices.flow,
        (
            (guild_data.get("guild_id"), announce_live(notices, live, guild_data, item))
            for guild_data, item in notices_of(notices, followers, live.name)
        ),
        **_named(notices, live.name),
    )
    count = sum(1 for share in served if share.outcome is Outcome.DELIVERED)
    logger.info(
        f"Notifications sent for **{live.name}** in {count} guilds",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    return count


async def announce_live(
    notices: LiveNotices,
    live: Live,
    guild_data: Mapping[str, Any],
    notification: Mapping[str, Any],
) -> None:
    """Post a live in the channel one server chose for it, in that server's language,
    pinging only who the notice mentions, and keep the message to edit later."""
    notice = item_values(notification)
    mention = mention_of(notice.get("mention"), notice.get("mention_roles"))
    guild, channel = destination(guild_data.get("guild_id"), notice.get("channel"))
    embed = live.embed(await server_language(guild.id))

    try:
        message = await channel.send(
            content=mention.before(one_message(notices, notice, live.name, live.link)),
            embed=embed,
            allowed_mentions=mention.allowed,
        )
    except discord.Forbidden as error:
        analytics.record_permission_failure(guild.id, notices.key, error)
        raise

    await data().guild_messages.save_message(
        guild.id, channel.id, notices.key, live.saved_as, message.id
    )
    analytics.record_value(guild.id, notices.key)


async def edit_notifications(
    notices: LiveNotices, name: str, followers: Followers, status: Status
) -> int:
    """Write the live's status on every notice the account's live has."""
    logger.info(
        f"Editing notifications to {status} for **{name}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    count = await update_notification_status(notices, followers, name, status)
    logger.info(
        f"Notifications edited to {status} for **{name}** in {count} guilds",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    return count


async def mark_ended(
    notices: LiveNotices,
    name: str,
    followers: Followers,
    duration: timedelta | None,
    subject: str | None = None,
) -> int:
    """Mark every notice of the account's live offline, with how long it ran when
    known; how many notices were edited."""
    logger.info(
        f"Editing notifications to offline for **{name}**",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    count = await update_notification_status(
        notices,
        followers,
        name,
        "offline",
        format_datetime_output(duration) if duration else None,
        subject,
    )
    logger.info(
        f"Notifications edited to offline for **{name}** in {count} guilds",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    return count


async def end_live(notices: LiveNotices, name: str, followers: Followers) -> int:
    """Mark every notice of the account's live offline, with how long it ran since the
    start of the live Keiko recorded last."""
    last_live = await last_live_of(notices.platform, name)
    duration = datetime.now(timezone.utc) - last_live if last_live else None
    return await mark_ended(notices, name, followers, duration)


async def update_notification_status(
    notices: LiveNotices,
    followers: Followers,
    name: str,
    status: Status,
    duration: str | None = None,
    subject: str | None = None,
) -> int:
    """Write the live's status on every notice it has, each server on its own; how
    many notices were edited."""
    served = await fan_out(
        notices.flow,
        (
            (
                guild_data.get("guild_id"),
                show_status(
                    notices, guild_data, item, subject or name, status, duration
                ),
            )
            for guild_data, item in notices_of(notices, followers, name)
        ),
        **_named(notices, name),
    )
    return sum(1 for share in served if share.value)


async def show_status(
    notices: LiveNotices,
    guild_data: Mapping[str, Any],
    notification: Mapping[str, Any],
    subject: str,
    status: Status,
    duration: str | None,
) -> bool:
    """Write the status on the notice one server got; False when that notice is gone."""
    guild, channel = destination(
        guild_data.get("guild_id"), item_values(notification).get("channel")
    )
    message = await posted_message(notices.key, guild, channel, subject)
    if not message:
        return False

    locale = await server_language(guild.id)
    footer = stream_status(status, locale, duration)
    message.embeds[0].set_footer(text=f"• {footer}")
    await message.edit(embed=message.embeds[0])
    return True


def notices_of(notices: LiveNotices, followers: Followers, name: str) -> list[Notice]:
    """Every notice a server set for the account, with the server's document."""
    return [
        (guild_data, notification)
        for guild_data in followers
        for notification in items_of(guild_data, constants.NOTIFICATIONS_KEY)
        if str(item_values(notification).get(notices.item_key) or "").lower() == name
    ]


def stream_status(status: Status, locale: str, duration: str | None = None) -> str:
    """The footer of a live notice: whether it is on and, once over, how long it ran."""
    return i18n.resolved_texts(
        "commands.live_notice",
        facts(GLOBAL, live_status=status, duration=duration or ""),
        locale,
    )["footer"]


def notice_message(notices: LiveNotices, message: str, name: str, link: str) -> str:
    """One message of a notice with the account and the live link filled in, the link
    added unless the message already writes it."""
    written = re.sub(r"^https?://(www\.)?", "", link)
    linked = bool(name) and re.search(
        rf"{re.escape(written)}(?![\w-])", message, flags=re.IGNORECASE
    )
    return fill_placeholders(
        message,
        {notices.item_key: name, notices.link_variable: link},
        required=None if linked else notices.link_variable,
    )


def one_message(
    notices: LiveNotices, notice: Mapping[str, Any], name: str, link: str
) -> str:
    """One of the notice's messages, picked at random, filled for this live."""
    messages = split_messages(notice.get("notification_messages"))
    return notice_message(notices, random.choice(messages or [""]), name, link)


async def send_preview(
    notices: LiveNotices,
    interaction: discord.Interaction,
    responses: Sequence[Mapping[str, Any]],
    embed: PreviewEmbed,
) -> None:
    """The live notice as it will arrive, one written message per click, its mention
    written but pinging nobody, under the embed `embed` draws for the account."""
    values = values_of([dict(response) for response in responses])
    name = str(values.get(notices.item_key) or "")
    link = notices.link(name)
    mention = mention_of(values.get("mention"), values.get("mention_roles"))
    texts = [
        mention.before(notice_message(notices, message, name, link)) or ""
        for message in split_messages(values.get("notification_messages"))
    ]
    drawn = await embed(name, str(interaction.locale))
    await MessagePreviewView(texts, interaction.locale, drawn).send(interaction)


def _named(notices: LiveNotices, name: str) -> dict[str, Any]:
    return {notices.item_key: name}
