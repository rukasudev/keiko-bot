import time
from typing import Any, Dict

import certifi
import redis
import redis.client
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, monitoring
from pymongo.errors import ServerSelectionTimeoutError

from app import logger
from app.bot import DiscordBot
from app.config import AppConfig
from app.constants import DBConfigs
from app.services import metrics

bot: DiscordBot


class MongoLatency(monitoring.CommandListener):
    """Times every command the Mongo clients send, as the `mongo` dependency."""

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


def reach_mongo(client: Any) -> Dict[str, Any]:
    """Ping Mongo at boot, asking again while no server answers, for a bounded time."""
    deadline = time.monotonic() + DBConfigs.MONGO_BOOT_WAIT_SECONDS

    while True:
        try:
            return client.guild.command("ping")
        except ServerSelectionTimeoutError as error:
            if time.monotonic() >= deadline:
                raise
            logger.warn(f"MongoDB is not answering yet, asking again: {type(error).__name__}")


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
    global mongo_client, motor_client, redis_client, bot

    mongo_client = connect_mongo(MongoClient, config)
    motor_client = connect_mongo(AsyncIOMotorClient, config)

    mongo_status = "OK" if reach_mongo(mongo_client).get("ok") == 1.0 else "Error"
    config.load_db_configs()
    logger.info(f"MongoDB: {mongo_status}")

    redis_client = connect_redis(config.REDIS_URL)
    redis_status = "OK" if redis_client.ping() else "Error"
    logger.info(f"Redis: {redis_status}")

    try:
        from app.data.indexes import ensure_indexes

        ensure_indexes()
    except Exception as error:
        # Indexes are an optimization and a retention policy, never a reason
        # to keep the bot from starting.
        logger.warn(f"Could not ensure Mongo indexes: {type(error).__name__}: {error}")

    from app.services import analytics, debug_logs

    analytics.configure(config)
    debug_logs.start_writer()

    bot = DiscordBot(config)

    return bot
