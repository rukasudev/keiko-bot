"""Keiko's product analytics, from the count to the operator's screen: the counting
windows in Redis, what is counted (a greeting, a change with the analytics off), the
reports that read stored configuration, and the numbers `/admin insights`, `/admin
guild`, `/admin overview`, the weekly digest and `/admin forget` show."""
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.commands.admin import analytics as admin_analytics
from app.commands.admin import digest as admin_digest
from app.commands.admin import service as admin
from app.commands.admin.cog import Admin
from app.commands.setup.service import GreetingsView, guild_history, history_fields
from app.constants import Commands
from app.core import analytics, analytics_reports, analytics_writer, cache
from app.core.utils import ml
from app.data import wait
from tests.behavioral.flows.block_links import ENABLED
from tests.behavioral.flows.common import GUILD_ID, seed_document
from tests.generators.events import age_events, record_event
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks.database import answering
from tests.mocks.discord import MockInteraction, create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("analytics_reports"),
]

COUNTED_GUILD = "80"
COUNTED_FEATURE = "block_links"
WINDOW = Commands.ANALYTICS_ATTEMPTS_WINDOW_SECONDS
HOUR = 60 * 60
GREETED_GUILD = 987
FEATURE = "welcome_messages"
LOCALE = "pt-br"


def greeted_guild(db, *channels):
    guild = SimpleNamespace(
        id=GREETED_GUILD, text_channels=list(channels), me=SimpleNamespace()
    )
    recorded_server(db, guild.id)
    return guild


def greeting_channel(writable=True, send=None):
    return SimpleNamespace(
        permissions_for=lambda _member: SimpleNamespace(send_messages=writable),
        send=send or AsyncMock(),
    )


def greetings(recorded):
    return [
        event["props"]["channel_found"]
        for event in recorded
        if event["type"] == "guild.greeting_sent"
    ]


def field(usage, key):
    return next(row for row in usage["fields"] if row["key"] == key)


def configure_birthdays(deps, guild_id, *, birthdays=1, default_message=True):
    """A guild configured through the real birthday flow, stored as it stores."""
    seed_cog(deps.mongo_client, "reminders_birthday", {
        "guild_id": guild_id,
        "channel": f"chan-{guild_id}",
        "mention": "everyone",
        "notification_time": "09:00",
        **({"default_message": {
            "mode": "custom",
            "title": "Happy birthday!",
            "content": "{user} is one year older today",
        }} if default_message else {}),
    })
    for index in range(birthdays):
        deps.mongo_client.guild.members.insert_one({
            "_id": f"{guild_id}:u{index}",
            "guild_id": guild_id,
            "user_id": f"u{index}",
            "options": {"reminders_birthday": {"date": "03-15", "month": 3, "day": 15}},
        })


def flush_as_of(days):
    """Write what was emitted as if it had happened `days` ago."""
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    batch = analytics.drain()
    for envelope in batch:
        envelope["ts"] = moment
    wait(analytics_writer.persist(batch))


def opened_and_completed(guild_id, session):
    record_event(
        "feature.setup_opened", guild_id=guild_id, user_id="9", feature=FEATURE,
        source="slash", session_id=session,
    )
    record_event(
        "setup.completed", guild_id=guild_id, user_id="9", feature=FEATURE,
        source="slash", session_id=session, duration_ms=1000, steps_viewed=3,
    )


def percentages(text):
    return [int(share) for share in re.findall(r"\((\d+)%\)", text)]


def refusing(name):
    def refuse(*_args, **_kwargs):
        raise AssertionError(f"the overview called {name}")

    return refuse


