"""A form with a hidden yes or no gate before a list, a shape kept for its contracts."""

from app.settings.form.form_yaml import compile_form
from tests.forms.form.card_fixtures import text

GATED_LIST_FORM = {
    "steps": [
        {
            "action": "form",
            "key": "form",
            "title": text("🧪 Fixture"),
            "description": text("intro"),
        },
        {
            "action": "options",
            "key": "add_now",
            "hidden": True,
            "required": True,
            "unique": True,
            "styled_values": True,
            "auto_confirm": True,
            "style": "boolean",
            "title": text("Add one now?"),
            "description": text("now or later"),
            "options": [
                {"label": text("Yes"), "value": True, "style": "success"},
                {"label": text("Later"), "value": False},
            ],
        },
        {
            "action": "composition",
            "key": "things",
            "parent_key": "things",
            "max": 5,
            "style": "composition",
            "title": text("Things"),
            "description": text("one at a time"),
            "when": {"key": "add_now", "not_in": [False, "false", "False"]},
            "steps": [
                {
                    "action": "modal",
                    "key": "thing",
                    "title": text("Thing"),
                    "description": text("a thing"),
                    "label": text("Type it"),
                }
            ],
        },
        {
            "action": "resume",
            "key": "confirm",
            "title": text("ok?"),
            "description": text("review"),
        },
    ]
}


def gated_list():
    """The compiled gated form."""
    return compile_form("gated_list", GATED_LIST_FORM)
