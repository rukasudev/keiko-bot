"""Every call Keiko makes to something it does not run is bounded in time and timed.

17 of the 24 `requests` calls waited for an answer for as long as the server kept the
socket open, and the Mongo clients waited 30 seconds to find a server and forever for
a read. A call that never returns holds its thread, or the whole bot when it runs on
the loop. Guaranteed: every HTTP call goes through `app/integrations/http_client.py`,
waits at most the default connect and read timeouts unless it gives its own, and is
counted in `keiko_dependency_latency_seconds` under one of the dependencies Keiko
names, whether it answered or raised; the Mongo clients give up on a server they
cannot find and on a read that hangs; Mongo, Redis, Discord and the translation's
language detection are timed under their own names. The metric's only label is the
dependency, from a closed list, never an id.
"""
import ast
import os
from types import SimpleNamespace

import detectlanguage
import discord
import pytest
import redis
import redis.client
import requests
from motor.motor_asyncio import AsyncIOMotorClient
from prometheus_client import REGISTRY
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

import app as app_module
from app.bot import DiscordBot, discord_latency
from app.constants import DBConfigs, Dependencies
from app.integrations import http_client
from app.integrations.notion import NotionIntegration
from app.integrations.reminder_webhook import ReminderWebhook
from app.integrations.stream_elements import StreamElementsClient
from app.integrations.twitch import TwitchClient
from app.integrations.youtube import YoutubeClient
from app.services import metrics

pytestmark = [pytest.mark.unit, pytest.mark.shared_contract("outside_calls")]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
OWN_REQUESTS = {
    "app/integrations/http_client.py": "the shared client itself",
    "app/cogs/heartbeat.py": (
        "the ping to the uptime monitor: not a dependency a feature waits on, its URL "
        "carries the monitor's token, and it keeps HEARTBEAT_TIMEOUT_SECONDS"
    ),
    "app/services/images.py": (
        "a picture streamed with its own bounds on time, size and pixels "
        "(DBConfigs.IMAGE_*), which the shared client does not do"
    ),
}
DEFAULT_TIMEOUT = (
    Dependencies.HTTP_CONNECT_TIMEOUT_SECONDS,
    Dependencies.HTTP_READ_TIMEOUT_SECONDS,
)
REQUEST_FUNCTIONS = {"get", "post", "put", "delete", "patch", "head", "options", "request"}


def calls(dependency):
    return REGISTRY.get_sample_value(
        "keiko_dependency_latency_seconds_count", {"dependency": dependency}
    ) or 0.0


class Answer:
    def __init__(self, body, status=200):
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = str(body)
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def web(monkeypatch):
    """`requests` answering from memory, every request recorded with its options."""
    sent = []

    def answer(method):
        def send(url, **options):
            sent.append(SimpleNamespace(method=method, url=url, options=options))
            if "oauth2/token" in url:
                return Answer({"access_token": "token", "expires_in": 3600})
            return Answer({"items": [], "data": [], "id": 1}, status=200)
        return send

    for method in ("get", "post", "put", "delete"):
        monkeypatch.setattr(requests, method, answer(method))
    return sent


@pytest.fixture
def config():
    return SimpleNamespace(
        WEBHOOK_URL="https://keiko.test/v1/webhooks",
        TWITCH_CLIENT_ID="client-id",
        TWITCH_SECRET="secret",
        TWITCH_HMAC_SECRET="hmac",
        YOUTUBE_API_KEY="key",
        YOUTUBE_HUB_SECRET="hub-secret",
        REMINDER_APPLICATION_ID="1",
        REMINDER_API_KEY="api-key",
        REMINDER_AUTH_PASSWORD="password",
        NOTION_TOKEN="token",
        NOTION_DATABASE_ID="database",
        NOTION_ENABLED=True,
        MONGO_URL="mongodb://127.0.0.1:1",
        is_prod=lambda: False,
    )


def test_a_call_waits_at_most_the_default_timeouts(web):
    http_client.get("twitch", "https://api.twitch.tv/helix/users")

    assert web[0].options["timeout"] == DEFAULT_TIMEOUT


def test_a_call_that_names_its_own_timeout_keeps_it(web):
    http_client.post("youtube", "https://pubsubhubbub.appspot.com/subscribe", timeout=3.0)

    assert web[0].options["timeout"] == 3.0


