"""A guild adding or removing Keiko has to reach the log channel.

Reported: the "Left Guild" message stopped appearing. Nothing was broken in the
listener — `guild.logs` in production holds every one of those records, and the
matching `guild.removed` analytics events are all there. What was broken is the
trip to Discord: every listener runs in a trace with `silent_when_clean=True`
(`with_error_context` then, `trace.listener` now) so `on_message` cannot flood the channel,
and `on_guild_remove` never fails. The line was folded into a trace that was
then thrown away.

The blast radius is every listener, but only these two lose anything today:
`on_member_join`, `on_message` and `on_raw_message_edit` log nothing on a
successful run, so their silence was always the intent. That is the difference
this suite pins — the guild lifecycle publishes, the routine check does not.
"""
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import logger as logger_module
from app.constants import Commands, LogTypes as logconstants
from app.data import analytics as analytics_data
from app.services import analytics, trace as trace_service

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


class _NoopCoroutine:
    def __await__(self):
        yield
        return None

    def close(self):
        return None


@pytest.fixture
def log_channel():
    """The admin log channel, plus the handler pair `LoggerHooks.start` installs."""
    sends = []

    channel = SimpleNamespace(send=lambda **kwargs: (sends.append(kwargs), _NoopCoroutine())[1])
    bot = SimpleNamespace(
        loop=MagicMock(),
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda channel_id: channel,
        guilds=[object(), object(), object()],
        user=SimpleNamespace(id=99),
    )

    trace_service.clear_sinks()
    folding = logger_module.TraceFoldingHandler()
    handler = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(handler)
    handler.schedule_send = lambda coroutine: coroutine.close()
    logger_module.logger.addHandler(folding)
    logger_module.logger.addHandler(handler)

    yield SimpleNamespace(bot=bot, sends=sends)

    logger_module.logger.removeHandler(folding)
    logger_module.logger.removeHandler(handler)
    trace_service.clear_sinks()


@pytest.fixture
def events(log_channel, monkeypatch):
    """The real cog, with only the Discord round trips stubbed out."""
    from app.cogs import events as events_module

    monkeypatch.setattr(
        events_module, "GreetingsView",
        lambda *args, **kwargs: SimpleNamespace(send=lambda guild: _NoopCoroutine()),
    )
    return events_module.Events(log_channel.bot)


def guild_double(guild_id=4242, owner_id=313):
    return SimpleNamespace(
        id=guild_id,
        member_count=120,
        owner=SimpleNamespace(id=owner_id, mention=f"<@{owner_id}>"),
    )


def rendered(embed):
    parts = [embed.title or "", embed.description or ""]
    parts += [f"{field.name} {field.value}" for field in embed.fields]
    return "\n".join(parts)


def embeds(log_channel):
    return [send["embed"] for send in log_channel.sends if "embed" in send]


async def test_a_guild_removing_keiko_reaches_the_log_channel(deps, events, log_channel):
    guild = guild_double()
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": str(guild.id), "welcome_messages": True, "is_bot_online": True,
    })

    await events.on_guild_remove(guild)

    posted = embeds(log_channel)
    assert posted, "the record was written to guild.logs and never posted"
    left = [embed for embed in posted if embed.title == logconstants.EVENT_LEFT_GUILD_TITLE]
    assert len(left) == 1
    assert "Left guild by 313" in rendered(left[0])
    assert "4242" in rendered(left[0]), "the message has to say which guild left"


async def test_the_removal_analytics_event_was_never_the_broken_part(deps, events):
    """Production has the events and not the messages; keep both true together."""
    await events.on_guild_remove(guild_double())

    assert analytics_data.count_events({"event": "guild.removed"}) == 1


async def test_a_guild_adding_keiko_reaches_the_log_channel(deps, events, log_channel):
    """Production has no `guild.joined` document at all, so pin both halves.

    Nobody added Keiko in the days after analytics was deployed, which reads the
    same as a broken emit until something exercises the path.
    """
    await events.on_guild_join(guild_double(guild_id=777, owner_id=42))

    joined = [
        embed for embed in embeds(log_channel)
        if embed.title == logconstants.EVENT_JOIN_GUILD_TITLE
    ]
    assert len(joined) == 1
    assert "777" in rendered(joined[0])

    # `on_guild_remove` flushes by hand because the guild is about to be wiped;
    # a join rides the cog's periodic flush, which is what this stands in for.
    analytics.flush()
    assert analytics_data.count_events({"event": "guild.joined"}) == 1


