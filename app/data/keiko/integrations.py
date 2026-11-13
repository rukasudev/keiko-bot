"""keiko.integrations: one document per integration, its switch and settings in
`options`."""

from typing import Any, Dict

from app import mongo_client


def find_integration(name: str) -> Dict[str, Any]:
    """The options of an integration; empty when it has none saved."""
    document = mongo_client.keiko.integrations.find_one({"_id": name}) or {}
    return dict(document.get("options") or {})
