import contextvars
import copy
import threading
from collections import OrderedDict
from itertools import count
from time import monotonic
from typing import Any, Dict, List, Optional

from bson import json_util
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from app import logger, redis_client
from app.constants import Commands, DBConfigs
from app.constants import LogTypes as logconstants
from app.data import cogs as cogs_data
from app.services.cogs import is_feature_on

_lock = threading.Lock()
_last_known: "OrderedDict[str, str]" = OrderedDict()
_generations: "OrderedDict[str, int]" = OrderedDict()
_sequence = count(1)
_unsent_keys: Dict[str, int] = {}
_failed_at: Dict[str, float] = {}
_errors: Dict[str, Exception] = {}
_warned_at: Dict[str, float] = {}


def set_data_in_redis(key: str, data: Dict[str, Any]):
    redis_client.set(key, json_util.dumps(data))

def set_data_in_redis_with_expiration(key: str, data: Dict[str, Any], expiration: int):
    redis_client.setex(key, expiration, json_util.dumps(data))

def get_data_from_redis(key: str) -> Dict[str, Any]:
    data = redis_client.get(key)
    if data:
        return json_util.loads(data)
    return {}

def increment_redis_key(key: str, increment_by=1):
    return redis_client.incrby(key, increment_by)


def increment_redis_key_with_expiration(key: str, increment_by: int, expiration: int):
    """Counters that must not outlive their window: every analytics key expires."""
    value = redis_client.incrby(key, increment_by)
    redis_client.expire(key, expiration)
    return value


def increment_redis_hash(key: str, increments: Dict[str, int], expiration: int) -> None:
    """Add to several counters of one hash and keep it `expiration` seconds from now,
    in a single round trip."""
    pipeline = redis_client.pipeline(transaction=False)

    for field, amount in increments.items():
        pipeline.hincrby(key, field, amount)

    pipeline.expire(key, expiration)
    pipeline.execute()


def get_redis_hash_counters(key: str) -> Dict[str, int]:
    """Every counter of one hash, by field."""
    return {field: _count(value) for field, value in redis_client.hgetall(key).items()}


def get_redis_counters(keys: List[str]) -> Dict[str, int]:
    """Several counters in one round trip; a key that does not exist counts zero."""
    if not keys:
        return {}
    return {key: _count(value) for key, value in zip(keys, redis_client.mget(keys))}


def delete_redis_keys(keys: List[str]) -> None:
    """Delete keys by name, and again as soon as Redis answers when it does not now."""
    _delete_cached(keys)


def get_cog_data_or_populate(
    guild_id: str, key: str, manager: bool = False
) -> Optional[Dict[str, Any]]:
    """A feature's settings for a guild: None when nothing is saved, `{}` when it is off."""
    raw = _cached(_cog_key(guild_id, key))
    if raw is None:
        raw = _stored(str(guild_id), key)

    document = json_util.loads(raw) if raw else None
    if document is None:
        return None
    return document if manager or is_feature_on(str(guild_id), key, document) else {}


def remove_cog_cache_by_guild(guild_id: str, key: str) -> None:
    """Forget a feature's cached settings for a guild, in this process and in Redis."""
    redis_key = _cog_key(guild_id, key)
    with _lock:
        _forget(redis_key)

    _delete_cached([redis_key])


def remove_all_cache_by_guild(guild_id: str) -> None:
    """Forget every feature's cached settings for a guild, in this process and in Redis."""
    prefix = f"guild:{guild_id}:"
    with _lock:
        features = [_cog_key(guild_id, spec["command_key"]) for spec in Commands.SETUP_FEATURES]
        remembered = [known for known in _last_known if known.startswith(prefix)]
        keys = sorted({*features, *remembered})
        for redis_key in keys:
            _forget(redis_key)

    _delete_cached(keys)


def claim_redis_key(key: str, expiration: int) -> bool:
    """Set `key` with its expiry in one command, only when it is absent; True when this call set it."""
    return bool(redis_client.set(key, 1, nx=True, ex=expiration))


def release_redis_key(key: str) -> None:
    """Give back a claim, so the next attempt can take it."""
    redis_client.delete(key)


def reset() -> None:
    """Forget the settings read so far and every outage, as a fresh process would."""
    with _lock:
        _last_known.clear()
        _generations.clear()
        _unsent_keys.clear()
        _failed_at.clear()
        _errors.clear()
        _warned_at.clear()


def _cog_key(guild_id: Any, key: str) -> str:
    return f"guild:{guild_id}:cog.{key}"


