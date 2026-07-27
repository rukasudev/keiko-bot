"""Strict fakes for the discord.Interaction surface the form engine uses.

One FakeInteraction is minted per user action, exactly like Discord. The
response object is a state machine: violations of Discord's ordering rules
(second initial response, followup before a response, editing a deleted
message) raise HarnessProtocolError instead of passing silently.
"""
from types import SimpleNamespace
from typing import Any, Dict, Optional

import discord

from tests.behavioral.harness.errors import HarnessProtocolError
from tests.behavioral.harness.message_store import FakeMessage, MessageStore
from tests.behavioral.harness.transcript import format_transcript


class FakeResponse:
    def __init__(self, interaction: "FakeInteraction"):
        self._interaction = interaction
        self._done = False

    def is_done(self) -> bool:
        return self._done

    def _mark(self, api: str) -> None:
        if self._done:
            raise HarnessProtocolError(
                f"{api}: interaction already responded to (Discord would 40060)",
                self._interaction._transcript(),
            )
        self._done = True

    async def send_message(self, content=None, *, embed=None, embeds=None, view=None,
                           ephemeral=False, delete_after=None, **kwargs):
        self._mark("response.send_message")
        all_embeds = list(embeds) if embeds else ([embed] if embed else [])
        store = self._interaction.store
        message = store.create(all_embeds, view, ephemeral)
        self._interaction._original_response = message
        store.record("send", message=message.id, content=content, embeds=all_embeds,
                     view=view, ephemeral=ephemeral, delete_after=delete_after)

    async def defer(self, *, ephemeral=False, thinking=False):
        self._mark("response.defer")
        self._interaction.store.record("defer", ephemeral=ephemeral, thinking=thinking)

    async def edit_message(self, *, content=None, embed=None, view=None, **kwargs):
        self._mark("response.edit_message")
        message = self._interaction.message
        if message is None:
            raise HarnessProtocolError(
                "response.edit_message on an interaction without a component message",
                self._interaction._transcript(),
            )
        message.apply_edit(embed=embed, view=view)
        self._interaction.store.record(
            "edit", message=message.id, embeds=message.embeds, view=message.view,
            ephemeral=message.ephemeral,
        )

    async def send_modal(self, modal: discord.ui.Modal):
        self._mark("response.send_modal")
        self._interaction.store.pending_modal = modal
        self._interaction.store.record("modal", modal=modal)


class FakeFollowup:
    def __init__(self, interaction: "FakeInteraction"):
        self._interaction = interaction

    def _require_done(self, api: str) -> None:
        if not self._interaction.response.is_done():
            raise HarnessProtocolError(
                f"{api} before any initial response (Discord requires one)",
                self._interaction._transcript(),
            )

    async def send(self, content=None, *, embed=None, embeds=None, view=None,
                   ephemeral=False, **kwargs) -> FakeMessage:
        self._require_done("followup.send")
        all_embeds = list(embeds) if embeds else ([embed] if embed else [])
        store = self._interaction.store
        message = store.create(all_embeds, view, ephemeral)
        store.record("followup_send", message=message.id, content=content,
                     embeds=all_embeds, view=view, ephemeral=ephemeral)
        return message

    async def edit_message(self, message_id: int, *, content=None, embed=None,
                           view=None, **kwargs) -> FakeMessage:
        store = self._interaction.store
        message = store.messages.get(message_id)
        if message is None or message.deleted:
            raise HarnessProtocolError(
                f"followup.edit_message on missing/deleted message {message_id}",
                self._interaction._transcript(),
            )
        message.apply_edit(embed=embed, view=view)
        store.record("followup_edit", message=message.id, embeds=message.embeds,
                     view=message.view, ephemeral=message.ephemeral)
        return message

    async def delete_message(self, message_id: int) -> None:
        store = self._interaction.store
        message = store.messages.get(message_id)
        if message is None:
            raise HarnessProtocolError(
                f"followup.delete_message on unknown message {message_id}",
                self._interaction._transcript(),
            )
        message.deleted = True
        store.record("delete", message=message.id)


class FakeInteraction:
    """Covers the attribute surface the form engine touches (verified by audit)."""

    def __init__(self, store: MessageStore, *, guild, user, locale: discord.Locale,
                 message: Optional[FakeMessage] = None,
                 data: Optional[Dict[str, Any]] = None):
        self.store = store
        self.guild = guild
        self.guild_id = str(guild.id)
        self.user = user
        self.channel = guild.text_channels[0] if guild.text_channels else None
        self.locale = locale  # engine reassigns this; keep writable
        self.message = message
        self.data = data or {}
        self.client = SimpleNamespace(app_commands=[])
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)
        self._original_response: Optional[FakeMessage] = None

    def _transcript(self) -> str:
        return format_transcript(self.store.events)

    def _original_target(self) -> FakeMessage:
        target = self._original_response or self.message
        if target is None:
            raise HarnessProtocolError(
                "edit/delete_original_response without an original response or "
                "component message",
                self._transcript(),
            )
        return target

    async def edit_original_response(self, *, content=None, embed=None, view=None,
                                     **kwargs) -> FakeMessage:
        message = self._original_target()
        if message.deleted:
            raise discord.NotFound(
                SimpleNamespace(status=404), {"code": 10008, "message": "Unknown Message"}
            )
        message.apply_edit(embed=embed, view=view)
        self.store.record("edit_original", message=message.id, embeds=message.embeds,
                          view=message.view, ephemeral=message.ephemeral)
        return message

    async def delete_original_response(self) -> None:
        message = self._original_target()
        if message.deleted:
            raise discord.NotFound(
                SimpleNamespace(status=404), {"code": 10008, "message": "Unknown Message"}
            )
        message.deleted = True
        self.store.record("delete_original", message=message.id)
