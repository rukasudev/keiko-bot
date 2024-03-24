import certifi
import redis
from pymongo import MongoClient

from app import logger
from app.bot import DiscordBot
from app.config import AppConfig


def create_app(config: AppConfig) -> DiscordBot:
    global mongo_client, redis_client, bot

    mongo_client = MongoClient(
        config.MONGO_URL,
        tls=config.is_prod(),
        tlsCAFile=certifi.where() if config.is_prod() else None,
    )

    config.load_db_configs()
    mongo_status = (
        "OK" if mongo_client.guild.command("ping").get("ok") == 1.0 else "Error"
    )
    logger.info(f"MongoDB: {mongo_status}")

    redis_client = redis.from_url(
        url=config.REDIS_URL, health_check_interval=30, decode_responses=True
    )
    redis_status = "OK" if redis_client.ping() else "Error"
    logger.info(f"Redis: {redis_status}")

    bot = DiscordBot(config)

    return bot
