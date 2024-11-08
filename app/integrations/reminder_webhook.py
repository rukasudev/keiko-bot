import requests

from app import logger
from app.constants import LogTypes as logconstants

REMINDER_API_URL = "https://reminders-api.com/api"
REMINDER_TIMEZONE = "America/Sao_Paulo"
REMINDER_AUTH_USER = "keiko"

class ReminderWebhook:
    def __init__(self, bot):
        from app import DiscordBot

        self.bot: DiscordBot = bot
        self.webhook_url = f"{self.bot.config.WEBHOOK_URL}/reminder"
        self.reminder_application_id = self.bot.config.REMINDER_APPLICATION_ID
        self.headers = {
            "Authorization": f"Bearer {self.bot.config.REMINDER_API_KEY}",
        }

    def create_reminder(self, reminder_data: dict) -> None:
        return requests.post(
            f"{REMINDER_API_URL}/applications/{self.reminder_application_id}/reminders/",
            headers=self.headers,
            data={
                "title": reminder_data.get("title"),
                "timezone": REMINDER_TIMEZONE,
                "date_tz": reminder_data.get("date_tz"),
                "notes": reminder_data.get("notes"),
                "webhook_url": self.webhook_url,
                "http_basic_auth_username": REMINDER_AUTH_USER,
                "http_basic_auth_password": self.bot.config.REMINDER_AUTH_PASSWORD,
            },
        )

    def update_reminder(self, reminder_id: str, reminder_data: dict) -> None:
        return requests.put(
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
            data={
                "date_tz": reminder_data.get("renew_date"),
                "timezone": REMINDER_TIMEZONE,
                "webhook_url": self.webhook_url,
                "http_basic_auth_username": REMINDER_AUTH_USER,
                "http_basic_auth_password": self.bot.config.REMINDER_AUTH_PASSWORD,
            },
        )

    def delete_reminder(self, reminder_id: str) -> None:
        return requests.delete(
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
        )
