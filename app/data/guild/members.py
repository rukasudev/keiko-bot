"""guild.members: one document per server and member, what the member chose for each cog
in `options` and what Keiko learned in `state`, read and written as the cog's document."""

from typing import Any, Dict, List, Mapping, Optional

from app import mongo_client
from app.data.util import parse_update_timestamp


def _member_id(guild_id: Any, user_id: Any) -> str:
    return f"{guild_id}:{user_id}"


def _member_document(
    stored: Optional[Mapping[str, Any]], cog: str
) -> Optional[Dict[str, Any]]:
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
    guild_id: str, cog: str, filters: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"guild_id": str(guild_id)}
    if not filters:
        query[f"options.{cog}"] = {"$exists": True}
    for path, value in (filters or {}).items():
        query[f"options.{cog}.{path}"] = value
    return query


def find_member_option(
    guild_id: str, user_id: str, cog: str
) -> Optional[Dict[str, Any]]:
    """The member's document of `cog`, with what Keiko keeps about them under `state`."""
    stored = mongo_client.guild.members.find_one({"_id": _member_id(guild_id, user_id)})
    return _member_document(stored, cog)


def find_members_by_option(
    guild_id: str, cog: str, filters: Optional[Mapping[str, Any]] = None
) -> List[Dict[str, Any]]:
    """The server's members whose `cog` saved each value of `filters` at its dotted path."""
    cursor = mongo_client.guild.members.find(_option_query(guild_id, cog, filters))
    found = (_member_document(stored, cog) for stored in cursor)
    return [document for document in found if document is not None]


def count_members_by_option(
    guild_id: str, cog: str, filters: Optional[Mapping[str, Any]] = None
) -> int:
    """How many of the server's members' `cog` saved each value of `filters`."""
    query = _option_query(guild_id, cog, filters)
    return mongo_client.guild.members.count_documents(query)


def set_member_option(
    guild_id: str,
    user_id: str,
    cog: str,
    value: Mapping[str, Any],
    display_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Save what the member chose for `cog` in place of what was saved; the document saved."""
    fields: Dict[str, Any] = {
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        f"options.{cog}": dict(value),
    }
    if display_name:
        fields["state.display_name"] = str(display_name)

    update = parse_update_timestamp(fields)
    mongo_client.guild.members.update_one(
        {"_id": _member_id(guild_id, user_id)},
        {"$set": update, "$setOnInsert": {"created_at": update["updated_at"]}},
        upsert=True,
    )
    return find_member_option(guild_id, user_id, cog) or {}


def unset_member_option(
    guild_id: str, user_id: str, cog: str
) -> Optional[Dict[str, Any]]:
    """Forget what the member chose for `cog`, keeping what Keiko learned; what was saved."""
    saved = find_member_option(guild_id, user_id, cog)
    if saved is None:
        return None

    mongo_client.guild.members.update_one(
        {"_id": _member_id(guild_id, user_id)},
        {"$unset": {f"options.{cog}": ""}},
    )
    return saved


def unset_option_in_guild(guild_id: str, cog: str) -> int:
    """Forget what every member of the server chose for `cog`; how many members changed."""
    result = mongo_client.guild.members.update_many(
        _option_query(guild_id, cog, None),
        {"$unset": {f"options.{cog}": ""}},
    )
    return result.modified_count


def claim_member_state(
    guild_id: str,
    user_id: str,
    cog: str,
    field: str,
    value: Any,
    display_name: Optional[str] = None,
) -> bool:
    """Set the member's `field` of `cog` to `value`; False when it already held it."""
    path = f"state.{cog}.{field}"
    fields: Dict[str, Any] = {path: value}
    if display_name:
        fields["state.display_name"] = str(display_name)

    result = mongo_client.guild.members.update_one(
        {"_id": _member_id(guild_id, user_id), path: {"$ne": value}},
        {"$set": fields},
    )
    return result.modified_count == 1


def restore_member_state(
    guild_id: str, user_id: str, cog: str, field: str, expected: Any, previous: Any
) -> bool:
    """Put back `previous` in the member's `field` of `cog` while it still holds `expected`."""
    path = f"state.{cog}.{field}"
    result = mongo_client.guild.members.update_one(
        {"_id": _member_id(guild_id, user_id), path: expected},
        {"$set": {path: previous}},
    )
    return result.modified_count == 1
