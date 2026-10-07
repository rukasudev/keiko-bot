"""A secret Keiko holds never travels in the URL of a request it sends.

Reported in the v1 review: the Twitch client asked for its token with the client
secret in the query string (`/oauth2/token?client_id=...&client_secret=...`), and
the YouTube Data API key travelled as `?key=...` on every lookup. A URL is what
`requests` names in every exception it raises, what `urllib3` writes at DEBUG and
what a proxy keeps in its access log, so either secret could reach the logs, the
error channel or someone else's disk.

Shared behaviour: every integration under `app/integrations/`, which now send
through `app/integrations/http_client.py`. Exposed by: reading the clients.

Guaranteed: the Twitch secret is sent in the body of the token request, the YouTube
key in the `X-Goog-Api-Key` header, and no secret the configuration holds (the
Twitch secret, the YouTube key and hub secret, the reminders-api key and password,
the Notion token) appears in any URL an integration requests.
"""
from types import SimpleNamespace

import pytest
import requests

from app.integrations.notion import NotionIntegration
from app.integrations.reminder_webhook import ReminderWebhook
from app.integrations.twitch import TwitchClient
from app.integrations.youtube import YoutubeClient

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

TWITCH_SECRET = "twitch-client-secret-0042"
YOUTUBE_KEY = "AIza-youtube-key-0042"
HUB_SECRET = "youtube-hub-secret-0042"
REMINDERS_KEY = "reminders-api-key-0042"
REMINDERS_PASSWORD = "reminders-password-0042"
NOTION_TOKEN = "notion-token-0042"
SECRETS = (
    TWITCH_SECRET, YOUTUBE_KEY, HUB_SECRET, REMINDERS_KEY, REMINDERS_PASSWORD, NOTION_TOKEN,
)


class Answer:
    """What a service answers: a status and a JSON body."""

    def __init__(self, body, status=200):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = str(body)
        self._body = body

    def json(self):
        return self._body


class Web:
    """Every request an integration sends, answered from the URL it asks for."""

    def __init__(self):
        self.sent = []

    def answer(self, method, url, **options):
        self.sent.append(SimpleNamespace(method=method, url=url, options=options))
        if "oauth2/token" in url:
            return Answer({"access_token": "token", "expires_in": 3600})
        if "googleapis" in url:
            return Answer({"items": [{"id": "UC-channel", "snippet": {}}]})
        if "notion" in url:
            return Answer({"url": "https://notion.so/ticket"})
        if "reminders-api" in url:
            return Answer({"id": 7001})
        return Answer({"data": []}, status=202)

    def requested_urls(self):
        """Each URL as `requests` builds it, query parameters included."""
        built = []
        for request in self.sent:
            prepared = requests.Request(
                request.method, request.url, params=request.options.get("params")
            ).prepare()
            built.append(prepared.url)
        return built


@pytest.fixture
def web(monkeypatch):
    sent = Web()
    for method in ("get", "post", "put", "delete"):
        monkeypatch.setattr(
            requests,
            method,
            lambda url, _method=method.upper(), **options: sent.answer(_method, url, **options),
        )
    return sent


@pytest.fixture
def config():
    return SimpleNamespace(
        WEBHOOK_URL="https://keiko.test/v1/webhooks",
        TWITCH_CLIENT_ID="twitch-client-id",
        TWITCH_SECRET=TWITCH_SECRET,
        TWITCH_HMAC_SECRET="twitch-hmac",
        YOUTUBE_API_KEY=YOUTUBE_KEY,
        YOUTUBE_HUB_SECRET=HUB_SECRET,
        REMINDER_APPLICATION_ID="1",
        REMINDER_API_KEY=REMINDERS_KEY,
        REMINDER_AUTH_PASSWORD=REMINDERS_PASSWORD,
        NOTION_TOKEN=NOTION_TOKEN,
        NOTION_DATABASE_ID="notion-database",
        NOTION_ENABLED=True,
    )


def test_the_twitch_secret_travels_in_the_body_of_the_token_request(web, config):
    TwitchClient(SimpleNamespace(config=config)).authenticate()

    token_request = web.sent[0]
    assert TWITCH_SECRET not in web.requested_urls()[0]
    assert token_request.method == "POST"
    assert token_request.options["data"]["client_secret"] == TWITCH_SECRET


def test_the_youtube_key_travels_in_a_header(web, config):
    YoutubeClient(SimpleNamespace(config=config)).get_channel_info("UC-channel")

    lookup = web.sent[0]
    assert YOUTUBE_KEY not in web.requested_urls()[0]
    assert lookup.options["headers"]["X-Goog-Api-Key"] == YOUTUBE_KEY


def test_no_secret_reaches_a_url_any_integration_requests(web, config):
    bot = SimpleNamespace(config=config)
    twitch = TwitchClient(bot)
    youtube = YoutubeClient(bot)
    reminders = ReminderWebhook(bot)

    twitch.get_user_info("gaules")
    twitch.subscribe_to_stream_online_event("123")
    twitch.unsubscribe_from_stream_event("sub-1")
    youtube.get_channel_id_from_username("pewdiepie")
    youtube.get_video_info("video-1")
    youtube.subscribe_to_new_video_event("UC-channel")
    reminders.create_reminder({"title": "birthday_reminder", "date_tz": "2026-05-12"})
    reminders.update_reminder("7001", "2026-05-12", time_tz="08:00")
    reminders.delete_reminder("7001")
    NotionIntegration(config).create_report("title", "description", "command", "1", None)

    urls = web.requested_urls()
    assert len(urls) == 11, "the token request comes first, then every call above"
    leaked = [(secret, url) for url in urls for secret in SECRETS if secret in url]
    assert leaked == []
