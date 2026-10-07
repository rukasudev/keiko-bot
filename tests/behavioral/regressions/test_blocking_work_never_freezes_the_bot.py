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
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.decorators import keiko_command
from app.services import analytics, analytics_sink, debug_logs
from tests.behavioral.regressions.test_event_loop_is_never_blocked import (
    MIN_TICKS,
    blocking,
    ticks_while,
)
from tests.mocks.discord import MockInteraction, create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("event_loop")]


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
        flush_events=MagicMock(), send_weekly_digest=MagicMock(), export_daily_logs=MagicMock()
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
