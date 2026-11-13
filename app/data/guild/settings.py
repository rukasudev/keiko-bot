"""guild.settings: one document per server, its language, time zone and prefix in
`options`, its owner and Keiko's presence in `state`, read as the flat server
document."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from app import mongo_client


def find_settings(guild_id: Any) -> Optional[Dict[str, Any]]:
    """The server document: `guild_id`, the options at the top, its owner, Keiko's
    presence and the stamps."""
    return _flat_document(mongo_client.guild.settings.find_one({"_id": str(guild_id)}))


def find_guild_ids_with_settings(guild_ids: Sequence[Any]) -> Set[str]:
    documents = mongo_client.guild.settings.find(
        {"_id": {"$in": [str(guild_id) for guild_id in guild_ids]}}, {"_id": True}
    )
    return {str(document["_id"]) for document in documents}


def record_join(guild_id: Any, owner_id: str, options: Mapping[str, Any]) -> Any:
    """Keiko is in the server from now, owned by `owner_id`; `options` only where the
    server has none saved."""
    now = datetime.now(tz=timezone.utc)
    return mongo_client.guild.settings.update_one(
        {"_id": str(guild_id)},
        {
            "$set": {
                "state.owner_id": str(owner_id),
                "state.present": True,
                "state.joined_at": now,
                "updated_at": now,
            },
            "$setOnInsert": {"options": dict(options), "created_at": now},
        },
        upsert=True,
    )


def record_leave(guild_id: Any) -> Any:
    """Keiko left the server now."""
    now = datetime.now(tz=timezone.utc)
    return mongo_client.guild.settings.update_one(
        {"_id": str(guild_id)},
        {"$set": {"state.present": False, "state.left_at": now, "updated_at": now}},
    )


def set_option(guild_id: Any, key: str, value: Any) -> Any:
    return mongo_client.guild.settings.update_one(
        {"_id": str(guild_id)},
        {
            "$set": {
                f"options.{key}": value,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        },
    )


def count_servers_by_owner(owner_id: str) -> int:
    return mongo_client.guild.settings.count_documents(
        {"state.owner_id": str(owner_id)}
    )


def find_servers_by_owner(owner_id: str) -> List[Dict[str, Any]]:
    found = mongo_client.guild.settings.find({"state.owner_id": str(owner_id)})
    return [_flat_document(stored) for stored in found]


def count_absent(guild_ids: Sequence[Any]) -> int:
    """How many of `guild_ids` Keiko is no longer in."""
    return mongo_client.guild.settings.count_documents(
        {
            "_id": {"$in": [str(guild_id) for guild_id in guild_ids]},
            "state.present": False,
        }
    )


def aggregate_settings(pipeline: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The documents an aggregation `pipeline` over every server's settings returns."""
    return list(mongo_client.guild.settings.aggregate(list(pipeline)))


def _flat_document(stored: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if stored is None:
        return None

    state = stored.get("state") or {}
    document: Dict[str, Any] = {"guild_id": str(stored["_id"])}
    document.update(stored.get("options") or {})
    for field in ("owner_id", "present", "joined_at", "left_at"):
        document[field] = state.get(field)
    for stamp in ("created_at", "updated_at"):
        document[stamp] = stored.get(stamp)
    return document
