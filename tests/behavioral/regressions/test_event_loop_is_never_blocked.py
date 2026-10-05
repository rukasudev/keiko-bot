"""A synchronous call inside a coroutine stops the whole bot, not one command.

Everything runs on one event loop: the gateway heartbeat, every interaction,
every listener. `requests`, `pymongo` and `time.sleep` are all synchronous, so a
coroutine that reaches them directly freezes the bot for as long as they take.

Production showed both halves of the damage:

- 52 `heartbeat blocked for more than 20 seconds` warnings in one day, each with
  `on_message -> check_message -> get_cog_data_or_populate -> find_one` in the
  traceback, ending in two restarts;
- a `10062 Unknown interaction` on a guild that had just subscribed a youtuber.
  Discord gives an interaction three seconds to be acknowledged, and two
  `requests.post` calls had already spent them. The youtuber was saved; the
  person saw "This interaction failed" and started over.

These tests do not assert that a helper was called. They run the real entry
point against a call that blocks, and count how many times the event loop came
back to life while it ran. A blocked loop cannot count.
"""
import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import redis
import requests

from app.constants import Commands as commands_constants
from app.services import (
    analytics,
    analytics_sink,
    block_links,
    debug_logs,
    default_roles,
    logs_archive,
    notifications_twitch,
    stream_elements,
    welcome_messages,
)
from app.constants import DBConfigs
from tests.mocks import create_member, create_message
from tests.mocks.web import png, respond
from tests.mocks.discord import MockInteraction
from unittest.mock import AsyncMock, MagicMock
import discord
from app.decorators import keiko_command
import ast
import os
from typing import Dict, Iterator, List, NamedTuple, Optional, Set, Tuple
from pyrage import x25519
from app.cogs.analytics import Analytics

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("event_loop")]

REAL_DRAW_BANNER = welcome_messages.draw_banner

BLOCKING_SECONDS = 0.25
TICK_SECONDS = 0.005
# A loop that never yields ticks zero or one time; a healthy one ticks ~50.
MIN_TICKS = 5


async def ticks_while(awaitable):
    """How many times the loop got control back while `awaitable` ran."""
    ticks = 0
    running = True

    async def heartbeat():
        nonlocal ticks
        while running:
            await asyncio.sleep(TICK_SECONDS)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    try:
        await awaitable
    finally:
        running = False
        beat.cancel()

    return ticks


def blocking(return_value=None):
    """A stand-in for `requests`, `find_one` or `time.sleep`."""
    def call(*args, **kwargs):
        time.sleep(BLOCKING_SECONDS)
        return return_value
    return call


async def test_checking_a_message_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    """`on_message` runs for every message in every guild, and it reads Mongo."""
    mock_cache.side_effect = blocking({})
    message = create_message(content="hello", author=member, channel=channel)

    ticks = await ticks_while(block_links.check_message(str(guild.id), message))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: the gateway heartbeat was "
        f"blocked for the whole read, which is what restarted the bot on 31/08"
    )


async def test_a_prefixed_message_no_command_matches_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member
):
    """discord.py hands it to `on_command_error`, which reads the server's features."""
    import discord
    from discord.ext import commands

    from app.cogs.errors import Errors

    mock_cache.side_effect = blocking({})
    guild.preferred_locale = discord.Locale.american_english
    message = create_message(content="ks!mouse", author=member, channel=channel)
    context = SimpleNamespace(guild=guild, message=message, send=channel.send)

    ticks = await ticks_while(
        Errors(bot).on_command_error(context, commands.CommandNotFound())
    )

    assert ticks >= MIN_TICKS


async def test_a_stream_elements_command_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    mock_cache.side_effect = blocking({})
    message = create_message(content="ks!mouse", author=member, channel=channel)

    ticks = await ticks_while(
        stream_elements.check_message(str(guild.id), message, "ks!")
    )

    assert ticks >= MIN_TICKS


async def test_answering_a_stream_elements_command_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    """The cache read is not the only blocking call on this path.

    A `ks!` command that misses the cache goes to the StreamElements API over
    `requests`, from the same coroutine, right after the read that was fixed.
    """
    mock_cache.return_value = {"streamer": "gaules", "channel_id": "c1", "enabled": True}
    monkeypatch.setattr(
        "app.services.stream_elements.get_reply_in_cache_or_populate", blocking(None)
    )
    message = create_message(content="ks!mouse", author=member, channel=channel)

    ticks = await ticks_while(
        stream_elements.check_message(str(guild.id), message, "ks!")
    )

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: the API call beside the cache "
        f"read blocks the gateway just the same"
    )


async def test_listing_stream_elements_commands_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, monkeypatch
):
    mock_cache.return_value = {"streamer": "gaules", "channel_id": "c1", "enabled": True}
    monkeypatch.setattr(
        "app.services.stream_elements.get_commands_in_cache_or_populate", blocking([])
    )
    message = create_message(content="ks!commands", author=member, channel=channel)

    ticks = await ticks_while(
        stream_elements.check_message(str(guild.id), message, "ks!")
    )

    assert ticks >= MIN_TICKS


async def test_storing_a_failed_command_never_freezes_the_bot(deps, monkeypatch):
    """The record of a failed command counts it in Redis, a synchronous client."""
    import discord
    from discord import app_commands

    from app.cogs.errors import Errors
    from tests.behavioral.harness.fake_interaction import FakeInteraction
    from tests.behavioral.harness.message_store import MessageStore
    from tests.mocks import create_guild

    monkeypatch.setattr("app.cogs.errors.increment_redis_key", blocking())
    guild = create_guild()
    interaction = FakeInteraction(
        MessageStore(),
        guild=guild,
        user=create_member(guild),
        locale=discord.Locale.american_english,
    )
    interaction.command = SimpleNamespace(
        qualified_name="moderations block links", _attr="block_links"
    )
    error = app_commands.CommandInvokeError(
        SimpleNamespace(name="links"), KeyError("allowed_links")
    )
    handler = Errors(SimpleNamespace(config=SimpleNamespace(is_prod=lambda: False)))

    ticks = await ticks_while(handler.on_app_command_error(interaction, error))

    assert ticks >= MIN_TICKS


