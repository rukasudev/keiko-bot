from datetime import datetime
from typing import Dict

from pymongo import UpdateOne

from app import mongo_client


def count_blocked_links(total: int, hosts: Dict[str, int], moment: datetime):
    """Add blocks to Keiko's own count: the total and each site, with no guild and no member."""
    operations = [
        UpdateOne(
            {"_id": "total"},
            {"$inc": {"blocked": total}, "$set": {"updated_at": moment}},
            upsert=True,
        )
    ]
    operations += [
        UpdateOne(
            {"_id": f"site:{host}"},
            {"$inc": {"blocked": count}, "$set": {"host": host, "updated_at": moment}},
            upsert=True,
        )
        for host, count in hosts.items()
    ]
    return mongo_client.guild.block_links_totals.bulk_write(operations, ordered=False)
