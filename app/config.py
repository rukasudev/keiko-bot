import logging
import os
from os.path import dirname, join

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from app.constants import DBConfigs as constants


def app_version() -> str:
    """The release this process runs, as the deploy names it, or `dev`."""
    return os.getenv("APP_VERSION") or "dev"


class AppConfig:
    """
    Represents an in-memory copy of the configuration .json file for the bot
    """

    def __init__(self):
        dotenv_path = join(dirname(__file__), "..", ".env")
        load_dotenv(dotenv_path, override=True)

        self.ENVIRONMENT = os.getenv("APPLICATION_ENVIRONMENT")
        self.APP_VERSION = app_version()
        self.DEBUG = os.getenv("DEBUG")
        self.ANALYTICS_ENABLED = os.getenv("ANALYTICS_ENABLED", "true").lower() != "false"
        self.DEBUG_LOGS_ENABLED = os.getenv("DEBUG_LOGS_ENABLED", "true").lower() != "false"
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
        self.DETECT_LANGUAGE_API_KEY = os.getenv("DETECT_LANGUAGE_API_KEY")
        self.RUN_LOCAL_WEBHOOK_API = os.getenv("RUN_LOCAL_WEBHOOK_API")
        self.WEBHOOK_URL = os.getenv("WEBHOOK_URL")
        self.YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
        self.YOUTUBE_HUB_SECRET = os.getenv("YOUTUBE_HUB_SECRET")
        self.REMINDER_APPLICATION_ID = os.getenv("REMINDER_APPLICATION_ID")
        self.REMINDER_AUTH_PASSWORD = os.getenv("REMINDER_AUTH_PASSWORD")
        self.REMINDER_API_KEY = os.getenv("REMINDER_API_KEY")
        self.HEARTBEAT_URL = os.getenv("HEARTBEAT_URL", "")
        self.BACKUP_AGE_PUBLIC_KEY = os.getenv("BACKUP_AGE_PUBLIC_KEY", "")


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
        try:
            self.YOUTUBE_HUB_SECRET = ssm.get_parameter(Name="/keiko/youtube/hub_secret", WithDecryption=True)[
                "Parameter"]["Value"]
        except ClientError as error:
            logging.getLogger(__name__).warning(
                f"/keiko/youtube/hub_secret could not be read ({error.response['Error']['Code']}): "
                "YouTube notices are refused and nothing is subscribed until it can"
            )
            self.YOUTUBE_HUB_SECRET = None
        self.REDIS_URL = ssm.get_parameter(Name="/keiko/redis/url", WithDecryption=True)["Parameter"]["Value"]
        self.NOTION_TOKEN = ssm.get_parameter(Name="/keiko/notion/token", WithDecryption=True)["Parameter"]["Value"]
        self.DETECT_LANGUAGE_API_KEY = ssm.get_parameter(
            Name="/keiko/detect_language/api_key", WithDecryption=True)["Parameter"]["Value"]
        self.REMINDER_APPLICATION_ID = ssm.get_parameter(Name="/keiko/reminder/application_id", WithDecryption=True)[
            "Parameter"]["Value"]
        self.REMINDER_AUTH_PASSWORD = ssm.get_parameter(Name="/keiko/reminder/auth_password", WithDecryption=True)[
            "Parameter"]["Value"]
        self.REMINDER_API_KEY = ssm.get_parameter(Name="/keiko/reminder/api_key", WithDecryption=True)["Parameter"][
            "Value"]
        self.HEARTBEAT_URL = self.get_optional_parameter(ssm, "/keiko/heartbeat/url")
        self.BACKUP_AGE_PUBLIC_KEY = self.get_optional_parameter(
            ssm, "/keiko/backup/age_public_key"
        )

    def get_optional_parameter(self, ssm, name: str) -> str:
        """A parameter the bot runs without: empty when it is missing or cannot be read."""
        try:
            return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
        except ClientError:
            return ""

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
        self.ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID = int(
            admin_configs.get(constants.ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID, 0)
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
