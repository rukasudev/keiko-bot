"""Which external lookups a submitted modal needs, decided by the platform."""

import copy
from datetime import datetime, timezone

import pytest

from app.settings.form import events as ev
from app.settings.form.form_state import AddItem, Origin, Setup, new_session
from app.settings.form.form_yaml import DefinitionRegistry, compile_form
from app.settings.form.lookups import Lookup, lookup_for

pytestmark = pytest.mark.unit

REGISTRY = DefinitionRegistry()
ORIGIN = Origin(guild_id="123456789", user_id="555", locale="pt-br")
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _text(value):
    return {"en-us": value, "pt-br": value}


STREAMER_CARD_FORM = {
    "steps": [
        {
            "action": "form",
            "key": "form",
            "title": _text("🧪 Fixture"),
            "description": _text("intro"),
        },
        {
            "action": "configuration_card",
            "key": "streamer_card",
            "title": _text("Card"),
            "description": _text("card"),
            "required": ["streamer"],
            "header": {"title": _text("Card")},
            "fields": [{"key": "streamer", "label": _text("Streamer")}],
            "sections": [
                {
                    "key": "streamer",
                    "icon": "🎮",
                    "type": "modal-input",
                    "label": _text("Streamer"),
                    "state": {"value": "streamer"},
                    "modal": {
                        "title": _text("Streamer"),
                        "validation": "validate_streamer_name",
                        "fields": [
                            {
                                "key": "streamer",
                                "normalize": "handle",
                                "label": _text("Streamer"),
                            }
                        ],
                    },
                }
            ],
        },
        {
            "action": "resume",
            "key": "confirm",
            "title": _text("ok?"),
            "description": _text("review"),
        },
    ]
}


def streamer_card():
    return compile_form("streamer_card_form", copy.deepcopy(STREAMER_CARD_FORM))


def _session(definition, mode, cursor, **kwargs):
    return new_session(
        (definition.key, definition.version),
        mode,
        ORIGIN,
        ttl_seconds=60,
        now=NOW,
        **kwargs,
    ).at(cursor)


def test_a_list_item_card_asks_for_the_lookup_of_its_streamer_modal():
    definition = REGISTRY.get("notifications_twitch")
    session = _session(definition, AddItem(), "notification", parent_id="p")
    event = ev.Answered("e1", None, "section:1", {"inputs": [" @Gaules "]})

    assert lookup_for(definition, session, event) == Lookup(("twitch",), "gaules")


def test_a_card_modal_asks_for_the_services_its_validator_needs():
    definition = streamer_card()
    session = _session(definition, Setup(), "streamer_card")
    event = ev.Answered("e1", None, "section:0", {"inputs": ["@Shroud"]})

    assert lookup_for(definition, session, event) == Lookup(("twitch",), "shroud")


def test_a_modal_without_external_needs_asks_for_nothing():
    definition = REGISTRY.get("block_links")
    session = _session(definition, Setup(), "link_settings")
    answer = ev.Answered("e1", None, "section:2", {"inputs": ["no links"]})
    pick = ev.Answered("e2", None, "section:0", "1")

    assert lookup_for(definition, session, answer) is None
    assert lookup_for(definition, session, pick) is None


def test_a_text_modal_asks_for_the_services_its_lookup_answers_name():
    raw = {
        "steps": [
            {
                "action": "form",
                "key": "form",
                "title": _text("🧪"),
                "description": _text("i"),
            },
            {
                "action": "modal",
                "key": "streamer",
                "validation": "validate_streamer_name",
                "lookup_answers": {"count": "stream_elements.enabled_commands"},
                "title": _text("Name"),
                "description": _text("type"),
                "label": _text("Name"),
            },
            {
                "action": "resume",
                "key": "confirm",
                "title": _text("ok?"),
                "description": _text("r"),
            },
        ]
    }
    definition = compile_form("counting", raw)
    session = _session(definition, Setup(), "streamer")
    event = ev.Answered("e1", None, "streamer", {"inputs": ["Shroud"]})

    lookup = lookup_for(definition, session, event)

    assert lookup is not None
    assert set(lookup.services) == {"twitch", "stream_elements"}
