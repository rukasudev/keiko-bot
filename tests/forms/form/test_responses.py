"""What a saved document reads as, for the readers that act on it."""

import pytest

from app.settings.form.responses.responses import (
    document_values,
    item_values,
    items_of,
    list_values,
)

pytestmark = pytest.mark.unit

DOCUMENT = {
    "guild_id": "1",
    "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": ["100"]},
    "custom_links": {
        "style": "composition",
        "values": [
            {
                "link": {"value": "meusite.com.br", "title": "Link", "style": "code"},
                "match_type": {
                    "value": "🌐 Todos os links desse site",
                    "_raw_value": "domain",
                    "title": "Como Devo Considerar?",
                },
            },
            {"link": {"value": "docs.example.org", "title": "Link"}},
        ],
    },
}


def test_a_saved_list_reads_as_its_machine_values_never_its_labels():
    assert list_values(DOCUMENT, "custom_links") == [
        {"link": "meusite.com.br", "match_type": "domain"},
        {"link": "docs.example.org"},
    ]


def test_one_saved_item_reads_as_its_machine_values():
    first = items_of(DOCUMENT, "custom_links")[0]
    assert item_values(first) == {"link": "meusite.com.br", "match_type": "domain"}


def test_a_list_nothing_was_saved_under_reads_empty():
    assert list_values(DOCUMENT, "notifications") == []
    assert list_values({"custom_links": None}, "custom_links") == []


def test_the_settings_read_as_their_machine_values():
    values = document_values(DOCUMENT)
    assert values["mode"] == "block_all"
    assert values["allowed_chats"] == ["100"]
    assert "guild_id" not in values and "enabled" not in values