@pytest.fixture
def overview_bot(monkeypatch):
    monkeypatch.setattr(cache, "redis_client", SimpleNamespace(
        scan_iter=refusing("SCAN"), get=refusing("GET"), dbsize=lambda: 7,
    ))
    return SimpleNamespace(
        ready_time=datetime.now() - timedelta(hours=1),
        latency=0.05,
        status=discord.Status.online,
        activity=SimpleNamespace(name="Keiko"),
        guilds=[],
        extensions={},
        get_guild=lambda guild_id: None,
        twitch=SimpleNamespace(get_subscriptions=refusing("Twitch")),
        config=SimpleNamespace(ENVIRONMENT="test"),
    )


def test_the_attempt_window_ends_a_day_after_the_first_attempt(deps):
    """A counting window closes a fixed time after its first count, however busy it is.

    Broke as: the increment helper sent EXPIRE with every INCRBY, so each attempt pushed the
    end of the window another 24 hours away. A server that opened a feature at least once a
    day never saw its window close, and the log footnote kept telling the operator "the 9th
    run in the last 24h" about runs spread over a week.

    Shared behaviour: `cache.increment_redis_key_with_expiration`, the one helper every
    expiring counter of Redis goes through.
    Exposed by: `analytics.count_attempt`, the attempt footnote on a command's log message.

    Guaranteed: the TTL is set by the first count only; a later count adds to the window
    without moving its end, and the first count after the end starts a new window.
    """
    assert analytics.count_attempt(COUNTED_GUILD, COUNTED_FEATURE) == 1

    deps.redis_client.advance(WINDOW - HOUR)
    second = analytics.count_attempt(COUNTED_GUILD, COUNTED_FEATURE)
    assert analytics.describe_attempt(second, COUNTED_FEATURE) == (
        f"⚠️ 2º run of `{COUNTED_FEATURE}` by this guild in the last 24h"
    )

    deps.redis_client.advance(HOUR)
    after_the_window = analytics.count_attempt(COUNTED_GUILD, COUNTED_FEATURE)

    assert after_the_window == 1, "the second attempt pushed the window's end a day away"
    assert analytics.describe_attempt(after_the_window, COUNTED_FEATURE) is None


def test_a_later_count_keeps_the_ttl_the_first_one_set(deps):
    key = Commands.REDIS_ATTEMPTS_WINDOW.format(
        guild_id=COUNTED_GUILD, feature=COUNTED_FEATURE
    )

    cache.increment_redis_key_with_expiration(key, 1, WINDOW)
    deps.redis_client.advance(HOUR)
    cache.increment_redis_key_with_expiration(key, 2, WINDOW)

    assert deps.redis_client.get(key) == "3"
    assert deps.redis_client.ttl(key) == WINDOW - HOUR


async def test_a_greeting_discord_refused_is_never_counted_as_sent(deps, analytics_events):
    """A server is counted as greeted only once its greeting is on a channel.

    Broke as: `GreetingsView.send` emitted `guild.greeting_sent` with
    `channel_found=True` before `channel.send`, so a greeting Discord refused (a
    channel override the permission check does not see, an outage) was still counted
    as delivered, and the guild profile stamped `greeting_sent_at` for a server that
    never saw it.

    Shared behaviour: the onboarding greeting (app/commands/setup/service.py), sent by
    `Events.on_guild_join`, and the guild profile and admin digest that read its
    event (app/core/analytics_writer.py, app/commands/admin/analytics.py).

    Guaranteed: a greeting that reached a channel is counted once with
    `channel_found=True`; a greeting whose send failed is never counted as sent; a
    server with no writable channel is still counted with `channel_found=False`.
    """
    refused = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Access"
    )
    guild = greeted_guild(
        deps.mongo_client, greeting_channel(send=AsyncMock(side_effect=refused))
    )

    with pytest.raises(discord.Forbidden):
        await GreetingsView().send(guild)

    assert greetings(analytics_events) == [], (
        "a greeting Discord refused was counted as posted on a channel"
    )


async def test_a_greeting_on_a_channel_is_counted_once(deps, analytics_events):
    channel = greeting_channel()
    guild = greeted_guild(deps.mongo_client, greeting_channel(writable=False), channel)

    await GreetingsView().send(guild)

    channel.send.assert_awaited_once()
    assert greetings(analytics_events) == [True]


