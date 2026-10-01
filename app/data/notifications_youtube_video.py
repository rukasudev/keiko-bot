
from typing import Any, Dict, List

from app import mongo_client
from app.constants import Commands as constants


def count_youtube_video_subscription_by_guilds(youtuber: str) -> Dict[str, Any]:
    return mongo_client.guild[constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY].count_documents(
        {
            "notifications.values.youtuber.value": youtuber,
            "enabled": True,
        }
    )

def count_servers_following(youtuber: str) -> int:
    """How many servers follow the youtuber, paused or not."""
    return mongo_client.guild[constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY].count_documents(
        {"notifications.values.youtuber.value": youtuber}
    )

def find_guilds_by_youtuber(youtuber: str) -> Dict[str, Any]:
    return mongo_client.guild[constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY].find(
        {
            "notifications.values.youtuber.value": youtuber,
            "enabled": True,
        }
    )

def find_followed_youtubers() -> List[str]:
    """Every youtuber an enabled guild follows, once each."""
    documents = mongo_client.guild[constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY].find({"enabled": True})
    return sorted({
        str(notification["youtuber"]["value"])
        for document in documents
        for notification in (document.get("notifications") or {}).get("values") or []
        if (notification.get("youtuber") or {}).get("value")
    })
