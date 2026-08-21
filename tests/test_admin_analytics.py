"""The /admin analytics screens.

The reason these are worth pinning: with fewer than a hundred guilds a
percentage without its population is misleading, and the churn screen exists
precisely to compare two tiny groups. The guard rails are part of the feature.
"""
import pytest

from app.data import analytics as analytics_data
from app.services import admin_analytics, analytics, analytics_sink

pytestmark = pytest.mark.unit


def flush():
    analytics.flush()


def complete_setup(guild_id, feature, session="s"):
    analytics.emit(
        "feature.setup_opened", guild_id=guild_id, user_id="9", feature=feature,
        source="slash", session_id=session,
    )
    analytics.emit(
        "setup.completed", guild_id=guild_id, user_id="9", feature=feature,
        source="slash", session_id=session, duration_ms=1000, steps_viewed=3,
    )


def test_the_funnel_screen_shows_absolute_numbers_next_to_percentages():
    complete_setup("1", "block_links")
    analytics.record_value("1", "block_links")
    flush()

    embed = admin_analytics.build_funnel_embed("block_links")
    assert "Setup opened** — 1" in embed.description
    assert "Setup completed** — 1 (100%)" in embed.description
    assert "Delivered value** — 1 (100%)" in embed.description


def test_the_funnel_screen_survives_a_feature_nobody_touched():
    embed = admin_analytics.build_funnel_embed("welcome_messages")
    assert "Setup opened** — 0" in embed.description


def test_the_friction_screen_ranks_the_steps_that_reject_people():
    for _ in range(3):
        analytics.emit(
            "setup.validation_failed", guild_id="2", user_id="9",
            feature="block_links", session_id="s", step_key="custom_link",
            validation="validate_link", error_key="link-not-recognized",
        )
    analytics.emit(
        "setup.validation_failed", guild_id="2", user_id="9",
        feature="reminders_birthday", session_id="s2", step_key="date",
        validation="validate_date", error_key="invalid-date",
    )
    flush()

    embed = admin_analytics.build_friction_embed()
    assert embed.description.index("custom_link") < embed.description.index("date")
    assert "link-not-recognized" in embed.description


def test_the_churn_screen_refuses_to_read_a_tiny_population_as_a_conclusion():
    complete_setup("3", "block_links")
    analytics.emit("guild.removed", guild_id="3", days_in_guild=5)
    flush()

    embed = admin_analytics.build_churn_embed()
    assert "N=1" in embed.description
    assert "too small" in embed.description


def test_the_churn_screen_never_claims_a_cause():
    embed = admin_analytics.build_churn_embed()
    assert "correlation, never cause" in embed.footer.text


def test_the_guild_screen_tells_the_story_in_order():
    analytics.emit("guild.joined", guild_id="4", returning=False)
    analytics.emit(
        "guild.greeting_sent", guild_id="4", features_offered=4, channel_found=True
    )
    complete_setup("4", "welcome_messages")
    flush()

    embed = admin_analytics.build_guild_journey_embed("4")
    description = embed.description
    assert description.index("guild.joined") < description.index("guild.greeting_sent")
    assert description.index("guild.greeting_sent") < description.index("setup.completed")


def test_the_guild_screen_flags_a_greeting_nobody_could_see():
    analytics.emit(
        "guild.greeting_sent", guild_id="5", features_offered=4, channel_found=False
    )
    flush()

    embed = admin_analytics.build_guild_journey_embed("5")
    assert "no writable channel" in embed.description


def test_the_abandoned_screen_lists_configured_but_dead_features():
    complete_setup("6", "welcome_messages")
    flush()
    profile = analytics_data.find_profile("6")
    profile["features"]["welcome_messages"]["completed_at"] = (
        profile["features"]["welcome_messages"]["completed_at"].replace(year=2020)
    )

    embed = admin_analytics.build_abandoned_embed()
    assert "welcome_messages" in embed.description


def test_forgetting_a_guild_removes_every_trace_of_it():
    complete_setup("7", "block_links")
    analytics.record_value("7", "block_links")
    flush()

    assert analytics_data.find_profile("7")
    deleted = analytics_data.delete_analytics_by_guild("7")

    assert deleted["profile"] == 1
    assert analytics_data.find_profile("7") is None
    assert analytics_data.find_events_by_guild("7") == []
    assert analytics_data.find_month_buckets("7") == []


def test_forgetting_one_guild_leaves_the_others_alone():
    complete_setup("8", "block_links")
    complete_setup("9", "block_links")
    flush()

    analytics_data.delete_analytics_by_guild("8")

    assert analytics_data.find_profile("9")
    assert analytics_data.find_events_by_guild("9")


def test_every_section_of_insights_builds_without_data(deps):
    """The menu must open on a fresh install, not only once data exists."""
    for section in admin_analytics.insight_sections():
        embed = section["build"]()
        assert embed.title
        assert embed.description


def test_the_sections_cover_the_commands_that_were_collapsed():
    keys = {section["key"] for section in admin_analytics.insight_sections()}
    assert {"dropoff", "friction", "abandoned", "funnel", "churn", "pipeline"} <= keys
    assert {"config", "timing"} <= keys, "the two new reports must be reachable"


def test_the_funnel_section_lists_every_feature_that_had_activity():
    complete_setup("20", "block_links")
    complete_setup("21", "welcome_messages")
    flush()

    description = admin_analytics.build_all_funnels_embed().description
    assert "block_links" in description
    assert "welcome_messages" in description
    assert "notifications_twitch" not in description


def test_the_settings_section_names_what_nobody_fills(deps):
    for index in range(6):
        deps.mongo_client.guild["welcome_messages"].insert_one({
            "guild_id": str(index),
            "welcome_messages_channel": {"values": ["1"]},
            "welcome_custom_image": None,
        })

    description = admin_analytics.build_config_usage_embed().description
    assert "welcome_custom_image" in description
    assert "0** of **6" in description


def test_the_timing_section_states_what_the_number_is_not():
    analytics.emit(
        "setup.step_completed", guild_id="1", user_id="9", feature="block_links",
        source="slash", session_id="s", step_key="answer", step_action="modal",
        ms_on_step=45000,
    )
    flush()

    embed = admin_analytics.build_step_timing_embed()
    assert "My response when blocking" in embed.description, (
        "reports must name the step the user saw, not its YAML key"
    )
    assert "not attention" in embed.footer.text


def test_the_pipeline_screen_reports_what_was_dropped():
    analytics.emit("nope.not_real", value=1)
    embed = admin_analytics.build_pipeline_embed()
    assert "Unknown events: **1**" in embed.description
