"""A direct message to Keiko is not an error.

Broke as: `Events.on_message` read `message.guild.id` before asking where the
message came from. A DM has no guild, so every DM to the bot raised an
AttributeError inside `with_error_context`, and each one posted an error embed to
the admin error channel.

Shared behaviour: `Events.on_message`, the listener behind block links and the
StreamElements commands. Consumer that exposed it: anyone writing to the bot.

Guaranteed: a DM is ignored before anything reads its guild: no error, no message
in any admin channel, and no feature looks at it.
"""
from types import SimpleNamespace

import pytest

from tests.mocks.discord import MockMember, MockMessage

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def direct_message(content):
    author = MockMember(id=111, name="TestUser")
    return MockMessage(
        id=1, content=content, author=author, channel=SimpleNamespace(id=55, guild=None)
    )


@pytest.mark.parametrize("content", ["hi Keiko!", "ks!mouse", "https://spam.example"])
async def test_a_direct_message_reaches_no_admin_channel(
    content, log_channels, mock_cache
):
    from app.cogs import events as events_module

    await events_module.Events(SimpleNamespace()).on_message(direct_message(content))

    assert log_channels.errors.embeds == []
    assert log_channels.logs.embeds == [] and log_channels.calls.embeds == []
    assert [trace for trace in log_channels.traces if trace.is_noteworthy] == []
    assert mock_cache.call_count == 0, "no feature reads the settings of a DM"
