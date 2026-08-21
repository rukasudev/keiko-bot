"""Which settings of a feature are actually used, read from what is stored.

This report needs no event at all: the answer is already in `guild.<cog_key>`,
so it covers every guild configured long before analytics existed. The privacy
line is the same one the engine uses — a value may be tabulated only when the
YAML declares the list it came from.
"""
import pytest

from app.services import analytics_reports

pytestmark = pytest.mark.unit


def save(deps, feature, **document):
    deps.mongo_client.guild[feature].insert_one({"guild_id": "g", **document})


def field(usage, key):
    return next(row for row in usage["fields"] if row["key"] == key)


def test_a_setting_nobody_fills_shows_zero_without_any_event(deps):
    for index in range(4):
        save(deps, "welcome_messages",
             guild_id=str(index),
             welcome_messages_channel={"values": ["123"]},
             welcome_custom_image=None)

    usage = analytics_reports.config_usage("welcome_messages")

    assert usage["guilds"] == 4
    assert field(usage, "welcome_messages_channel")["filled"] == 4
    assert field(usage, "welcome_custom_image")["filled"] == 0
    assert field(usage, "welcome_custom_image")["share"] == 0


def test_an_empty_envelope_does_not_count_as_configured(deps):
    save(deps, "block_links", guild_id="1", allowed_links={"values": []})
    save(deps, "block_links", guild_id="2", allowed_links={"values": ["youtube.com"]})

    assert field(analytics_reports.config_usage("block_links"), "allowed_links")["filled"] == 1


def test_a_closed_vocabulary_field_reports_its_distribution(deps):
    for index in range(3):
        save(deps, "block_links", guild_id=str(index), mode="block_all")
    save(deps, "block_links", guild_id="9", mode="allow_all")

    mode = field(analytics_reports.config_usage("block_links"), "mode")

    assert mode["closed_vocabulary"]
    assert mode["values"] == {"block_all": 3, "allow_all": 1}


def test_a_free_text_field_never_reports_what_people_wrote(deps):
    save(deps, "block_links", guild_id="1", answer="meu servidor secreto discord.gg/xyz")

    answer = field(analytics_reports.config_usage("block_links"), "answer")

    assert answer["filled"] == 1, "it still counts as configured"
    assert answer["values"] == {}, "but never says what was written"


def test_channel_and_role_fields_never_report_their_ids(deps):
    save(deps, "block_links", guild_id="1",
         allowed_chats={"values": ["111", "222"]},
         allowed_roles={"values": ["333"]})

    usage = analytics_reports.config_usage("block_links")

    for key in ("allowed_chats", "allowed_roles"):
        assert field(usage, key)["filled"] == 1
        assert field(usage, key)["values"] == {}, (
            f"{key} resolves to ids at runtime and must never be tabulated"
        )


def test_a_boolean_setting_reports_how_many_turned_it_on(deps):
    save(deps, "reminders_birthday", guild_id="1", mention_everyone=True)
    save(deps, "reminders_birthday", guild_id="2", mention_everyone=False)
    save(deps, "reminders_birthday", guild_id="3", mention_everyone=True)

    mention = field(analytics_reports.config_usage("reminders_birthday"), "mention_everyone")

    assert mention["values"] == {"True": 2, "False": 1}


def test_multi_valued_settings_count_each_option_once_per_guild(deps):
    save(deps, "block_links", guild_id="1",
         allowed_links={"values": ["youtube.com", "spotify.com"]})
    save(deps, "block_links", guild_id="2", allowed_links={"values": ["youtube.com"]})

    values = field(analytics_reports.config_usage("block_links"), "allowed_links")["values"]

    assert values == {"youtube.com": 2, "spotify.com": 1}


def test_a_feature_nobody_configured_reports_zero_guilds_and_does_not_crash(deps):
    usage = analytics_reports.config_usage("notifications_twitch")

    assert usage["guilds"] == 0
    assert all(row["share"] == 0 for row in usage["fields"])


def test_unused_settings_lists_the_candidates_for_removal(deps):
    for index in range(10):
        save(deps, "welcome_messages", guild_id=str(index),
             welcome_messages_channel={"values": ["1"]}, welcome_custom_image=None)

    rows = analytics_reports.unused_settings()
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