async def test_the_heartbeat_ping_never_freezes_the_bot(monkeypatch):
    """The ping says the bot is alive; waiting for it on the loop would make it a lie."""
    from app.cogs import heartbeat

    monkeypatch.setattr(
        requests, "get", blocking(SimpleNamespace(raise_for_status=lambda: None))
    )
    beating = heartbeat.Heartbeat(SimpleNamespace(config=SimpleNamespace(
        HEARTBEAT_URL="https://hc-ping.example/token", APP_VERSION="dev"
    )))

    ticks = await ticks_while(heartbeat.Heartbeat.beat.coro(beating))

    assert ticks >= MIN_TICKS


async def test_a_birthday_reminder_never_freezes_the_bot(deps, monkeypatch):
    """The webhook job runs on the loop: reading the birthdays and claiming each one block."""
    from app.data.birthdays import upsert_birthday_config
    from app.services.moderations import update_moderations_by_guild
    from app.webhooks import birthday_handler

    guild_id = str(deps.guild.id)
    channel = deps.guild.text_channels[0]
    create_member(deps.guild, id=555, name="Tester")
    upsert_birthday_config(guild_id, str(channel.id), False)
    update_moderations_by_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, True)
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    birthday = {"guild_id": guild_id, "user_id": "555", "date": "03-15", "reminder_id": "reminder-1"}
    monkeypatch.setattr(
        "app.data.birthdays.find_birthday_items_by_reminder_and_date", blocking([birthday])
    )
    monkeypatch.setattr("app.data.birthdays.mark_birthday_celebrated", blocking(True))

    ticks = await ticks_while(
        birthday_handler.process_birthday_webhook("reminder-1", "03-15")
    )

    assert ticks >= MIN_TICKS
    assert len(channel._sent_messages) == 1, "the job reached the claim and the message"


async def test_renewing_the_youtube_subscriptions_at_start_never_freezes_the_bot(
    deps, monkeypatch
):
    """The start renews every followed channel: one Mongo read and two calls to Google each."""
    from app.cogs.notifications import Notifications

    deps.bot.config.is_dev = lambda: False
    deps.bot.config.YOUTUBE_HUB_SECRET = "hub-secret"
    deps.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    monkeypatch.setattr(
        "app.services.notifications_youtube_video.find_followed_youtubers",
        blocking(["pewdiepie"]),
    )

    ticks = await ticks_while(Notifications(deps.bot).resubscribe_youtube())

    assert ticks >= MIN_TICKS
    assert deps.youtube.subscribe_calls == ["UC-pewdiepie"], "the renewal reached the hub"


async def test_waiting_for_a_stream_never_freezes_the_bot(deps, monkeypatch):
    """The retry loop sleeps 15 seconds at a time, twice, between Twitch calls."""
    monkeypatch.setattr(
        "app.services.notifications_twitch.wait_for_stream_info", blocking(None)
    )

    ticks = await ticks_while(
        notifications_twitch.handle_send_streamer_notification("gaules")
    )

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: a `time.sleep` in a coroutine "
        f"stops the gateway, not just this notification"
    )


async def test_subscribing_a_youtuber_never_freezes_the_bot(deps, monkeypatch):
    """The reported bug: two blocking POSTs between the click and the ack.

    The platform commits an added item through its feature module, which
    runs the subscription in a worker thread; a blocking subscribe must
    still leave the loop free to acknowledge the interaction.
    """
    from datetime import datetime, timezone

    from app.settings.form.form_state import Answer
    from app.settings.features import feature_for
    from app.settings.features.feature import CommitContext

    deps.bot.config.is_dev = lambda: False
    deps.mongo_client.guild[commands_constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY].insert_one(
        {"guild_id": "123456789", "enabled": True,
         "notifications": {"style": "composition", "values": []}}
    )
    feature = feature_for(commands_constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)
    monkeypatch.setattr(feature, "subscribe", blocking())
    context = CommitContext(
        "123456789", "555", "pt-br", {}, datetime.now(timezone.utc), "slash", "s1"
    )
    item = {"channel": Answer("100"), "youtuber": Answer("sondureacts"),
            "notification_messages": Answer("oi")}

    ticks = await ticks_while(feature.commit("add_item", {"answers": item}, context))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: Discord's three second budget "
        f"is spent inside the subscribe, so the acknowledgement arrives too late"
    )


async def test_a_member_joining_a_welcome_server_never_freezes_the_bot(
    deps, mock_cache, guild, bot, member
):
    """`on_member_join` reads the welcome configuration for every new member."""
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(welcome_messages.send_welcome_message(member))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: every member join froze the "
        f"gateway for the whole configuration read"
    )


async def test_a_member_joining_a_default_roles_server_never_freezes_the_bot(
    deps, mock_cache, guild, bot, member
):
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(default_roles.set_on_member_join(member))

    assert ticks >= MIN_TICKS


async def test_syncing_default_roles_never_freezes_the_bot(
    deps, mock_cache, guild, bot, interaction
):
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(default_roles.set_on_default_roles_sync(interaction))

    assert ticks >= MIN_TICKS


async def test_previewing_a_welcome_message_never_freezes_the_bot(
    deps, mock_cache, guild, bot, interaction
):
    mock_cache.side_effect = blocking({})

    ticks = await ticks_while(
        welcome_messages.send_welcome_message_preview(interaction, [])
    )

    assert ticks >= MIN_TICKS


async def test_checking_why_a_link_passed_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, member, interaction
):
    mock_cache.side_effect = blocking({})
    message = create_message(content="https://example.com", author=member, channel=channel)

    ticks = await ticks_while(block_links.send_link_check_message(interaction, message))

    assert ticks >= MIN_TICKS


async def test_drawing_a_welcome_banner_never_freezes_the_bot(
    deps, mock_cache, guild, bot, channel, monkeypatch
):
    """A member join draws a banner: two image downloads, then the drawing."""

    def slow_download(url, *args, **kwargs):
        if "/avatars/" in url:
            time.sleep(BLOCKING_SECONDS)
        return respond(url, png())

    monkeypatch.setattr(welcome_messages, "draw_banner", REAL_DRAW_BANNER)
    monkeypatch.setattr(requests, "get", slow_download)
    mock_cache.return_value = {
        "welcome_messages_channel": {"values": str(channel.id)},
        "welcome_messages": {"values": "Welcome {user}!"},
        "welcome_messages_title": "Welcome!",
        "welcome_design": "server_blur",
    }
    member = create_member(guild, id=880011, name="Slowpoke")
    member._user = SimpleNamespace(id=880011)

    ticks = await ticks_while(welcome_messages.send_welcome_message(member))

    channel.assert_message_sent()
    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times: the avatar download ran on the "
        f"loop, freezing the bot on every member join"
    )


