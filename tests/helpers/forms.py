"""Forms the platform tests compile that no command ships (a card whose one
modal asks for a Twitch streamer, validated by its lookup), and a card's
sections found by their key."""
import copy

from app.forms.form.form_yaml import compile_form


def both_locales(value):
    """A text written the same in both locales."""
    return {"en-us": value, "pt-br": value}


STREAMER_VALIDATION = {
    "name": "registered_and_found",
    "params": {
        "item": "streamer",
        "service": "twitch",
        "id": "user_id",
        "error": "streamer",
    },
}

STREAMER_CARD_FORM = {
    "steps": [
        {
            "type": "intro",
            "key": "form",
            "title": both_locales("🧪 Fixture"),
            "description": both_locales("intro"),
        },
        {
            "type": "card",
            "key": "streamer_card",
            "title": both_locales("Card"),
            "description": both_locales("card"),
            "required": {"all": ["streamer"]},
            "header": {"title": both_locales("Card")},
            "fields": [{"key": "streamer", "label": both_locales("Streamer")}],
            "sections": [
                {
                    "key": "streamer",
                    "emoji": "🎮",
                    "type": "modal_input",
                    "label": both_locales("Streamer"),
                    "keys": {"value": "streamer"},
                    "modal": {
                        "title": both_locales("Streamer"),
                        "validation": STREAMER_VALIDATION,
                        "inputs": [
                            {
                                "key": "streamer",
                                "normalize": {"name": "handle"},
                                "label": both_locales("Streamer"),
                            }
                        ],
                    },
                }
            ],
        },
        {
            "type": "review",
            "key": "confirm",
            "title": both_locales("ok?"),
            "description": both_locales("review"),
        },
    ]
}


def streamer_card():
    """The streamer card form, compiled fresh."""
    return compile_form("streamer_card_form", copy.deepcopy(STREAMER_CARD_FORM))


def index_of(card, key):
    """The position the engine numbers the section `key` of `card` by."""
    return [section.key for section in card.sections].index(key)


def section(card, key):
    """The `section:<n>` step key a modal or a picker of the section `key` answers."""
    return f"section:{index_of(card, key)}"

