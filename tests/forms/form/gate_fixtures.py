"""A form with a hidden yes or no gate before a list, a shape kept for its contracts."""

from app.forms.form.form_yaml import compile_form
from tests.forms.form.card_fixtures import text

GATED_LIST_FORM = {
    "steps": [
        {
            "type": "intro",
            "key": "form",
            "title": text("🧪 Fixture"),
            "description": text("intro"),
        },
        {
            "type": "single_choice",
            "key": "add_now",
            "hidden": True,
            "required": True,
            "auto_confirm": True,
            "format": "boolean",
            "title": text("Add one now?"),
            "description": text("now or later"),
            "options": [
                {"label": text("Yes"), "value": True, "style": "success"},
                {"label": text("Later"), "value": False},
            ],
        },
        {
            "type": "list",
            "key": "things",
            "items": {"max": 5},
            "title": text("Things"),
            "description": text("one at a time"),
            "when": {"key": "add_now", "not_in": [False, "false", "False"]},
            "steps": [
                {
                    "type": "text",
                    "key": "thing",
                    "title": text("Thing"),
                    "description": text("a thing"),
                    "label": text("Type it"),
                }
            ],
        },
        {
            "type": "review",
            "key": "confirm",
            "title": text("ok?"),
            "description": text("review"),
        },
    ]
}


def gated_list():
    """The compiled gated form."""
    return compile_form("gated_list", GATED_LIST_FORM)
