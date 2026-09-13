"""YouTube notifications: one PubSub subscription per channel."""

from __future__ import annotations

from app.constants import Commands
from app.forms.features.subscriptions import SubscriptionFeature
from app.services.notifications_youtube_video import (
    subscribe_youtube_new_video,
    unsubscribe_youtube_new_video,
)


class YouTubeFeature(SubscriptionFeature):
    """Youtubers to announce when they post a video."""

    item_key = "youtuber"
    external = "youtube"
    lookup_result = "channel_id"

    def __init__(self) -> None:
        super().__init__(Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY)
        self.subscribe = subscribe_youtube_new_video
        self.unsubscribe = unsubscribe_youtube_new_video


FEATURE = YouTubeFeature()
