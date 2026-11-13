"""keiko.settings: Keiko's one document, its presence, a new server's prefix, its owner
and the operator's server and channels, all in `options`."""

from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from app import mongo_client


def find_keiko_settings() -> Dict[str, Any]:
    """Keiko's options; empty when the migrations have not written them."""
    document = mongo_client.keiko.settings.find_one({"_id": "keiko"}) or {}
    return dict(document.get("options") or {})


def update_presence(data: Mapping[str, Any]) -> Any:
    return _set_options("presence", data)


def update_operator(data: Mapping[str, Any]) -> Any:
    return _set_options("operator", data)


def _set_options(group: str, data: Mapping[str, Any]) -> Any:
    fields = {f"options.{group}.{key}": value for key, value in data.items()}
    return mongo_client.keiko.settings.update_one(
        {"_id": "keiko"},
        {"$set": {**fields, "updated_at": datetime.now(tz=timezone.utc)}},
    )
