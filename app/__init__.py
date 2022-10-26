import certifi
import redis

from app.bot import DiscordBot
from pymongo import MongoClient


def create_app(config) -> DiscordBot:
    global mongo_client, redis_client, bot

    mongo_client = MongoClient(config.MONGO_URL, tlsCAFile=certifi.where())
    print(f"Mongo: {mongo_client.guild.command('ping')}")

    redis_client = redis.from_url(
        url=config.REDIS_URL, health_check_interval=30, decode_responses=True
    )
    print(f"Redis: {redis_client.ping()}")

    bot = DiscordBot(config)

    return bot