def test_every_call_is_timed_under_its_dependency_answered_or_not(web, monkeypatch):
    before = calls("notion")
    http_client.post("notion", "https://api.notion.com/v1/pages")

    def unreachable(url, **options):
        raise requests.ConnectTimeout("api.notion.com did not answer")

    monkeypatch.setattr(requests, "post", unreachable)
    with pytest.raises(requests.ConnectTimeout):
        http_client.post("notion", "https://api.notion.com/v1/pages")

    assert calls("notion") == before + 2


def test_a_dependency_keiko_does_not_name_is_refused_before_anything_is_sent(web):
    with pytest.raises(ValueError):
        http_client.get("google", "https://www.google.com")

    assert web == []


@pytest.mark.parametrize("dependency, call", [
    ("twitch", lambda bot: TwitchClient(bot).get_stream_info("gaules")),
    ("youtube", lambda bot: YoutubeClient(bot).get_video_info("video-1")),
    ("reminders", lambda bot: ReminderWebhook(bot).get_reminders()),
    ("notion", lambda bot: NotionIntegration(bot.config).create_report("t", "d", "c", "1", None)),
    ("stream_elements", lambda bot: StreamElementsClient().send_chat_command("c", "!mouse")),
], ids=["twitch", "youtube", "reminders", "notion", "stream_elements"])
def test_every_integration_sends_through_the_shared_client(web, config, dependency, call):
    before = calls(dependency)

    call(SimpleNamespace(config=config))

    assert web, "the integration reached the web"
    assert all(request.options.get("timeout") for request in web), "every request is bounded"
    assert calls(dependency) == before + len(web)


def test_nothing_calls_requests_but_the_shared_client_and_its_named_exceptions():
    callers = set()
    for folder, _subfolders, filenames in os.walk(APP):
        for filename in filenames:
            if not filename.endswith(".py") or filename.endswith("_test.py"):
                continue
            path = os.path.join(folder, filename)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "requests"
                and node.func.attr in REQUEST_FUNCTIONS
                for node in ast.walk(tree)
            ):
                callers.add(os.path.relpath(path, ROOT))

    assert callers == set(OWN_REQUESTS)


def test_the_language_detection_is_bounded_and_timed(monkeypatch):
    from app.integrations.google_translate import GoogleTranslate

    monkeypatch.setattr(detectlanguage, "simple_detect", lambda content: "pt")
    before = calls("translate")

    assert GoogleTranslate.detect("olá") == "pt"
    assert calls("translate") == before + 1
    assert detectlanguage.configuration.timeout == Dependencies.LANGUAGE_DETECTION_TIMEOUT_SECONDS


def test_a_translation_google_does_not_answer_is_no_translation_and_names_no_text(
    monkeypatch, caplog
):
    """The URL carries the member's message, and `requests` names it in its errors."""
    from app.integrations.google_translate import GoogleTranslate

    def unreachable(url, **options):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(requests, "get", unreachable)

    with caplog.at_level("INFO"):
        translated = GoogleTranslate.google_translate("my secret plans", "en")

    assert translated is None
    assert "secret" not in caplog.text
    assert "ConnectionError" in caplog.text


@pytest.mark.parametrize("client_class", [MongoClient, AsyncIOMotorClient],
                         ids=["pymongo", "motor"])
def test_the_mongo_clients_give_up_on_a_server_and_on_a_read_and_time_commands(
    config, client_class
):
    client = app_module.connect_mongo(client_class, config, connect=False)
    try:
        options = client.options
        listeners = options.event_listeners
    finally:
        client.close()

    assert options.server_selection_timeout == DBConfigs.MONGO_SERVER_SELECTION_TIMEOUT_SECONDS
    assert options.pool_options.connect_timeout == DBConfigs.MONGO_CONNECT_TIMEOUT_SECONDS
    assert options.pool_options.socket_timeout == DBConfigs.MONGO_SOCKET_TIMEOUT_SECONDS
    assert [type(listener) for listener in listeners] == [app_module.MongoLatency]


def test_a_mongo_command_is_timed_whether_it_succeeded_or_failed():
    timer = app_module.MongoLatency()
    before = calls("mongo")

    timer.succeeded(SimpleNamespace(duration_micros=2500))
    timer.failed(SimpleNamespace(duration_micros=20_000_000))

    assert calls("mongo") == before + 2


