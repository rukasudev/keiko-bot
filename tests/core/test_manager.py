"""The History of changes: one numbered field per change, timed by Discord."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.manager import change_label, parse_history_data

pytestmark = pytest.mark.unit

ASKING = SimpleNamespace(
    locale="pt-br", client=SimpleNamespace(user=SimpleNamespace(id=999))
)


def _change(kind, when, cog="block_links", actor="555", who="user"):
    return {"ts": when, "type": kind, "cog": cog, "actor": {"kind": who, "id": actor}}


def test_a_change_names_what_changed_and_puts_who_and_when_in_its_value():
    """Discord draws neither a timestamp nor a mention in a field's name, so the
    name says what changed and the value who did it and when, in the reader's own
    time."""
    moment = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)

    fields = parse_history_data([_change("config.changed", moment)], ASKING)

    assert fields == {"1. 📝 Editado": "<@555> · <t:1767263400:f>"}


def test_a_change_stored_without_a_zone_is_read_as_utc():
    fields = parse_history_data(
        [_change("feature.paused", datetime(2026, 1, 1, 10, 30))], ASKING
    )

    assert list(fields.values()) == ["<@555> · <t:1767263400:f>"]


def test_two_changes_in_the_same_second_keep_a_field_each():
    moment = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)

    fields = parse_history_data(
        [_change("feature.item_added", moment), _change("feature.item_added", moment)],
        ASKING,
    )

    assert list(fields) == ["1. ➕ Item adicionado", "2. ➕ Item adicionado"]


def test_a_change_keeps_its_feature_beside_what_changed():
    moment = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)

    fields = parse_history_data(
        [_change("feature.unpaused", moment)],
        ASKING,
        label_for=lambda key: change_label(key, "pt-br"),
    )

    assert list(fields) == ["1. 🚫 Bloquear Links · ▶️ Retomado"]


def test_a_change_keeps_a_key_no_feature_answers_in_backticks():
    assert change_label("gone_feature", "pt-br") == "`gone_feature`"


def test_an_automatic_change_says_so_under_its_value():
    moment = datetime(2026, 1, 1, 10, 30, tzinfo=timezone.utc)

    fields = parse_history_data(
        [_change("feature.paused", moment, actor="999", who="bot")], ASKING
    )

    assert list(fields.values()) == [
        "<@999> · <t:1767263400:f>\n"
        "-# Essa é uma ação automática que eu faço ao sair do servidor"
    ]