async def test_a_server_without_a_writable_channel_is_counted_as_not_found(
    deps, analytics_events
):
    guild = greeted_guild(deps.mongo_client, greeting_channel(writable=False))

    await GreetingsView().send(guild)

    assert greetings(analytics_events) == [False]


async def test_a_pause_with_analytics_off_still_lists_in_the_history(
    scenario_factory, deps
):
    """A change still reaches the History with the product analytics turned off.

    Broke as: `ANALYTICS_ENABLED=false` made `record` return before it wrote a change, and
    `events.changes` is the only history a server has, so a feature paused, edited or set up while the
    analytics were off never showed in /setup's History or its panel's.

    Shared behaviour: `app.core.analytics.record`, the one writer of every change.
    Consumer that exposed it: every feature's commit (a block links pause here) and the History.
    Guaranteed: the kill switch silences the queue (counts, activity, interactions) and
    the journey, never a `collection: changes` event.
    """
    analytics.configure(SimpleNamespace(ANALYTICS_ENABLED=False))

    seed_document(deps, "block_links", ENABLED)
    scenario = await scenario_factory(locale=LOCALE).start_command("block_links")
    await scenario.click("pause")
    await analytics.written()
    await scenario.finish()

    records = await guild_history(GUILD_ID)
    interaction = SimpleNamespace(
        locale=LOCALE, guild=None, client=SimpleNamespace(user=SimpleNamespace(id=999))
    )
    rendered = "\n".join(history_fields(records, interaction, LOCALE))

    assert [(record["cog"], record["type"]) for record in records] == [
        ("block_links", "feature.paused")
    ]
    feature = ml("block_links.command.setup.label", locale=LOCALE)
    paused = ml("commands.records.history.lines.paused", locale=LOCALE)
    assert f"{feature} · {paused}" in rendered
    assert analytics.drain() == [], "the queue still took events with analytics off"


def test_a_feature_that_stores_its_own_shape_is_not_reported_as_unused(deps):
    """The reports that read stored configuration must agree with the database.

    The first weekly digest that reached the admin channel said seven settings were
    used by nobody. Five of them were used by everybody: `config_usage` walked the
    raw `guild.<cog_key>` document looking for the keys the YAML names, and a
    feature that keeps its own storage shape — birthdays then stored `channel_id`, a
    nested `default_message`, and the birthdays themselves in another database —
    answered every lookup with `None`. The report is read as "candidates for
    removal", so a false zero is an invitation to delete a setting every guild uses.

    What must stay guaranteed:

    - a feature whose storage shape differs from its form is read through the same
      translation the manager uses, never through the raw document;
    - a setting left on the default its YAML declares is not reported as unused
      without saying so;
    - the digest never prints `None` where a step key belongs;
    - the headline counts the guilds the bot is in, not the guilds analytics
      happened to see since it was deployed.

    The birthdays live in another database, the default message is one object.
    """
    for guild_id in ("1", "2", "3"):
        configure_birthdays(deps, guild_id, birthdays=2)

    usage = wait(analytics_reports.config_usage("reminders_birthday"))

    assert usage["guilds"] == 3
    assert field(usage, "channel")["filled"] == 3
    assert field(usage, "reminders_birthday")["filled"] == 3, (
        "the registered birthdays live in guild.members, not in the guild document"
    )
    assert field(usage, "default_message_title")["filled"] == 3, (
        "the three default_message_* settings are one nested object in storage"
    )


def test_a_guild_that_left_the_optional_parts_empty_still_counts_as_zero(deps):
    configure_birthdays(deps, "1", birthdays=0, default_message=False)

    usage = wait(analytics_reports.config_usage("reminders_birthday"))

    assert field(usage, "channel")["filled"] == 1
    assert field(usage, "reminders_birthday")["filled"] == 0
    assert field(usage, "default_message_title")["filled"] == 0


