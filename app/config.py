import os
from os.path import join, dirname
from dotenv import load_dotenv


class BotConfig:
    """
    Represents an in-memory copy of the configuration .json file for the bot
    """

    def __init__(self):
        dotenv_path = join(dirname(__file__), "..", ".env")
        load_dotenv(dotenv_path)

        self.BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
        self.PREFIX = os.getenv("DISCORD_BOT_PREFIX")
        self.OWNER_ID = os.getenv("DISCORD_BOT_OWNER_ID")
        self.DESCRIPTION = os.getenv("DISCORD_BOT_DESCRIPTION")
        self.MONGO_URL = os.getenv("MONGO_URL")
        self.MONGO_DB = os.getenv("MONGO_DB")
        self.TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
        self.REDIS_URL = os.getenv("REDIS_URL")
