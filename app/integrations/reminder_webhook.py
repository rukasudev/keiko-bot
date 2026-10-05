import hmac
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests
from werkzeug.datastructures import Authorization

from app.integrations import http_client

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


def reminder_time(wait: timedelta = timedelta(0)) -> datetime:
    """The moment `wait` from now, in the zone reminders-api reads dates and hours in."""
    return datetime.now(ZoneInfo(REMINDER_TIMEZONE)) + wait


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

    def verify_basic_auth(self, authorization: Optional[Authorization]) -> bool:
        """Whether a callback carries the credentials registered on every reminder."""
        password = self.bot.config.REMINDER_AUTH_PASSWORD
        if not password or authorization is None or authorization.type != "basic":
            return False

        user_matches = hmac.compare_digest(
            str(authorization.username or "").encode("utf-8"),
            REMINDER_AUTH_USER.encode("utf-8"),
        )
        password_matches = hmac.compare_digest(
            str(authorization.password or "").encode("utf-8"),
            str(password).encode("utf-8"),
        )
        return user_matches and password_matches

    def get_reminders(self) -> List[Dict[str, Any]]:
        return _parse(http_client.get(
            "reminders",
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

        return _parse(http_client.post(
            "reminders",
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

        return _parse(http_client.put(
            "reminders",
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
            data=body,
        ))

    def delete_reminder(self, reminder_id: str) -> None:
        """Delete a reminder; one reminders-api does not have is already deleted."""
        response = http_client.delete(
            "reminders",
            f"{REMINDER_API_URL}/reminders/{reminder_id}",
            headers=self.headers,
        )
        if response.status_code == HTTPStatus.NOT_FOUND:
            return None
        return _parse(response)
