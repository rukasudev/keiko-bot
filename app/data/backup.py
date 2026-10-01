"""Keiko's databases and collections, read whole for the daily backup."""
from typing import Any, Dict, Iterator, List

from app import mongo_client


def collection_names(database: str) -> List[str]:
    return mongo_client[database].list_collection_names()


def iter_documents(database: str, collection: str) -> Iterator[Dict[str, Any]]:
    """Stream a collection, so the backup never holds one whole in memory."""
    for document in mongo_client[database][collection].find({}):
        yield document
