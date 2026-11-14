import asyncio
import time
from typing import Any, Mapping, Optional, Set

import certifi
import redis
import redis.client
from pymongo import AsyncMongoClient, monitoring
from pymongo.errors import ServerSelectionTimeoutError

from app import logger
from app.bot import DiscordBot
from app.config import AppConfig
from app.constants import DBConfigs
from app.core import metrics
from app.data import Data, build, expect_loop, install
from app.data.collection import Client

bot: DiscordBot
_CONFIG: Optional[AppConfig] = None


class MongoLatency(monitoring.CommandListener):
    """Times every command the Mongo client sends, as the `mongo` dependency."""

    def started(self, event: monitoring.CommandStartedEvent) -> None:
        pass

    def succeeded(self, event: monitoring.CommandSucceededEvent) -> None:
        metrics.record_dependency_latency("mongo", event.duration_micros / 1_000_000)

    def failed(self, event: monitoring.CommandFailedEvent) -> None:
        metrics.record_dependency_latency("mongo", event.duration_micros / 1_000_000)


class TimedPipeline(redis.client.Pipeline):
    """A Redis pipeline, timing each round trip as one call to the `redis` dependency."""

    def execute(self, raise_on_error: bool = True) -> Any:
        with metrics.timed("redis"):
            return super().execute(raise_on_error)


class TimedRedis(redis.Redis):
    """The Redis client, timing every command and every pipeline as the `redis` dependency."""

    def execute_command(self, *args: Any, **options: Any) -> Any:
        with metrics.timed("redis"):
            return super().execute_command(*args, **options)

    def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> TimedPipeline:
        return TimedPipeline(
            self.connection_pool, self.response_callbacks, transaction, shard_hint
        )


def connect_mongo(client_class: Any, config: AppConfig, **options: Any) -> Any:
    """A Mongo client that gives up on a server it cannot find or a read that hangs."""
    return client_class(
        config.MONGO_URL,
        tls=config.is_prod(),
        tlsCAFile=certifi.where() if config.is_prod() else None,
        serverSelectionTimeoutMS=_milliseconds(DBConfigs.MONGO_SERVER_SELECTION_TIMEOUT_SECONDS),
        connectTimeoutMS=_milliseconds(DBConfigs.MONGO_CONNECT_TIMEOUT_SECONDS),
        socketTimeoutMS=_milliseconds(DBConfigs.MONGO_SOCKET_TIMEOUT_SECONDS),
        event_listeners=[MongoLatency()],
        **options,
    )


def _milliseconds(seconds: float) -> int:
    return int(seconds * 1000)


async def reach_mongo(client: Client) -> Mapping[str, Any]:
    """Ping Mongo at boot, asking again while no server answers, for a bounded time."""
    deadline = time.monotonic() + DBConfigs.MONGO_BOOT_WAIT_SECONDS

    while True:
        try:
            return await client["guild"].command("ping")
        except ServerSelectionTimeoutError as error:
            if time.monotonic() >= deadline:
                raise
            logger.warn(f"MongoDB is not answering yet, asking again: {type(error).__name__}")


def require_migrations(recorded: Set[str]) -> None:
    """Stop the boot, naming each file, when the ledger lacks one this code has."""
    from app.migrations.runner import MigrationsPending, require_recorded

    try:
        require_recorded(recorded)
    except MigrationsPending as error:
        logger.error(str(error))
        raise


async def prepare_mongo(config: AppConfig) -> str:
    """On a client of the boot alone: wait for Mongo, refuse a database its migrations
    miss, and read Keiko's settings."""
    boot = build(connect_mongo(AsyncMongoClient, config))
    try:
        answer = await reach_mongo(boot.client)
        require_migrations(await boot.migrations_changelog.applied_ids())
        await config.load_db_configs(boot)
    finally:
        await boot.close()
    return "OK" if answer.get("ok") == 1.0 else "Error"


def connect_data(config: AppConfig) -> Data:
    """Install the repositories over a new client, which the first loop to use it owns."""
    global _CONFIG
    _CONFIG = config
    connected = build(connect_mongo(AsyncMongoClient, config))
    install(connected)
    expect_loop()
    return connected


def reconnect_data() -> None:
    """A new client for a new loop, once the bot's loop is gone; none before a boot."""
    if _CONFIG is not None:
        connect_data(_CONFIG)


def connect_redis(url: str) -> redis.Redis:
    """A Redis client that gives up on a connection or a read that hangs."""
    return TimedRedis.from_url(
        url=url,
        health_check_interval=30,
        decode_responses=True,
        socket_timeout=DBConfigs.REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=DBConfigs.REDIS_SOCKET_TIMEOUT_SECONDS,
    )


def create_app(config: AppConfig) -> DiscordBot:
    global redis_client, bot

    mongo_status = asyncio.run(prepare_mongo(config))
    connect_data(config)
    logger.info(f"MongoDB: {mongo_status}")

    redis_client = connect_redis(config.REDIS_URL)
    redis_status = "OK" if redis_client.ping() else "Error"
    logger.info(f"Redis: {redis_status}")

    from app.core import analytics

    analytics.configure(config)

    bot = DiscordBot(config)

    return bot
