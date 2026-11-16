"""Card forms compiled in tests, for section types no shipped form uses yet."""

import copy

from app.forms.form.form_yaml import compile_form


def text(value):
    return {"en-us": value, "pt-br": value}


WELCOME_LIKE_CARD_FORM = {
    "steps": [
        {
            "type": "intro",
            "key": "form",
            "title": text("🧪 Fixture"),
            "description": text("intro"),
        },
        {
            "type": "card",
            "key": "welcome_card",
            "title": text("Card"),
            "description": text("card"),
            "required": {"all": ["channel", "image"]},
            "defaults": {"design": "server_blur"},
            "header": {"title": text("Card")},
            "fields": [
                {"key": "channel", "label": text("Channel"), "format": "channel"},
                {"key": "design", "label": text("Design")},
                {"key": "image", "label": text("Image"), "hidden": True},
                {"key": "title", "label": text("Title")},
                {"key": "messages", "label": text("Messages"), "format": "bullet"},
                {"key": "footer", "label": text("Footer")},
            ],
            "sections": [
                {
                    "key": "channel",
                    "emoji": "#️⃣",
                    "type": "channel_select",
                    "label": text("Channel"),
                    "keys": {"value": "channel"},
                },
                {
                    "key": "design",
                    "emoji": "🎨",
                    "type": "design_select",
                    "label": text("Design"),
                    "keys": {"value": "design"},
                    "options": [
                        {
                            "value": "server_blur",
                            "label": text("Server"),
                            "description": text("server picture"),
                        },
                        {
                            "value": "custom_only",
                            "label": text("Your image"),
                            "description": text("your picture"),
                        },
                    ],
                },
                {
                    "key": "image",
                    "emoji": "🖼️",
                    "type": "file_upload",
                    "label": text("Image"),
                    "keys": {"image": "image"},
                    "when": {"key": "design", "not_in": ["server_blur"]},
                    "modal": {"title": text("Image")},
                },
                {
                    "key": "messages",
                    "emoji": "💬",
                    "type": "modal_input",
                    "label": text("Messages"),
                    "keys": {"value": "messages"},
                    "format": "bullet",
                    "modal": {
                        "title": text("Messages"),
                        "inputs": [
                            {"key": "title", "label": text("Title")},
                            {"label": text("Message")},
                            {"label": text("Message"), "required": False},
                            {
                                "key": "footer",
                                "label": text("Footer"),
                                "required": False,
                            },
                        ],
                    },
                },
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


def welcome_like_card(by_field=False):
    raw = copy.deepcopy(WELCOME_LIKE_CARD_FORM)
    if by_field:
        raw["steps"][1]["edit"] = "field"
    return compile_form("welcome_like_card", raw)


WELCOME_LIKE_DOC = {
    "guild_id": "123456789",
    "enabled": True,
    "channel": {"style": "channel", "values": "100"},
    "design": "server_blur",
    "title": "Oi!",
    "messages": {"style": "bullet", "values": "A;B"},
    "footer": "F",
}


DEPENDENTS_CARD_FORM = {
    "steps": [
        {
            "type": "intro",
            "key": "form",
            "title": text("🧪 Fixture"),
            "description": text("intro"),
        },
        {
            "type": "card",
            "key": "site_card",
            "title": text("Card"),
            "description": text("card"),
            "header": {"title": text("Card")},
            "fields": [
                {"key": "mode", "label": text("Mode")},
                {"key": "link", "label": text("Link")},
            ],
            "sections": [
                {
                    "key": "mode",
                    "emoji": "🚦",
                    "type": "value_select",
                    "keys": {"value": "mode"},
                    "label": text("Mode"),
                    "screen": {"title": text("Choose the mode")},
                    "placeholder": text("Choose mode"),
                    "dependents": [
                        {
                            "key": "link",
                            "validation": {"name": "link_or_domain"},
                        }
                    ],
                    "options": [
                        {"label": text("Block"), "value": "block"},
                        {"label": text("Allow"), "value": "allow"},
                    ],
                },
                {
                    "key": "link",
                    "emoji": "🔗",
                    "type": "modal_input",
                    "keys": {"value": "link"},
                    "label": text("Link"),
                    "modal": {
                        "title": text("Link"),
                        "inputs": [{"key": "link", "label": text("Link")}],
                    },
                },
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


def dependents_card():
    return compile_form("site_card", copy.deepcopy(DEPENDENTS_CARD_FORM))
