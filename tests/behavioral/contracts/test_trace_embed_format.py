"""How the admin log channel labels an event.

The rule: the emoji names the kind of event, and the same kind always gets the
same one. What happened wins over where it came from, because "an item was
added" tells the reader more than "a slash command ran".

Before this, a plain command and a command that opened a form session rendered
under different icons (`🧵 Trace ping` against `🧭 moderations birthdays`), one
of them with no event label at all, and the body was a wall of text between
`────` rules. Two messages of the same kind looked like two different things.
"""
import logging
from datetime import datetime, timezone

import pytest

from app import logger as logger_module
from app.constants import DiscordLimits as limits
from app.constants import TraceTitles as titles
from app.services.trace import Trace

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("logging")]


def build(name="ping", source="slash", result=None, lines=(), **kwargs):
    trace = Trace(name, source=source, **kwargs)
    for message, level in lines:
        trace.add(message, level)
    trace.finish(result)
    return logger_module.build_trace_embed(trace)


def fields(embed):
    return {field.name: field.value for field in embed.fields}


# --------------------------------------------------------------------------
# One emoji per kind, always the same one
# --------------------------------------------------------------------------

def test_the_same_kind_of_event_always_carries_the_same_emoji():
    """Broke as: a form session rendered under a different icon than a plain
    command, so the channel could not be scanned by shape."""
    plain = build("ping")
    with_session = build("moderations birthdays reminders")
    with_session_trace = Trace("moderations birthdays reminders", source="slash")
    with_session_trace.is_journey = True
    with_session_trace.finish()

    assert plain.title == logger_module.build_trace_embed(with_session_trace).title
    assert plain.title == with_session.title == titles.DEFAULT


@pytest.mark.parametrize("outcome,expected", [
    ("added", "➕ Item Added"),
    ("removed", "🗑️ Item Removed"),
    ("edited", "📝 Config Edited"),
    ("disabled", "🚫 Command Disabled"),
    ("paused", "⏸️ Command Paused"),
    ("saved", "✅ Setup Saved"),
])
def test_what_happened_names_the_event(outcome, expected):
    assert build("moderations birthdays", result=outcome).title == expected


@pytest.mark.parametrize("source,expected", [
    ("webhook", "🔔 Webhook Event"),
    ("job", "⏰ Scheduled Job"),
    ("internal", "👂 Listener Event"),
    ("slash", "▶️ Command Run"),
])
def test_where_it_came_from_is_the_fallback(source, expected):
    assert build("twitch", source=source, result="success").title == expected


def test_an_error_outranks_everything_else():
    embed = build(
        "setup", result="saved", lines=[("boom", logging.ERROR)]
    )

    assert embed.title == "❌ Command Error"
    assert embed.color.value == 0xFF0000


def test_every_title_carries_exactly_one_leading_emoji():
    """The writing-style skill: `{ONE_EMOJI} {Title Case}`."""
    for title in list(titles.BY_OUTCOME.values()) + list(titles.BY_SOURCE.values()) + [titles.DEFAULT]:
        emoji, _, label = title.partition(" ")
        assert label, title
        assert label[0].isupper(), title
        assert not any(character.isalpha() for character in emoji), title


# --------------------------------------------------------------------------
# Organised, and inside Discord's limits
# --------------------------------------------------------------------------

def test_the_metadata_lives_in_fields_not_in_a_wall_of_text():
    embed = build(
        "moderations birthdays", guild_id="1246843812538613831", user_id="99",
        result="added", lines=[("setup opened", logging.INFO)],
    )

    named = fields(embed)
    assert named["User"] == "<@99>"
    assert named["Guild"] == "`1246843812538613831`"
    assert named["Source"] == "`slash`"
    assert "Duration" in named and "Result" in named
    assert "setup opened" in named["Timeline"]


def test_the_command_is_the_subject_and_reads_as_a_command():
    assert build("moderations birthdays").description == "`/moderations birthdays`"


def test_a_webhook_subject_is_not_dressed_up_as_a_slash_command():
    assert build("twitch", source="webhook").description == "`twitch`"


def test_the_footer_keeps_the_house_prefix():
    assert build("ping").footer.text.startswith("• ")


def test_an_enormous_timeline_stays_inside_the_field_limit():
    """A field caps at 1024 where the description caps at 4096, so the timeline
    moving into a field is exactly where this could start failing silently."""
    embed = build(
        "ping", lines=[("x" * 180, logging.INFO) for _ in range(20)]
    )

    assert len(fields(embed)["Timeline"]) <= limits.EMBED_FIELD_VALUE


def test_no_rendered_text_uses_the_word_the_style_guide_forbids():
    embed = build("ping", lines=[("`/ping` started", logging.INFO)])
    rendered = " ".join(
        [embed.title, embed.description or ""]
        + [f"{field.name} {field.value}" for field in embed.fields]
    )

    assert "invoke" not in rendered.lower()
    assert "—" not in rendered and "–" not in rendered


def test_a_trace_without_a_guild_or_user_omits_those_fields():
    named = fields(build("twitch", source="webhook"))

    assert "User" not in named and "Guild" not in named
    assert "Duration" in named
