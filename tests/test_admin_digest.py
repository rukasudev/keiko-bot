"""The weekly digest — the only surface that does not wait to be asked for.

Because it arrives on its own, the bar is different from a command: it has to
be readable when nothing happened, lead with what needs a decision, and never
break on a fresh install with no data at all.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import admin_digest
from app.core import analytics, analytics_sink
from tests.generators.moderations import seed_cog
from tests.generators.events import age_events, record_event

pytestmark = pytest.mark.unit


def flush():
    analytics.flush()


def flush_as_of(days):
    """Write what was emitted as if it had happened `days` ago."""
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    batch = analytics.drain()
    for envelope in batch:
        envelope["ts"] = moment
    analytics_sink.persist(batch)


def age_everything(deps, days):
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    age_events(deps.mongo_client, moment)


def test_it_still_sends_something_on_a_completely_empty_install():
    embed = admin_digest.build_weekly_digest()

    assert embed.title.startswith("📊 Keiko")
    assert "0** guilds" in embed.description
    assert "Nothing needs a decision this week." in embed.description


def test_a_quiet_week_says_so_instead_of_going_silent():
    record_event("guild.joined", guild_id="1", returning=False)
    analytics.record_value("1", "welcome_messages")
    flush()

    description = admin_digest.build_weekly_digest().description

    assert "Nothing needs a decision this week." in description
    assert "welcomes sent" in description


def test_it_leads_with_what_needs_a_decision(deps):
    record_event(
        "feature.setup_opened", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id="s1",
    )
    record_event(
        "setup.step_viewed", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id="s1", step_key="custom_link",
        step_action="modal", step_index=2,
    )
    flush()
    age_everything(deps, days=1)

    description = admin_digest.build_weekly_digest().description

    attention = description.index("Needs attention")
    assert "custom_link" in description
    assert attention < description.index("Delivered value") if "Delivered value" in description else True


def test_permission_failures_are_called_out_as_the_bot_being_unable_to_work():
    for guild in ("1", "2"):
        record_event(
            "value.blocked_by_permission", guild_id=guild,
            feature="block_links", error_type="Forbidden",
        )
    flush()

    description = admin_digest.build_weekly_digest().description
    assert "2** guilds where Discord refused" in description


def test_it_reports_settings_nobody_uses(deps):
    for index in range(5):
        seed_cog(deps.mongo_client, "welcome_messages", {
            "guild_id": str(index),
            "welcome_messages_channel": {"values": ["1"]},
            "welcome_design": "server_blur",
            "welcome_messages_title": "Bem-vindo",
            "welcome_messages_footer": "rodape",
            "welcome_messages": "oi",
            "welcome_custom_image": None,
        })

    description = admin_digest.build_weekly_digest().description
    assert "welcome_custom_image" in description
    assert "0** of **5** guilds" in description


def test_the_unused_block_does_not_get_filled_by_one_noisy_form(deps):
    """Three lines from the same feature would crowd out every other one."""
    for index in range(4):
        seed_cog(deps.mongo_client, "block_links", {"guild_id": str(index)})
        seed_cog(deps.mongo_client, "welcome_messages", {"guild_id": str(index)})

    lines = [
        line for line in admin_digest.build_weekly_digest().description.splitlines()
        if line.startswith("• **") and "guilds" in line
    ]
    features = [line.split("**")[1] for line in lines]

    assert len(features) == len(set(features)), "one line per feature at most"


def test_deliveries_are_summed_from_the_counters_not_from_documents():
    for _ in range(12):
        analytics.record_value("1", "block_links")
    for _ in range(3):
        analytics.record_value("1", "reminders_birthday")
    flush()

    description = admin_digest.build_weekly_digest().description

    assert "**12** links blocked" in description
    assert "**3** birthdays celebrated" in description


def test_guild_movement_shows_both_directions():
    record_event("guild.joined", guild_id="1", returning=False)
    record_event("guild.joined", guild_id="2", returning=False)
    record_event("guild.removed", guild_id="3", days_in_guild=10)
    flush()

    description = admin_digest.build_weekly_digest().description
    assert "+2 joined" in description
    assert "-1 left" in description


def test_events_older_than_the_window_do_not_count(deps):
    record_event("guild.joined", guild_id="1", returning=False)
    flush_as_of(days=30)

    description = admin_digest.build_weekly_digest(days=7).description
    assert "joined" not in description


def test_it_points_at_where_to_dig_deeper():
    assert "/admin insights" in admin_digest.build_weekly_digest().description


class SentTo:
    """The logs channel the digest is posted to."""

    def __init__(self):
        self.embeds = []

    async def send(self, embed):
        self.embeds.append(embed)


async def test_the_digest_goes_out_on_monday_in_utc_whatever_the_host_clock_says(
    monkeypatch,
):
    """Broke as: the loop fires at `ANALYTICS_DIGEST_HOUR` UTC, and the weekday it
    checked was the host's local one, so on a host behind UTC a Monday 09:00 UTC run
    read Sunday and the week's digest never went out. Guaranteed: the digest is
    sent when it is Monday in UTC."""
    from app.cogs import analytics as analytics_cog

    monday = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)

    class HostBehindUtc(datetime):
        @classmethod
        def now(cls, tz=None):
            return monday if tz else (monday - timedelta(hours=10)).replace(tzinfo=None)

    monkeypatch.setattr(analytics_cog, "datetime", HostBehindUtc)
    channel = SentTo()
    cog = analytics_cog.Analytics.__new__(analytics_cog.Analytics)
    cog.bot = SimpleNamespace(
        get_channel=lambda channel_id: channel,
        config=SimpleNamespace(ADMIN_LOGS_CHANNEL_ID=1),
        guilds=[],
    )

    await analytics_cog.Analytics.send_weekly_digest.coro(cog)

    assert len(channel.embeds) == 1, "Monday in UTC sent no digest"
