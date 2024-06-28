import os
from os.path import dirname, join

from dotenv import load_dotenv

from app.constants import DBConfigs as constants


class AppConfig:
    """
    Represents an in-memory copy of the configuration .json file for the bot
    """

    def __init__(self):
        dotenv_path = join(dirname(__file__), "..", ".env")
        load_dotenv(dotenv_path, override=True)

        self.DEBUG = os.getenv("DEBUG_MODE")
        self.ENVIRONMENT = os.getenv("ENVIRONMENT")
        self.BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
        self.PREFIX = os.getenv("DISCORD_BOT_PREFIX")
        self.OWNER_ID = os.getenv("DISCORD_BOT_OWNER_ID")
        self.DESCRIPTION = os.getenv("DISCORD_BOT_DESCRIPTION")
        self.MONGO_URL = os.getenv("MONGO_URL")
        self.MONGO_DB = os.getenv("MONGO_DB")
        self.TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
        self.REDIS_URL = os.getenv("REDIS_URL")
        self.APPLICATION_ID = os.getenv("APPLICATION_ID")
        self.NOTION_TOKEN = os.getenv("NOTION_TOKEN")
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

    def get_admin_db_configs(self):
        return [{key: getattr(self, key) for key in constants.ADMIN_CONFIGS_LIST}]

    def load_db_configs(self) -> None:
        from app.data.admin import find_admin_configs
        from app.data.config import find_db_configs, find_db_integration_configs

        db_configs = find_db_configs()
        admin_configs = find_admin_configs()

        self.STATUS = db_configs[constants.KEIKO_STATUS]
        self.DESCRIPTION = db_configs[constants.KEIKO_DESCRIPTION]
        self.ACTIVITY = db_configs[constants.KEIKO_ACTIVITY]

        notion_configs = find_db_integration_configs(constants.INTEGRATION_NOTION)
        self.NOTION_ENABLED = notion_configs.get(constants.INTEGRATION_NOTION_ENABLED)
        self.NOTION_DATABASE_ID = notion_configs.get(constants.INTEGRATION_NOTION_DATABASE_ID)

        openai_configs = find_db_integration_configs(constants.INTEGRATION_OPENAI)
        self.OPENAI_ENABLED = openai_configs.get(constants.INTEGRATION_OPENAI_ENABLED)

        self.ADMIN_GUILD_ID = int(admin_configs[constants.ADMIN_GUILD_ID])
        self.ADMIN_REPORTS_CHANNEL_ID = int(admin_configs[constants.ADMIN_REPORTS_CHANNEL_ID])
        self.ADMIN_LOGS_CHANNEL_ID = int(admin_configs[constants.ADMIN_LOGS_CHANNEL_ID])
        self.ADMIN_LOGS_ERROR_CHANNEL_ID = int(
            admin_configs[constants.ADMIN_LOGS_ERROR_CHANNEL_ID]
        )
        self.ADMIN_LOGS_FILES_CHANNEL_ID = int(
            admin_configs[constants.ADMIN_LOGS_FILES_CHANNEL_ID]
        )
        self.ADMIN_DUMP_CHANNEL_ID = int(admin_configs[constants.ADMIN_DUMP_CHANNEL_ID])

    def is_dev(self) -> bool:
        return self.ENVIRONMENT.upper() == "DEV"

    def is_prod(self) -> bool:
        return self.ENVIRONMENT.upper() == "PROD"

    def is_debug(self) -> bool:
        return self.DEBUG.lower() == "true"
