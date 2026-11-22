"""Kick's client as the forms see it: channels found by slug and the live
subscriptions Keiko holds, answered from memory, every subscribe and delete kept."""

from typing import Any, Dict, Iterable, List, Set

from app.constants import Commands
from app.integrations.kick import KickSubscription


class MockKickAPI:
    """The calls of `KickClient` a command makes, on channels added by the test."""

    def __init__(self):
        self._channels: Dict[str, Dict[str, Any]] = {}
        self._subscriptions: Dict[str, KickSubscription] = {}
        self.subscribe_calls: List[int] = []
        self.delete_calls: List[str] = []

    def add_channel(
        self,
        slug: str,
        broadcaster_user_id: int,
        title: str = "Live now",
        category: str = "Just Chatting",
        live: bool = False,
    ) -> "MockKickAPI":
        """A channel Kick knows, live or not."""
        self._channels[slug.lower()] = {
            "broadcaster_user_id": broadcaster_user_id,
            "slug": slug.lower(),
            "stream_title": title,
            "channel_description": f"{slug} streams on Kick",
            "banner_picture": f"https://files.kick.com/images/channel/{slug}/banner.webp",
            "category": {
                "name": category,
                "thumbnail": "https://files.kick.com/images/subcategories/1/banner.webp",
            },
            "stream": {
                "is_live": live,
                "thumbnail": f"https://images.kick.com/video_thumbnails/{slug}/live.webp"
                if live
                else "",
                "start_time": "2026-11-22T12:00:00Z" if live else "",
            },
        }
        return self

    def channels(self, slugs: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        wanted = {str(slug).strip().lower() for slug in slugs}
        return {slug: dict(self._channels[slug]) for slug in wanted if slug in self._channels}

    def subscribe_live_status(self, broadcaster_user_id: int) -> str:
        self.subscribe_calls.append(int(broadcaster_user_id))
        subscription_id = f"sub-{len(self.subscribe_calls)}"
        self._subscriptions[subscription_id] = KickSubscription(
            subscription_id, int(broadcaster_user_id), Commands.KICK_LIVE_STATUS_EVENT
        )
        return subscription_id

    def subscriptions(self) -> List[KickSubscription]:
        return list(self._subscriptions.values())

    def delete_subscription(self, subscription_id: str) -> None:
        self.delete_calls.append(subscription_id)
        self._subscriptions.pop(subscription_id, None)

    def subscribed(self) -> Set[int]:
        """The broadcasters Kick tells Keiko about now."""
        return {subscription.broadcaster_user_id for subscription in self._subscriptions.values()}
