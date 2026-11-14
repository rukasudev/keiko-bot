"""guild.messages: the messages Keiko posted in a server and edits later, one per
channel, cog and subject."""

from datetime import datetime, timezone
from typing import Any, Optional

from app.data.collection import Document, Repository


class Messages(Repository):
    """The messages Keiko keeps editing, by channel, cog and subject."""

    async def find_message(
        self, guild_id: Any, channel_id: Any, cog: str, subject: str
    ) -> Optional[Document]:
        """The message a cog posted about `subject` in one channel of a server."""
        return await self._collection.find_one(
            {"_id": f"{guild_id}:{channel_id}:{cog}:{subject}"}
        )

    async def save_message(
        self, guild_id: Any, channel_id: Any, cog: str, subject: str, message_id: Any
    ) -> None:
        """Keep the id of the message a cog posted about `subject`, replacing the one
        before."""
        await self._collection.update_one(
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
