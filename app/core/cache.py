import asyncio
import copy
import threading
from collections import OrderedDict
from itertools import count
from time import monotonic
from typing import Any, Callable, Dict, List, Optional, Tuple

from bson import json_util
from pymongo.errors import PyMongoError
from redis.exceptions import RedisError

from app import logger, redis_client
from app.constants import Commands, DBConfigs
from app.constants import LogTypes as logconstants
from app.data.guild import cogs as guild_cogs
from app.core.cogs import is_feature_on
from app.core.trace import outside_any_trace

_lock = threading.Lock()
_last_known: "OrderedDict[str, str]" = OrderedDict()
_generations: "OrderedDict[str, int]" = OrderedDict()
_sequence = count(1)
_unsent_keys: Dict[str, int] = {}
_failed_at: Dict[str, float] = {}
_errors: Dict[str, Exception] = {}
_warned_at: Dict[str, float] = {}
_remembered: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()


def set_data_in_redis_with_expiration(key: str, data: Dict[str, Any], expiration: int):
    redis_client.setex(key, expiration, json_util.dumps(data))


def get_data_from_redis(key: str) -> Dict[str, Any]:
    data = redis_client.get(key)
    if data:
        return json_util.loads(data)
    return {}


def increment_redis_key_with_expiration(key: str, increment_by: int, expiration: int):
    """Add to a counter whose window ends `expiration` seconds after its first count."""
    pipeline = redis_client.pipeline()
    pipeline.set(key, 0, nx=True, ex=expiration)
    pipeline.incrby(key, increment_by)
    _opened, value = pipeline.execute()
    return value


def count_keys() -> int:
    """How many keys Redis holds, in one command that walks nothing."""
    return redis_client.dbsize()


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
    return document if manager or is_feature_on(document) else {}


def remove_cog_cache_by_guild(guild_id: str, key: str) -> None:
    """Forget a feature's cached settings for a guild, in this process and in Redis."""
    redis_key = _cog_key(guild_id, key)
    with _lock:
        _forget(redis_key)

    _delete_cached([redis_key])


def remove_all_cache_by_guild(guild_id: str) -> None:
    """Forget every feature's cached settings for a guild, in this process and in Redis."""
    prefix = _cog_key(guild_id, "")
    with _lock:
        features = [_cog_key(guild_id, spec["command_key"]) for spec in Commands.SETUP_FEATURES]
        remembered = [known for known in _last_known if known.startswith(prefix)]
        keys = sorted({*features, *remembered})
        for redis_key in keys:
            _forget(redis_key)

    _delete_cached(keys)


def recalled(key: str, read: Callable[[], Any], seconds: float) -> Any:
    """What `read` returns, kept in this process for `seconds`; a read that raced a
    `forget_remembered(key)` is returned but not kept, and during a Mongo outage what
    was kept last is served however old."""
    held, fresh, generation = _held(key)
    if fresh or (held is not None and not _may_try("mongo")):
        return held[1]

    try:
        value = read()
    except PyMongoError as error:
        return _last_kept(held, error)

    _recover("mongo")
    _keep(key, generation, value, seconds)
    return value


async def remembered(key: str, read: Callable[[], Any], seconds: float) -> Any:
    """What `recalled` keeps, with `read` run off the loop when nothing fresh is kept."""
    held, fresh, generation = _held(key)
    if fresh or (held is not None and not _may_try("mongo")):
        return held[1]

    try:
        value = await asyncio.to_thread(read)
    except PyMongoError as error:
        return _last_kept(held, error)

    _recover("mongo")
    _keep(key, generation, value, seconds)
    return value


def forget_remembered(key: str) -> None:
    """Drop what `remembered` keeps under `key`, so the next call reads it again."""
    with _lock:
        _remembered.pop(key, None)
        _forget(key)


def claim_redis_key(key: str, expiration: int) -> bool:
    """Set `key` with its expiry in one command, only when it is absent; True when this call set it."""
    return bool(redis_client.set(key, 1, nx=True, ex=expiration))


def release_redis_key(key: str) -> None:
    """Give back a claim, so the next attempt can take it."""
    redis_client.delete(key)


def held_entries() -> int:
    """How many entries this process keeps beside Redis: reads, memos, deletes."""
    with _lock:
        return len(_last_known) + len(_generations) + len(_remembered) + len(_unsent_keys)


def reset() -> None:
    """Forget the settings read so far and every outage, as a fresh process would."""
    with _lock:
        _last_known.clear()
        _generations.clear()
        _unsent_keys.clear()
        _failed_at.clear()
        _errors.clear()
        _warned_at.clear()
        _remembered.clear()


def _cog_key(guild_id: Any, key: str) -> str:
    return DBConfigs.CACHE_COG_KEY.format(guild_id=guild_id, cog=key)


def _held(key: str) -> Tuple[Optional[Tuple[float, Any]], bool, int]:
    with _lock:
        held = _remembered.get(key)
        generation = _generations.get(key, 0)
    return held, held is not None and monotonic() < held[0], generation


def _last_kept(held: Optional[Tuple[float, Any]], error: PyMongoError) -> Any:
    _fail("mongo", error, warn=held is not None)
    if held is None:
        raise error
    return held[1]


def _keep(key: str, generation: int, value: Any, seconds: float) -> None:
    with _lock:
        if _generations.get(key, 0) != generation:
            return

        _remembered[key] = (monotonic() + seconds, value)
        _remembered.move_to_end(key)
        while len(_remembered) > DBConfigs.SETTINGS_MEMO_SIZE:
            _remembered.popitem(last=False)


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
        document = guild_cogs.find_cog_by_guild_id(guild_id, key)
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
    with outside_any_trace():
        logger.warn(message, log_type=logconstants.COMMAND_WARN_TYPE)
