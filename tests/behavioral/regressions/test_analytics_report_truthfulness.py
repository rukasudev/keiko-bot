"""The reports that read stored configuration must agree with the database.

The first weekly digest that reached the admin channel said seven settings were
used by nobody. Five of them were used by everybody: `config_usage` walked the
raw `guild.<cog_key>` document looking for the keys the YAML names, and a
feature that keeps its own storage shape — birthdays store `channel_id`, a
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
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import admin_analytics, admin_digest, analytics, analytics_reports

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("analytics_reports")]


def field(usage, key):
    return next(row for row in usage["fields"] if row["key"] == key)


def configure_birthdays(deps, guild_id, *, birthdays=1, default_message=True):
    """A guild configured through the real birthday flow, stored as it stores."""
    deps.mongo_client.guild.reminders_birthday.insert_one({
        "guild_id": guild_id,
        "channel_id": f"chan-{guild_id}",
        "locale": "pt-br",
        "mention_everyone": True,
        "timezone": "America/Sao_Paulo",
        "notification_time": "09:00",
        **({"default_message": {
            "mode": "custom",
            "title": "Happy birthday!",
            "content": "{user} is one year older today",
        }} if default_message else {}),
    })
    for index in range(birthdays):
        deps.mongo_client.reminders.birthdays.insert_one({
            "guild_id": guild_id,
            "user_id": f"u{index}",
            "date": "03-15",
            "month": 3,
            "day": 15,
        })


def test_a_feature_that_stores_its_own_shape_is_not_reported_as_unused(deps):
    """`channel` lives in `channel_id`, and the birthdays live in another database."""
    for guild_id in ("1", "2", "3"):
        configure_birthdays(deps, guild_id, birthdays=2)

    usage = analytics_reports.config_usage("reminders_birthday")

    assert usage["guilds"] == 3
    assert field(usage, "channel")["filled"] == 3, "stored as channel_id, still the channel"
    assert field(usage, "reminders_birthday")["filled"] == 3, (
        "the registered birthdays live in reminders.birthdays, not in the guild document"
    )
    assert field(usage, "default_message_title")["filled"] == 3, (
        "the three default_message_* settings are one nested object in storage"
    )


def test_a_guild_that_left_the_optional_parts_empty_still_counts_as_zero(deps):
    configure_birthdays(deps, "1", birthdays=0, default_message=False)

    usage = analytics_reports.config_usage("reminders_birthday")

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

    usage = analytics_reports.config_usage("reminders_birthday")

    assert field(usage, "default_message_mode")["filled"] == 1, (
        "only the guild that actually chose a mode has one"
    )
    assert field(usage, "default_message_mode")["default"] == "default", (
        "and the YAML still says what the other two are running"
    )


def test_a_boolean_nobody_touched_is_not_a_boolean_somebody_turned_off(deps):
    """`bool(None)` is `False`, and `False` is a value someone picked."""
    deps.mongo_client.guild.reminders_birthday.insert_one({
        "guild_id": "1", "channel_id": "c1",
    })
    deps.mongo_client.guild.reminders_birthday.insert_one({
        "guild_id": "2", "channel_id": "c2", "mention_everyone": False,
    })

    mention = field(analytics_reports.config_usage("reminders_birthday"), "mention_everyone")

    assert mention["filled"] == 1
    assert mention["values"] == {"False": 1}


def test_a_setting_left_on_its_yaml_default_says_so_instead_of_reading_as_dead(deps):
    """No guild stores `mode`, because block_all is the default nobody changed."""
    for guild_id in ("1", "2"):
        deps.mongo_client.guild.block_links.insert_one({
            "guild_id": guild_id,
            "allowed_links": {"values": ["youtube.com"]},
            "answer": "no links here",
        })

    mode = field(analytics_reports.config_usage("block_links"), "mode")

    assert mode["filled"] == 0
    assert mode["default"] == "block_all", "the YAML declares what the silence means"

    description = admin_analytics.build_config_usage_embed("block_links").description
    assert "block_all" in description


def test_a_setting_with_no_declared_default_stays_a_plain_zero(deps):
    deps.mongo_client.guild.block_links.insert_one({
        "guild_id": "1", "answer": "no links here",
    })

    assert field(analytics_reports.config_usage("block_links"), "custom_links")["default"] is None


def test_the_digest_never_prints_none_where_a_step_belongs(deps):
    """A setup abandoned on the opening screen reached no step at all."""
    analytics.emit(
        "feature.setup_opened", guild_id="1", user_id="9",
        feature="reminders_birthday", source="slash", session_id="s1",
    )
    analytics.flush()
    for document in deps.mongo_client.guild.analytics_events._data:
        document["ts"] = datetime.now(timezone.utc) - timedelta(days=1)

    description = admin_digest.build_weekly_digest().description

    assert "None" not in description
    assert "died before the first step" in description


def test_the_headline_counts_the_guilds_the_bot_is_in(deps):
    """A guild with no event since the deploy has no profile, and still exists."""
    analytics.record_value("1", "welcome_messages")
    analytics.flush()

    description = admin_digest.build_weekly_digest(guild_count=73).description

    assert "**73** guilds" in description
    assert "**1** delivered value" in description