async def test_a_returning_guild_is_still_reported(deps, events, log_channel):
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": "777", "is_bot_online": False,
    })

    await events.on_guild_join(guild_double(guild_id=777))

    assert any(
        embed.title == logconstants.EVENT_JOIN_GUILD_TITLE
        for embed in embeds(log_channel)
    )


async def test_a_routine_listener_line_still_stays_out_of_the_channel(log_channel):
    """The flood protection is the reason the rule exists — it has to survive.

    `on_message` runs on every message in every guild; whatever it decides to
    log on a successful check belongs in the file, not in Discord.
    """
    async def routine():
        logger_module.info(
            "checked a message", log_type=logconstants.COMMAND_INFO_TYPE
        )

    async with trace_service.listener("on_message", 4242) as checked:
        await checked.run("block_links", routine())

    assert embeds(log_channel) == []


async def test_a_failing_listener_still_reports_as_a_failure(log_channel):
    async def broken():
        raise RuntimeError("Discord said no")

    async with trace_service.listener("on_member_join", 4242) as joined:
        await joined.run("welcome_messages", broken())

    posted = embeds(log_channel)
    assert posted, "an error was always published, and still is"
    assert any(
        logconstants.TRACE_RESULT_FAILURE in rendered(embed)
        or embed.title == logger_module.outcome_title(logconstants.TRACE_RESULT_FAILURE)
        for embed in posted
    )


def test_the_reported_event_vocabulary_is_the_one_the_channel_renders():
    """A type that publishes a trace must have a title and colour to publish with."""
    for log_type in logconstants.REPORTED_EVENT_TYPES:
        assert log_type in logconstants.LOG_TYPE_MAP


def test_a_silent_trace_with_no_event_line_is_not_noteworthy():
    trace = trace_service.Trace("on_message", silent_when_clean=True)
    trace.add("checked a message", logging.INFO, log_type=logconstants.COMMAND_INFO_TYPE)
    trace.finish()

    assert trace.is_noteworthy is False


GUILD_ID = "4242"


def left_line(log_channel):
    left = [
        embed for embed in embeds(log_channel)
        if embed.title == logconstants.EVENT_LEFT_GUILD_TITLE
    ]
    assert len(left) == 1
    return next(
        line for line in rendered(left[0]).splitlines() if "Active commands" in line
    )


@pytest.mark.regression
async def test_the_left_guild_line_names_what_was_on_by_its_saved_settings(
    deps, events, log_channel
):
    """The "Left Guild" message names the features that were on, by the record the bot runs on.

    Reported in the v1 review: `on_guild_remove` listed "Active commands" from the old
    moderations flags, which wave 1 stopped trusting (`is_feature_on` is the one source of
    "on"). A feature saved without the flag (StreamElements) was missing from the line, and a
    feature disabled while its flag stayed on was listed. The line is what whoever is on call
    reads to know what a server lost.

    Shared behaviour: `Events.on_guild_remove`, the listener every server removal runs.
    Exposed by: the wave 1 review of every reader of the old flags.

    Guaranteed: the line lists exactly the features `is_feature_on` calls on before Keiko
    pauses them, and says "none" when nothing was.
    """
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": GUILD_ID,
        "is_bot_online": True,
        Commands.BLOCK_LINKS_KEY: True,
        Commands.WELCOME_MESSAGES_KEY: False,
    })
    deps.mongo_client.guild[Commands.BLOCK_LINKS_KEY].insert_one(
        {"guild_id": GUILD_ID, "enabled": False}
    )
    deps.mongo_client.guild[Commands.WELCOME_MESSAGES_KEY].insert_one(
        {"guild_id": GUILD_ID, "enabled": True}
    )
    deps.mongo_client.guild[Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY].insert_one(
        {"guild_id": GUILD_ID, "enabled": True}
    )

    await events.on_guild_remove(guild_double(guild_id=int(GUILD_ID)))

    line = left_line(log_channel)
    assert Commands.WELCOME_MESSAGES_KEY in line, "on without the old flag"
    assert Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY in line
    assert Commands.BLOCK_LINKS_KEY not in line, "disabled while its old flag stayed on"


@pytest.mark.regression
async def test_a_guild_with_nothing_on_says_none(deps, events, log_channel):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, "is_bot_online": True, Commands.BLOCK_LINKS_KEY: True}
    )

    await events.on_guild_remove(guild_double(guild_id=int(GUILD_ID)))

    assert "Active commands: none" in left_line(log_channel)
