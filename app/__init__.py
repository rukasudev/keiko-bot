import certifi
import redis
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from app import logger
from app.bot import DiscordBot
from app.config import AppConfig
from app.constants import DBConfigs


def connect_redis(url: str) -> redis.Redis:
    """A Redis client that gives up on a connection or a read that hangs."""
    return redis.from_url(
        url=url,
        health_check_interval=30,
        decode_responses=True,
        socket_timeout=DBConfigs.REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=DBConfigs.REDIS_SOCKET_TIMEOUT_SECONDS,
    )


def create_app(config: AppConfig) -> DiscordBot:
    global mongo_client, motor_client, redis_client, bot

    mongo_client = MongoClient(
        config.MONGO_URL,
        tls=config.is_prod(),
        tlsCAFile=certifi.where() if config.is_prod() else None,
    )
    motor_client = AsyncIOMotorClient(
        config.MONGO_URL,
        tls=config.is_prod(),
        tlsCAFile=certifi.where() if config.is_prod() else None,
    )

    config.load_db_configs()
    mongo_status = (
        "OK" if mongo_client.guild.command("ping").get("ok") == 1.0 else "Error"
    )
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
