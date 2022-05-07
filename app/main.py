from config import BotConfig
from mongo import MongoDB

# from cache import Redis
import redis
from bot import DiscordBot
from logger import logger


class Main:
    def app():
        config = BotConfig()
        mongo_client = MongoDB(config.MONGO_URL)
        redis_client = redis.from_url(config.REDIS_URL)

        bot = DiscordBot(config, mongo_client)
        bot.run()


if __name__ == "__main__":
    try:
        Main.app()
    except Exception as error:
        logger.exception(f"Error when initializing bot: {error}")
