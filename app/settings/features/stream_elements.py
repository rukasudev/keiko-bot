"""StreamElements commands: the channel id is looked up when the setup commits."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast

import app as app_module
from app.constants import Commands
from app.constants import ViewConstants as view_constants
from app.integrations.stream_elements import StreamElementsClient
from app.services.stream_elements import (
    get_commands_in_cache_or_populate,
    send_commands_view,
)
from app.settings.features.feature import (
    AsideAction,
    CommitContext,
    GenericCogFeature,
    OpenContext,
    Opened,
)
from app.settings.form.components import Button
from app.settings.form.copy import text
from app.settings.form.form_state import Answer
from app.settings.form.lookups import Lookup
from app.settings.form.responses.responses import unwrap


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
            enabled=opened.enabled,
            info=await _loaded(opened.document, context),
            info_title=_copy("loaded.title", context.locale),
            extra_buttons=self.extra_buttons(context),
        )

    def extra_buttons(self, context: OpenContext) -> tuple[Button, ...]:
        """The command list button."""
        locale = context.locale
        return (
            Button(
                _copy("commands.button.label", locale),
                "aside:commands",
                "secondary",
                "💡",
                description=_copy("commands.button.desc", locale),
            ),
        )

    def asides(self) -> Mapping[str, AsideAction]:
        """The command list answers with a paginated message of its own."""

        async def commands(interaction: Any, _responses: Any) -> None:
            await send_commands_view(interaction)

        return {"commands": AsideAction(commands, own_response=True)}

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
                    _enabled_commands, lookup.value
                )
            except Exception:
                pass
        return found

    async def before_setup(
        self,
        document: dict[str, Any],
        answers: Mapping[str, Answer],
        context: CommitContext,
    ) -> dict[str, Any]:
        """The StreamElements channel id of the streamer, outside dev."""
        return await _with_channel(document)

    async def before_edit(
        self,
        changes: dict[str, Any],
        answers: Mapping[str, Answer],
        context: CommitContext,
    ) -> dict[str, Any]:
        """A new streamer brings its own StreamElements channel id, outside dev."""
        if "streamer" not in changes:
            return changes
        return await _with_channel(changes)


def _profile_image(streamer: str) -> str:
    try:
        info = cast(Any, app_module).bot.twitch.get_user_info(streamer) or {}
        return str(info.get("profile_image_url") or "")
    except Exception:
        return ""


def _copy(key: str, locale: str) -> str:
    return text(f"commands.commands.commons.stream-elements-manager.{key}", locale)


async def _loaded(document: Mapping[str, Any], context: OpenContext) -> str:
    """How many commands the panel says it answers, or that it could not tell."""
    locale = context.locale
    try:
        commands = await asyncio.to_thread(
            get_commands_in_cache_or_populate,
            str(unwrap(document.get("channel_id")) or ""),
            context.member,
        )
    except Exception:
        commands = None
    if not commands:
        return _copy("loaded.unknown", locale)
    return (
        _copy("loaded.message", locale)
        .replace("$count", str(len(commands)))
        .replace("$streamer", str(unwrap(document.get("streamer")) or ""))
    )


def _enabled_commands(streamer: str) -> dict[str, Any]:
    info = StreamElementsClient.get_channel_info(streamer)
    channel_id = info["_id"]
    commands: Any = StreamElementsClient.get_chat_commands(channel_id) or []
    enabled = [command for command in commands if command.get("enabled")]
    named = [command.get("command") for command in enabled if command.get("command")]
    sample = named[: view_constants.COMMANDS_PREVIEW_LIMIT]
    prefix = str(cast(Any, app_module).bot.config.PREFIX)
    return {
        "channel_id": channel_id,
        "enabled_commands": len(enabled),
        "top_commands": ", ".join(f"`!{name}`" for name in sample),
        "keiko_commands": ", ".join(f"`{prefix}{name}`" for name in sample),
    }


async def _with_channel(document: dict[str, Any]) -> dict[str, Any]:
    if cast(Any, app_module).bot.config.is_dev():
        return document
    info = await asyncio.to_thread(
        StreamElementsClient.get_channel_info, str(document.get("streamer", ""))
    )
    return {**document, "channel_id": info["_id"]}


FEATURE = StreamElementsFeature()
