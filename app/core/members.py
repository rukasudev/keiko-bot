"""A server's member, found without holding the server's whole member list:
discord.py's own cache first, then the members Keiko met in the last minutes,
then Discord's API."""

import threading
from collections import OrderedDict
from time import monotonic
from typing import Any, AsyncIterator, Iterable, Iterator, Optional, Tuple

import discord

from app.constants import Commands

_lock = threading.Lock()
_recent: "OrderedDict[Tuple[int, int], Tuple[float, Any]]" = OrderedDict()


def remember(member: Any) -> None:
    """Keep a member an event carried (author, click, join, pick) for a while."""
    guild = getattr(member, "guild", None)
    if guild is None or getattr(member, "id", None) is None:
        return

    key = (int(guild.id), int(member.id))
    with _lock:
        _recent[key] = (monotonic() + Commands.MEMBERS_RECENT_SECONDS, member)
        _recent.move_to_end(key)
        while len(_recent) > Commands.MEMBERS_RECENT_SIZE:
            _recent.popitem(last=False)


def known(guild: Any, member_id: Any) -> Optional[Any]:
    """The member from discord.py's cache or the recent ones, without a request."""
    if guild is None or not str(member_id).isdigit():
        return None

    cached = guild.get_member(int(member_id))
    if cached is not None:
        return cached

    key = (int(guild.id), int(member_id))
    with _lock:
        held = _recent.get(key)
        if held is None:
            return None
        if monotonic() >= held[0]:
            del _recent[key]
            return None
        _recent.move_to_end(key)
        return held[1]


async def find(guild: Any, member_id: Any) -> Optional[Any]:
    """The member, asked to Discord when no cache has it; None when it left."""
    member = known(guild, member_id)
    if member is not None or guild is None or not str(member_id).isdigit():
        return member

    try:
        member = await guild.fetch_member(int(member_id))
    except discord.NotFound:
        return None

    remember(member)
    return member


async def display_name(guild: Any, member_id: Any) -> Optional[str]:
    """The member's display name, or None when no member is named or it left."""
    member = await find(guild, member_id) if member_id else None
    return str(member.display_name) if member is not None else None


async def every_member(guild: Any) -> AsyncIterator[Any]:
    """Every member of the server, a page at a time from the API, none of them kept."""
    async for member in guild.fetch_members(limit=None):
        yield member


async def one_by_one(members: Iterable[Any]) -> AsyncIterator[Any]:
    """The given members, one at a time, the way `every_member` hands a server's."""
    for member in members:
        yield member


def known_ids(guild: Any) -> Iterator[str]:
    """The ids of the server's members some cache holds now."""
    if guild is None:
        return iter(())
    cached = {str(member.id) for member in getattr(guild, "members", None) or ()}
    with _lock:
        recent = {str(user) for server, user in _recent if server == int(guild.id)}
    return iter(sorted(cached | recent))


def held() -> int:
    """How many members the recent cache holds."""
    with _lock:
        return len(_recent)


def reset() -> None:
    """Forget every recent member, as a fresh process would."""
    with _lock:
        _recent.clear()