def test_a_stalled_redis_gives_up_instead_of_holding_a_thread(monkeypatch):
    """Cache reads run in threads; a Redis that never answers must not keep them."""
    from app import connect_redis

    monkeypatch.setattr(DBConfigs, "REDIS_SOCKET_TIMEOUT_SECONDS", 0.2)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    accepted = []

    def serve():
        while True:
            try:
                accepted.append(listener.accept()[0])
            except OSError:
                return

    threading.Thread(target=serve, daemon=True).start()
    try:
        client = connect_redis(f"redis://127.0.0.1:{listener.getsockname()[1]}/0")
        started = time.monotonic()
        with pytest.raises(redis.exceptions.TimeoutError):
            client.get("guild:1:cog.block_links")
        assert time.monotonic() - started < 3
    finally:
        listener.close()
        for connection in accepted:
            connection.close()


TRANSLATED = {"src": "🇧🇷", "dest": "🇺🇸", "original": "bom dia", "translated_message": "good morning"}


@pytest.fixture
def translate(deps, guild, monkeypatch):
    from app.cogs.base.translate import Translate
    from app.integrations.google_translate import GoogleTranslate

    interaction = MockInteraction(user=create_member(guild), guild=guild)
    answered_before_google = []

    async def run(text, google_answers=None):
        def translate(content, locale):
            answered_before_google.append([dict(answer) for answer in interaction._responses])
            return google_answers

        monkeypatch.setattr(GoogleTranslate, "translate", staticmethod(translate))
        message = create_message(guild.text_channels[0], create_member(guild), text)
        message.jump_url = "https://discord.com/channels/1/2/3"
        await Translate.translate_message(SimpleNamespace(), interaction, message)

    return SimpleNamespace(run=run, interaction=interaction, before_google=answered_before_google)


@pytest.mark.regression
@pytest.mark.parametrize("google_answers", [TRANSLATED, None], ids=["translated", "google-failed"])
async def test_a_translation_is_acknowledged_before_it_waits_on_google(translate, google_answers):
    """A translation Google takes its time on still reaches the person who asked for it.

    Reported in the v1 review of the loop and runtime PR: the message context menu answered
    only once Google had, and Discord drops an interaction nobody acknowledged within three
    seconds. With the time limits every outside call now has, a translation may take up to
    five seconds to detect the language and then the read of the translation itself, so a
    slow Google meant "The application did not respond" instead of the translation.

    Shared behaviour: `Translate.translate_message`, the Translate message context menu.
    Exposed by: the time limits of the shared HTTP client and of the language detection.

    Guaranteed: the two checks that need no outside call answer at once; anything that
    waits on Google is acknowledged first, privately and with Discord's thinking indicator,
    and its answer, the translation or the error, follows up privately.
    """
    await translate.run("bom dia", google_answers)

    [answered] = translate.before_google
    assert [answer["type"] for answer in answered] == ["defer"], "Discord hears from Keiko first"
    translate.interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    final = translate.interaction.get_last_response()
    assert final["type"] == "followup_send" and final["ephemeral"] is True
    assert final["embed"] is not None


@pytest.mark.regression
@pytest.mark.parametrize("text", ["   ", "https://keiko.gg"], ids=["empty", "only-a-link"])
async def test_a_message_with_nothing_to_translate_is_answered_at_once(translate, text):
    await translate.run(text)

    assert translate.before_google == [], "Google is never asked"
    translate.interaction.response.defer.assert_not_awaited()
    answer = translate.interaction.get_last_response()
    assert answer["type"] == "send_message" and answer["ephemeral"] is True


def interaction_in(guild, user=None, command=None):
    interaction = MockInteraction(user=user or create_member(guild), guild=guild)
    interaction.command = command
    interaction.client = SimpleNamespace(app_commands=[])
    return interaction


def slow_reads(collection, monkeypatch):
    """A Mongo collection whose every `find_one` takes as long as a blocked call."""
    read = collection.find_one

    def find_one(*args, **kwargs):
        time.sleep(0.25)
        return read(*args, **kwargs)

    monkeypatch.setattr(collection, "find_one", find_one)


async def test_translating_a_message_never_freezes_the_bot(deps, guild, monkeypatch):
    """What a command, a listener or a loop waits on runs off the event loop.

    Reported in the v1 review, after wave 1 moved the hottest paths to threads: `/translate`
    scraped Google Translate from the loop, `/report` waited on Notion there, `/birthday`
    and `/moderations birthdays add` read Mongo and called reminders-api there, the reconcile
    pass ran its whole batch there every 30 minutes, the analytics cog wrote both queues to
    Mongo there every five seconds, every slash command and every feature button counted
    itself in Redis there, and a guild joining or leaving read and wrote moderations there.
    Any of them stops the gateway heartbeat and every interaction for as long as the call
    takes.

    Shared behaviour: every entry point into a blocking call (`asyncio.to_thread` at the
    boundary) and the pool the loop hands them to. Exposed by: the review of what was left.

    Guaranteed, the way `test_event_loop_is_never_blocked.py` measures it: while each of
    these waits on its call, the loop keeps getting control back.
    """
    from app.cogs.base.translate import Translate
    from app.integrations.google_translate import GoogleTranslate

    monkeypatch.setattr(GoogleTranslate, "translate", staticmethod(blocking(None)))
    interaction = interaction_in(guild)
    message = create_message(guild.text_channels[0], create_member(guild), "bom dia")

    ticks = await ticks_while(Translate.translate_message(SimpleNamespace(), interaction, message))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    interaction.assert_response_sent("followup_send", ephemeral=True)


async def test_reporting_a_bug_never_freezes_the_bot(deps, guild, monkeypatch):
    from app.cogs.base.report import Report

    reports = MagicMock()
    reports.send = AsyncMock()
    bot = SimpleNamespace(
        notion=SimpleNamespace(create_report=blocking(None)),
        config=SimpleNamespace(ADMIN_REPORTS_CHANNEL_ID=1),
        get_channel=lambda channel_id: reports,
    )
    monkeypatch.setattr(Report, "_send_report_dm", AsyncMock())
    interaction = interaction_in(guild, command=SimpleNamespace(qualified_name="report"))

    ticks = await ticks_while(Report.report.callback(
        Report(bot), interaction, "title", "description", "block_links"
    ))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    reports.send.assert_awaited_once()


