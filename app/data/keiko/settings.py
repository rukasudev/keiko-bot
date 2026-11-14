"""keiko.settings: Keiko's one document, its presence, a new server's prefix, its owner
and the operator's server and channels, all in `options`."""

from datetime import datetime, timezone
from typing import Any, Mapping

from app.data.collection import Document, Repository


class Settings(Repository):
    """Keiko's own settings."""

    async def find_keiko_settings(self) -> Document:
        """Keiko's options; empty when the migrations have not written them."""
        document = await self._collection.find_one({"_id": "keiko"}) or {}
        return dict(document.get("options") or {})

    async def update_presence(self, data: Mapping[str, Any]) -> Any:
        """Save the fields of Keiko's presence that `data` holds."""
        return await self._set_options("presence", data)

    async def update_operator(self, data: Mapping[str, Any]) -> Any:
        """Save the fields of the operator's server and channels that `data` holds."""
        return await self._set_options("operator", data)

    async def _set_options(self, group: str, data: Mapping[str, Any]) -> Any:
        fields = {f"options.{group}.{key}": value for key, value in data.items()}
        return await self._collection.update_one(
            {"_id": "keiko"},
            {"$set": {**fields, "updated_at": datetime.now(tz=timezone.utc)}},
        )
