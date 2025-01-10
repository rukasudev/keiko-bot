
from typing import Any, Dict

from app import mongo_client
from app.constants import Commands as constants


def count_streamers_guilds(streamer_name: str) -> int:
    return mongo_client.guild[constants.NOTIFICATIONS_TWITCH_KEY].count_documents(
        {
            "notifications.values.streamer.value": streamer_name
        }
    )

def find_guilds_by_streamer_name(streamer_name: str) -> Dict[str, Any]:
    return mongo_client.guild[constants.NOTIFICATIONS_TWITCH_KEY].find(
        {
            "notifications.values.streamer.value": streamer_name
        }
    )
