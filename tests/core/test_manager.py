"""The History of changes: one numbered field per change, timed by Discord."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.manager import change_label, parse_history_data
from app.core.utils import fill, ml

pytestmark = pytest.mark.unit

ASKING = SimpleNamespace(
    locale="pt-br", client=SimpleNamespace(user=SimpleNamespace(id=999))
)
MOMENT = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)
DISCORD_TIME = "<t:1767263400:f>"


def _change(kind, when, cog="block_links", actor="555", who="user"):
    return {"ts": when, "type": kind, "cog": cog, "actor": {"kind": who, "id": actor}}


def _line(key):
    return ml(f"commands.records.history.lines.{key}", locale="pt-br")


def _by(user):
    return fill(_line("by"), user=user, date=DISCORD_TIME)


def test_a_change_names_what_changed_and_puts_who_and_when_in_its_value():
    """Discord draws neither a timestamp nor a mention in a field's name, so the
    name says what changed and the value who did it and when, in the reader's own
    time."""
    fields = parse_history_data([_change("config.changed", MOMENT)], ASKING)

    assert fields == {f"1. {_line('edited')}": _by("<@555>")}
    (value,) = fields.values()
    assert "<@555>" in value and DISCORD_TIME in value


def test_a_change_stored_without_a_zone_is_read_as_utc():
    fields = parse_history_data(
        [_change("feature.paused", datetime(2026, 1, 1, 10, 30))], ASKING
    )

    assert list(fields.values()) == [_by("<@555>")]


def test_two_changes_in_the_same_second_keep_a_field_each():
    fields = parse_history_data(
        [_change("feature.item_added", MOMENT), _change("feature.item_added", MOMENT)],
        ASKING,
    )

    assert list(fields) == [f"1. {_line('added')}", f"2. {_line('added')}"]


def test_a_change_keeps_its_feature_beside_what_changed():
    fields = parse_history_data(
        [_change("feature.unpaused", MOMENT)],
        ASKING,
        label_for=lambda key: change_label(key, "pt-br"),
    )

    feature = ml("block_links.command.setup.label", locale="pt-br")
    assert list(fields) == [f"1. {feature} · {_line('unpaused')}"]


def test_a_change_keeps_a_key_no_feature_answers_in_backticks():
    assert change_label("gone_feature", "pt-br") == "`gone_feature`"


def test_an_automatic_change_says_so_under_its_value():
    fields = parse_history_data(
        [_change("feature.paused", MOMENT, actor="999", who="bot")], ASKING
    )

    assert list(fields.values()) == [f"{_by('<@999>')}\n-# {_line('system')}"]
