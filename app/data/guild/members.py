"""guild.members: one document per server and member, what the member chose for each
cog in `options` and what Keiko learned in `state`, read and written as the cog's
document."""

from typing import Any, Dict, List, Mapping, Optional

from app.data.collection import Document, Repository
from app.data.util import parse_update_timestamp


class Members(Repository):
    """Every server's members, what each chose for each cog."""

    async def find_member_option(
        self, guild_id: Any, user_id: Any, cog: str
    ) -> Optional[Document]:
        """The member's document of `cog`, with what Keiko keeps about them under
        `state`."""
        stored = await self._collection.find_one({"_id": _member_id(guild_id, user_id)})
        return _member_document(stored, cog)

    async def find_members_by_option(
        self, guild_id: Any, cog: str, filters: Optional[Mapping[str, Any]] = None
    ) -> List[Document]:
        """The server's members whose `cog` saved each value of `filters` at its
        dotted path."""
        found = self._collection.find(_option_query(guild_id, cog, filters))
        documents = [_member_document(stored, cog) async for stored in found]
        return [document for document in documents if document is not None]

    async def count_members_by_option(
        self, guild_id: Any, cog: str, filters: Optional[Mapping[str, Any]] = None
    ) -> int:
        """How many of the server's members' `cog` saved each value of `filters`."""
        query = _option_query(guild_id, cog, filters)
        return await self._collection.count_documents(query)

    async def set_member_option(
        self,
        guild_id: Any,
        user_id: Any,
        cog: str,
        value: Mapping[str, Any],
        display_name: Optional[str] = None,
    ) -> Document:
        """Save what the member chose for `cog` in place of what was saved; the
        document saved."""
        fields: Dict[str, Any] = {
            "guild_id": str(guild_id),
            "user_id": str(user_id),
            f"options.{cog}": dict(value),
        }
        if display_name:
            fields["state.display_name"] = str(display_name)

        update = parse_update_timestamp(fields)
        await self._collection.update_one(
            {"_id": _member_id(guild_id, user_id)},
            {"$set": update, "$setOnInsert": {"created_at": update["updated_at"]}},
            upsert=True,
        )
        return await self.find_member_option(guild_id, user_id, cog) or {}

    async def unset_member_option(
        self, guild_id: Any, user_id: Any, cog: str
    ) -> Optional[Document]:
        """Forget what the member chose for `cog`, keeping what Keiko learned; what
        was saved."""
        saved = await self.find_member_option(guild_id, user_id, cog)
        if saved is None:
            return None

        await self._collection.update_one(
            {"_id": _member_id(guild_id, user_id)},
            {"$unset": {f"options.{cog}": ""}},
        )
        return saved

    async def unset_option_in_guild(self, guild_id: Any, cog: str) -> int:
        """Forget what every member of the server chose for `cog`; how many members
        changed."""
        result = await self._collection.update_many(
            _option_query(guild_id, cog, None),
            {"$unset": {f"options.{cog}": ""}},
        )
        return result.modified_count

    async def claim_member_state(
        self,
        guild_id: Any,
        user_id: Any,
        cog: str,
        field: str,
        value: Any,
        display_name: Optional[str] = None,
    ) -> bool:
        """Set the member's `field` of `cog` to `value`; False when it held it."""
        path = f"state.{cog}.{field}"
        fields: Dict[str, Any] = {path: value}
        if display_name:
            fields["state.display_name"] = str(display_name)

        result = await self._collection.update_one(
            {"_id": _member_id(guild_id, user_id), path: {"$ne": value}},
            {"$set": fields},
        )
        return result.modified_count == 1

    async def restore_member_state(
        self,
        guild_id: Any,
        user_id: Any,
        cog: str,
        field: str,
        expected: Any,
        previous: Any,
    ) -> bool:
        """Put back `previous` in the member's `field` of `cog` while it still holds
        `expected`."""
        path = f"state.{cog}.{field}"
        result = await self._collection.update_one(
            {"_id": _member_id(guild_id, user_id), path: expected},
            {"$set": {path: previous}},
        )
        return result.modified_count == 1


def _member_id(guild_id: Any, user_id: Any) -> str:
    return f"{guild_id}:{user_id}"


def _member_document(
    stored: Optional[Mapping[str, Any]], cog: str
) -> Optional[Document]:
    if stored is None or not (stored.get("options") or {}).get(cog):
        return None

    state = stored.get("state") or {}
    known: Dict[str, Any] = {}
    if "display_name" in state:
        known["display_name"] = state["display_name"]
    known.update(state.get(cog) or {})
    return {
        "guild_id": stored.get("guild_id"),
        "user_id": stored.get("user_id"),
        **stored["options"][cog],
        "state": known,
        "created_at": stored.get("created_at"),
        "updated_at": stored.get("updated_at"),
    }


def _option_query(
    guild_id: Any, cog: str, filters: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"guild_id": str(guild_id)}
    if not filters:
        query[f"options.{cog}"] = {"$exists": True}
    for path, value in (filters or {}).items():
        query[f"options.{cog}.{path}"] = value
    return query
