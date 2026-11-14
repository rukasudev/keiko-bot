"""The stamps every write carries, and the operations that reach any collection."""

import datetime
from typing import Any, AsyncGenerator, Dict, List

from app.data.collection import Client, Document


def parse_update_timestamp(data: Dict[Any, Any]) -> Dict[Any, Any]:
    """`data` stamped as updated now."""
    date = datetime.datetime.now(tz=datetime.timezone.utc)
    new_data = {"updated_at": date}
    new_data.update(data)
    return new_data


class Databases:
    """What reaches any collection by name: listing, a server's erasure, a backup."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def collection_names(self, database: str) -> List[str]:
        """The collections `database` holds."""
        return await self._client[database].list_collection_names()

    async def delete_guild_rows(
        self, database: str, collection: str, guild_id: Any, field: str = "guild_id"
    ) -> int:
        """Delete the documents of one guild, found by `field`, and count them."""
        result = await self._client[database][collection].delete_many(
            {field: str(guild_id)}
        )
        return result.deleted_count

    async def iter_documents(
        self, database: str, collection: str
    ) -> AsyncGenerator[Document, None]:
        """Stream a collection, so the backup never holds one whole in memory."""
        async for document in self._client[database][collection].find({}):
            yield document
