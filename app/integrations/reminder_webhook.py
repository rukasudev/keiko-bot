from typing import Any, Dict, List

import requests

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

    def get_reminders(self) -> List[Dict[str, Any]]:
        return requests.get(
            f"{REMINDER_API_URL}/reminders/",
            headers=self.headers,
        ).json()

    def create_reminder(self, reminder_data: dict) -> None:
        return requests.post(
            f"{REMINDER_API_URL}/applications/{self.reminder_application_id}/reminders/",
            headers=self.headers,
            data={
                "title": reminder_data.get("title"),
                "timezone": REMINDER_TIMEZONE,
                "date_tz": str(reminder_data.get("date_tz")),
                "notes": reminder_data.get("notes"),
                "webhook_url": self.webhook_url,
                "http_basic_auth_username": REMINDER_AUTH_USER,
                "http_basic_auth_password": self.bot.config.REMINDER_AUTH_PASSWORD,
            },
        ).json()

    def update_reminder(self, reminder_id: str, date_tz: str) -> None:
        return requests.put(
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
            data={
                "date_tz": str(date_tz),
                "timezone": REMINDER_TIMEZONE,
                "webhook_url": self.webhook_url,
                "http_basic_auth_username": REMINDER_AUTH_USER,
                "http_basic_auth_password": self.bot.config.REMINDER_AUTH_PASSWORD,
            },
        ).json()

    def delete_reminder(self, reminder_id: str) -> None:
        return requests.delete(
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
        ).json()
