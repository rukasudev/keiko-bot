"""The execution context every listener, webhook job and notice runs in.

`app/services/work.py` is what the Twitch, YouTube and birthday fan-outs and the
`on_message` and `on_member_join` listeners share; each consumer has its own suite, and
this one pins the shared rules once: a server's share runs on its own and says how it
went, a server Keiko cannot reach is a warning and anything else an error with its
context, the lookup of a server and its channel says which one is missing, a server is
spoken to in the language its settings saved or else its own, and a listener's parts run
each on their own under one trace that is posted only when a part failed. And a line
logged outside any trace is a message of its own, whatever trace is open.
"""
import logging
from types import SimpleNamespace

import discord
import pytest

from app import logger as logger_module
from app.exceptions import DestinationNotFound
from app.services import trace as trace_service
from app.services import work
from app.services.work import Outcome

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("fan_out")]


async def delivered(value):
    return value


async def raising(error):
    raise error


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")


async def test_every_server_is_served_on_its_own_and_says_how_it_went(caplog):
    with caplog.at_level("INFO"):
        served = await work.fan_out("probe_notice", [
            ("1", raising(DestinationNotFound("guild 1 not found"))),
            ("2", raising(RuntimeError("the embed is too long"))),
            ("3", delivered(5)),
        ], video_id="v1")

    assert [(share.guild_id, share.outcome) for share in served] == [
        ("1", Outcome.UNREACHABLE), ("2", Outcome.FAILED), ("3", Outcome.DELIVERED),
    ]
    assert served[2].value == 5
    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].context.flow == "probe_notice" and errors[0].context.guild_id == "2"
    assert errors[0].context.extra == {"video_id": "v1"}
    assert errors[0].exc_info is not None


async def test_a_server_discord_refuses_is_one_keiko_cannot_reach(caplog):
    with caplog.at_level("INFO"):
        served = await work.fan_out("probe_notice", [("1", raising(forbidden()))])

    assert served[0].outcome is Outcome.UNREACHABLE
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_the_lookup_says_which_of_the_server_or_the_channel_is_missing(deps):
    guild = SimpleNamespace(id=1, get_channel=lambda channel_id: "general" if channel_id == 10 else None)
    deps.bot.get_guild = lambda guild_id: guild if guild_id == 1 else None

    assert work.destination("1", "10") == (guild, "general")
    with pytest.raises(DestinationNotFound, match="guild 2 not found"):
        work.destination("2", "10")
    with pytest.raises(DestinationNotFound, match="channel 11 of guild 1 not found"):
        work.destination("1", "11")


def test_a_server_is_spoken_to_in_the_language_its_settings_saved_else_its_own():
    brazilian = SimpleNamespace(preferred_locale=discord.Locale.brazil_portuguese)
    american = SimpleNamespace(preferred_locale=discord.Locale.american_english)

    assert work.guild_locale(brazilian) == "pt-br"
    assert work.guild_locale(american, saved="pt-br") == "pt-br"
    assert work.guild_locale(SimpleNamespace()) == "en-us"


async def test_a_listeners_parts_run_each_on_its_own_under_one_trace(caplog):
    closed = []
    trace_service.register_sink(closed.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    ran = []
    try:
        async with work.listener("on_probe", 7, user_id=8, channel_id=9) as unit:
            await unit.run("first", raising(RuntimeError("down")))
            await unit.run("second", delivered(ran.append("second")))
        async with work.listener("on_probe", 7) as unit:
            await unit.run("only", delivered(None))
    finally:
        logger_module.logger.removeHandler(folding)

    assert ran == ["second"]
    failed, clean = closed
    assert failed.is_noteworthy and failed.has_error, "a failed part posts the listener's trace"
    assert not clean.is_noteworthy, "a clean listener stays out of the channel"
    error = next(record for record in caplog.records if record.levelno >= logging.ERROR)
    assert (error.context.guild_id, error.context.user_id, error.context.channel_id) == ("7", "8", "9")
    assert error.context.extra == {"part": "first"}


async def test_a_line_logged_outside_any_trace_is_a_message_of_its_own(log_channels):
    with trace_service.trace_scope("on_message", source="internal", silent_when_clean=True) as trace:
        with trace_service.outside_any_trace():
            logger_module.warn("Settings cache (Redis) unreachable")
        logger_module.info("checked a message")

    assert [line["message"] for line in trace.lines] == ["checked a message"]
    assert len(log_channels.logs.embeds) == 1, "the warning is a message of its own"
