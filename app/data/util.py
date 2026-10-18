import datetime
from typing import Any, Dict, Iterator, List

from app import mongo_client


def parse_insert_timestamp(data: Dict[Any, Any]) -> Dict[Any, Any]:
    date = datetime.datetime.now(tz=datetime.timezone.utc)
    new_data = {"created_at": date, "updated_at": date}
    new_data.update(data)
    return new_data


def parse_update_timestamp(data: Dict[Any, Any]) -> Dict[Any, Any]:
    date = datetime.datetime.now(tz=datetime.timezone.utc)
    new_data = {"updated_at": date}
    new_data.update(data)
    return new_data


def collection_names(database: str) -> List[str]:
    return mongo_client[database].list_collection_names()


def iter_documents(database: str, collection: str) -> Iterator[Dict[str, Any]]:
    """Stream a collection, so the backup never holds one whole in memory."""
    for document in mongo_client[database][collection].find({}):
        yield document
