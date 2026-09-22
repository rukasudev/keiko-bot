"""The Preview on a review shows the announcement, not "nothing yet".

Setting up a Twitch or YouTube notification ends on a review whose Preview
button reads the answers through `FeatureModule.responses_for_preview`. From a
review the answers hold the whole composition as one value, so the senders,
which read flat keys, found nothing and the preview answered with the empty
settings value.

Shared behaviour: `responses_for_aside` on the generic feature, read by every
side action of the adapter. Consumer that exposed it: notifications_twitch. Guaranteed: a
preview opened from a review shows the same text as one opened from the item
card it lists.
"""

import pytest

from app.settings.features import feature_for
from app.settings.form.form_state import Answer
from app.settings.form.responses.responses import item_answers

pytestmark = pytest.mark.unit

STORED = {
    "streamer": {"value": "gaules", "title": "Streamer", "style": None},
    "notification_messages": {
        "value": "{streamer} está ao vivo!",
        "title": "Mensagens",
        "style": "bullet",
    },
}


def _texts(feature, answers):
    from app.services.notifications_twitch import parse_streamer_message

    values = {
        entry["key"]: entry.get("_raw_value", entry.get("value"))
        for entry in feature.responses_for_aside(answers, "pt-br")
        if entry.get("key")
    }
    streamer = str(values.get("streamer") or "")
    link = f"https://www.twitch.tv/{streamer}"
    return [
        parse_streamer_message(message.lstrip(), streamer, link)
        for message in str(values.get("notification_messages") or "").split(";")
        if message.strip()
    ]


def test_a_preview_from_the_review_says_what_one_from_the_item_card_says():
    feature = feature_for("notifications_twitch")
    item = item_answers(STORED)

    from_review = _texts(feature, {"notifications": Answer([item])})
    from_card = _texts(feature, item)

    assert from_review == from_card
    assert from_review and "gaules está ao vivo!" in from_review[0]


def test_a_review_with_no_item_still_previews_what_was_answered():
    feature = feature_for("notifications_twitch")

    texts = _texts(feature, item_answers(STORED))

    assert texts and "gaules" in texts[0]