def test_the_value_a_card_draws_is_not_a_value_a_guild_chose(deps):
    """The mirror image of the false zero, and just as wrong.

    The manager needs every row to render, so a guild that never picked a
    message mode still draws as `default`. Reading that back as configuration
    reported three untouched guilds as having chosen one.
    """
    configure_birthdays(deps, "1", default_message=False)
    configure_birthdays(deps, "2", default_message=False)
    configure_birthdays(deps, "3", default_message=True)

    usage = wait(analytics_reports.config_usage("reminders_birthday"))

    assert field(usage, "default_message_mode")["filled"] == 1, (
        "only the guild that actually chose a mode has one"
    )
    assert field(usage, "default_message_mode")["default"] == "default", (
        "and the YAML still says what the other two are running"
    )


def test_a_choice_nobody_touched_is_not_a_choice_somebody_made(deps):
    """A config that never saved a mention is not one that chose to ping nobody,
    the way `bool(None)` once read as a `False` someone picked."""
    seed_cog(deps.mongo_client, "reminders_birthday", {
        "guild_id": "1", "channel": "c1",
    })
    seed_cog(deps.mongo_client, "reminders_birthday", {
        "guild_id": "2", "channel": "c2", "mention": "none",
    })

    mention = field(wait(analytics_reports.config_usage("reminders_birthday")), "mention")

    assert mention["filled"] == 1
    assert mention["values"] == {"none": 1}


def test_a_setting_left_on_its_yaml_default_says_so_instead_of_reading_as_dead(deps):
    """No guild stores `mode`, because block_all is the default nobody changed."""
    for guild_id in ("1", "2"):
        seed_cog(deps.mongo_client, "block_links", {
            "guild_id": guild_id,
            "allowed_links": {"values": ["youtube.com"]},
            "answer": "no links here",
        })

    mode = field(wait(analytics_reports.config_usage("block_links")), "mode")

    assert mode["filled"] == 0
    assert mode["default"] == "block_all", "the YAML declares what the silence means"

    description = wait(admin_analytics.build_config_usage_embed("block_links")).description
    assert "block_all" in description


def test_a_setting_with_no_declared_default_stays_a_plain_zero(deps):
    seed_cog(deps.mongo_client, "block_links", {
        "guild_id": "1", "answer": "no links here",
    })

    usage = wait(analytics_reports.config_usage("block_links"))
    assert field(usage, "custom_links")["default"] is None


def test_the_digest_never_prints_none_where_a_step_belongs(deps):
    """A setup abandoned on the opening screen reached no step at all."""
    record_event(
        "feature.setup_opened", guild_id="1", user_id="9",
        feature="reminders_birthday", source="slash", session_id="s1",
    )
    wait(analytics.flush())
    age_events(deps.mongo_client, datetime.now(timezone.utc) - timedelta(days=1))

    description = wait(admin_digest.build_weekly_digest()).description

    assert "None" not in description
    assert "died before the first step" in description


def test_the_headline_counts_the_guilds_the_bot_is_in(deps):
    """A guild with no event since the deploy has no profile, and still exists."""
    analytics.record_value("1", "welcome_messages")
    wait(analytics.flush())

    description = wait(admin_digest.build_weekly_digest(guild_count=73)).description

    assert "**73** guilds" in description
    assert "**1** delivered value" in description


