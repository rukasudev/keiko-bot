"""The operator reports of `app.core.analytics_reports`, read from what was recorded.

Drop-off: where a setup died and why, rebuilt from the session's events. A session
whose view outlived `LONG_TIMEOUT_SECONDS` with no terminal event cannot finish, so
"abandoned at step X" is a fact, not a guess; the human reason is never claimed, only
what failed.

Value: deliveries tell a feature that was set up and works from one that was set up
and never delivered anything, which a command-usage dashboard shows as identical.

Settings usage: which settings are filled, read from the saved `guild.<cog_key>`
documents with no event at all, so it covers servers configured before analytics. A
value is tabulated only when the YAML declares the list it came from: free text,
channel and role ids never are.
"""
from datetime import datetime, timedelta, timezone

import discord
import pytest

from app.commands.admin import analytics as admin_analytics
from app.constants import ViewConstants as view_constants
from app.core import analytics, analytics_reports
from app.data import data, wait
from tests.generators.events import age_events, record_event
from tests.generators.moderations import seed_cog
from tests.mocks.database import fake_store

pytestmark = pytest.mark.unit

FEATURE = "block_links"


def flush():
    wait(analytics.flush())


def open_setup(session, guild="1"):
    record_event(
        "feature.setup_opened", guild_id=guild, user_id="9", feature=FEATURE,
        source="slash", session_id=session,
    )


def view_step(session, step, index, guild="1"):
    record_event(
        "setup.step_viewed", guild_id=guild, user_id="9", feature=FEATURE,
        source="slash", session_id=session, step_key=step,
        step_action="options", step_index=index,
    )


def age_session(session, minutes):
    """Push a session's events into the past so the view would have expired."""
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    age_events(fake_store(), moment, session)


def save(deps, feature, **document):
    seed_cog(deps.mongo_client, feature, {"guild_id": "g", **document})


def field(usage, key):
    return next(row for row in usage["fields"] if row["key"] == key)


