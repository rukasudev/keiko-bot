"""Keiko asks Discord only for the gateway events it reads.

`discord.Intents.all()` asked for presences, a privileged intent nothing reads,
and for every event type the code ignores. Guaranteed: presences stay off, what
the code does read (its guilds, their members, messages and their text) stays
on, and no code reads a member's presence, so leaving it off costs nothing.
"""
import os
import re

import pytest

from app.bot import gateway_intents

pytestmark = pytest.mark.unit

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
READS_PRESENCE = re.compile(
    r"\.(?:activities|raw_status|desktop_status|mobile_status|web_status|client_status)\b"
    r"|is_on_mobile|on_presence_update|(?<!bot)\.activity\b|presences\s*=\s*True"
    r"|(?:member|user|author)\.status\b"
)


def test_presences_are_never_requested():
    assert gateway_intents().presences is False


def test_what_the_code_reads_is_requested():
    intents = gateway_intents()

    assert intents.guilds and intents.members
    assert intents.guild_messages and intents.message_content
    assert intents.dm_messages, "a prefixed command in a DM still gets the slash hint"


def test_events_nobody_listens_to_are_not_requested():
    intents = gateway_intents()

    assert not (intents.voice_states or intents.typing or intents.reactions)
    assert not (intents.invites or intents.webhooks or intents.integrations)
    assert not (intents.moderation or intents.emojis_and_stickers)
    assert not (intents.guild_scheduled_events or intents.auto_moderation)


def test_nothing_reads_a_members_presence():
    """Only the bot's own activity and status are read, and those need no intent."""
    offenders = []
    for folder, _subfolders, filenames in os.walk(APP):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(folder, filename)
            with open(path, encoding="utf-8") as handle:
                if READS_PRESENCE.search(handle.read()):
                    offenders.append(os.path.relpath(path, APP))

    assert offenders == []