def test_no_step_of_the_funnel_holds_more_servers_than_the_setups_opened(deps):
    """The operator screens read numbers the analytics writer already added up, and
    those agree with what happened.

    Each test of the operator screens drives the events through `analytics.record` and
    the analytics writer, then reads the screen an operator opens (`/admin insights`,
    `/admin guild`, `/admin overview`, the weekly digest, `/admin forget`), so it
    asserts what the operator sees.

    Broke as (the inventory of database PR 2):

    - the funnel divided guild counts kept for good by setup events kept 90 days, so a
      step could hold more servers than the step before it (200% of the setups opened);
    - `feature.disabled` stamped `disabled_at` and nothing cleared it, so a feature turned
      off and on again was never active again on `/admin guild`;
    - at-risk read only servers that once delivered value, so a server that added Keiko
      and never got anything from it, the likeliest to leave, was never listed;
    - drop-off ignored `setup.abandoned` and guessed expiry from the clock, so a session
      the engine had just closed as abandoned still read as open and was not counted;
    - the overview walked Redis for counters no event fed and asked Twitch live on every
      open, so a command's calls came from a second counting path and Twitch's outage
      emptied the integrations line;
    - the digest put the 90-day drop-off and step times under a week's title;
    - `/admin guild` read every event of a server to show 25;
    - `/admin forget` erased three old collections and left the numbers, the traces and
      the block records of the server behind.

    Shared behaviour: the analytics writer (`app/core/analytics_writer.py`), the reports
    (`app/core/analytics_reports.py`), the admin services and the report browser.
    """
    for guild_id in ("101", "102"):
        opened_and_completed(guild_id, f"s{guild_id}")
        analytics.record_value(guild_id, FEATURE)
    wait(analytics.flush())
    deps.mongo_client.guild.analytics_events.delete_many({})
    record_event(
        "feature.setup_opened", guild_id="103", user_id="9", feature=FEATURE,
        source="slash", session_id="s103",
    )
    wait(analytics.flush())

    description = wait(admin_analytics.build_funnel_embed(FEATURE)).description

    assert percentages(description) and max(percentages(description)) <= 100, description
    assert "**Setup opened** — 3 (100%)" in description
    assert "**Delivered value** — 2 (67%)" in description


def test_a_feature_turned_off_and_on_again_counts_as_active(deps):
    opened_and_completed("111", "s111")
    record_event("feature.disabled", guild_id="111", feature=FEATURE, source="manager")
    wait(analytics.flush())
    record_event("feature.enabled", guild_id="111", feature=FEATURE, source="manager")
    wait(analytics.flush())

    description = wait(admin_analytics.build_guild_journey_embed("111")).description

    assert "**1** active of **1** configured" in description, description


def test_a_server_that_never_got_value_is_at_risk_once_it_is_old_enough(deps):
    record_event("guild.joined", guild_id="121", returning=False)
    flush_as_of(days=30)
    record_event("guild.joined", guild_id="122", returning=False)
    wait(analytics.flush())

    description = wait(admin_analytics.build_churn_embed()).description

    assert "**At risk** (1 guilds quiet for 14+ days)" in description, description
    assert "`121` — quiet **30d**, never delivered" in description
    assert "`122`" not in description, "a server that just arrived is not at risk"


def test_a_session_the_engine_closed_as_abandoned_is_counted_where_it_died(deps):
    session = dict(guild_id="131", user_id="9", feature=FEATURE, source="slash")
    record_event("feature.setup_opened", session_id="s131", **session)
    record_event(
        "setup.step_viewed", session_id="s131", step_key="channel",
        step_action="select", step_index=1, **session,
    )
    record_event("setup.abandoned", session_id="s131", step_key="channel", **session)
    wait(analytics.flush())

    description = wait(admin_analytics.build_dropoff_embed(FEATURE)).description

    assert "`1x` **welcome_messages**" in description, description
    assert "0 discarded, 1 expired" in description