@pytest.fixture
def birthdays_on(monkeypatch):
    """A guild whose birthday feature is on, every read and the save blocking."""
    monkeypatch.setattr("app.data.birthdays.is_birthday_enabled", blocking(True))
    monkeypatch.setattr("app.data.birthdays.find_birthday_config", blocking({"channel_id": "1"}))
    monkeypatch.setattr("app.data.birthdays.find_birthday_item", blocking(None))
    monkeypatch.setattr("app.services.reminders_birthdays.upsert_birthday", blocking(None))


async def test_registering_a_birthday_never_freezes_the_bot(deps, guild, birthdays_on):
    from app.cogs.birthdays import Birthday

    interaction = interaction_in(
        guild, command=SimpleNamespace(qualified_name="birthday")
    )

    ticks = await ticks_while(
        Birthday.birthday_personal.callback(SimpleNamespace(), interaction, month=5, day=12)
    )

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    assert interaction.get_last_response()["type"] == "followup_send"


async def test_adding_a_members_birthday_never_freezes_the_bot(deps, guild, birthdays_on):
    from app.cogs.moderations.birthdays import Birthdays

    admin = create_member(guild, id=222, name="Admin")
    admin.guild_permissions = SimpleNamespace(administrator=True)
    interaction = interaction_in(
        guild, user=admin, command=SimpleNamespace(qualified_name="moderations birthdays add")
    )
    member = create_member(guild, id=555, name="Tester")

    ticks = await ticks_while(
        Birthdays.add.callback(SimpleNamespace(), interaction, member, month=5, day=12)
    )

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    assert interaction.get_last_response()["type"] == "followup_send"


async def test_finishing_the_pending_birthday_reminders_never_freezes_the_bot(monkeypatch):
    from app.cogs.birthdays import Birthday

    monkeypatch.setattr(
        "app.services.reminders_birthdays.reconcile_missing_reminders", blocking(0)
    )

    ticks = await ticks_while(Birthday.reconcile_reminders.coro(SimpleNamespace()))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"


@pytest.fixture
def slow_storage(monkeypatch):
    """Both queues hold a record, and writing either to Mongo blocks."""
    written = []

    def write(batch):
        time.sleep(0.25)
        written.extend(batch)

    monkeypatch.setattr(analytics_sink, "persist", write)
    monkeypatch.setattr("app.data.logs.insert_logs", write)
    analytics.emit("guild.joined", guild_id=1, returning=False)
    debug_logs.record(debug_logs.build_document(level="INFO", message="queued"))
    return written


async def test_flushing_the_queues_never_freezes_the_bot(slow_storage):
    from app.cogs.analytics import Analytics

    ticks = await ticks_while(Analytics.flush_events.coro(SimpleNamespace()))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    assert len(slow_storage) == 2, "both queues were written"


async def test_unloading_the_analytics_cog_never_freezes_the_bot(slow_storage):
    from app.cogs.analytics import Analytics

    loops = SimpleNamespace(
        flush_events=MagicMock(), send_weekly_digest=MagicMock(), export_daily_logs=MagicMock(),
        archive_months=MagicMock(), measure_guild_sizes=MagicMock(), post_daily_backup=MagicMock(),
    )

    ticks = await ticks_while(Analytics.cog_unload(loops))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    assert len(slow_storage) == 2, "both queues were written"


async def test_counting_a_slash_command_never_freezes_the_bot(deps, guild, monkeypatch):
    from app.cogs import events as events_module

    monkeypatch.setattr("app.services.cache.increment_redis_key", blocking(1))
    monkeypatch.setattr(events_module, "increment_redis_key", blocking(1), raising=False)
    interaction = interaction_in(
        guild, command=SimpleNamespace(qualified_name="moderations block links", _attr="block_links")
    )
    interaction.type = discord.InteractionType.application_command

    ticks = await ticks_while(
        events_module.Events(SimpleNamespace(owner_id=1)).on_interaction(interaction)
    )

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"


class Probe:
    """A slash command that answers at once, as every Keiko command is wrapped."""

    answered = []

    @keiko_command(name="probe", description="Keiko answers a probe")
    async def probe(self, interaction: discord.Interaction) -> None:
        Probe.answered.append(interaction)


async def test_a_slash_command_never_waits_on_its_attempt_counter(deps, guild, monkeypatch):
    monkeypatch.setattr(analytics, "count_attempt", blocking(2))
    monkeypatch.setattr(Probe, "answered", [])
    interaction = interaction_in(
        guild, command=SimpleNamespace(qualified_name="moderations block links", _attr="block_links")
    )

    ticks = await ticks_while(Probe.probe.callback(SimpleNamespace(), interaction))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    assert Probe.answered == [interaction]


async def test_opening_a_feature_from_a_button_never_freezes_the_bot(deps, guild, monkeypatch):
    from app.components import buttons

    monkeypatch.setattr(analytics, "count_attempt", blocking(2))
    monkeypatch.setattr("app.services.cache.increment_redis_key", blocking(1))
    monkeypatch.setattr(buttons, "increment_redis_key", blocking(1), raising=False)
    opened = AsyncMock()
    monkeypatch.setattr("app.settings.open_feature", opened)
    interaction = interaction_in(guild)

    ticks = await ticks_while(
        buttons.run_feature_command(interaction, "block_links", "setup_dashboard")
    )

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    opened.assert_awaited_once()


async def test_refreshing_a_session_message_never_freezes_the_bot(deps, guild, monkeypatch):
    from app.components.buttons import JourneyRefreshButton

    monkeypatch.setattr("app.services.journey.rebuild", blocking(None))
    interaction = interaction_in(guild)

    ticks = await ticks_while(JourneyRefreshButton("abc123").callback(interaction))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    interaction.assert_response_sent("defer")


def guild_double(guild_id=4242, owner_id=313):
    return SimpleNamespace(
        id=guild_id,
        member_count=120,
        owner=SimpleNamespace(id=owner_id, mention=f"<@{owner_id}>"),
    )


@pytest.fixture
def events(monkeypatch):
    from app.cogs import events as events_module

    monkeypatch.setattr(
        events_module, "GreetingsView",
        lambda *args, **kwargs: SimpleNamespace(send=AsyncMock()),
    )
    return events_module.Events(
        SimpleNamespace(user=SimpleNamespace(id=99), guilds=[object(), object()])
    )


async def test_a_guild_removing_keiko_never_freezes_the_bot(deps, events, monkeypatch):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": "4242", "is_bot_online": True, "block_links": True}
    )
    slow_reads(deps.mongo_client.guild.moderations, monkeypatch)

    ticks = await ticks_while(events.on_guild_remove(guild_double()))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    moderations = deps.mongo_client.guild.moderations.find_one({"guild_id": "4242"})
    assert moderations["is_bot_online"] is False, "leaving still marks Keiko offline"