def test_an_expired_session_reports_the_step_it_reached():
    open_setup("s1")
    view_step("s1", "mode", 1)
    view_step("s1", "permissions", 2)
    flush()
    age_session("s1", minutes=view_constants.LONG_TIMEOUT_SECONDS // 60 + 5)

    session = wait(analytics_reports.setup_sessions(FEATURE))[0]
    assert session["outcome"] == "expired"
    assert session["last_step"] == "permissions"


def test_a_session_still_inside_the_timeout_is_open_not_abandoned():
    open_setup("s2")
    view_step("s2", "mode", 1)
    flush()

    session = wait(analytics_reports.setup_sessions(FEATURE))[0]
    assert session["outcome"] == "open", (
        "an attempt the user may still be working on must not count as lost"
    )
    assert wait(analytics_reports.dropoff(FEATURE)) == []


def test_a_completed_session_is_never_counted_as_a_dropoff():
    open_setup("s3")
    view_step("s3", "mode", 1)
    record_event(
        "setup.completed", guild_id="1", user_id="9", feature=FEATURE,
        source="slash", session_id="s3", duration_ms=1000, steps_viewed=1,
    )
    flush()
    age_session("s3", minutes=120)

    assert wait(analytics_reports.setup_sessions(FEATURE))[0]["outcome"] == "completed"
    assert wait(analytics_reports.dropoff(FEATURE)) == []


def test_a_discard_reports_the_step_it_was_discarded_from():
    open_setup("s4")
    view_step("s4", "mode", 1)
    view_step("s4", "custom_link", 2)
    record_event(
        "setup.discarded", guild_id="1", user_id="9", feature=FEATURE,
        session_id="s4", step_key="custom_link", steps_viewed=2,
        validation_failures=0, back_count=0,
    )
    flush()

    session = wait(analytics_reports.setup_sessions(FEATURE))[0]
    assert session["outcome"] == "discarded"
    assert session["last_step"] == "custom_link"
    assert session["reason"] == "discarded-without-error"


def test_a_session_that_kept_failing_a_field_says_so():
    open_setup("s5")
    view_step("s5", "custom_link", 1)
    for _ in range(3):
        record_event(
            "setup.validation_failed", guild_id="1", user_id="9", feature=FEATURE,
            source="slash", session_id="s5", step_key="custom_link",
            validation="validate_link", error_key="link-not-recognized",
        )
    flush()
    age_session("s5", minutes=120)

    session = wait(analytics_reports.setup_sessions(FEATURE))[0]
    assert session["reason"] == "repeated-validation-failure"
    assert session["validation_failures"] == 3
    assert session["failed_keys"] == ["link-not-recognized"] * 3


def test_a_session_that_left_with_nothing_failing_says_exactly_that():
    open_setup("s6")
    view_step("s6", "permissions", 1)
    flush()
    age_session("s6", minutes=120)

    session = wait(analytics_reports.setup_sessions(FEATURE))[0]
    assert session["reason"] == "left-without-a-recorded-problem", (
        "the absence of a recorded problem must not be dressed up as a cause"
    )


def test_the_dropoff_report_ranks_the_steps_that_lose_the_most_people():
    for index, session in enumerate(["a1", "a2", "a3"]):
        open_setup(session)
        view_step(session, "custom_link", 2)
    open_setup("b1")
    view_step("b1", "permissions", 3)
    flush()
    for session in ["a1", "a2", "a3", "b1"]:
        age_session(session, minutes=120)

    rows = wait(analytics_reports.dropoff(FEATURE))
    assert rows[0]["step_key"] == "custom_link"
    assert rows[0]["lost"] == 3
    assert rows[1]["step_key"] == "permissions"


def test_the_screen_separates_discarded_from_expired():
    open_setup("c1")
    view_step("c1", "mode", 1)
    record_event(
        "setup.discarded", guild_id="1", user_id="9", feature=FEATURE,
        session_id="c1", step_key="mode",
    )
    open_setup("c2")
    view_step("c2", "mode", 1)
    flush()
    age_session("c1", minutes=120)
    age_session("c2", minutes=120)

    embed = wait(admin_analytics.build_dropoff_embed(FEATURE))
    assert "1 discarded, 1 expired" in embed.description
    assert "the view timed out" in embed.description


def test_a_single_session_can_be_read_on_its_own():
    open_setup("d1")
    view_step("d1", "custom_link", 1)
    record_event(
        "setup.validation_failed", guild_id="1", user_id="9", feature=FEATURE,
        source="slash", session_id="d1", step_key="custom_link",
        validation="validate_link", error_key="link-not-recognized",
    )
    flush()
    age_session("d1", minutes=120)

    session = wait(analytics_reports.setup_sessions(FEATURE))[0]
    embed = admin_analytics.build_session_embed(session)
    assert "custom_link" in embed.description
    assert "link-not-recognized" in embed.description


def test_a_configured_feature_with_no_delivery_is_reported_as_abandoned():
    record_event(
        "setup.completed", guild_id="1", user_id="9", feature="welcome_messages",
        source="slash", session_id="s", duration_ms=1000, steps_viewed=7,
    )
    flush()

    profile = wait(data().analytics_lifetime.find_lifetime("1"))
    feature = profile["features"]["welcome_messages"]
    assert feature["completed_at"] is not None
    assert feature.get("first_value_at") is None
    assert profile.get("last_value_at") is None


def test_delivery_moves_a_feature_out_of_the_abandoned_set():
    record_event(
        "setup.completed", guild_id="2", user_id="9", feature="default_roles",
        source="slash", session_id="s", duration_ms=1000, steps_viewed=1,
    )
    analytics.record_value("2", "default_roles")
    flush()

    profile = wait(data().analytics_lifetime.find_lifetime("2"))
    assert profile["features"]["default_roles"]["first_value_at"] is not None
    assert wait(analytics_reports.abandoned_features(days=0)) == []


def test_permission_failures_are_counted_separately_from_deliveries():
    analytics.record_value("3", "welcome_messages")
    analytics.record_permission_failure("3", "welcome_messages", discord.Forbidden.__new__(discord.Forbidden))
    flush()

    profile = wait(data().analytics_lifetime.find_lifetime("3"))
    feature = profile["features"]["welcome_messages"]
    assert feature["value_count"] == 1
    assert feature["permission_failures"] == 1


def test_feature_history_reports_what_a_pause_or_disable_should_carry():
    record_event(
        "setup.completed", guild_id="4", user_id="9", feature="block_links",
        source="slash", session_id="s", duration_ms=1000, steps_viewed=3,
    )
    for _ in range(4):
        record_event(
            "link.blocked", guild_id="4", feature="block_links", host="spam.com",
            mode="block_all", reason="blocked-no-rule", deleted=True,
        )
    flush()

    history = wait(analytics_reports.feature_history("4", "block_links"))
    assert history["value_events_since_enabled"] == 4
    assert history["days_since_enabled"] == 0


def test_a_blocked_link_is_one_activity_and_one_value_delivered():
    """Broke as: a blocked link was written twice, a `guild.blocked_links` row and a
    `feature.action_performed` count, and the two disagreed. Guaranteed: one
    `link.blocked` event, one activity document, and the value it counts."""
    for _ in range(20):
        record_event(
            "link.blocked", guild_id="5", feature="block_links", host="spam.com",
            mode="block_all", reason="blocked-no-rule", deleted=True,
        )
    flush()

    assert len(wait(data().events_activity.find_by_guild("5"))) == 20
    profile = wait(data().analytics_lifetime.find_lifetime("5"))
    assert profile["features"]["block_links"]["value_count"] == 20


def test_a_failed_delete_is_reported_as_a_permission_problem():
    record_event(
        "value.blocked_by_permission", guild_id="6",
        feature="block_links", error_type="Forbidden",
    )
    flush()

    events = wait(data().events_activity.find_by_guild("6"))
    assert [event["type"] for event in events] == ["value.blocked_by_permission"]
    assert events[0]["props"]["error_type"] == "Forbidden"


def test_the_funnel_reports_activation_not_only_completion():
    record_event(
        "feature.setup_opened", guild_id="7", user_id="9", feature="block_links",
        source="slash", session_id="s1",
    )
    record_event(
        "setup.completed", guild_id="7", user_id="9", feature="block_links",
        source="slash", session_id="s1", duration_ms=1000, steps_viewed=3,
    )
    record_event(
        "feature.setup_opened", guild_id="8", user_id="9", feature="block_links",
        source="slash", session_id="s2",
    )
    record_event(
        "setup.discarded", guild_id="8", user_id="9", feature="block_links",
        session_id="s2",
    )
    analytics.record_value("7", "block_links")
    flush()

    funnel = wait(analytics_reports.funnel("block_links"))
    assert funnel["setup_opened"] == 2
    assert funnel["setup_completed"] == 1
    assert funnel["setup_discarded"] == 1
    assert funnel["activated"] == 1


def test_recurring_value_needs_two_distinct_days():
    analytics.record_value("11", "welcome_messages")
    analytics.record_value("11", "welcome_messages")
    flush()

    assert wait(analytics_reports.funnel("welcome_messages"))["recurring"] == 0


def test_a_setting_nobody_fills_shows_zero_without_any_event(deps):
    for index in range(4):
        save(deps, "welcome_messages",
             guild_id=str(index),
             welcome_messages_channel={"values": ["123"]},
             welcome_custom_image=None)

    usage = wait(analytics_reports.config_usage("welcome_messages"))

    assert usage["guilds"] == 4
    assert field(usage, "welcome_messages_channel")["filled"] == 4
    assert field(usage, "welcome_custom_image")["filled"] == 0
    assert field(usage, "welcome_custom_image")["share"] == 0


def test_an_empty_envelope_does_not_count_as_configured(deps):
    save(deps, "block_links", guild_id="1", allowed_links={"values": []})
    save(deps, "block_links", guild_id="2", allowed_links={"values": ["youtube.com"]})

    assert field(wait(analytics_reports.config_usage("block_links")), "allowed_links")["filled"] == 1


def test_a_closed_vocabulary_field_reports_its_distribution(deps):
    for index in range(3):
        save(deps, "block_links", guild_id=str(index), mode="block_all")
    save(deps, "block_links", guild_id="9", mode="allow_all")

    mode = field(wait(analytics_reports.config_usage("block_links")), "mode")

    assert mode["closed_vocabulary"]
    assert mode["values"] == {"block_all": 3, "allow_all": 1}


def test_a_free_text_field_never_reports_what_people_wrote(deps):
    save(deps, "block_links", guild_id="1", answer="meu servidor secreto discord.gg/xyz")

    answer = field(wait(analytics_reports.config_usage("block_links")), "answer")

    assert answer["filled"] == 1, "it still counts as configured"
    assert answer["values"] == {}, "but never says what was written"


def test_channel_and_role_fields_never_report_their_ids(deps):
    save(deps, "block_links", guild_id="1",
         allowed_chats={"values": ["111", "222"]},
         allowed_roles={"values": ["333"]})

    usage = wait(analytics_reports.config_usage("block_links"))

    for key in ("allowed_chats", "allowed_roles"):
        assert field(usage, key)["filled"] == 1
        assert field(usage, key)["values"] == {}, (
            f"{key} resolves to ids at runtime and must never be tabulated"
        )


def test_a_choice_setting_reports_how_many_picked_each_option(deps):
    save(deps, "reminders_birthday", guild_id="1", mention="everyone")
    save(deps, "reminders_birthday", guild_id="2", mention="none")
    save(deps, "reminders_birthday", guild_id="3", mention="everyone")

    mention = field(wait(analytics_reports.config_usage("reminders_birthday")), "mention")

    assert mention["values"] == {"everyone": 2, "none": 1}


def test_multi_valued_settings_count_each_option_once_per_guild(deps):
    save(deps, "block_links", guild_id="1",
         allowed_links={"values": ["youtube.com", "spotify.com"]})
    save(deps, "block_links", guild_id="2", allowed_links={"values": ["youtube.com"]})

    values = field(wait(analytics_reports.config_usage("block_links")), "allowed_links")["values"]

    assert values == {"youtube.com": 2, "spotify.com": 1}


def test_a_feature_nobody_configured_reports_zero_guilds_and_does_not_crash(deps):
    usage = wait(analytics_reports.config_usage("notifications_twitch"))

    assert usage["guilds"] == 0
    assert all(row["share"] == 0 for row in usage["fields"])


def test_unused_settings_lists_the_candidates_for_removal(deps):
    for index in range(10):
        save(deps, "welcome_messages", guild_id=str(index),
             welcome_messages_channel={"values": ["1"]}, welcome_custom_image=None)

    rows = wait(analytics_reports.unused_settings())
    dead = [row for row in rows if row["key"] == "welcome_custom_image"]

    assert dead, "a setting nobody uses must surface"
    assert dead[0]["filled"] == 0
    assert dead[0]["total"] == 10
    assert not [row for row in rows if row["key"] == "welcome_messages_channel"]


def test_transient_gate_steps_are_not_counted_as_settings():
    """`add_custom` in block_links is a hidden yes/no gate, not configuration."""
    keys = [f["key"] for f in analytics_reports.configurable_fields("block_links")]

    assert "add_custom" not in keys
    assert "mode" in keys
