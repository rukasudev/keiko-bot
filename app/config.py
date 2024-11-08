import os
from os.path import dirname, join

import boto3
from dotenv import load_dotenv

from app.constants import DBConfigs as constants


class AppConfig:
    """
    Represents an in-memory copy of the configuration .json file for the bot
    """

    def __init__(self):
        dotenv_path = join(dirname(__file__), "..", ".env")
        load_dotenv(dotenv_path, override=True)

        self.ENVIRONMENT = os.getenv("APPLICATION_ENVIRONMENT")
        self.DEBUG = os.getenv("DEBUG")
        self.get_ssm_configs() if self.is_prod() else self.get_local_configs()

    def get_local_configs(self):
        self.BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
        self.MONGO_URL = os.getenv("MONGO_URL")
        self.TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
        self.TWITCH_SECRET = os.getenv("TWITCH_SECRET")
        self.TWITCH_HMAC_SECRET = os.getenv("TWITCH_HMAC_SECRET")
        self.REDIS_URL = os.getenv("REDIS_URL")
        self.APPLICATION_ID = os.getenv("APPLICATION_ID")
        self.NOTION_TOKEN = os.getenv("NOTION_TOKEN")
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        self.DETECT_LANGUAGE_API_KEY = os.getenv("DETECT_LANGUAGE_API_KEY")
        self.RUN_LOCAL_WEBHOOK_API = os.getenv("RUN_LOCAL_WEBHOOK_API")
        self.WEBHOOK_URL = os.getenv("WEBHOOK_URL")
        self.YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
        self.REMINDER_APPLICATION_ID = os.getenv("REMINDER_APPLICATION_ID")
        self.REMINDER_AUTH_PASSWORD = os.getenv("REMINDER_AUTH_PASSWORD")
        self.REMINDER_API_KEY = os.getenv("REMINDER_API_KEY")


    def get_ssm_configs(self):
        ssm = boto3.client("ssm", region_name="sa-east-1")

        self.APPLICATION_ID = ssm.get_parameter(Name="/keiko/discord/application_id")["Parameter"][
            "Value"
        ]
        self.RUN_LOCAL_WEBHOOK_API = ssm.get_parameter(Name="/keiko/discord/run_local_webhook_api")["Parameter"][
            "Value"
        ]
        self.WEBHOOK_URL = ssm.get_parameter(Name="/keiko/webhook/url")["Parameter"]["Value"]
        self.BOT_TOKEN = ssm.get_parameter(Name="/keiko/discord/bot_token", WithDecryption=True)["Parameter"]["Value"]
        self.MONGO_URL = ssm.get_parameter(Name="/keiko/mongo/url", WithDecryption=True)["Parameter"]["Value"]
        self.TWITCH_CLIENT_ID = ssm.get_parameter(Name="/keiko/twitch/client_id", WithDecryption=True)["Parameter"][
            "Value"
        ]
        self.TWITCH_SECRET = ssm.get_parameter(Name="/keiko/twitch/secret", WithDecryption=True)["Parameter"]["Value"]
        self.TWITCH_HMAC_SECRET = ssm.get_parameter(Name="/keiko/twitch/hmac_secret", WithDecryption=True)["Parameter"][
            "Value"
        ]
        self.YOUTUBE_API_KEY = ssm.get_parameter(Name="/keiko/youtube/api_key", WithDecryption=True)["Parameter"]["Value"]
        self.REDIS_URL = ssm.get_parameter(Name="/keiko/redis/url", WithDecryption=True)["Parameter"]["Value"]
        self.NOTION_TOKEN = ssm.get_parameter(Name="/keiko/notion/token", WithDecryption=True)["Parameter"]["Value"]
        self.OPENAI_API_KEY = ssm.get_parameter(Name="/keiko/openai/api_key", WithDecryption=True)["Parameter"]["Value"]
        self.DETECT_LANGUAGE_API_KEY = ssm.get_parameter(
            Name="/keiko/detect_language/api_key", WithDecryption=True)["Parameter"]["Value"]
        self.REMINDER_APPLICATION_ID = ssm.get_parameter(Name="/keiko/reminder/application_id", WithDecryption=True)[
            "Parameter"]["Value"]
        self.REMINDER_AUTH_PASSWORD = ssm.get_parameter(Name="/keiko/reminder/auth_password", WithDecryption=True)[
            "Parameter"]["Value"]
        self.REMINDER_API_KEY = ssm.get_parameter(Name="/keiko/reminder/api_key", WithDecryption=True)["Parameter"][
            "Value"]

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
        self.OWNER_ID = db_configs[constants.KEIKO_OWNER_ID]
        self.PREFIX = db_configs[constants.KEIKO_PREFIX]

        notion_configs = find_db_integration_configs(constants.INTEGRATION_NOTION)
        self.NOTION_ENABLED = notion_configs.get(constants.INTEGRATION_NOTION_ENABLED)
        self.NOTION_DATABASE_ID = notion_configs.get(constants.INTEGRATION_NOTION_DATABASE_ID)

        openai_configs = find_db_integration_configs(constants.INTEGRATION_OPENAI)
        self.OPENAI_ENABLED = openai_configs.get(constants.INTEGRATION_OPENAI_ENABLED)

        self.ADMIN_GUILD_ID = int(admin_configs[constants.ADMIN_GUILD_ID])
        self.ADMIN_REPORTS_CHANNEL_ID = int(admin_configs[constants.ADMIN_REPORTS_CHANNEL_ID])
        self.ADMIN_LOGS_CHANNEL_ID = int(admin_configs[constants.ADMIN_LOGS_CHANNEL_ID])
        self.ADMIN_LOGS_COMMAND_CALL_ID = int(
            admin_configs[constants.ADMIN_LOGS_COMMAND_CALL_ID]
        )
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
        return False if self.DEBUG is None else self.DEBUG.lower() == "true"

    def run_local_webhook_api(self) -> bool:
        return self.RUN_LOCAL_WEBHOOK_API.lower() == "true"