async def test_a_guild_adding_keiko_never_freezes_the_bot(deps, events, monkeypatch):
    slow_reads(deps.mongo_client.guild.moderations, monkeypatch)

    ticks = await ticks_while(events.on_guild_join(guild_double(guild_id=777)))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"
    assert deps.mongo_client.guild.moderations.find_one({"guild_id": "777"}), (
        "joining still records the guild"
    )


async def test_a_live_notice_never_waits_on_the_database_on_the_loop(deps, guild, monkeypatch):
    from app.services import notifications_twitch

    deps.twitch.add_user("gaules", user_id="123")
    deps.twitch.set_stream_online("gaules", game="CS2")
    monkeypatch.setattr(
        notifications_twitch, "wait_for_stream_info",
        lambda streamer: deps.twitch.get_stream_info(streamer),
    )
    monkeypatch.setattr(notifications_twitch, "find_last_stream_date", blocking(None))
    monkeypatch.setattr(notifications_twitch, "find_guilds_by_streamer_name", blocking([]))
    monkeypatch.setattr(notifications_twitch, "update_last_stream_date", blocking(None))

    ticks = await ticks_while(notifications_twitch.handle_send_streamer_notification("gaules"))

    assert ticks >= MIN_TICKS, f"the loop only came back {ticks} times"


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


PRIMITIVES = {
    "time.sleep",
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.patch", "requests.head", "requests.options", "requests.request",
}


PRIMITIVE_ROOTS = ("app.mongo_client.", "app.redis_client.", "detectlanguage.")


HANDOFFS = {"asyncio.to_thread", "threading.Thread"}


ADMIN_COMMAND = (
    "an operator command in the admin guild, run by hand; its file is outside the loop "
    "PR of wave 2"
)


INSPECTION_VIEW = "operator inspection views, follow-up"


ALLOWED: Dict[str, str] = {
    "app/cogs/admin/__init__.py::Admin.forget_guild -> "
    "app.data.analytics.delete_analytics_by_guild": ADMIN_COMMAND,
    "app/cogs/admin/__init__.py::Admin.show_guild_journey -> "
    "app.services.admin_analytics.build_guild_journey_embed": ADMIN_COMMAND,
    "app/cogs/admin/configs.py::Configs.show_config -> "
    "app.services.admin.get_admin_configs": ADMIN_COMMAND,
    "app/cogs/admin/configs.py::Configs.update_config -> "
    "app.services.admin.update_admin_configs": ADMIN_COMMAND,
    "app/cogs/admin/subscriptions.py::Subscriptions.show_reminder_subscriptions -> "
    "app.services.subscriptions.get_reminder_subscriptions": ADMIN_COMMAND,
    "app/cogs/admin/subscriptions.py::Subscriptions.show_twitch_subscriptions -> "
    "app.services.subscriptions.get_twitch_subscriptions": ADMIN_COMMAND,
    "app/services/config.py::update_activity -> app.data.config.update_db_configs": (
        ADMIN_COMMAND
    ),
    "app/services/config.py::update_description -> app.data.config.update_db_configs": (
        ADMIN_COMMAND
    ),
    "app/services/config.py::update_status -> app.data.config.update_db_configs": (
        ADMIN_COMMAND
    ),
    "app/settings/discord/callbacks.py::Runtime._open -> "
    "app.settings.discord.observability.open_journey": (
        "the journey reads its history in a worker thread whenever a loop runs; only "
        "the no-loop fallback stays synchronous"
    ),
    "app/views/log_inspection.py::LogInspectionView._get_offline_guild_info_embed -> "
    "app.data.moderations.find_moderations_by_guild": INSPECTION_VIEW,
    "app/views/log_inspection.py::LogInspectionView._get_offline_guild_info_embed -> "
    "app.views.log_inspection.LogInspectionView._add_commands_status": INSPECTION_VIEW,
    "app/views/log_inspection.py::LogInspectionView.get_guild_info_embed -> "
    "app.data.moderations.find_moderations_by_guild": INSPECTION_VIEW,
    "app/views/log_inspection.py::LogInspectionView.get_guild_info_embed -> "
    "app.views.log_inspection.LogInspectionView._add_commands_status": INSPECTION_VIEW,
    "app/views/user_inspection.py::UserInspectionView.send -> "
    "app.views.user_inspection.UserInspectionView._get_embed": INSPECTION_VIEW,
}


Names = List[str]


class Function(NamedTuple):
    """A function of `app/`, with the class it belongs to and the defs nested in it."""

    qualname: str
    node: ast.AST
    owner: Optional[str]
    nested: Dict[str, str]


