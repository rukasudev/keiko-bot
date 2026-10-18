"""Log aggregation contract: one unit of work is one Discord message.

Before this suite `app/logger.py` had no coverage at all, and a webhook that
logged seven times produced seven messages in an order that did not match the
order things happened. Everything below pins the behavior that replaced it.
"""
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import logger as logger_module
from app.constants import LogTypes as logconstants
from app.services import trace as trace_service
from app.services.trace import Outcome, Trace, trace_scope
import discord
from app.exceptions import DestinationNotFound
from app.services.utils import destination, guild_locale
from tests.mocks.discord import create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


class _NoopCoroutine:
    def __await__(self):
        yield
        return None

    def close(self):
        return None


class FakeChannel:
    """Records what would have been posted, without a coroutine ever running."""

    def __init__(self, name, sends, routed):
        self.name = name
        self._sends = sends
        self._routed = routed

    def send(self, **kwargs):
        self._sends.append(kwargs)
        self._routed.append(self.name)
        return _NoopCoroutine()


@pytest.fixture
def discord_logs():
    """A handler wired to fake channels, detached from the global logger."""
    sends = []
    routed = []
    ids = {1: "logs", 2: "errors", 3: "calls", 4: "actions"}
    channels = {name: FakeChannel(name, sends, routed) for name in ids.values()}

    bot = SimpleNamespace(
        loop=MagicMock(),
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda channel_id: channels.get(ids.get(channel_id)),
    )

    trace_service.clear_sinks()
    # Folding lives on the bus now, not inside DiscordLogsHandler, so the wiring
    # under test is the same pair `LoggerHooks.start` installs.
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    handler = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(handler)
    handler.schedule_send = lambda coroutine: coroutine.close()

    def dispatch(record):
        """What `Logger.callHandlers` does: every handler sees every record.

        Folding and rendering are two handlers now, so a test that drives only
        the Discord one is testing half the bus.
        """
        folding.emit(record)
        handler.emit(record)

    yield SimpleNamespace(
        handler=handler, channels=channels, sends=sends, routed=routed,
        emit=dispatch,
    )

    logger_module.logger.removeHandler(folding)

    trace_service.clear_sinks()


def rendered(embed):
    """Everything the reader sees, wherever the layout puts it.

    These tests pin the contract (one unit of work is one message, in order),
    not whether a line lives in the description or in a field.
    """
    parts = [embed.title or "", embed.description or ""]
    parts += [f"{field.name} {field.value}" for field in embed.fields]
    parts.append(embed.footer.text or "")
    return "\n".join(parts)


