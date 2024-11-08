import requests

from app import logger
from app.constants import LogTypes as logconstants


class ReminderWebhook:
    def __init__(self, bot):
        from app import DiscordBot

        self.bot: DiscordBot = bot
        self.webhook_url = f"{self.bot.config.WEBHOOK_URL}/reminder"
        self.reminder_application_id = self.bot.config.REMINDER_APPLICATION_ID
        self.remider_api_key = self.bot.config.REMINDER_API_KEY

    def create_reminder(self, reminder_data: dict) -> None:
        pass

    def update_reminder(self, reminder_id: str, reminder_data: dict) -> None:
        pass

    def delete_reminder(self, reminder_id: str) -> None:
        pass
