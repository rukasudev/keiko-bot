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


class StreamElementsFeature(GenericCogFeature):
    """Commands fetched from a streamer's StreamElements channel."""

    def __init__(self) -> None:
        super().__init__(Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY)

    async def prefetch(
        self, step_key: str, payload: Any, context: OpenContext
    ) -> Mapping[str, Mapping[str, Any]]:
        """The Twitch user behind the typed streamer name."""
        if step_key != "streamer":
            return {}
        name = _typed(payload)
        twitch = cast(Any, app_module).bot.twitch
        user_id = await asyncio.to_thread(twitch.get_user_id_from_login, name)
        return {"twitch": {"user_id": user_id}}

    async def before_setup(
        self,
        document: dict[str, Any],
        answers: Mapping[str, Answer],
        context: CommitContext,
    ) -> dict[str, Any]:
        """The StreamElements channel id of the streamer, outside dev."""
        if cast(Any, app_module).bot.config.is_dev():
            return document
        info = await asyncio.to_thread(
            StreamElementsClient.get_channel_info, str(document.get("streamer", ""))
        )
        return {**document, "channel_id": info["_id"]}


def _typed(payload: Any) -> str:
    if isinstance(payload, Mapping):
        inputs = payload.get("inputs") or [""]
        return str(inputs[0]).lower()
    return str(payload or "").lower()


FEATURE = StreamElementsFeature()
