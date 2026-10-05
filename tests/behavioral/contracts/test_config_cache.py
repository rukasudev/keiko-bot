"""The config cache: what every runtime reader of a feature's settings goes through.

`get_cog_data_or_populate` (app/services/cache.py) answers block links on every
message, welcome messages and auto roles on every member join, StreamElements on
every `ks!` command, and every manager panel when it opens.

Broke as: a server with no configuration read Mongo on every message, because
"nothing saved" was never cached; a Redis outage raised on every message instead
of reading Mongo, so block links, welcome messages, auto roles and StreamElements
all stopped while Mongo was fine; and a Mongo outage stopped them too, although
the bot had read the same settings seconds before. The first in-process copy
could also bring a disabled feature back: it kept what Redis said, stale or not,
and a read racing a Disable kept and cached again the document it deleted. Then
four interleavings still served a disabled feature for up to five minutes: a
Disable landing between a read's check and its Redis write; a Disable skipped
during a Redis blip and never sent; a read's Redis write landing after the
Disable's delete and then timing out; and a Disable queued while an older delete
was on the wire, which that delete then counted as sent. A Mongo outage longer
than an hour stopped every feature again, since nothing older was served, and
an outage met inside a listener never reached the log channel, because a
listener's clean trace is not posted.

Guaranteed: "nothing saved" is cached briefly and reads as a miss to v0.9.0
(an empty value, so a rollback reads Mongo instead of a fake document); a Redis
outage answers from Mongo, costs one Redis call per window, invalidations
included, and one log line per window, a message of its own on the log channel
even from a listener, and the invalidations it skipped are sent as soon as Redis
answers; a Mongo outage serves what this process last read from Mongo, however
long ago, from a bounded memory and with one log line per window, and fails at
once for anything else instead of waiting for the driver on every message; and
an invalidation made by this process always wins: a read that raced it neither
keeps nor leaves cached what it read, and its own failure never fails the save
that asked for it. Not guaranteed: a change made outside this process (v0.9.0
running alongside, a manual edit) is read only when the cached entry expires,
and during a Mongo outage not until Mongo answers again.
"""
import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bson import json_util
from pymongo.errors import ServerSelectionTimeoutError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app import logger as logger_module
from app.constants import DBConfigs
from app.decorators import with_error_context
from app.services import cache
from app.services import trace as trace_service
from app.settings.features import feature_for
from app.settings.features.feature import CommitContext

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("config_cache")]

GUILD_ID = "123456789"
KEY = f"guild:{GUILD_ID}:cog.block_links"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

BLOCK_LINKS = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "answer": "Nada de links aqui! :p",
}


def read(guild_id=GUILD_ID, manager=False):
    return cache.get_cog_data_or_populate(guild_id, "block_links", manager)


def counted(monkeypatch, owner, method):
    """Counts the calls `owner.method` receives, and still makes them."""
    original = getattr(owner, method)
    calls = []

    def counting(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, method, counting)
    return calls


def failing(monkeypatch, owner, method, error):
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(owner, method, fail)
    return calls


def redis_is_down(monkeypatch, deps):
    error = RedisConnectionError("Error 111 connecting to redis. Connection refused.")
    return {
        method: failing(monkeypatch, deps.redis_client, method, error)
        for method in ("get", "setex", "delete", "keys")
    }


def mongo_is_down(monkeypatch, deps, collection="block_links"):
    error = ServerSelectionTimeoutError("No servers found yet")
    return failing(monkeypatch, deps.mongo_client.guild[collection], "find_one", error)


