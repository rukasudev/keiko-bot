"""StreamElements commands: the streamer's channel id is saved with the document."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

import app as app_module
from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.exceptions import StreamElementsUnavailable
from app.integrations.stream_elements import StreamElementsClient
from app.services.stream_elements import (
    channel_of,
    find_channel,
    get_commands_in_cache_or_populate,
    keep_channel,
    send_commands_view,
    warn_unavailable,
)
from app.settings.features.feature import (
    AsideAction,
    CommitContext,
    CommitResult,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.settings.form.components import Button
from app.settings.form.copy import text
from app.settings.form.form_state import Answer
from app.settings.form.lookups import Lookup
from app.settings.form.manager import PanelExtras
from app.settings.form.responses.summary import unwrap


class StreamElementsFeature(GenericCogFeature):
    """Commands fetched from a streamer's StreamElements channel."""

    def __init__(self) -> None:
        super().__init__(Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY)

    async def open(self, context: OpenContext) -> Opened:
        """The document, how many commands are loaded, and a way to read them."""
        opened = await super().open(context)
        if opened.document is None:
            return opened
        return Opened(
            document=opened.document,
            panel=PanelExtras(
                info=await _loaded(opened.document, context),
                info_title=_copy("loaded.title", context.locale),
                info_action=_commands_button(context.locale),
                enabled=opened.panel.enabled,
            ),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The statistics, and the command list on a paginated message of its own."""

        async def commands(interaction: Any, _responses: Any) -> None:
            await send_commands_view(interaction)

        return {
            **super().asides(),
            "commands": AsideAction(commands, own_response=True),
        }

    async def prefetch(
        self, lookup: Lookup, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """The streamer's Twitch user and how many StreamElements commands it has."""
        found: dict[str, Mapping[str, Any]] = {}
        if "twitch" in lookup.services:
            twitch = cast(Any, app_module).bot.twitch
            user_id = await asyncio.to_thread(
                twitch.get_user_id_from_login, lookup.value
            )
            image = await asyncio.to_thread(_profile_image, lookup.value)
            found["twitch"] = {"user_id": user_id, "profile_image": image}
        if "stream_elements" in lookup.services:
            try:
                found["stream_elements"] = await asyncio.to_thread(
                    _enabled_commands, lookup.value, context.prefix
                )
            except Exception:
                pass
        return found

    async def commit_setup(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Store the document, then keep its streamer's StreamElements channel id."""
        written = await super().commit_setup(payload, context)
        document = await _with_channel(
            context.guild_id, written.document or {}, payload["answers"]
        )
        return replace(written, document=document)

    async def commit_edit(
        self, payload: Mapping[str, Any], context: CommitContext
    ) -> CommitResult:
        """Merge the edit; a new streamer brings its own StreamElements channel id."""
        written = await super().commit_edit(payload, context)
        document = written.document or {}
        if document.get("streamer") == context.document.get("streamer"):
            return written
        document = await _with_channel(context.guild_id, document, payload["answers"])
        return replace(written, document=document)


def _commands_button(locale: str) -> Button:
    return Button(
        _copy("commands.button.label", locale),
        "aside:commands",
        "secondary",
        "💡",
        description=_copy("commands.button.desc", locale),
    )


def _profile_image(streamer: str) -> str:
    try:
        info = cast(Any, app_module).bot.twitch.get_user_info(streamer) or {}
        return str(info.get("profile_image_url") or "")
    except Exception:
        return ""


def _copy(key: str, locale: str) -> str:
    return text(f"commands.commands.commons.stream-elements-manager.{key}", locale)


async def _loaded(document: Mapping[str, Any], context: OpenContext) -> str:
    """How many commands the panel says it answers, none, or that it could not tell."""
    locale = context.locale
    streamer = str(unwrap(document.get("streamer")) or "")
    try:
        channel_id = await channel_of(
            context.guild_id, streamer, (document.get("state") or {}).get("channel_id")
        )
        commands = await asyncio.to_thread(
            get_commands_in_cache_or_populate, channel_id, context.member
        )
    except StreamElementsUnavailable as unavailable:
        await asyncio.to_thread(warn_unavailable, streamer, unavailable)
        return _copy("loaded.unknown", locale)
    except Exception:
        return _copy("loaded.unknown", locale)
    if not commands:
        return _copy("commands.empty", locale).replace("$streamer", streamer)
    return (
        _copy("loaded.message", locale)
        .replace("$count", str(len(commands)))
        .replace("$streamer", streamer)
    )


def _enabled_commands(streamer: str, prefix: str) -> dict[str, Any]:
    channel_id = find_channel(streamer)
    commands: Any = StreamElementsClient.get_chat_commands(channel_id) or []
    enabled = [command for command in commands if command.get("enabled")]
    named = [command.get("command") for command in enabled if command.get("command")]
    sample = named[: view_constants.COMMANDS_PREVIEW_LIMIT]
    return {
        "channel_id": channel_id,
        "enabled_commands": len(enabled),
        "top_commands": ", ".join(f"`!{name}`" for name in sample),
        "keiko_commands": ", ".join(f"`{prefix}{name}`" for name in sample),
    }


async def _with_channel(
    guild_id: str, document: Mapping[str, Any], answers: Mapping[str, Answer]
) -> dict[str, Any]:
    looked_up = answers.get("stream_elements_channel_id")
    channel_id = looked_up.raw if looked_up is not None else None
    if not channel_id:
        channel_id = await asyncio.to_thread(
            find_channel, str(document.get("streamer", ""))
        )

    await keep_channel(guild_id, str(channel_id))
    state = {**(document.get("state") or {}), "channel_id": str(channel_id)}
    return {**document, "state": state}


FEATURE = StreamElementsFeature()
