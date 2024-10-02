
from typing import Any, Dict

from app import mongo_client
from app.constants import Commands as constants


def count_youtube_video_subscription_by_guilds(youtuber: str) -> Dict[str, Any]:
    return mongo_client.guild[constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY].count_documents(
        {
            "notifications.values.youtuber.value": youtuber
        }
    )

def find_guilds_by_youtuber(youtuber: str) -> Dict[str, Any]:
    return mongo_client.guild[constants.NOTIFICATIONS_YOUTUBE_VIDEO_KEY].find(
        {
            "notifications.values.youtuber.value": youtuber
        }
    )
