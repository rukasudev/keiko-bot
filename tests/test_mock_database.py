"""The in-memory Mongo the suite runs on answers a filter the way Mongo does.

Guaranteed: a dotted filter reaches into embedded documents and into every item of a
list on the way, in `find_one`, `update_one`, `delete_one` and `find_one_and_delete` as
in `find`; a dotted path that reaches nothing matches only an expected `None`.
"""

from tests.mocks.database import MockMongoClient


def _cogs():
    collection = MockMongoClient().guild.cogs
    collection.insert_one({
        "_id": "1:notifications_twitch",
        "cog": "notifications_twitch",
        "options": {
            "notifications": {
                "style": "composition",
                "values": [
                    {"streamer": {"value": "first"}},
                    {"streamer": {"value": "second"}},
                ],
            },
            "title": "Live",
        },
    })
    return collection


def test_find_one_reads_an_embedded_field_by_its_dotted_path():
    cogs = _cogs()

    assert cogs.find_one({"options.title": "Live"})["_id"] == "1:notifications_twitch"
    assert cogs.find_one({"options.title": "Other"}) is None


def test_a_dotted_path_matches_any_item_of_a_list_it_crosses():
    cogs = _cogs()
    path = "options.notifications.values.streamer.value"

    assert len(list(cogs.find({path: "second"}))) == 1
    assert cogs.count_documents({"cog": "notifications_twitch", path: "first"}) == 1
    assert cogs.find_one({path: "third"}) is None


def test_update_one_matches_a_dotted_filter_before_it_writes():
    cogs = _cogs()

    missed = cogs.update_one(
        {"_id": "1:notifications_twitch", "options.title": "Old"},
        {"$set": {"options.title": "New"}},
    )
    written = cogs.update_one(
        {"_id": "1:notifications_twitch", "options.title": "Live"},
        {"$set": {"options.title": "New"}},
    )

    assert (missed.matched_count, written.matched_count) == (0, 1)
    assert cogs.find_one({"_id": "1:notifications_twitch"})["options"]["title"] == "New"


def test_a_dotted_path_that_reaches_nothing_matches_only_none():
    cogs = _cogs()

    assert cogs.find_one({"state.channel_id": None}) is not None
    assert cogs.find_one({"state.channel_id": "5f"}) is None


def test_deletes_match_a_dotted_filter():
    cogs = _cogs()

    assert cogs.delete_one({"options.title": "Other"}).deleted_count == 0
    assert cogs.find_one_and_delete({"options.title": "Live"})["cog"] == (
        "notifications_twitch"
    )
    assert cogs.count_documents({}) == 0
