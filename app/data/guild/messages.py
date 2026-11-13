"""The messages Keiko posted in a server and edits later, one per channel, cog and subject."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app import mongo_client


def find_message(
    guild_id: Any, channel_id: Any, cog: str, subject: str
) -> Optional[Dict[str, Any]]:
    """The message a cog posted about `subject` in one channel of a server."""
    return mongo_client.guild.messages.find_one(
        {"_id": f"{guild_id}:{channel_id}:{cog}:{subject}"}
    )


def save_message(
    guild_id: Any, channel_id: Any, cog: str, subject: str, message_id: Any
) -> None:
    """Keep the id of the message a cog posted about `subject`, replacing the one before."""
    mongo_client.guild.messages.update_one(
        {"_id": f"{guild_id}:{channel_id}:{cog}:{subject}"},
        {
            "$set": {
                "message_id": str(message_id),
                "updated_at": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
                "cog": cog,
                "subject": subject,
            },
        },
        upsert=True,
    )
