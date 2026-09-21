"""Card forms compiled in tests, for section types no shipped form uses yet."""

import copy

from app.settings.form.form_yaml import compile_form


def text(value):
    return {"en-us": value, "pt-br": value}


WELCOME_LIKE_CARD_FORM = {
    "steps": [
        {
            "action": "form",
            "key": "form",
            "title": text("🧪 Fixture"),
            "description": text("intro"),
        },
        {
            "action": "configuration_card",
            "key": "welcome_card",
            "title": text("Card"),
            "description": text("card"),
            "required": ["channel", "image"],
            "defaults": {"design": "server_blur"},
            "header": {"title": text("Card")},
            "fields": [
                {"key": "channel", "label": text("Channel"), "style": "channel"},
                {"key": "design", "label": text("Design")},
                {"key": "image", "label": text("Image"), "hidden": True},
                {"key": "title", "label": text("Title")},
                {"key": "messages", "label": text("Messages"), "style": "bullet"},
                {"key": "footer", "label": text("Footer")},
            ],
            "sections": [
                {
                    "key": "channel",
                    "icon": "#️⃣",
                    "type": "channel-select",
                    "label": text("Channel"),
                    "state": {"value": "channel"},
                },
                {
                    "key": "design",
                    "icon": "🎨",
                    "type": "design-select",
                    "label": text("Design"),
                    "state": {"value": "design"},
                    "designs": [
                        {
                            "key": "server_blur",
                            "label": text("Server"),
                            "description": text("server picture"),
                        },
                        {
                            "key": "custom_only",
                            "label": text("Your image"),
                            "description": text("your picture"),
                        },
                    ],
                },
                {
                    "key": "image",
                    "icon": "🖼️",
                    "type": "file-upload",
                    "label": text("Image"),
                    "state": {"url": "image"},
                    "visible-when": {"key": "design", "not_in": ["server_blur"]},
                    "modal": {"title": text("Image")},
                },
                {
                    "key": "messages",
                    "icon": "💬",
                    "type": "modal-input",
                    "label": text("Messages"),
                    "state": {"value": "messages"},
                    "style": "bullet",
                    "modal": {
                        "title": text("Messages"),
                        "fields": [
                            {
                                "key": "title",
                                "label": text("Title"),
                                "default": text("Hello!"),
                            },
                            {
                                "label": text("Message"),
                                "default": text("Welcome {user}"),
                            },
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
            "action": "resume",
            "key": "confirm",
            "title": text("ok?"),
            "description": text("review"),
        },
    ]
}


def welcome_like_card(edit_by_field=False):
    raw = copy.deepcopy(WELCOME_LIKE_CARD_FORM)
    if edit_by_field:
        raw["steps"][1]["edit_by_field"] = True
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


RESET_ON_CHANGE_CARD_FORM = {
    "steps": [
        {
            "action": "form",
            "key": "form",
            "title": text("🧪 Fixture"),
            "description": text("intro"),
        },
        {
            "action": "configuration_card",
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
                    "icon": "🚦",
                    "type": "value-select",
                    "state": {"value": "mode"},
                    "label": text("Mode"),
                    "picker-title": text("Choose the mode"),
                    "customize-label": text("Choose mode"),
                    "reset-on-change": [
                        {"key": "link", "validation": "validate_link_or_domain"}
                    ],
                    "options": [
                        {"label": text("Block"), "value": "block"},
                        {"label": text("Allow"), "value": "allow"},
                    ],
                },
                {
                    "key": "link",
                    "icon": "🔗",
                    "type": "modal-input",
                    "state": {"value": "link"},
                    "label": text("Link"),
                    "modal": {
                        "title": text("Link"),
                        "fields": [{"key": "link", "label": text("Link")}],
                    },
                },
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


def reset_on_change_card():
    return compile_form("site_card", copy.deepcopy(RESET_ON_CHANGE_CARD_FORM))