class Module:
    """One module of `app/`: its imports, aliases, definitions, classes and functions."""

    def __init__(self, root: str, path: str) -> None:
        self.path = os.path.relpath(path, root)
        dotted = self.path[:-3].replace(os.sep, ".")
        self.is_package = dotted.endswith(".__init__")
        self.name = dotted[: -len(".__init__")] if self.is_package else dotted
        with open(path, encoding="utf-8") as handle:
            self.tree = ast.parse(handle.read())
        self.imports: Dict[str, str] = {}
        self.aliases: Dict[str, Names] = {}
        self.definitions: Dict[str, str] = {}
        self.classes: Set[str] = set()
        self.functions: List[Function] = []
        for node in self.tree.body:
            self.bind_import(node, self.imports)
        self.collect(self.tree.body, self.name, None)

    def bind_import(self, node: ast.AST, table: Dict[str, str]) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    table[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    table[root] = root
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = self.name.split(".")
                package = parts if self.is_package else parts[:-1]
                package = package[: len(package) - (node.level - 1)]
                base = ".".join(package + ([base] if base else []))
            for alias in node.names:
                table[alias.asname or alias.name] = f"{base}.{alias.name}"

    def collect(self, body: List[ast.stmt], prefix: str, owner: Optional[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{node.name}"
                if owner is None:
                    self.definitions[node.name] = qualname
                inner = [
                    child for child in ast.walk(node)
                    if child is not node
                    and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                nested = {child.name: f"{qualname}.{child.name}" for child in inner}
                self.functions.append(Function(qualname, node, owner, nested))
                for child in inner:
                    self.functions.append(Function(nested[child.name], child, owner, nested))
            elif isinstance(node, ast.ClassDef):
                qualname = f"{prefix}.{node.name}"
                self.definitions[node.name] = qualname
                self.classes.add(qualname)
                self.collect(node.body, qualname, qualname)
            elif owner is None:
                target = _assigned_name(node)
                expansion = _names(node.value) if target else None
                if target and expansion:
                    self.aliases[target] = expansion


Context = Tuple[Module, Dict[str, str], Dict[str, Names], Optional[str]]


class Scan:
    """The blocking functions of `app/`, and the coroutines that call one directly."""

    def __init__(self, root: str) -> None:
        self.modules = {
            module.name: module for module in map(lambda path: Module(root, path), _files(root))
        }
        self.known = {
            function.qualname
            for module in self.modules.values()
            for function in module.functions
        }
        self.classes = {
            qualname for module in self.modules.values() for qualname in module.classes
        }
        self.clients = self._clients()
        self.returns = self._returns()
        self.calls = {
            function.qualname: (module, function.node, list(self._calls(module, function)))
            for module in self.modules.values()
            for function in module.functions
        }
        self.blocking = self._blocking()

    def findings(self) -> Set[str]:
        found = set()
        for qualname, (module, node, calls) in self.calls.items():
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for callee in calls:
                if self.blocks(callee):
                    found.add(f"{module.path}::{_local(qualname, module)} -> {callee}")
        return found

    def blocks(self, callee: str) -> bool:
        return callee in PRIMITIVES or callee.startswith(PRIMITIVE_ROOTS) or (
            callee in self.blocking
        )

    def _blocking(self) -> Set[str]:
        blocking: Set[str] = set()
        changed = True
        while changed:
            changed = False
            for qualname, (_, node, calls) in self.calls.items():
                if isinstance(node, ast.AsyncFunctionDef) or qualname in blocking:
                    continue
                if any(
                    callee in PRIMITIVES
                    or callee.startswith(PRIMITIVE_ROOTS)
                    or callee in blocking
                    for callee in calls
                ):
                    blocking.add(qualname)
                    changed = True
        return blocking

    def _clients(self) -> Dict[str, str]:
        bot = self.modules.get("app.bot")
        clients: Dict[str, str] = {}
        if bot is None:
            return clients
        for function in bot.functions:
            if not function.qualname.endswith(".DiscordBot.__init__"):
                continue
            for statement in ast.walk(function.node):
                if not (
                    isinstance(statement, ast.Assign)
                    and isinstance(statement.value, ast.Call)
                    and isinstance(statement.value.func, ast.Name)
                ):
                    continue
                created = bot.imports.get(statement.value.func.id)
                for target in statement.targets:
                    if (
                        created
                        and isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        clients[target.attr] = created
        return clients

    def _returns(self) -> Dict[str, Tuple[Context, Names]]:
        """The functions whose whole body hands back a client or a collection."""
        returns: Dict[str, Tuple[Context, Names]] = {}
        for module in self.modules.values():
            for function in module.functions:
                if not isinstance(function.node, ast.FunctionDef):
                    continue
                body = [statement for statement in function.node.body if not _docstring(statement)]
                if len(body) != 1 or not isinstance(body[0], ast.Return):
                    continue
                names = _names(body[0].value) if body[0].value else None
                if names and "()" not in names:
                    context = (module, dict(function.nested), dict(module.aliases), function.owner)
                    returns[function.qualname] = (context, names)
        return returns

    def _calls(self, module: Module, function: Function) -> Iterator[str]:
        local = dict(function.nested)
        for node in ast.walk(function.node):
            module.bind_import(node, local)
        context = (module, local, self._aliases(module, function, local), function.owner)
        coroutine = isinstance(function.node, ast.AsyncFunctionDef)
        for node in _own_nodes(function.node, coroutine):
            if not isinstance(node, ast.Call):
                continue
            callee = self._resolve(node.func, context)
            if callee:
                yield callee
            if coroutine or callee in HANDOFFS:
                continue
            for argument in [*node.args, *(keyword.value for keyword in node.keywords)]:
                handed = self._resolve(argument, context)
                if handed:
                    yield handed

    def _aliases(
        self, module: Module, function: Function, local: Dict[str, str]
    ) -> Dict[str, Names]:
        """What each local name holds: a receiver, a collection, a partial or a lambda."""
        parameters = {
            node.arg for node in ast.walk(function.node.args) if isinstance(node, ast.arg)
        }
        aliases = {
            name: names
            for name, names in module.aliases.items()
            if name not in parameters and name not in local
        }
        context = (module, local, aliases, function.owner)
        assignments = sorted(
            (node for node in _own_nodes(function.node, True) if _assigned_name(node)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for statement in assignments:
            expansion = self._expansion(statement.value, context)
            if expansion:
                aliases[_assigned_name(statement)] = expansion
        return aliases

    def _expansion(self, value: Optional[ast.AST], context: Context) -> Optional[Names]:
        if isinstance(value, ast.Lambda) and isinstance(value.body, ast.Call):
            return _names(value.body.func)
        if (
            isinstance(value, ast.Call)
            and value.args
            and self._resolve(value.func, context) == "functools.partial"
        ):
            return _names(value.args[0])
        return _names(value) if value is not None else None

    def _resolve(self, expression: ast.AST, context: Context) -> Optional[str]:
        names = _names(expression)
        return self._resolve_names(names, context, 0) if names else None

    def _resolve_names(self, names: Names, context: Context, depth: int) -> Optional[str]:
        module, local, aliases, owner = context
        root = names[0]
        if depth > 10:
            return None
        if root in aliases:
            return self._resolve_names([*aliases[root], *names[1:]], context, depth + 1)
        if "()" in names:
            return self._through_a_call(names, context, depth) or self._client_method(names)
        resolved = None
        if root == "self" and owner and len(names) == 2:
            resolved = f"{owner}.{names[1]}"
        elif root in local:
            resolved = self._canonical(".".join([local[root], *names[1:]]))
        elif root in module.imports:
            resolved = self._canonical(".".join([module.imports[root], *names[1:]]))
        elif root in module.definitions:
            resolved = self._canonical(".".join([module.definitions[root], *names[1:]]))
        if resolved in self.known:
            return resolved
        return self._client_method(names) or resolved

    def _client_method(self, names: Names) -> Optional[str]:
        if len(names) >= 3 and names[-2] in self.clients:
            return f"{self.clients[names[-2]]}.{names[-1]}"
        return None

    def _through_a_call(self, names: Names, context: Context, depth: int) -> Optional[str]:
        """A method of what a call made: an instance of a class of `app/`, or what a
        function that only returns a client or a collection hands back."""
        cut = names.index("()")
        made, rest = names[:cut], names[cut + 1:]
        maker = self._resolve_names(made, context, depth + 1) if rest else None
        if maker in self.classes:
            method = ".".join([maker, *rest])
            return method if method in self.known else None
        if maker in self.returns:
            returned_context, returned = self.returns[maker]
            return self._resolve_names([*returned, *rest], returned_context, depth + 1)
        return None

    def _canonical(self, dotted: str, depth: int = 0) -> str:
        parts = dotted.split(".")
        for cut in range(len(parts), 0, -1):
            module = self.modules.get(".".join(parts[:cut]))
            if module is None:
                continue
            rest = parts[cut:]
            if not rest:
                return dotted
            if rest[0] in module.definitions:
                return ".".join([module.definitions[rest[0]], *rest[1:]])
            if rest[0] in module.imports and depth < 10:
                return self._canonical(".".join([module.imports[rest[0]], *rest[1:]]), depth + 1)
            return dotted
        return dotted


def _files(root: str) -> Iterator[str]:
    for folder, _subfolders, filenames in os.walk(os.path.join(root, "app")):
        if "__pycache__" in folder:
            continue
        for filename in sorted(filenames):
            if filename.endswith(".py") and not filename.endswith("_test.py"):
                yield os.path.join(folder, filename)


def _own_nodes(function: ast.AST, coroutine: bool) -> Iterator[ast.AST]:
    skipped: Tuple[type, ...] = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    if coroutine:
        skipped += (ast.Lambda,)
    stack = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, skipped):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _assigned_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target: ast.AST = node.targets[0]
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        target = node.target
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


def _docstring(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)


def _names(expression: ast.AST) -> Optional[List[str]]:
    names: List[str] = []
    while isinstance(expression, (ast.Attribute, ast.Subscript)):
        names.append(expression.attr if isinstance(expression, ast.Attribute) else "[]")
        expression = expression.value
    if isinstance(expression, ast.Name):
        return [expression.id, *reversed(names)]
    if isinstance(expression, ast.Call):
        inner = _names(expression.func)
        return [*inner, "()", *reversed(names)] if inner else None
    return None


def _local(qualname: str, module: Module) -> str:
    return qualname[len(module.name) + 1:]


@pytest.mark.unit
def test_no_coroutine_calls_a_blocking_function():
    """No coroutine anywhere in `app/` calls a blocking function directly.

    Everything runs on one event loop: the gateway heartbeat, every interaction, every
    listener and job. `requests`, the pymongo and Redis clients, `time.sleep`, the language
    detection and every synchronous function that reaches one of them block it, so a
    coroutine hands them to a thread (`asyncio.to_thread`) instead of calling them. Rule 18
    of `.claude/rules/code-style.md` says it for `app/settings/`, where
    `tests/forms/test_boundary.py` refuses the imports; here the whole app is read call by
    call, because a coroutine may import a module that blocks and still only hand its
    functions to a thread.

    How it reads: a synchronous function blocks when it calls (or, synchronously, hands
    along) a blocking primitive or another blocking function of `app/`. Names are resolved
    through each module's imports, a method through `self`, a def nested in a function by
    its name, and a client the bot holds (`bot.twitch`, `bot.youtube`, `bot.reminder`,
    `bot.notion`) through `DiscordBot.__init__`. A name assigned in the function or at the
    top of its module stands for what it holds: a receiver, a collection, an instance of a
    class of `app/`, a `functools.partial` or a lambda; and a function whose whole body
    returns a client or a collection stands for what it returns. A coroutine that calls a
    blocking function directly is a finding. A finding that has to stay for now is listed in
    `ALLOWED` with its reason, and an entry that no longer matches anything fails too, so the
    list only shrinks.

    What it cannot see: `open` and the other builtins that touch files, `subprocess`, a
    method a class inherits (it is found only on the class that defines it), and a
    synchronous helper of higher order that a coroutine hands a blocking function to
    (`helper(things.find_thing)`), which blocks only when its own body names what it calls.
    """
    found = Scan(ROOT).findings()

    assert sorted(found - set(ALLOWED)) == [], (
        "hand the call to a thread (`await asyncio.to_thread(...)`), or list it in "
        "ALLOWED with the reason it has to stay"
    )


@pytest.mark.unit
def test_every_allowed_call_still_exists():
    """An entry whose call moved off the loop goes, so the list only shrinks."""
    assert sorted(set(ALLOWED) - Scan(ROOT).findings()) == []


@pytest.mark.unit
def test_every_allowed_call_says_why():
    assert [entry for entry, reason in ALLOWED.items() if not reason.strip()] == []


def _write(root, path, source):
    full = root / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(source, encoding="utf-8")


@pytest.mark.unit
def test_the_scan_sees_every_way_a_coroutine_reaches_a_blocking_call(tmp_path):
    """The scan is a test too: a checker that went blind would pass everything."""
    _write(tmp_path, "app/__init__.py", "")
    _write(tmp_path, "app/data/__init__.py", "")
    _write(tmp_path, "app/data/things.py", (
        "from app import mongo_client\n"
        "guild_database = mongo_client.guild\n"
        "def find_thing(guild_id):\n"
        "    return mongo_client.guild['things'].find_one({'guild_id': guild_id})\n"
        "def database():\n"
        "    \"\"\"The guild database.\"\"\"\n"
        "    return mongo_client.guild\n"
        "def count_things():\n"
        "    return database().things.count_documents({})\n"
        "def find_other(guild_id):\n"
        "    return guild_database.others.find_one({'guild_id': guild_id})\n"
        "def find_kept(guild_id):\n"
        "    kept = mongo_client.guild.kept\n"
        "    return kept.find_one({'guild_id': guild_id})\n"
    ))
    _write(tmp_path, "app/integrations/__init__.py", "")
    _write(tmp_path, "app/integrations/web.py", (
        "import requests\n"
        "class Client:\n"
        "    def fetch(self, url):\n"
        "        return requests.get(url, timeout=5)\n"
    ))
    _write(tmp_path, "app/bot.py", (
        "from app.integrations.web import Client\n"
        "class DiscordBot:\n"
        "    def __init__(self):\n"
        "        self.web = Client()\n"
    ))
    _write(tmp_path, "app/services/__init__.py", "")
    _write(tmp_path, "app/services/feature.py", (
        "import asyncio\n"
        "import functools\n"
        "import time\n"
        "from functools import partial\n"
        "from app import bot, mongo_client\n"
        "from app.data import things\n"
        "from app.integrations.web import Client\n"
        "def summary(guild_id):\n"
        "    return run(things.find_thing, guild_id)\n"
        "def run(operation, guild_id):\n"
        "    return operation(guild_id)\n"
        "async def direct(guild_id):\n"
        "    return things.find_thing(guild_id)\n"
        "async def through_a_helper(guild_id):\n"
        "    return summary(guild_id)\n"
        "async def through_the_bot(url):\n"
        "    return bot.web.fetch(url)\n"
        "async def sleeping():\n"
        "    time.sleep(1)\n"
        "async def through_a_nested_def(guild_id):\n"
        "    def load():\n"
        "        return things.find_thing(guild_id)\n"
        "    return load()\n"
        "async def through_a_receiver_in_a_local(url):\n"
        "    web = bot.web\n"
        "    return web.fetch(url)\n"
        "async def through_an_instance(url):\n"
        "    client = Client()\n"
        "    return client.fetch(url)\n"
        "async def through_a_collection_in_a_local(guild_id):\n"
        "    collection = mongo_client.guild.things\n"
        "    return collection.find_one({'guild_id': guild_id})\n"
        "async def through_a_partial(guild_id):\n"
        "    load = functools.partial(things.find_thing, guild_id)\n"
        "    later = partial(summary, guild_id)\n"
        "    return load(), later()\n"
        "async def through_data_functions(guild_id):\n"
        "    return things.count_things(), things.find_other(guild_id), things.find_kept(1)\n"
        "async def handed_to_a_thread(guild_id):\n"
        "    def load():\n"
        "        return things.find_thing(guild_id)\n"
        "    held = functools.partial(things.find_thing, guild_id)\n"
        "    await asyncio.to_thread(things.find_thing, guild_id)\n"
        "    await asyncio.to_thread(lambda: things.find_thing(guild_id))\n"
        "    await asyncio.to_thread(load)\n"
        "    await asyncio.to_thread(held)\n"
        "    return await asyncio.to_thread(summary, guild_id)\n"
    ))

    found = Scan(str(tmp_path)).findings()

    feature = "app/services/feature.py::"
    assert found == {
        f"{feature}direct -> app.data.things.find_thing",
        f"{feature}through_a_helper -> app.services.feature.summary",
        f"{feature}through_the_bot -> app.integrations.web.Client.fetch",
        f"{feature}sleeping -> time.sleep",
        f"{feature}through_a_nested_def -> app.services.feature.through_a_nested_def.load",
        f"{feature}through_a_receiver_in_a_local -> app.integrations.web.Client.fetch",
        f"{feature}through_an_instance -> app.integrations.web.Client.fetch",
        f"{feature}through_a_collection_in_a_local -> "
        "app.mongo_client.guild.things.find_one",
        f"{feature}through_a_partial -> app.data.things.find_thing",
        f"{feature}through_a_partial -> app.services.feature.summary",
        f"{feature}through_data_functions -> app.data.things.count_things",
        f"{feature}through_data_functions -> app.data.things.find_other",
        f"{feature}through_data_functions -> app.data.things.find_kept",
    }


@pytest.mark.regression
async def test_the_daily_backup_never_freezes_the_bot(deps, monkeypatch):
    """The daily backup reads, zips and encrypts the database without holding the loop.

    The backup reads every collection Keiko keeps, once a day, from a loop task, and
    zips and encrypts it: all of it synchronous work that would stop the gateway,
    every interaction and every listener for as long as it took.

    Broke as: the first version of this test sat in `test_event_loop_is_never_blocked.py`
    and used `AsyncMock`, which that module no longer imports since the runtime PR,
    so it would fail with a NameError rather than on the loop. It lives here, with
    its own imports.

    Shared behaviour: the `asyncio.to_thread` seam of `Analytics.post_daily_backup`.
    Consumer that exposed it: the daily backup (roadmap #28). Guaranteed: the loop
    keeps ticking while the backup is built, and the one message still goes out;
    and it keeps ticking while the pass reads and moves how far back the logs files
    channel is known clear of old files (`logs_archive.delete_old_files`).
    """
    deps.mongo_client.guild.moderations.insert_one({"guild_id": "1"})
    monkeypatch.setattr("app.data.util.iter_documents", blocking([{"guild_id": "1"}]))
    async def nothing_older():
        return
        yield

    channel = SimpleNamespace(
        guild=SimpleNamespace(filesize_limit=10 * 1024 * 1024),
        send=AsyncMock(),
        history=lambda **kwargs: nothing_older(),
    )
    bot = SimpleNamespace(
        config=SimpleNamespace(
            ADMIN_LOGS_FILES_CHANNEL_ID=7,
            BACKUP_AGE_PUBLIC_KEY=str(x25519.Identity.generate().to_public()),
        ),
        get_channel=lambda _id: channel,
        user=SimpleNamespace(id=4242),
    )

    ticks = await ticks_while(Analytics.post_daily_backup.coro(SimpleNamespace(bot=bot)))

    assert ticks >= MIN_TICKS
    channel.send.assert_awaited_once()


@pytest.mark.regression
@pytest.mark.parametrize("call", ["find_retention", "update_retention"])
async def test_clearing_the_old_files_never_freezes_the_bot(monkeypatch, call):
    monkeypatch.setattr(f"app.data.logs.{call}", blocking(None))
    posted = logs_archive.BackupFile("keiko_backup_2026-10-05.zip.age", b"sealed", {"guild.x": 1})
    monkeypatch.setattr(logs_archive, "build_backup", lambda *args: ([posted], []))
    walks = []

    def history(**kwargs):
        async def nothing_older():
            walks.append(kwargs)
            return
            yield

        return nothing_older()

    channel = SimpleNamespace(
        guild=SimpleNamespace(filesize_limit=10 * 1024 * 1024),
        send=AsyncMock(),
        history=history,
    )
    bot = SimpleNamespace(
        config=SimpleNamespace(
            ADMIN_LOGS_FILES_CHANNEL_ID=7,
            BACKUP_AGE_PUBLIC_KEY=str(x25519.Identity.generate().to_public()),
        ),
        get_channel=lambda _id: channel,
        user=SimpleNamespace(id=4242),
    )

    ticks = await ticks_while(Analytics.post_daily_backup.coro(SimpleNamespace(bot=bot)))

    assert ticks >= MIN_TICKS
    assert len(walks) == 1, "the old files were not looked for"