def frozen_clock(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(cache, "monotonic", lambda: clock[0])
    return clock


def warnings_about(caplog, words):
    return [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and words in record.getMessage()
    ]


class _Posted:
    def __await__(self):
        yield
        return None

    def close(self):
        return None


@pytest.fixture
def log_channel():
    """What the admin log channel receives, through the handlers the bot installs."""
    sends = []
    channel = SimpleNamespace(send=lambda **kwargs: (sends.append(kwargs), _Posted())[1])
    bot = SimpleNamespace(
        loop=MagicMock(),
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=lambda channel_id: channel,
    )
    trace_service.clear_sinks()
    folding = logger_module.TraceFoldingHandler()
    handler = logger_module.DiscordLogsHandler(bot)
    logger_module.logger.removeHandler(handler)
    handler.schedule_send = lambda coroutine: coroutine.close()
    logger_module.logger.addHandler(folding)
    logger_module.logger.addHandler(handler)

    yield sends

    logger_module.logger.removeHandler(folding)
    logger_module.logger.removeHandler(handler)
    trace_service.clear_sinks()


async def test_a_server_without_settings_reads_mongo_once_not_on_every_message(
    deps, monkeypatch
):
    reads = counted(monkeypatch, deps.mongo_client.guild["block_links"], "find_one")

    answers = [read() for _message in range(5)]

    assert answers == [None] * 5
    assert len(reads) == 1, f"five messages, {len(reads)} Mongo reads"


def test_nothing_saved_reads_as_a_miss_to_v0_9_0(deps):
    """v0.9.0 reads the same key with `if data:` and falls through to Mongo."""
    read()

    stored = deps.redis_client.get(KEY)
    assert stored == "", "a miss is cached as an empty value"
    assert deps.redis_client.ttl(KEY) == DBConfigs.COG_CACHE_MISSING_TTL_SECONDS


async def test_a_setup_replaces_nothing_saved_at_once(deps):
    read()

    await feature_for("block_links").write_document(GUILD_ID, BLOCK_LINKS)

    assert read()["answer"] == BLOCK_LINKS["answer"]


def test_a_redis_outage_still_answers_from_mongo(deps, monkeypatch):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    redis_is_down(monkeypatch, deps)

    assert read()["answer"] == BLOCK_LINKS["answer"]


def test_a_redis_outage_costs_one_call_and_one_line_per_window(
    deps, monkeypatch, caplog
):
    """A Redis that times out costs the socket timeout on every call it gets."""
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    calls = redis_is_down(monkeypatch, deps)
    clock = frozen_clock(monkeypatch)

    with caplog.at_level(logging.WARNING):
        for _message in range(10):
            read()
        assert len(calls["get"]) == 1, "Redis is skipped for a while after it failed"
        assert len(warnings_about(caplog, "Redis")) == 1, "one line, not one per message"

        clock[0] += DBConfigs.COG_CACHE_WARN_SECONDS + 1
        for _message in range(3):
            read()

    assert len(calls["get"]) == 2, "then one message tries it again, not every one"
    assert len(warnings_about(caplog, "Redis")) == 2, "a long outage is logged again"


async def test_an_outage_inside_a_listener_posts_one_warning_per_window(
    deps, monkeypatch, log_channel
):
    """A listener's trace is posted only when it fails, and a fallback is no failure."""
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    redis_is_down(monkeypatch, deps)
    clock = frozen_clock(monkeypatch)

    @with_error_context("on_message")
    async def on_message():
        await asyncio.to_thread(read)

    for _message in range(5):
        await on_message()

    clock[0] += DBConfigs.COG_CACHE_WARN_SECONDS + 1
    await on_message()

    posted = [
        sent["embed"] for sent in log_channel if "Redis" in str(sent["embed"].description)
    ]
    assert len(posted) == 2, "the outage warning stayed inside the listener's silent trace"


def test_invalidations_during_a_redis_outage_cost_no_redis_call(deps, monkeypatch):
    """Leaving a guild drops seven features and the guild: eight timeouts, before."""
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    calls = redis_is_down(monkeypatch, deps)
    frozen_clock(monkeypatch)
    read()

    for key in ("block_links", "welcome_messages", "default_roles"):
        cache.remove_cog_cache_by_guild(GUILD_ID, key)
    cache.remove_all_cache_by_guild(GUILD_ID)

    made = sum(len(made_calls) for made_calls in calls.values())
    assert made == 1, f"{made} Redis calls during one outage window"


def test_redis_answering_an_invalidation_is_read_again_at_once(deps, monkeypatch):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    clock = frozen_clock(monkeypatch)
    down = [True]
    gets = []
    original_get = deps.redis_client.get

    def get(key):
        gets.append(key)
        if down[0]:
            raise RedisConnectionError("Error 111 connecting to redis. Connection refused.")
        return original_get(key)

    monkeypatch.setattr(deps.redis_client, "get", get)
    read()
    down[0] = False
    clock[0] += DBConfigs.COG_CACHE_RETRY_SECONDS + 1

    cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")
    read()

    assert len(gets) == 2, "Redis answered the invalidation and reads still skipped it"


def test_a_mongo_outage_serves_the_last_known_settings(deps, monkeypatch, caplog):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    read()
    deps.redis_client.delete(KEY)
    mongo_is_down(monkeypatch, deps)

    with caplog.at_level(logging.WARNING):
        answer = read()

    assert answer["answer"] == BLOCK_LINKS["answer"]
    assert len(warnings_about(caplog, "Mongo")) == 1


def test_a_mongo_outage_serves_a_known_absence_too(deps, monkeypatch):
    read()
    deps.redis_client.delete(KEY)
    mongo_is_down(monkeypatch, deps)

    assert read() is None


def test_a_mongo_outage_with_nothing_known_still_fails(deps, monkeypatch):
    mongo_is_down(monkeypatch, deps)

    with pytest.raises(ServerSelectionTimeoutError):
        read()


def test_a_mongo_outage_is_not_waited_on_for_every_message(deps, monkeypatch):
    """The driver gives up after its server selection timeout, thirty seconds."""
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    read()
    calls = mongo_is_down(monkeypatch, deps)
    frozen_clock(monkeypatch)

    for _message in range(5):
        deps.redis_client.delete(KEY)
        assert read()["answer"] == BLOCK_LINKS["answer"]

    assert len(calls) == 1, "Mongo is asked once, then skipped for a while"


def test_during_a_mongo_outage_a_setting_never_read_fails_at_once(deps, monkeypatch):
    """Nothing to serve is no reason to wait for the driver on every message."""
    calls = mongo_is_down(monkeypatch, deps)
    frozen_clock(monkeypatch)

    with pytest.raises(ServerSelectionTimeoutError):
        read("1")
    with pytest.raises(ServerSelectionTimeoutError):
        read("2")

    assert len(calls) == 1, "the second guild failed with the first one's error"


def test_the_last_known_settings_are_bounded(deps, monkeypatch):
    monkeypatch.setattr(DBConfigs, "COG_CACHE_LAST_KNOWN_SIZE", 2)
    for guild_id in ("1", "2", "3"):
        deps.mongo_client.guild["block_links"].insert_one(
            {**BLOCK_LINKS, "guild_id": guild_id}
        )
        read(guild_id)
    for guild_id in ("1", "2", "3"):
        deps.redis_client.delete(f"guild:{guild_id}:cog.block_links")
    mongo_is_down(monkeypatch, deps)

    assert read("3")["guild_id"] == "3"
    assert read("2")["guild_id"] == "2"
    with pytest.raises(ServerSelectionTimeoutError):
        read("1")


def test_a_mongo_outage_longer_than_an_hour_keeps_serving_what_was_read(
    deps, monkeypatch, caplog
):
    """v0.9.0's month-long Redis copy kept block links running through one."""
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    clock = frozen_clock(monkeypatch)
    read()
    mongo_is_down(monkeypatch, deps)

    with caplog.at_level(logging.WARNING):
        for _minute in range(3 * 60):
            deps.redis_client.delete(KEY)
            assert read()["answer"] == BLOCK_LINKS["answer"]
            clock[0] += 60

    windows = 3 * 60 * 60 // DBConfigs.COG_CACHE_WARN_SECONDS
    assert len(warnings_about(caplog, "Mongo")) <= windows + 1, "a line per message"


def test_a_disable_racing_the_redis_write_never_leaves_the_old_settings(
    deps, monkeypatch
):
    collection = deps.mongo_client.guild["block_links"]
    collection.insert_one(dict(BLOCK_LINKS))
    original_setex = deps.redis_client.setex

    def setex_after_a_disable(key, seconds, value):
        monkeypatch.setattr(deps.redis_client, "setex", original_setex)
        collection.delete_one({"guild_id": GUILD_ID})
        cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")
        return original_setex(key, seconds, value)

    monkeypatch.setattr(deps.redis_client, "setex", setex_after_a_disable)
    read()

    assert deps.redis_client.get(KEY) is None, "the Disable was undone by the read's write"
    assert read() is None


def test_a_disable_skipped_during_a_redis_blip_is_sent_when_redis_answers(
    deps, monkeypatch
):
    collection = deps.mongo_client.guild["block_links"]
    collection.insert_one(dict(BLOCK_LINKS))
    clock = frozen_clock(monkeypatch)
    read()
    down = [True]
    original_get = deps.redis_client.get

    def get(key):
        if down[0]:
            raise RedisConnectionError("Error 111 connecting to redis. Connection refused.")
        return original_get(key)

    monkeypatch.setattr(deps.redis_client, "get", get)
    read()
    down[0] = False
    collection.delete_one({"guild_id": GUILD_ID})
    cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")
    clock[0] += DBConfigs.COG_CACHE_RETRY_SECONDS + 1

    assert read() is None, "block links came back from Redis after it was disabled"


def test_a_redis_write_that_lands_and_then_fails_never_serves_the_old_settings(
    deps, monkeypatch
):
    """The Disable's delete reached Redis first, then the read's write, then its timeout."""
    collection = deps.mongo_client.guild["block_links"]
    collection.insert_one(dict(BLOCK_LINKS))
    clock = frozen_clock(monkeypatch)
    original_setex = deps.redis_client.setex

    def setex_after_a_disable_then_timed_out(key, seconds, value):
        monkeypatch.setattr(deps.redis_client, "setex", original_setex)
        collection.delete_one({"guild_id": GUILD_ID})
        cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")
        original_setex(key, seconds, value)
        raise RedisTimeoutError("Timeout reading from socket")

    monkeypatch.setattr(deps.redis_client, "setex", setex_after_a_disable_then_timed_out)
    read()
    clock[0] += DBConfigs.COG_CACHE_RETRY_SECONDS + 1

    assert read() is None, "block links came back from Redis after it was disabled"


def test_a_disable_queued_behind_a_delete_in_flight_is_still_sent(deps, monkeypatch):
    """Another thread's delete is still on the wire while a message reads, a Redis
    blip opens the window and a Disable is queued behind it."""
    collection = deps.mongo_client.guild["block_links"]
    collection.insert_one(dict(BLOCK_LINKS))
    frozen_clock(monkeypatch)
    original_get = deps.redis_client.get
    original_setex = deps.redis_client.setex
    original_delete = deps.redis_client.delete

    def get_that_blips(key):
        monkeypatch.setattr(deps.redis_client, "get", original_get)
        raise RedisConnectionError("Error 111 connecting to redis. Connection refused.")

    def setex_after_a_blip_and_a_disable(key, seconds, value):
        monkeypatch.setattr(deps.redis_client, "setex", original_setex)
        monkeypatch.setattr(deps.redis_client, "get", get_that_blips)
        read("2")
        collection.delete_one({"guild_id": GUILD_ID})
        cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")
        return original_setex(key, seconds, value)

    def delete_while_a_message_reads(*keys):
        monkeypatch.setattr(deps.redis_client, "delete", original_delete)
        deleted = original_delete(*keys)
        monkeypatch.setattr(deps.redis_client, "setex", setex_after_a_blip_and_a_disable)
        read()
        return deleted

    monkeypatch.setattr(deps.redis_client, "delete", delete_while_a_message_reads)
    cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")

    assert read() is None, "the older delete counted the Disable's delete as sent"


def test_a_stale_redis_entry_is_never_kept_as_the_last_known_settings(
    deps, monkeypatch
):
    """Redis can hold a document Mongo no longer has; only Mongo is remembered."""
    deps.redis_client.setex(KEY, 300, json_util.dumps(BLOCK_LINKS))
    read()
    deps.redis_client.delete(KEY)
    mongo_is_down(monkeypatch, deps)

    with pytest.raises(ServerSelectionTimeoutError):
        read()


def test_a_read_racing_a_disable_never_keeps_or_caches_what_it_read(
    deps, monkeypatch
):
    collection = deps.mongo_client.guild["block_links"]
    collection.insert_one(dict(BLOCK_LINKS))
    original = collection.find_one

    def read_then_disabled(*args, **kwargs):
        document = original(*args, **kwargs)
        collection.delete_one({"guild_id": GUILD_ID})
        cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")
        return document

    monkeypatch.setattr(collection, "find_one", read_then_disabled)
    read()
    monkeypatch.setattr(collection, "find_one", original)

    assert deps.redis_client.get(KEY) is None, "the deleted document went back to Redis"
    mongo_is_down(monkeypatch, deps)
    with pytest.raises(ServerSelectionTimeoutError):
        read()


def test_an_invalidation_forgets_the_last_known_settings(deps, monkeypatch):
    """A Mongo outage must never bring back settings an admin just changed."""
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    read()
    cache.remove_cog_cache_by_guild(GUILD_ID, "block_links")
    mongo_is_down(monkeypatch, deps)

    with pytest.raises(ServerSelectionTimeoutError):
        read()


def test_leaving_a_guild_forgets_its_last_known_settings(deps, monkeypatch):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    read()
    cache.remove_all_cache_by_guild(GUILD_ID)
    mongo_is_down(monkeypatch, deps)

    with pytest.raises(ServerSelectionTimeoutError):
        read()


def test_leaving_a_guild_while_redis_is_down_still_forgets_its_settings(
    deps, monkeypatch
):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    read()
    calls = redis_is_down(monkeypatch, deps)

    cache.remove_all_cache_by_guild(GUILD_ID)

    assert len(calls["delete"]) == 1, "one Redis call, the delete of the known keys"
    assert calls["keys"] == [], "leaving a guild never walks the keyspace"
    mongo_is_down(monkeypatch, deps)
    with pytest.raises(ServerSelectionTimeoutError):
        read()


async def test_a_failed_invalidation_never_fails_a_save(deps, monkeypatch):
    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS))
    redis_is_down(monkeypatch, deps)
    context = CommitContext(GUILD_ID, "555", "pt-br", BLOCK_LINKS, NOW, "slash", "s1")

    await feature_for("block_links").commit("pause", {}, context)

    stored = deps.mongo_client.guild["block_links"].find_one({"guild_id": GUILD_ID})
    assert stored["enabled"] is False
