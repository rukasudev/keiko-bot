"""The operator screens read numbers the sink already added up, and those agree with
what happened.

Each test drives the events through `analytics.record` and the sink, then reads the
screen an operator opens (`/admin insights`, `/admin guild`, `/admin overview`, the
weekly digest, `/admin forget`), so it asserts what the operator sees.

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

Shared behaviour: the analytics sink (`app/core/analytics_sink.py`), the reports
(`app/core/analytics_reports.py`), the admin services and the report browser.
"""
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from app.data import wait
from app.core import analytics, analytics_sink
from app.services import admin, admin_analytics, admin_digest
from tests.mocks.database import answering
from tests.generators.events import record_event
from tests.mocks.discord import MockInteraction, create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("analytics_reports")]

FEATURE = "welcome_messages"


def flush_as_of(days):
    """Write what was emitted as if it had happened `days` ago."""
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    batch = analytics.drain()
    for envelope in batch:
        envelope["ts"] = moment
    wait(analytics_sink.persist(batch))


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


def test_no_step_of_the_funnel_holds_more_servers_than_the_setups_opened(deps):
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


def refusing(name):
    def refuse(*_args, **_kwargs):
        raise AssertionError(f"the overview called {name}")

    return refuse


@pytest.fixture
def overview_bot(monkeypatch):
    monkeypatch.setattr("app.core.cache.redis_client", SimpleNamespace(
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
    from app.cogs.admin import Admin

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