def test_a_redis_command_is_timed(monkeypatch):
    monkeypatch.setattr(redis.Redis, "execute_command", lambda self, *args, **options: True)
    client = app_module.connect_redis("redis://127.0.0.1:1/0")
    before = calls("redis")

    assert client.ping() is True
    assert calls("redis") == before + 1


def test_a_redis_pipeline_is_timed_once_per_round_trip(monkeypatch):
    monkeypatch.setattr(
        redis.client.Pipeline, "execute", lambda self, raise_on_error=True: [1, True]
    )
    client = app_module.connect_redis("redis://127.0.0.1:1/0")
    before = calls("redis")

    pipeline = client.pipeline()
    pipeline.incrby("guild:1:block_links:total", 1)
    pipeline.expire("guild:1:block_links:total", 60)

    assert pipeline.execute() == [1, True]
    assert calls("redis") == before + 1


@pytest.fixture
def boot(monkeypatch):
    """`create_app` with every client faked, and the process's globals put back after."""
    for name in ("mongo_client", "motor_client", "redis_client", "bot"):
        monkeypatch.setattr(app_module, name, getattr(app_module, name, None), raising=False)
    monkeypatch.setattr(app_module, "AsyncIOMotorClient", lambda *args, **options: object())
    monkeypatch.setattr(app_module, "connect_redis", lambda url: SimpleNamespace(ping=lambda: True))
    monkeypatch.setattr(app_module, "DiscordBot", lambda config: "the bot")
    monkeypatch.setattr("app.data.indexes.ensure_indexes", lambda: None)
    monkeypatch.setattr("app.services.analytics.configure", lambda config: None)
    monkeypatch.setattr("app.services.debug_logs.start_writer", lambda: None)

    def start(answers):
        """Boot against a Mongo that answers each ping with the next of `answers`."""
        pings = []

        def ping(command):
            pings.append(command)
            answer = answers[min(len(pings), len(answers)) - 1]
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(
            app_module, "MongoClient",
            lambda *args, **options: SimpleNamespace(guild=SimpleNamespace(command=ping)),
        )
        config = SimpleNamespace(
            MONGO_URL="mongodb://mongo:27017",
            REDIS_URL="redis://redis:6379/0",
            is_prod=lambda: False,
            load_db_configs=lambda: None,
        )
        return app_module.create_app(config), pings

    return start


def test_the_boot_waits_for_a_mongo_that_answers_a_moment_later(boot):
    """A deploy that starts while Mongo elects a primary used to wait 30 s, not fail at 5."""
    not_yet = ServerSelectionTimeoutError("no server is up yet")

    bot, pings = boot([not_yet, not_yet, {"ok": 1.0}])

    assert bot == "the bot"
    assert len(pings) == 3


def test_the_boot_gives_up_on_a_mongo_that_never_answers(boot, monkeypatch):
    monkeypatch.setattr(DBConfigs, "MONGO_BOOT_WAIT_SECONDS", 0.0)

    with pytest.raises(ServerSelectionTimeoutError):
        boot([ServerSelectionTimeoutError("no server at all")])


async def test_a_discord_request_is_timed_answered_or_not():
    trace = discord_latency()
    trace.freeze()
    before = calls("discord")

    for ending in (trace.on_request_end, trace.on_request_exception):
        request = trace.trace_config_ctx()
        await trace.on_request_start.send(None, request, None)
        await ending.send(None, request, None)

    assert calls("discord") == before + 2


def test_the_bot_hands_discord_py_the_request_timer(config):
    bot = DiscordBot(SimpleNamespace(
        **vars(config),
        PREFIX="ke!",
        APPLICATION_ID=1,
        OWNER_ID=2,
        STATUS=discord.Status.online,
        ACTIVITY=discord.ActivityType.playing,
        DESCRIPTION="Keiko",
    ))

    assert bot.http.http_trace is not None
    assert bot.http.http_trace.on_request_start and bot.http.http_trace.on_request_end


def test_the_latency_label_is_the_dependency_from_a_closed_list():
    assert metrics.DEPENDENCY_LATENCY._labelnames == ("dependency",)
    assert set(Dependencies.NAMES) >= {
        "mongo", "redis", "discord", "twitch", "youtube", "reminders", "notion", "translate",
    }
    with pytest.raises(ValueError):
        metrics.record_dependency_latency("guild:1234", 0.1)
