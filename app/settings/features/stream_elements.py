"""StreamElements commands: the channel id is looked up when the setup commits."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, cast

import app as app_module
from app.constants import Commands
from app.integrations.stream_elements import StreamElementsClient
from app.settings.features.feature import CommitContext, GenericCogFeature, OpenContext
from app.settings.form.form_state import Answer
from app.settings.form.lookups import Lookup


class StreamElementsFeature(GenericCogFeature):
    """Commands fetched from a streamer's StreamElements channel."""

    def __init__(self) -> None:
        super().__init__(Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY)

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
            found["twitch"] = {"user_id": user_id}
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


def _enabled_commands(streamer: str) -> dict[str, Any]:
    info = StreamElementsClient.get_channel_info(streamer)
    channel_id = info["_id"]
    commands: Any = StreamElementsClient.get_chat_commands(channel_id) or []
    count = sum(1 for command in commands if command.get("enabled"))
    return {"channel_id": channel_id, "enabled_commands": count}


async def _with_channel(document: dict[str, Any]) -> dict[str, Any]:
    if cast(Any, app_module).bot.config.is_dev():
        return document
    info = await asyncio.to_thread(
        StreamElementsClient.get_channel_info, str(document.get("streamer", ""))
    )
    return {**document, "channel_id": info["_id"]}


FEATURE = StreamElementsFeature()
