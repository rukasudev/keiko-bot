from typing import Any, Dict, List, Optional

import requests

REMINDER_API_URL = "https://reminders-api.com/api"
REMINDER_TIMEZONE = "America/Sao_Paulo"
REMINDER_AUTH_USER = "keiko"
RESPONSE_PREVIEW = 400


class ReminderAPIError(RuntimeError):
    """A refusal from reminders-api, carrying what it actually said.

    The API answers a rejected field with a JSON body and no id. Returning that
    body as if it were a reminder is how a broken payload stayed invisible for
    three weeks, so the status and the body travel with the failure.
    """

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"reminders-api returned {status}: {body}")


def _parse(response: requests.Response) -> Any:
    if not response.ok:
        raise ReminderAPIError(response.status_code, response.text[:RESPONSE_PREVIEW])
    return response.json()

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
        return _parse(requests.get(
            f"{REMINDER_API_URL}/reminders/",
            headers=self.headers,
        ))

    def create_reminder(self, reminder_data: dict) -> None:
        body = {
            "title": reminder_data.get("title"),
            "timezone": reminder_data.get("timezone") or REMINDER_TIMEZONE,
            "date_tz": str(reminder_data.get("date_tz")),
            "notes": reminder_data.get("notes"),
            "webhook_url": self.webhook_url,
            "http_basic_auth_username": REMINDER_AUTH_USER,
            "http_basic_auth_password": self.bot.config.REMINDER_AUTH_PASSWORD,
        }
        # Only when the caller has an hour to give: the API supplies its own
        # default otherwise, which is what every working reminder relied on.
        if reminder_data.get("time_tz"):
            body["time_tz"] = reminder_data["time_tz"]
        if reminder_data.get("rrule"):
            body["rrule"] = reminder_data["rrule"]

        return _parse(requests.post(
            f"{REMINDER_API_URL}/applications/{self.reminder_application_id}/reminders/",
            headers=self.headers,
            data=body,
        ))

    def update_reminder(
        self,
        reminder_id: str,
        date_tz: str,
        rrule: str = None,
        timezone: str = None,
        time_tz: Optional[str] = None,
    ) -> None:
        body = {
            "date_tz": str(date_tz),
            "timezone": timezone or REMINDER_TIMEZONE,
            "webhook_url": self.webhook_url,
            "http_basic_auth_username": REMINDER_AUTH_USER,
            "http_basic_auth_password": self.bot.config.REMINDER_AUTH_PASSWORD,
        }
        if time_tz:
            body["time_tz"] = time_tz
        if rrule:
            body["rrule"] = rrule

        return _parse(requests.put(
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
            data=body,
        ))

    def delete_reminder(self, reminder_id: str) -> None:
        return _parse(requests.delete(
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
        ))
