"""keiko.subscriptions: the external accounts Keiko follows, one document per
`{platform}:{account}`."""

from typing import Any, Dict, Mapping

from app.data.collection import Repository


class Subscriptions(Repository):
    """The accounts Keiko follows on every platform."""

    async def subscription_state(self, platform: str, account: str) -> Dict[str, Any]:
        """What Keiko learned about one account on one platform, empty when it follows
        none."""
        stored = await self._collection.find_one(
            {"_id": f"{platform}:{account}"}, {"state": True}
        )
        return dict((stored or {}).get("state") or {})

    async def update_subscription_state(
        self, platform: str, account: str, fields: Mapping[str, Any]
    ) -> None:
        """Set `fields` in the account's `state`, creating its subscription when it has
        none."""
        await self._collection.update_one(
            {"_id": f"{platform}:{account}"},
            {
                "$set": {f"state.{name}": value for name, value in fields.items()},
                "$setOnInsert": {"platform": platform, "account": account},
            },
            upsert=True,
        )

    async def count_subscriptions(self, platform: str) -> int:
        """How many accounts Keiko follows on one platform."""
        return await self._collection.count_documents(
            {"_id": {"$gte": f"{platform}:", "$lt": f"{platform};"}}
        )
