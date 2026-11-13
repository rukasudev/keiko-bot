"""The external accounts Keiko follows, one document per `{platform}:{account}`."""

from typing import Any, Dict

from app import mongo_client


def subscription_state(platform: str, account: str) -> Dict[str, Any]:
    """What Keiko learned about one account on one platform, empty when it follows none."""
    stored = mongo_client.keiko.subscriptions.find_one(
        {"_id": f"{platform}:{account}"}, {"state": True}
    )
    return dict((stored or {}).get("state") or {})


def update_subscription_state(platform: str, account: str, fields: Dict[str, Any]) -> None:
    """Set `fields` in the account's `state`, creating its subscription when it has none."""
    mongo_client.keiko.subscriptions.update_one(
        {"_id": f"{platform}:{account}"},
        {
            "$set": {f"state.{name}": value for name, value in fields.items()},
            "$setOnInsert": {"platform": platform, "account": account},
        },
        upsert=True,
    )