async def test_the_overview_counts_commands_from_their_events_and_twitch_from_keiko(
    deps, overview_bot, monkeypatch
):
    monkeypatch.setattr(admin, "servers_overview", answering({}))
    for _ in range(2):
        record_event("command.invoked", guild_id="141", command="birthday", source="slash")
    record_event("command.failed", guild_id="141", command="birthday", error_type="KeyError")
    await analytics.flush()
    for login in ("gaules", "alanzoka"):
        deps.mongo_client.keiko.subscriptions.insert_one(
            {"_id": f"twitch:{login}", "platform": "twitch", "account": login}
        )

    data = await admin.get_overview_data(overview_bot)
    embed = admin.build_overview_embed(overview_bot, data)
    fields = {field.name: field.value for field in embed.fields}

    [usage_name] = [name for name in fields if "Command Usage" in name]
    assert "last 30 days" in usage_name, "the usage title names its window"
    usage = fields[usage_name]
    assert "birthday: **2** calls | **1** errors (50.0%)" in usage, usage
    integrations = next(value for name, value in fields.items() if "Integrations" in name)
    assert "Twitch subs: **2** unique streamers" in integrations, integrations


def test_the_digest_says_which_sections_cover_ninety_days(deps):
    session = dict(guild_id="151", user_id="9", feature=FEATURE, source="slash")
    record_event("feature.setup_opened", session_id="s151", **session)
    record_event(
        "setup.step_completed", session_id="s151", step_key="channel",
        step_action="select", step_index=1, ms_on_step=4000, **session,
    )
    record_event("setup.discarded", session_id="s151", step_key="channel", **session)
    wait(analytics.flush())

    description = wait(admin_digest.build_weekly_digest()).description

    assert "**⏱️ Slowest steps** (last 90 days)" in description, description
    assert re.search(r"died at `[^`]+` \([^)]*, last 90 days\)", description), description


def test_a_servers_story_reads_only_the_events_it_shows(deps, monkeypatch):
    for index in range(60):
        record_event(
            "feature.setup_opened", guild_id="161", user_id="9", feature=FEATURE,
            source="slash", session_id=f"s{index}",
        )
    wait(analytics.flush())
    collection = deps.mongo_client.events.interactions
    find, yielded = collection.find, []

    def counting_find(*args, **kwargs):
        cursor = find(*args, **kwargs)
        iterate = cursor.__iter__
        cursor.__class__ = type("Counted", (cursor.__class__,), {
            "__iter__": lambda self: (yielded.append(row) or row for row in iterate()),
        })
        return cursor

    monkeypatch.setattr(collection, "find", counting_find)

    wait(admin_analytics.build_guild_journey_embed("161"))

    assert 0 < len(yielded) <= 25, f"read {len(yielded)} events to show 25"


async def test_forgetting_a_server_erases_its_events_numbers_traces_and_records(deps):
    opened_and_completed("171", "s171")
    analytics.record_value("171", FEATURE)
    record_event("feature.paused", guild_id="171", user_id="9", feature=FEATURE)
    await analytics.written()
    await analytics.flush()
    deps.mongo_client.guild.blocked_links.insert_one({"guild_id": "171", "host": "x.io"})
    deps.mongo_client.guild.analytics_events.insert_one({"guild_id": "171"})
    deps.mongo_client.events[FEATURE].insert_one({"guild_id": "171"})
    deps.mongo_client.traces.runs.insert_one({"trace_id": "abc123", "guild_id": "171"})
    opened_and_completed("172", "s172")
    await analytics.flush()
    guild = create_guild()
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = SimpleNamespace(qualified_name="admin forget", _attr=None)

    await Admin.forget_guild.callback(Admin.__new__(Admin), interaction, "171")

    left = {
        f"{database}.{name}": deps.mongo_client[database][name].count_documents(
            {"guild_id": "171"}
        ) + deps.mongo_client[database][name].count_documents({"_id": "171"})
        for database, name in (
            ("events", "changes"),
            ("events", "activity"),
            ("events", "interactions"),
            ("events", FEATURE),
            ("guild", "analytics_events"),
            ("guild", "blocked_links"),
            ("analytics", "counts"),
            ("analytics", "lifetime"),
            ("traces", "runs"),
        )
    }
    assert left == {name: 0 for name in left}, left
    assert deps.mongo_client.analytics.lifetime.find_one({"_id": "172"})
    assert deps.mongo_client.events.interactions.count_documents({"guild_id": "172"})
