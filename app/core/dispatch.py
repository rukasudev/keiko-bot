"""Runs the commands' hooks for one event, each hook a part labelled by its key."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import replace
from typing import Any

from app.core.commands import (
    GuildJoined,
    MemberJoined,
    MessageEdited,
    MessageSent,
    Reminder,
    WebhookEvent,
    WebhookReply,
    dynamic_item_hooks,
    edit_hooks,
    guild_join_hooks,
    member_hooks,
    message_hooks,
    prefix_hooks,
    reminder_handlers,
    view_hooks,
    webhook_handlers,
)
from app.core.trace import GuildWork, Outcome


async def member_joined(work: GuildWork, context: MemberJoined) -> None:
    """Run every member hook, the ones that change the member before the announcers."""
    for key, hook in member_hooks():
        await work.run(key, hook(context))


async def message_sent(
    work: GuildWork, context: MessageSent, prefix: Awaitable[str] | None = None
) -> None:
    """Let the first prefix hook that answers or fails end the prefix chain, then run
    every message hook; a prefix that could not be read ends the chain unstarted."""
    message = context
    if prefix is not None:
        served = await work.run("prefix", prefix)
        if served.outcome is Outcome.DELIVERED:
            message = replace(context, prefix=str(served.value))
        else:
            message = replace(context, prefix_handled=True)

    if message.prefix and message.content.startswith(message.prefix):
        for key, answer in prefix_hooks():
            answered = await work.run(key, answer(message))
            if answered.outcome is not Outcome.DELIVERED or answered.value is True:
                message = replace(message, prefix_handled=True)
                break

    for key, hook in message_hooks():
        await work.run(key, hook(message))


async def message_edited(work: GuildWork, context: MessageEdited) -> None:
    """Run every edit hook on an edited message."""
    for key, hook in edit_hooks():
        await work.run(key, hook(context))


async def guild_joined(work: GuildWork, context: GuildJoined) -> None:
    """Run every hook of a server that added Keiko."""
    for key, hook in guild_join_hooks():
        await work.run(key, hook(context))


def persistent_views() -> tuple[Any, ...]:
    """Every view Keiko keeps listening to after a restart."""
    return tuple(view for _key, hook in view_hooks() for view in hook())


def dynamic_items() -> tuple[type[Any], ...]:
    """Every item Keiko routes by its custom id alone, whatever message it sits on."""
    return tuple(item for _key, hook in dynamic_item_hooks() for item in hook())


def reminder(fired: Reminder) -> bool:
    """Hand a fired reminder to the command owning its title; False when none does."""
    handler = reminder_handlers().get(fired.title)
    if handler is None:
        return False

    handler(fired)
    return True


def webhook(event: WebhookEvent) -> WebhookReply | None:
    """Hand a posted event to the command that registered it; None when none did."""
    handler = webhook_handlers().get(event.name)
    if handler is None:
        return None

    return handler(event)
