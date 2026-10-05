"""An error never carries what a member wrote.

Reported in the v1 review: `ErrorContext.from_message` copied the first 200 characters of
the member's message into the error's context, and `with_error_context`, the listener
context of then, did the same for every listener that received a message. The context becomes fields of the embed in the
admin error channel, so a failure while checking a message published the member's text
to whoever reads that channel.

Shared behaviour: `ErrorContext` (`app/exceptions.py`), behind every error a feature logs
about a message (block links, StreamElements), and the listener's error context.
Exposed by: the review of what the error channel shows.

The next review found the same in the prefix commands' error handler
(`Errors.on_command_error`), whose warning quoted the whole message.

Guaranteed: the error names the guild, the member, the channel and the length of the
message, never its text; a prefix command's failure names the command and the length.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import logger as logger_module
from app.exceptions import ErrorContext
from tests.mocks.discord import create_guild, create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

SECRET = "my bank password is hunter2"


def shown(embed):
    return "\n".join([embed.description or "", *(f"{f.name} {f.value}" for f in embed.fields)])


def a_message():
    guild = create_guild()
    return create_message(guild.text_channels[0], create_member(guild), SECRET)


def test_an_error_about_a_message_names_its_length_never_its_text(log_channels):
    message = a_message()

    logger_module.error(
        "Failed to block link: RuntimeError: Discord is down",
        context=ErrorContext.from_message(flow="block_links", message=message),
    )

    posted = log_channels.errors.embeds
    assert len(posted) == 1
    assert "hunter2" not in shown(posted[0])
    assert str(len(SECRET)) in shown(posted[0]), "a length is fine"


async def test_a_listener_that_fails_on_a_message_never_shows_its_text(
    log_channels, deps, monkeypatch
):
    from app.cogs.events import Events

    deps.bot.config.PREFIX = "ke!"
    monkeypatch.setattr(
        "app.services.block_links.check_message",
        AsyncMock(side_effect=RuntimeError("Discord is down")),
    )

    await Events(deps.bot).on_message(a_message())

    shown_anywhere = "\n".join(shown(embed) for embed in log_channels.errors.embeds)
    assert log_channels.errors.embeds, "the failure still reaches the error channel"
    assert "hunter2" not in shown_anywhere
    assert str(len(SECRET)) in shown_anywhere, "a length is fine"


async def test_a_prefix_command_that_fails_never_shows_its_message(deps, caplog):
    from discord.ext import commands

    from app.cogs.errors import Errors

    deps.bot.config.is_prod = lambda: False
    message = a_message()
    context = SimpleNamespace(guild=message.guild, message=message, invoked_with="mouse")

    with caplog.at_level("INFO"):
        await Errors(deps.bot).on_command_error(
            context, commands.BadArgument(f'Converting "{SECRET}" failed')
        )

    assert "hunter2" not in caplog.text
    assert "mouse" in caplog.text and str(len(SECRET)) in caplog.text
