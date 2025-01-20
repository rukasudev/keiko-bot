
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

def find_last_stream_date(streamer_name: str) -> str:
    response = mongo_client.audit[constants.NOTIFICATIONS_TWITCH_KEY].find_one(
        {
            "streamer": str(streamer_name)
        }
    )
    return response.get("last_stream_date") if response else None

def update_last_stream_date(streamer_name: str, last_stream_date: str) -> str:
    return mongo_client.audit[constants.NOTIFICATIONS_TWITCH_KEY].update_one(
        {
            "streamer": str(streamer_name),
        },
        {"$set": {"last_stream_date": str(last_stream_date)}},
        upsert=True
    )

def find_stream_notification(guild_id: str, channel_id: str, streamer_name: str) -> Dict[str, Any]:
    return mongo_client.notifications[constants.NOTIFICATIONS_TWITCH_KEY].find_one(
        {
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
            "streamer": streamer_name,
        }
    )

def save_stream_notification(
    guild_id: str, channel_id: str, streamer_name: str, message_id: str
) -> str:
    return mongo_client.notifications[constants.NOTIFICATIONS_TWITCH_KEY].update_one(
        {
            "guild_id": str(guild_id),
            "channel_id": str(channel_id),
            "streamer": streamer_name,
        },
        {"$set": {"message_id": str(message_id)}},
        upsert=True,
    )
