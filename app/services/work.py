"""The context every listener, webhook job and notice runs its work in.

One unit of work is one trace. A unit that serves several servers serves each on its
own: a server Keiko cannot reach (it left, the channel is gone, Discord refuses) is a
warning, any other failure an error with its context, and neither stops the next
server. Each server is spoken to in its language. Reference: docs/analytics.md
"""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, AsyncIterator, Awaitable, Iterable, List, Optional, Tuple

import discord

from app import logger
from app.constants import LogTypes as logconstants
from app.exceptions import DestinationNotFound, ErrorContext
from app.services.trace import trace_scope
from app.services.utils import parse_locale


class Outcome(Enum):
    """How one server's share of a unit of work went."""

    DELIVERED = "delivered"
    UNREACHABLE = "unreachable"
    FAILED = "failed"


@dataclass(frozen=True)
class Served:
    """One server's share of a unit of work: which server, how it went, what it gave back."""

    guild_id: str
    outcome: Outcome
    value: Any = None


def guild_locale(guild: Any, saved: Optional[str] = None) -> str:
    """The language Keiko speaks in a server: the one its settings saved, else the server's."""
    return parse_locale(saved or getattr(guild, "preferred_locale", None) or "en-us")


def destination(guild_id: Any, channel_id: Any) -> Tuple[Any, Any]:
    """The server and the channel a delivery goes to, or DestinationNotFound for either."""
    from app import bot

    guild = bot.get_guild(int(guild_id))
    if guild is None:
        raise DestinationNotFound(f"guild {guild_id} not found")

    channel = guild.get_channel(int(channel_id or 0))
    if channel is None:
        raise DestinationNotFound(f"channel {channel_id} of guild {guild_id} not found")
    return guild, channel


async def serve(
    share: Awaitable[Any], context: ErrorContext, subject: Optional[str] = None
) -> Served:
    """Await one server's share on its own: a failure is logged with `context`, never raised."""
    subject = subject or context.flow

    try:
        value = await share
    except (discord.Forbidden, DestinationNotFound) as error:
        logger.warn(
            f"{subject} could not reach guild {context.guild_id}: "
            f"{type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return Served(str(context.guild_id), Outcome.UNREACHABLE)
    except Exception as error:
        logger.error(
            f"{subject} failed in guild {context.guild_id}: {type(error).__name__}: {error}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=error,
        )
        return Served(str(context.guild_id), Outcome.FAILED)
    return Served(str(context.guild_id), Outcome.DELIVERED, value)


async def fan_out(
    flow: str, shares: Iterable[Tuple[Any, Awaitable[Any]]], **extra: Any
) -> List[Served]:
    """Serve every server at once, each on its own, and say how each one went."""
    return list(await asyncio.gather(*(
        serve(share, ErrorContext(flow=flow, guild_id=str(guild_id), extra=dict(extra)))
        for guild_id, share in shares
    )))


class GuildWork:
    """A listener's unit of work for one server, its parts each on its own."""

    def __init__(self, context: ErrorContext) -> None:
        self.context = context

    async def run(self, part: str, coroutine: Awaitable[Any]) -> Served:
        """Run one part; its failure is logged with the listener's context and the next
        part still runs."""
        context = replace(self.context, extra={**self.context.extra, "part": part})
        return await serve(coroutine, context, subject=f"{self.context.flow} ({part})")


@asynccontextmanager
async def listener(
    flow: str,
    guild_id: Any,
    *,
    user_id: Any = None,
    channel_id: Any = None,
    **extra: Any,
) -> AsyncIterator[GuildWork]:
    """The unit of work a listener runs for one server, posted only when a part failed or
    it reported an event."""
    context = ErrorContext(
        flow=flow,
        guild_id=str(guild_id) if guild_id else None,
        user_id=str(user_id) if user_id else None,
        channel_id=str(channel_id) if channel_id else None,
        extra=extra,
    )
    async with trace_scope(
        flow,
        guild_id=guild_id,
        user_id=user_id,
        source="internal",
        silent_when_clean=True,
    ):
        yield GuildWork(context)
