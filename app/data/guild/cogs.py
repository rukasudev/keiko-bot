"""guild.cogs: one document per server and cog, what the form saved in `options` and
what Keiko learned in `state`, read and written as the flat feature document."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app import mongo_client, motor_client


def _cog_id(guild_id: Any, cog: str) -> str:
    return f"{guild_id}:{cog}"


def _flat_document(stored: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    if stored is None:
        return None

    document: Dict[str, Any] = {"guild_id": stored.get("guild_id")}
    if "enabled" in stored:
        document["enabled"] = stored["enabled"]
    document.update(stored.get("options") or {})
    document["state"] = dict(stored.get("state") or {})
    for stamp in ("created_at", "updated_at"):
        if stamp in stored:
            document[stamp] = stored[stamp]
    return document


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


def find_cog_by_guild_id(guild_id: str, cog: str) -> Optional[Dict[str, Any]]:
    return _flat_document(
        mongo_client.guild.cogs.find_one({"_id": _cog_id(guild_id, cog)})
    )


def find_cogs_by_guild(guild_id: str) -> Dict[str, Dict[str, Any]]:
    """Every cog document of one server, by cog."""
    return {
        stored["cog"]: _flat_document(stored)
        for stored in mongo_client.guild.cogs.find({"guild_id": str(guild_id)})
    }


def find_all_cogs(cog: str, fields: Sequence[str] = ()) -> List[Dict[str, Any]]:
    projection = {"guild_id": True, **{_field_path(field): True for field in fields}}
    found = mongo_client.guild.cogs.find({"cog": cog}, projection if fields else None)
    return [_flat_document(stored) for stored in found]


def find_cogs_by_option(
    cog: str, option: str, value: Any, enabled_only: bool = False
) -> List[Dict[str, Any]]:
    """The servers whose `cog` saved `value` at the dotted `option` path."""
    query = _option_query(cog, option, value, enabled_only)
    return [_flat_document(stored) for stored in mongo_client.guild.cogs.find(query)]


def count_cogs_by_option(
    cog: str, option: str, value: Any, enabled_only: bool = False
) -> int:
    """How many servers' `cog` saved `value` at the dotted `option` path."""
    query = _option_query(cog, option, value, enabled_only)
    return mongo_client.guild.cogs.count_documents(query)


def find_enabled_cogs(cog: str) -> List[Dict[str, Any]]:
    """Every server's document of `cog` while it is on."""
    found = mongo_client.guild.cogs.find({"cog": cog, "enabled": True})
    return [_flat_document(stored) for stored in found]


def find_enabled_guild_ids(cog: str) -> List[str]:
    """The ids of the servers that have `cog` on, read without their documents."""
    found = mongo_client.guild.cogs.find({"cog": cog, "enabled": True}, {"guild_id": True})
    return [str(stored["guild_id"]) for stored in found]


def insert_cog_by_guild_id(
    cog: str, data: Mapping[str, Any], updated_by: Optional[str] = None
) -> Any:
    guild_id = str(data["guild_id"])
    return mongo_client.guild.cogs.update_one(
        {"_id": _cog_id(guild_id, cog)},
        _upsert(guild_id, cog, data, updated_by),
        upsert=True,
    )


def update_cog_by_guild(
    guild_id: str, cog: str, data: Mapping[str, Any], updated_by: Optional[str] = None
) -> Any:
    return mongo_client.guild.cogs.update_one(
        {"_id": _cog_id(guild_id, cog)},
        {"$set": _stamped(_stored_fields(data), updated_by)},
    )


def delete_cog_by_guild_id(guild_id: str, cog: str) -> Any:
    return mongo_client.guild.cogs.delete_one({"_id": _cog_id(guild_id, cog)})


async def find_cog_by_guild_id_async(
    guild_id: str, cog: str
) -> Optional[Dict[str, Any]]:
    stored = await motor_client.guild.cogs.find_one({"_id": _cog_id(guild_id, cog)})
    return _flat_document(stored)


async def insert_cog_by_guild_id_async(
    cog: str, data: Mapping[str, Any], updated_by: Optional[str] = None
) -> Any:
    guild_id = str(data["guild_id"])
    return await motor_client.guild.cogs.update_one(
        {"_id": _cog_id(guild_id, cog)},
        _upsert(guild_id, cog, data, updated_by),
        upsert=True,
    )


async def update_cog_by_guild_async(
    guild_id: str, cog: str, data: Mapping[str, Any], updated_by: Optional[str] = None
) -> Any:
    return await motor_client.guild.cogs.update_one(
        {"_id": _cog_id(guild_id, cog)},
        {"$set": _stamped(_stored_fields(data), updated_by)},
    )


async def set_cog_state_async(
    guild_id: str, cog: str, fields: Mapping[str, Any]
) -> Any:
    """Keep what Keiko learned about a server's cog, beside what the form saved."""
    return await update_cog_by_guild_async(guild_id, cog, {"state": dict(fields)})


async def update_cog_if_unchanged_async(
    guild_id: str,
    cog: str,
    key: str,
    saved: Any,
    value: Any,
    updated_by: Optional[str] = None,
) -> bool:
    result = await motor_client.guild.cogs.update_one(
        {"_id": _cog_id(guild_id, cog), _field_path(key): saved},
        {"$set": _stamped({_field_path(key): value}, updated_by)},
    )
    return result.matched_count == 1


async def delete_cog_by_guild_id_async(
    guild_id: str, cog: str
) -> Optional[Dict[str, Any]]:
    stored = await motor_client.guild.cogs.find_one_and_delete(
        {"_id": _cog_id(guild_id, cog)}
    )
    return _flat_document(stored)


def _option_query(
    cog: str, option: str, value: Any, enabled_only: bool
) -> Dict[str, Any]:
    query: Dict[str, Any] = {"cog": cog, f"options.{option}": value}
    if enabled_only:
        query["enabled"] = True
    return query


def _stamped(fields: Dict[str, Any], updated_by: Optional[str] = None) -> Dict[str, Any]:
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
