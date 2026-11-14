"""guild.cogs: one document per server and cog, what the form saved in `options` and
what Keiko learned in `state`, read and written as the flat feature document."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.data.collection import Document, Repository


class Cogs(Repository):
    """Every server's cog documents."""

    async def find_cog_by_guild_id(self, guild_id: Any, cog: str) -> Optional[Document]:
        """One server's document of `cog`."""
        stored = await self._collection.find_one({"_id": _cog_id(guild_id, cog)})
        return _flat_document(stored)

    async def find_cogs_by_guild(self, guild_id: Any) -> Dict[str, Document]:
        """Every cog document of one server, by cog."""
        found = self._collection.find({"guild_id": str(guild_id)})
        return {stored["cog"]: _flat(stored) async for stored in found}

    async def find_all_cogs(
        self, cog: str, fields: Sequence[str] = ()
    ) -> List[Document]:
        """Every server's document of `cog`, only `fields` of it when given."""
        projection = {
            "guild_id": True,
            **{_field_path(field): True for field in fields},
        }
        found = self._collection.find({"cog": cog}, projection if fields else None)
        return [_flat(stored) async for stored in found]

    async def find_cogs_by_option(
        self, cog: str, option: str, value: Any, enabled_only: bool = False
    ) -> List[Document]:
        """The servers whose `cog` saved `value` at the dotted `option` path."""
        found = self._collection.find(_option_query(cog, option, value, enabled_only))
        return [_flat(stored) async for stored in found]

    async def count_cogs_by_option(
        self, cog: str, option: str, value: Any, enabled_only: bool = False
    ) -> int:
        """How many servers' `cog` saved `value` at the dotted `option` path."""
        query = _option_query(cog, option, value, enabled_only)
        return await self._collection.count_documents(query)

    async def find_enabled_cogs(self, cog: str) -> List[Document]:
        """Every server's document of `cog` while it is on."""
        found = self._collection.find({"cog": cog, "enabled": True})
        return [_flat(stored) async for stored in found]

    async def find_enabled_guild_ids(self, cog: str) -> List[str]:
        """The ids of the servers that have `cog` on, read without their documents."""
        found = self._collection.find({"cog": cog, "enabled": True}, {"guild_id": True})
        return [str(stored["guild_id"]) async for stored in found]

    async def insert_cog_by_guild_id(
        self, cog: str, data: Mapping[str, Any], updated_by: Optional[str] = None
    ) -> Any:
        """Save a server's document of `cog`, creating it when it has none."""
        guild_id = str(data["guild_id"])
        return await self._collection.update_one(
            {"_id": _cog_id(guild_id, cog)},
            _upsert(guild_id, cog, data, updated_by),
            upsert=True,
        )

    async def update_cog_by_guild(
        self,
        guild_id: Any,
        cog: str,
        data: Mapping[str, Any],
        updated_by: Optional[str] = None,
    ) -> Any:
        """Set the fields of `data` on a server's saved document of `cog`."""
        return await self._collection.update_one(
            {"_id": _cog_id(guild_id, cog)},
            {"$set": _stamped(_stored_fields(data), updated_by)},
        )

    async def set_cog_state(
        self, guild_id: Any, cog: str, fields: Mapping[str, Any]
    ) -> Any:
        """Keep what Keiko learned about a server's cog, beside what the form saved."""
        return await self.update_cog_by_guild(guild_id, cog, {"state": dict(fields)})

    async def update_cog_if_unchanged(
        self,
        guild_id: Any,
        cog: str,
        key: str,
        saved: Any,
        value: Any,
        updated_by: Optional[str] = None,
    ) -> bool:
        """Set `key` to `value` while it still holds `saved`; False when it changed."""
        result = await self._collection.update_one(
            {"_id": _cog_id(guild_id, cog), _field_path(key): saved},
            {"$set": _stamped({_field_path(key): value}, updated_by)},
        )
        return result.matched_count == 1

    async def delete_cog_by_guild_id(
        self, guild_id: Any, cog: str
    ) -> Optional[Document]:
        """Delete a server's document of `cog`; the document it held."""
        stored = await self._collection.find_one_and_delete(
            {"_id": _cog_id(guild_id, cog)}
        )
        return _flat_document(stored)


def _cog_id(guild_id: Any, cog: str) -> str:
    return f"{guild_id}:{cog}"


def _flat(stored: Mapping[str, Any]) -> Document:
    document: Document = {"guild_id": stored.get("guild_id")}
    if "enabled" in stored:
        document["enabled"] = stored["enabled"]
    document.update(stored.get("options") or {})
    document["state"] = dict(stored.get("state") or {})
    for stamp in ("created_at", "updated_at"):
        if stamp in stored:
            document[stamp] = stored[stamp]
    return document


def _flat_document(stored: Optional[Mapping[str, Any]]) -> Optional[Document]:
    return None if stored is None else _flat(stored)


def _field_path(key: str) -> str:
    if key in ("guild_id", "cog", "enabled"):
        return key
    return f"options.{key}"


def _stored_fields(data: Mapping[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for key, value in data.items():
        if key in ("_id", "guild_id", "cog", "created_at", "updated_at", "updated_by"):
            continue
        if key == "state":
            fields.update({f"state.{name}": item for name, item in value.items()})
            continue
        fields[_field_path(key)] = value
    return fields


def _option_query(
    cog: str, option: str, value: Any, enabled_only: bool
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"cog": cog, f"options.{option}": value}
    if enabled_only:
        query["enabled"] = True
    return query


def _stamped(
    fields: Dict[str, Any], updated_by: Optional[str] = None
) -> Dict[str, Any]:
    stamped = {**fields, "updated_at": datetime.now(tz=timezone.utc)}
    if updated_by is not None:
        stamped["updated_by"] = str(updated_by)
    return stamped


def _upsert(
    guild_id: str, cog: str, data: Mapping[str, Any], updated_by: Optional[str]
) -> Dict[str, Any]:
    fields = _stamped(_stored_fields(data), updated_by)
    return {
        "$set": fields,
        "$setOnInsert": {
            "guild_id": guild_id,
            "cog": cog,
            "created_at": fields["updated_at"],
        },
    }
