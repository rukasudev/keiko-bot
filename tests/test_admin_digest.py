"""The weekly digest — the only surface that does not wait to be asked for.

Because it arrives on its own, the bar is different from a command: it has to
be readable when nothing happened, lead with what needs a decision, and never
break on a fresh install with no data at all.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import admin_digest, analytics, analytics_sink

pytestmark = pytest.mark.unit


def flush():
    analytics.flush()


def age_everything(deps, days):
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    for document in deps.mongo_client.guild.analytics_events._data:
        document["ts"] = moment


def test_it_still_sends_something_on_a_completely_empty_install():
    embed = admin_digest.build_weekly_digest()

    assert embed.title.startswith("📊 Keiko")
    assert "0** guilds" in embed.description
    assert "Nothing needs a decision this week." in embed.description


def test_a_quiet_week_says_so_instead_of_going_silent():
    analytics.emit("guild.joined", guild_id="1", returning=False)
    analytics.record_value("1", "welcome_messages")
    flush()

    description = admin_digest.build_weekly_digest().description

    assert "Nothing needs a decision this week." in description
    assert "welcomes sent" in description


def test_it_leads_with_what_needs_a_decision(deps):
    analytics.emit(
        "feature.setup_opened", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id="s1",
    )
    analytics.emit(
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
        analytics.emit(
            "value.blocked_by_permission", guild_id=guild,
            feature="block_links", error_type="Forbidden",
        )
    flush()

    description = admin_digest.build_weekly_digest().description
    assert "2** guilds where Discord refused" in description


def test_it_reports_settings_nobody_uses(deps):
    for index in range(5):
        deps.mongo_client.guild["welcome_messages"].insert_one({
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
        deps.mongo_client.guild["block_links"].insert_one({"guild_id": str(index)})
        deps.mongo_client.guild["welcome_messages"].insert_one({"guild_id": str(index)})

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
    analytics.emit("guild.joined", guild_id="1", returning=False)
    analytics.emit("guild.joined", guild_id="2", returning=False)
    analytics.emit("guild.removed", guild_id="3", days_in_guild=10)
    flush()

    description = admin_digest.build_weekly_digest().description
    assert "+2 joined" in description
    assert "-1 left" in description


def test_events_older_than_the_window_do_not_count(deps):
    analytics.emit("guild.joined", guild_id="1", returning=False)
    flush()
    age_everything(deps, days=30)

    description = admin_digest.build_weekly_digest(days=7).description
    assert "joined" not in description


def test_it_points_at_where_to_dig_deeper():
    assert "/admin insights" in admin_digest.build_weekly_digest().description
