import certifi
import redis
from pymongo import MongoClient

from app.bot import DiscordBot
from app.config import AppConfig


def create_app(config: AppConfig) -> DiscordBot:
    global mongo_client, redis_client, bot

    mongo_client = MongoClient(
        config.MONGO_URL,
        tls=config.is_prod(),
        ssl=config.is_prod(),
        tlsCAFile=certifi.where() if config.is_prod() else None,
    )
    print(f"Mongo: {mongo_client.guild.command('ping')}")

    redis_client = redis.from_url(
        url=config.REDIS_URL, health_check_interval=30, decode_responses=True
    )
    print(f"Redis: {redis_client.ping()}")

    bot = DiscordBot(config)

    return bot
