"""Twitch notifications: one EventSub subscription per streamer."""

from __future__ import annotations

from app.constants import Commands
from app.services.notifications_twitch import subscribe_streamer, unsubscribe_streamer
from app.settings.features.subscriptions import SubscriptionFeature


class TwitchFeature(SubscriptionFeature):
    """Streamers to announce when they go live."""

    item_key = "streamer"
    external = "twitch"
    lookup_result = "user_id"

    def __init__(self) -> None:
        super().__init__(Commands.NOTIFICATIONS_TWITCH_KEY)
        self.subscribe = subscribe_streamer
        self.unsubscribe = unsubscribe_streamer


FEATURE = TwitchFeature()