def make_record(message, level=logging.INFO, **extra):
    record = logging.LogRecord(
        name="keiko", level=level, pathname="app/webhooks/reminder.py",
        lineno=1, msg=message, args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_seven_records_in_one_trace_become_one_message(discord_logs):
    with trace_scope("reminder", source="webhook") as trace:
        for index in range(7):
            discord_logs.emit(make_record(f"step {index}"))
        assert discord_logs.sends == []

    assert len(discord_logs.sends) == 1
    embed = discord_logs.sends[0]["embed"]
    for index in range(7):
        assert f"step {index}" in rendered(embed)


def test_the_timeline_keeps_the_order_things_happened(discord_logs):
    with trace_scope("reminder", source="webhook"):
        discord_logs.emit(make_record("webhook received"))
        discord_logs.emit(make_record("renewing subscription"))
        discord_logs.emit(make_record("renewal scheduled"))

    description = rendered(discord_logs.sends[0]["embed"])
    assert description.index("webhook received") < description.index("renewing subscription")
    assert description.index("renewing subscription") < description.index("renewal scheduled")


def test_records_outside_a_trace_still_get_their_own_message(discord_logs):
    discord_logs.emit(make_record("standalone"))

    assert len(discord_logs.sends) == 1
    assert discord_logs.sends[0]["embed"].description == "standalone"


def test_errors_keep_a_message_of_their_own_and_stay_in_the_timeline(discord_logs):
    with trace_scope("reminder", source="webhook"):
        discord_logs.emit(make_record("starting"))
        discord_logs.emit(
            make_record("boom", level=logging.ERROR,
                        log_type=logconstants.COMMAND_ERROR_TYPE)
        )

    assert len(discord_logs.sends) == 2
    trace_embed = discord_logs.sends[-1]["embed"]
    assert "boom" in rendered(trace_embed)
    assert "starting" in rendered(trace_embed)


def test_a_failed_trace_is_marked_as_such(discord_logs):
    with trace_scope("reminder", source="webhook"):
        discord_logs.emit(
            make_record("boom", level=logging.ERROR,
                        log_type=logconstants.COMMAND_ERROR_TYPE)
        )

    trace_embed = discord_logs.sends[-1]["embed"]
    assert logconstants.TRACE_RESULT_FAILURE in rendered(trace_embed)


def test_a_clean_silent_trace_never_reaches_discord(discord_logs):
    with trace_scope("on_message", source="internal", silent_when_clean=True):
        discord_logs.emit(make_record("checked a message"))

    assert discord_logs.sends == []


def test_a_silent_trace_that_fails_does_reach_discord(discord_logs):
    with trace_scope("on_message", source="internal", silent_when_clean=True):
        discord_logs.emit(
            make_record("boom", level=logging.ERROR,
                        log_type=logconstants.COMMAND_ERROR_TYPE)
        )

    assert any("embed" in send for send in discord_logs.sends)


def test_a_silent_trace_that_reported_an_event_does_reach_discord(discord_logs):
    """Silence protects `on_message` from flooding, not the guild lifecycle.

    A listener trace publishes nothing unless it failed, and `on_guild_remove`
    never fails: the "Left Guild" line was written to `guild.logs` and dropped
    on its way to the channel that exists for it.
    """
    with trace_scope("on_guild_remove", source="internal", silent_when_clean=True):
        discord_logs.emit(make_record(
            "Left guild by 313", guild_id="42",
            log_type=logconstants.EVENT_LEFT_GUILD_TYPE,
        ))

    assert len(discord_logs.sends) == 1
    embed = discord_logs.sends[0]["embed"]
    assert embed.title == logconstants.EVENT_LEFT_GUILD_TITLE, (
        "`👂 Listener Event` is not what someone scanning the channel looks for"
    )
    assert "Left guild by 313" in rendered(embed)
    assert "42" in rendered(embed), "the message has to say which guild left"


def test_an_event_line_keeps_its_trace_even_when_the_timeline_overflows(discord_logs):
    """What publishes the message is that the event happened, not that it fit."""
    with trace_scope("on_guild_join", source="internal", silent_when_clean=True):
        for index in range(logconstants.TRACE_MAX_LINES + 3):
            discord_logs.emit(make_record(f"noise {index}"))
        discord_logs.emit(make_record(
            "Joined new guild", log_type=logconstants.EVENT_JOIN_GUILD_TYPE,
        ))

    assert len(discord_logs.sends) == 1
    assert discord_logs.sends[0]["embed"].title == logconstants.EVENT_JOIN_GUILD_TITLE


def test_a_repeated_attempt_is_called_out_on_the_message_you_already_read(
    discord_logs,
):
    """The "why did they run it again" question, answered in place instead of
    by correlating two log messages by hand."""
    with trace_scope("moderations block links", user_id="9", guild_id="1",
                     source="slash") as trace:
        trace.footnote = "⚠️ 2º run of `block_links` by this guild in the last 24h"
        discord_logs.emit(make_record("`/moderations block links` started"))

    message = rendered(discord_logs.sends[-1]["embed"])
    assert "2º run of `block_links`" in message
    assert "`/moderations block links` started" in message


def test_a_first_attempt_carries_no_footnote():
    from app.services import analytics

    assert analytics.describe_attempt(1, "block_links") is None
    assert analytics.describe_attempt(2, "block_links")


def test_counting_an_attempt_never_breaks_the_command_it_measures():
    from app.services import analytics

    assert analytics.count_attempt(None, "block_links") == 0
    assert analytics.count_attempt("1", None) == 0


def test_the_trace_timeline_is_capped_so_a_loop_cannot_flood_an_embed():
    trace = Trace("runaway")
    for index in range(logconstants.TRACE_MAX_LINES + 25):
        trace.add(f"line {index}")

    assert len(trace.lines) == logconstants.TRACE_MAX_LINES
    assert trace.truncated == 25


def test_long_lines_are_trimmed_instead_of_breaking_the_embed():
    trace = Trace("verbose")
    trace.add("x" * 5000)

    assert len(trace.lines[0]["message"]) <= logconstants.TRACE_LINE_MAX_LENGTH


def test_nested_scopes_share_one_timeline(discord_logs):
    with trace_scope("outer", source="webhook"):
        discord_logs.emit(make_record("outer line"))
        with trace_scope("inner", source="webhook"):
            discord_logs.emit(make_record("inner line"))
        assert discord_logs.sends == []

    assert len(discord_logs.sends) == 1
    description = rendered(discord_logs.sends[0]["embed"])
    assert "outer line" in description
    assert "inner line" in description


def test_a_muted_record_never_becomes_a_trace_line(discord_logs):
    with trace_scope("gateway", source="internal") as trace:
        discord_logs.emit(make_record("We are being rate limited."))
        assert trace.lines == []


def test_user_traces_and_system_traces_go_to_different_channels(discord_logs):
    with trace_scope("moderations block links", user_id="9", guild_id="1", source="slash"):
        pass
    with trace_scope("reminder", source="webhook"):
        pass

    assert discord_logs.routed == ["calls", "logs"]


def test_errors_are_routed_to_the_error_channel(discord_logs):
    discord_logs.emit(
        make_record("boom", level=logging.ERROR,
                    log_type=logconstants.COMMAND_ERROR_TYPE)
    )

    assert discord_logs.routed == ["errors"]


def test_a_sink_that_explodes_never_breaks_the_traced_work():
    trace_service.clear_sinks()
    trace_service.register_sink(lambda trace: 1 / 0)

    with trace_scope("fragile", source="internal") as trace:
        trace.add("did the work")

    trace_service.clear_sinks()


def test_the_handler_formats_the_record_itself(discord_logs):
    """asctime only exists because the handler creates it, not because some
    other handler happened to run first."""
    record = make_record("hello")
    discord_logs.emit(record)

    assert hasattr(record, "asctime")
    assert discord_logs.sends[0]["embed"].footer.text.endswith(record.asctime)


def test_an_error_without_a_log_type_or_exception_still_reaches_discord(discord_logs):
    """`logger.error("something")` with neither log_type nor exc_info used to
    raise TypeError inside the handler (`record.exc_info[1]` on None), and
    logging swallows handler errors — so the one message that mattered most was
    the one that silently disappeared."""
    discord_logs.emit(make_record("plain failure", level=logging.ERROR))

    assert len(discord_logs.sends) == 1
    assert "plain failure" in discord_logs.sends[0]["embed"].description


def test_an_interaction_without_a_guild_does_not_crash_the_handler(discord_logs):
    interaction = SimpleNamespace(
        id=1, guild=None, user=SimpleNamespace(mention="<@9>"),
        channel=None, message=None, command=None,
    )
    discord_logs.emit(make_record("dm interaction", interaction=interaction))

    assert len(discord_logs.sends) == 1


def test_a_quiet_trace_that_fails_posts_only_the_error_record(discord_logs):
    """A form click that fails is told by the session message and by the error
    channel; the click's raw engine timeline is not a third message."""
    with trace_scope("moderations block links:CommitFailed", quiet=True, user_id="9"):
        discord_logs.emit(make_record(
            "form block_links commit setup failed: RuntimeError",
            logging.ERROR,
            log_type=logconstants.COMMAND_ERROR_TYPE,
        ))

    assert discord_logs.routed == ["errors"]


def test_the_work_a_trace_hands_over_keeps_its_publishing_rule():
    """A job that carries a request on posts under the request's rule."""
    request = Trace("twitch", source="webhook", silent_when_clean=True, quiet=True)

    successor = request.handover()

    assert request.publishing() == {"silent_when_clean": True, "quiet": True}
    assert successor.silent_when_clean and successor.quiet


async def delivered(value):
    return value


async def raising(error):
    raise error


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Access")


@pytest.mark.shared_contract("fan_out")
async def test_every_server_is_served_on_its_own_and_says_how_it_went(caplog):
    """The execution context every listener, webhook job and notice runs in.

    `fan_out` and `listener` (`app/services/trace.py`) are what the Twitch, YouTube and
    birthday fan-outs and the `on_message` and `on_member_join` listeners share; each consumer has its own suite, and
    this one pins the shared rules once: a server's share runs on its own and says how it
    went, a server Keiko cannot reach is a warning and anything else an error with its
    context, the lookup of a server and its channel says which one is missing, a server is
    spoken to in the language its settings saved or else its own, and a listener's parts run
    each on their own under one trace that is posted only when a part failed. And a line
    logged outside any trace is a message of its own, whatever trace is open.
    """
    with caplog.at_level("INFO"):
        served = await trace_service.fan_out("probe_notice", [
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


@pytest.mark.shared_contract("fan_out")
async def test_a_server_discord_refuses_is_one_keiko_cannot_reach(caplog):
    with caplog.at_level("INFO"):
        served = await trace_service.fan_out("probe_notice", [("1", raising(forbidden()))])

    assert served[0].outcome is Outcome.UNREACHABLE
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


@pytest.mark.shared_contract("fan_out")
def test_the_lookup_says_which_of_the_server_or_the_channel_is_missing(deps):
    guild = SimpleNamespace(id=1, get_channel=lambda channel_id: "general" if channel_id == 10 else None)
    deps.bot.get_guild = lambda guild_id: guild if guild_id == 1 else None

    assert destination("1", "10") == (guild, "general")
    with pytest.raises(DestinationNotFound, match="guild 2 not found"):
        destination("2", "10")
    with pytest.raises(DestinationNotFound, match="channel 11 of guild 1 not found"):
        destination("1", "11")


@pytest.mark.shared_contract("fan_out")
def test_a_server_is_spoken_to_in_the_language_its_settings_saved_else_its_own():
    brazilian = SimpleNamespace(preferred_locale=discord.Locale.brazil_portuguese)
    american = SimpleNamespace(preferred_locale=discord.Locale.american_english)

    assert guild_locale(brazilian) == "pt-br"
    assert guild_locale(american, saved="pt-br") == "pt-br"
    assert guild_locale(SimpleNamespace()) == "en-us"


@pytest.mark.shared_contract("fan_out")
async def test_a_listeners_parts_run_each_on_its_own_under_one_trace(caplog):
    closed = []
    trace_service.register_sink(closed.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    ran = []
    try:
        async with trace_service.listener("on_probe", 7, user_id=8, channel_id=9) as unit:
            await unit.run("first", raising(RuntimeError("down")))
            await unit.run("second", delivered(ran.append("second")))
        async with trace_service.listener("on_probe", 7) as unit:
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


@pytest.mark.shared_contract("fan_out")
async def test_a_line_logged_outside_any_trace_is_a_message_of_its_own(log_channels):
    with trace_service.trace_scope("on_message", source="internal", silent_when_clean=True) as trace:
        with trace_service.outside_any_trace():
            logger_module.warn("Settings cache (Redis) unreachable")
        logger_module.info("checked a message")

    assert [line["message"] for line in trace.lines] == ["checked a message"]
    assert len(log_channels.logs.embeds) == 1, "the warning is a message of its own"


BLOCK_ALL = {
    "mode": "block_all",
    "allowed_roles": {"values": []},
    "allowed_chats": {"values": []},
    "allowed_links": {"values": []},
    "answer": "No links here, {user}!",
}


@pytest.fixture
def events(deps, guild):
    from app.cogs.events import Events

    guild.owner = create_member(guild, id=313, name="Owner")
    guild.preferred_locale = discord.Locale.american_english
    deps.bot.config.PREFIX = "ke!"
    deps.bot.user = SimpleNamespace(id=99)
    deps.bot.owner_id = 1
    deps.bot.guilds = []
    return Events(deps.bot)


def failures(caplog):
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def broken(error):
    def fail(*args, **kwargs):
        raise error

    return fail


@pytest.mark.regression
async def test_a_new_member_is_welcomed_when_giving_roles_fails(
    events, guild, channel, mock_cache, mock_banner, monkeypatch, caplog
):
    """One part of a listener failing never stops the next part of that listener.

    Reported in the v1 review: `on_member_join` ran default roles and then the welcome
    message under one `with_error_context`, and `on_message` the StreamElements reply and
    then the block links check. When the first one raised (Keiko lost the permission to give
    roles, StreamElements did not answer), the second never ran: the new member got no
    welcome, and a blocked link stayed up. The next review found the same in the other
    listeners: a server leaving whose snapshot or flush failed was never paused, a server
    joining whose record failed was never greeted, and `with_error_context` was a second
    listener context that raised where `trace.listener` logs.

    Shared behaviour: the unit of work a listener runs for one server (`trace.listener`,
    `app/services/trace.py`), behind all six listeners of `app/cogs/events.py`. Exposed by:
    the reviews of the listeners.

    Guaranteed: each part of a listener runs on its own; a failure is an error with the
    listener's context (its guild, member and channel, never the message's text), nothing
    is raised to discord.py, and the next part still runs: the welcome reaches its channel,
    the blocked link is deleted, the server Keiko left is paused, the new server is greeted.
    """
    member = create_member(guild, id=999, name="Newcomer")
    member._user = MagicMock(id=999)
    mock_cache.return_value = {
        "welcome_messages_channel": {"values": str(channel.id)},
        "welcome_messages": {"values": "Welcome {user}!"},
        "welcome_messages_title": "Welcome!",
        "welcome_messages_footer": "Enjoy your stay!",
    }
    monkeypatch.setattr("app.cogs.events.get_available_roles_by_guild", lambda guild: ["Member"])
    monkeypatch.setattr(
        "app.services.default_roles.set_on_member_join",
        AsyncMock(side_effect=RuntimeError("Missing Permissions")),
    )

    with caplog.at_level("INFO"):
        await events.on_member_join(member)

    channel.assert_message_sent()
    assert channel.get_last_embed().title == "Welcome!"
    failed = failures(caplog)
    assert failed and failed[0].context.guild_id == str(guild.id)
    assert failed[0].context.user_id == str(member.id)


@pytest.mark.regression
async def test_a_message_is_checked_for_blocked_links_when_the_reply_fails(
    events, guild, channel, mock_cache, monkeypatch, caplog
):
    author = create_member(guild, id=111, name="Member")
    message = create_message(channel, author, "ke!mouse https://blocked.example")
    mock_cache.return_value = dict(BLOCK_ALL)
    monkeypatch.setattr(
        "app.services.stream_elements.check_message",
        AsyncMock(side_effect=RuntimeError("StreamElements did not answer")),
    )

    with caplog.at_level("INFO"):
        await events.on_message(message)

    message.assert_deleted()
    assert author.mention in str(channel._sent_messages[-1].content)
    failed = failures(caplog)
    assert failed and failed[0].context.channel_id == str(message.channel.id)
    assert "blocked.example" not in caplog.text


@pytest.mark.regression
@pytest.mark.parametrize("failing", ["report", "snapshot"])
async def test_a_server_keiko_left_is_paused_whatever_failed_before(
    events, deps, guild, monkeypatch, caplog, failing
):
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": str(guild.id), "is_bot_online": True,
    })
    if failing == "report":
        monkeypatch.setattr("app.cogs.events.features_on", broken(RuntimeError("Mongo hung up")))
    else:
        monkeypatch.setattr(
            "app.services.analytics_reports.guild_snapshot", broken(RuntimeError("Mongo hung up"))
        )

    with caplog.at_level("INFO"):
        await events.on_guild_remove(guild)

    record = deps.mongo_client.guild.moderations.find_one({"guild_id": str(guild.id)})
    assert record["is_bot_online"] is False, "the pause ran after the failure"
    failed = failures(caplog)
    assert failed and failed[0].context.guild_id == str(guild.id)
    assert failed[0].context.extra["part"] == failing


@pytest.mark.regression
async def test_a_new_server_is_greeted_when_recording_it_fails(
    events, guild, channel, monkeypatch, caplog
):
    monkeypatch.setattr("app.cogs.events.join_guild", broken(RuntimeError("Mongo hung up")))

    with caplog.at_level("INFO"):
        await events.on_guild_join(guild)

    channel.assert_message_sent()
    failed = failures(caplog)
    assert failed and failed[0].context.extra["part"] == "record"


@pytest.mark.regression
async def test_an_edited_message_check_that_fails_is_an_error_with_its_context(
    events, guild, channel, monkeypatch, caplog
):
    monkeypatch.setattr(
        "app.services.block_links.check_edited_message",
        AsyncMock(side_effect=RuntimeError("Discord is down")),
    )
    payload = SimpleNamespace(guild_id=guild.id, channel_id=channel.id, message_id=7)

    with caplog.at_level("INFO"):
        await events.on_raw_message_edit(payload)

    [failed] = failures(caplog)
    assert failed.context.guild_id == str(guild.id)
    assert failed.context.channel_id == str(channel.id)


@pytest.mark.regression
async def test_a_command_count_that_fails_is_an_error_with_its_context(
    events, guild, channel, monkeypatch, caplog
):
    monkeypatch.setattr("app.cogs.events.increment_redis_key", broken(RuntimeError("Redis is down")))
    interaction = SimpleNamespace(
        type=discord.InteractionType.application_command,
        command=SimpleNamespace(_attr="block_links", qualified_name="moderations block links"),
        guild_id=guild.id,
        channel_id=channel.id,
        user=create_member(guild, id=222, name="Admin"),
        extras={},
    )

    with caplog.at_level("INFO"):
        await events.on_interaction(interaction)

    [failed] = failures(caplog)
    assert failed.context.guild_id == str(guild.id)
    assert failed.context.user_id == "222"
    assert failed.context.channel_id == str(channel.id)