def _count(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cached(redis_key: str) -> Optional[str]:
    if not _send_deletes():
        return None

    try:
        raw = redis_client.get(redis_key)
    except RedisError as error:
        _fail("redis", error)
        return None

    _recover("redis")
    return raw


def _stored(guild_id: str, key: str) -> str:
    redis_key = _cog_key(guild_id, key)
    with _lock:
        generation = _generations.get(redis_key, 0)
        known = _last_known.get(redis_key)

    if not _may_try("mongo"):
        if known is not None:
            return known
        raise copy.copy(_errors["mongo"])

    try:
        document = cogs_data.find_cog_by_guild_id(guild_id, key)
    except PyMongoError as error:
        _fail("mongo", error, warn=known is not None)
        if known is None:
            raise
        return known

    _recover("mongo")
    raw = json_util.dumps(document) if document else ""
    if _remember(redis_key, raw, generation):
        _store(redis_key, raw, generation)
    return raw


def _remember(redis_key: str, raw: str, generation: int) -> bool:
    with _lock:
        if _generations.get(redis_key, 0) != generation:
            return False

        _last_known[redis_key] = raw
        _last_known.move_to_end(redis_key)
        while len(_last_known) > DBConfigs.COG_CACHE_LAST_KNOWN_SIZE:
            _last_known.popitem(last=False)

        return True


def _store(redis_key: str, raw: str, generation: int) -> None:
    if _is_failing("redis") or not _is_current(redis_key, generation):
        return

    seconds = (
        DBConfigs.COG_CACHE_TTL_SECONDS if raw else DBConfigs.COG_CACHE_MISSING_TTL_SECONDS
    )
    try:
        redis_client.setex(redis_key, seconds, raw)
    except RedisError as error:
        _fail("redis", error)
        _delete_cached([redis_key])
        return

    if not _is_current(redis_key, generation):
        _delete_cached([redis_key])


def _is_current(redis_key: str, generation: int) -> bool:
    with _lock:
        return _generations.get(redis_key, 0) == generation


def _delete_cached(keys: List[str]) -> None:
    with _lock:
        _unsent_keys.update((key, next(_sequence)) for key in keys)

    _send_deletes()


def _send_deletes() -> bool:
    if not _may_try("redis"):
        return False

    with _lock:
        keys = dict(_unsent_keys)
    if not keys:
        return True

    try:
        redis_client.delete(*keys)
    except RedisError as error:
        _fail("redis", error)
        return False

    with _lock:
        _drop_sent(_unsent_keys, keys)

    _recover("redis")
    return True


def _drop_sent(unsent: Dict[str, int], sent: Dict[str, int]) -> None:
    for name, version in sent.items():
        if unsent.get(name) == version:
            del unsent[name]


def _forget(redis_key: str) -> None:
    _last_known.pop(redis_key, None)
    _generations[redis_key] = next(_sequence)
    _generations.move_to_end(redis_key)
    while len(_generations) > DBConfigs.COG_CACHE_LAST_KNOWN_SIZE:
        _generations.popitem(last=False)


def _is_failing(store: str) -> bool:
    with _lock:
        failed = _failed_at.get(store)
    return failed is not None and monotonic() - failed < DBConfigs.COG_CACHE_RETRY_SECONDS


def _may_try(store: str) -> bool:
    now = monotonic()
    with _lock:
        failed = _failed_at.get(store)
        if failed is None:
            return True
        if now - failed < DBConfigs.COG_CACHE_RETRY_SECONDS:
            return False

        _failed_at[store] = now
        return True


def _recover(store: str) -> None:
    with _lock:
        _failed_at.pop(store, None)


def _fail(store: str, error: Exception, warn: bool = True) -> None:
    now = monotonic()
    with _lock:
        _failed_at[store] = now
        _errors[store] = copy.copy(error)
        warned = _warned_at.get(store)
        recently = warned is not None and now - warned < DBConfigs.COG_CACHE_WARN_SECONDS
        if recently or not warn:
            return
        _warned_at[store] = now

    fallback = {
        "redis": "Settings cache (Redis) unreachable, reading the database instead",
        "mongo": "Settings database (Mongo) unreachable, serving what this process last read",
    }
    message = f"{fallback[store]}: {type(error).__name__}: {error}"
    # An empty context has no trace, so the warning is a message of its own.
    contextvars.Context().run(logger.warn, message, log_type=logconstants.COMMAND_WARN_TYPE)
