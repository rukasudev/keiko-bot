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
        """The Twitch user behind the typed streamer name."""
        if "twitch" not in lookup.services:
            return {}
        twitch = cast(Any, app_module).bot.twitch
        user_id = await asyncio.to_thread(twitch.get_user_id_from_login, lookup.value)
        return {"twitch": {"user_id": user_id}}

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


async def _with_channel(document: dict[str, Any]) -> dict[str, Any]:
    if cast(Any, app_module).bot.config.is_dev():
        return document
    info = await asyncio.to_thread(
        StreamElementsClient.get_channel_info, str(document.get("streamer", ""))
    )
    return {**document, "channel_id": info["_id"]}


FEATURE = StreamElementsFeature()
