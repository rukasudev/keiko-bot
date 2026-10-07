"""/youtube announces a video only when YouTube's hub signed the notice, once per video.

Reported in the v1 review (3.1 and 3.2): Keiko subscribed to the WebSub hub without
`hub.secret` and never compared the video with the channel, so any POST could announce
any video in the name of a channel a server follows, in every server that follows it. The
route also ran a backtracking regex over the raw body before any check: 1.5 KB took a
quarter of a second, the time grew with about the sixth power of the size, and `re`
holds the GIL, so one request froze the gateway heartbeat and every interaction.

Found during the PR review: a hub secret missing from SSM, or one Keiko may not read,
stopped the whole bot from booting; a development secret was published in the code; a
channel or video YouTube failed to describe for a moment spent the video's claim and lost
it for every server; the announcement was sent with `create_task` from the Flask thread;
the claim and its expiry were two Redis commands; the announcement linked to
`watch?v=None`; anyone could have the hub unsubscribe a followed channel; the calls to
YouTube had no timeout. The second review round found that every subscribe verification
was confirmed, so anyone could subscribe Keiko's callback to a followed channel with their
own secret and silence it, while Keiko's own unsubscribes were refused; that an
announcement could run before the bot saw its guilds; that an unexpected delivery error
was only a warning; and that a channel the hub refused still counted as renewed. The final
review found that a pending mark outlived Keiko's own verification and a refused request,
leaving an hour for anyone who re-sends subscribe continuously; that a real `KeyError` in a
delivery read as a missing server; and that a refused verification said nothing about which.
The closing review found that spending the mark on the first confirmation let a stranger's
own GET spend it, so the hub's real verification was refused and the channel went silent
when its lease ended; that a malformed topic made the verification fail with a 500; that an
error answer from the Data API read as a missing channel; that `requests` names the URL it
failed on, so the API key the Data API takes in its URL reached the error log; and that a
notice the bot could not take was an error without its context. The review of the token
found that removing the exact-topic check left every test green, that a denial the hub sent
on Keiko's callback was logged like a stranger's request, that any id of letters, digits,
`-` and `_`, however long, reached the refusal line, and that the error raised for a Data
API failure still held, as its context, the exception whose URL carries the key.

Shared behaviour: the one route every followed YouTube channel's notices arrive on, the
Flask app every webhook hangs from (its body cap covers reminder and Twitch too), and the
configuration the bot boots from.
Exposed by: anyone who reads the public repository.

Guaranteed: a notice without the hub's HMAC posts nothing and is refused before the body
is read as XML, and without a configured secret every notice is refused and nothing is
subscribed unsigned, while the bot still boots; the feed is parsed with a real XML parser,
in bounded time; a video is announced only when YouTube says it belongs to the channel of
the notice and only once within the replay window, and a notice that fails before the
announcement is handed to the bot is left for the hub to deliver again; the announcement
is sent from the bot's loop once the bot is ready, links to the video, and one server
Keiko cannot reach does not stop the others, an unexpected failure there being an error
with its context; a body over the cap is a 413 in every environment. Keiko gives the hub a
callback per channel carrying a token only its hub secret derives; a verification is
confirmed only for subscribe or unsubscribe of an exact feed topic, on the callback with
that channel's token, and a refused one changes nothing; a notice without a token is a 410,
so the hub ends a subscription made before the token, and one on another channel's
callback is refused like a bad signature. Every followed channel is subscribed again on its
tokened callback when the bot starts, and only the channels the hub accepted count as
renewed. A refusal is logged with its mode and channel, never its challenge, its token or
anything else a stranger wrote, and a channel is named only when it has YouTube's channel
id shape; the hub's own denial is an error naming the channel, never its reason. Neither
the callback token nor the API key reaches a log, and the error a Data API failure raises
carries neither a cause nor a context. An error answer from the Data API raises instead of
reading as "not found", and a renewal it stops is tried again an hour later. The answer of a
request to the hub keeps no exception, since `requests` keeps the request on it and the
request's body holds the hub secret and the token. The start stamps every youtuber the hub
took again, so a renewal knows when the lease it keeps alive ends.
"""
import asyncio
import hashlib
import hmac
import logging
import signal
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl, urlencode, urlsplit

import discord
import pytest
import redis
import requests
from botocore.exceptions import ClientError

from app import config as config_module
from app import logger as logger_module
from app.api import config as api_config
from app.api import create_api
from app.constants import Commands
from app.integrations import youtube as youtube_integration
from app.integrations.youtube import YoutubeClient
from app.services import notifications_youtube_video
from app.services import trace as trace_service
from tests.mocks.discord import create_guild

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

URL = "/v1/webhooks/youtube"
WEBHOOK_URL = "https://keiko.test/v1/webhooks"
SECRET = "hub-secret-for-tests"
API_KEY = "AIza-keiko-test-api-key-0042"
HUB_SECRET_PARAMETER = "/keiko/youtube/hub_secret"
PEWDIEPIE = "UC-lHJZR3Gqxm24_Vd_AJ5Yw"
MRBEAST = "UCX6OQ3DkcsbYNE6H8uQQuVA"
STRANGER = "UC-stranger-channel-0000"
TOPIC = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={}"
VIDEO_LINK = "https://www.youtube.com/watch?v=video-1"
CLAIM = "youtube:video:video-1:notified"
HOSTILE_BODY = (
    "<yt:videoId></yt:videoId><yt:channelId></yt:channelId><published></published>" * 1000
).encode()[: 64 * 1024]
QUOTA_EXCEEDED = (403, {"error": {
    "code": 403,
    "message": "The request cannot be completed because you have exceeded your quota.",
    "errors": [{"reason": "quotaExceeded"}],
}})


class FakeGoogle:
    """YouTube's Data API and its WebSub hub, answered from memory."""

    def __init__(self):
        self.channels = {
            PEWDIEPIE: {"customUrl": "@pewdiepie", "title": "PewDiePie"},
            MRBEAST: {"customUrl": "@mrbeast", "title": "MrBeast"},
        }
        self.videos = {}
        self.hub_posts = []
        self.hub_statuses = {}
        self.timeouts = []
        self.outages = {}
        self.errors = {}

    def get(self, url, params=None, timeout=None, **kwargs):
        self.timeouts.append(timeout)
        query = dict(parse_qsl(urlsplit(url).query))
        query.update(params or {})
        path = urlsplit(url).path
        endpoint = path.rsplit("/", 1)[-1]
        if self.outages.get(endpoint):
            self.outages[endpoint] -= 1
            raise requests.ConnectionError(
                "HTTPSConnectionPool(host='www.googleapis.com', port=443): Max retries exceeded "
                f"with url: {path}?{urlencode(query)} "
                "(Caused by NewConnectionError('Failed to establish a new connection'))"
            )
        if endpoint in self.errors:
            status, answer = self.errors[endpoint]
            return SimpleNamespace(status_code=status, json=lambda: answer)

        if endpoint == "videos":
            owner = self.videos.get(query.get("id"))
            snippet = {"title": "A new video", "channelId": owner, "description": "Hi"}
            items = [{"id": query.get("id"), "snippet": snippet}] if owner else []
        elif "forHandle" in query:
            handle = f"@{query['forHandle']}".lower()
            items = [
                {"id": channel_id}
                for channel_id, snippet in self.channels.items()
                if snippet["customUrl"] == handle
            ]
        else:
            snippet = self.channels.get(query.get("id"))
            items = [{"id": query.get("id"), "snippet": snippet}] if snippet else []
        return SimpleNamespace(status_code=200, json=lambda: {"items": items})

    def post(self, url, data=None, timeout=None, **kwargs):
        self.timeouts.append(timeout)
        self.hub_posts.append(dict(data or {}))
        if self.outages.get("hub"):
            self.outages["hub"] -= 1
            raise requests.Timeout(
                "the hub did not answer in 10 s",
                request=SimpleNamespace(body=urlencode(data or {})),
            )
        topic = (data or {}).get("hub.topic")
        return SimpleNamespace(status_code=self.hub_statuses.get(topic, 202))


class FakeSSM:
    """Parameter Store, refusing the names it is told to with the given error."""

    def __init__(self, refused=(), error_code="ParameterNotFound"):
        self.refused = set(refused)
        self.error_code = error_code

    def get_parameter(self, Name, WithDecryption=False):
        if Name in self.refused:
            error = {"Error": {"Code": self.error_code, "Message": Name}}
            raise ClientError(error, "GetParameter")
        return {"Parameter": {"Value": f"value of {Name}"}}


@pytest.fixture
def traces():
    """Every trace that closed, with `logger.*` folded into it as in production."""
    captured = []
    trace_service.register_sink(captured.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    yield captured
    logger_module.logger.removeHandler(folding)


@pytest.fixture
async def hub(deps, monkeypatch, traces):
    """The real API app, YouTube client and fan-out, with Google answered from memory."""
    google = FakeGoogle()
    monkeypatch.setattr(youtube_integration, "requests", google)
    deps.bot.config.WEBHOOK_URL = WEBHOOK_URL
    deps.bot.config.YOUTUBE_API_KEY = API_KEY
    deps.bot.config.YOUTUBE_HUB_SECRET = SECRET
    deps.bot.config.is_dev = lambda: False
    deps.bot.youtube = YoutubeClient(deps.bot)
    deps.bot.loop = asyncio.get_running_loop()
    follow(deps, deps.guild.id, "pewdiepie")

    return SimpleNamespace(
        client=create_api("dev").test_client(),
        google=google,
        youtube=deps.bot.youtube,
        config=deps.bot.config,
        channel=deps.guild.text_channels[0],
        traces=traces,
    )


@pytest.fixture
def boot(monkeypatch):
    """`AppConfig` exactly as the bot builds it, reading no `.env` from disk."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: None)
    return monkeypatch


def follow(deps, guild_id, *youtubers, enabled=True):
    deps.mongo_client.guild.notifications_youtube_video.insert_one({
        "guild_id": str(guild_id),
        "enabled": enabled,
        "notifications": {"values": [
            {
                "youtuber": {"value": youtuber},
                "channel": {"value": "100"},
                "notification_messages": {"value": "{youtuber} posted: {video_link}"},
            }
            for youtuber in youtubers
        ]},
    })


def atom(video_id="video-1", channel_id=PEWDIEPIE, edited_after=timedelta(minutes=1)):
    published = datetime.now(timezone.utc).replace(microsecond=0)
    updated = published + edited_after
    return f"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
  <link rel="hub" href="https://pubsubhubbub.appspot.com"/>
  <link rel="self" href="{TOPIC.format(channel_id)}"/>
  <title>YouTube video feed</title>
  <updated>{updated.isoformat()}</updated>
  <entry>
    <id>yt:video:{video_id}</id>
    <yt:videoId>{video_id}</yt:videoId>
    <yt:channelId>{channel_id}</yt:channelId>
    <title>A new video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v={video_id}"/>
    <author><name>PewDiePie</name></author>
    <published>{published.isoformat()}</published>
    <updated>{updated.isoformat()}</updated>
  </entry>
</feed>""".encode()


def sign(body, secret=SECRET, method="sha1"):
    return f"{method}={hmac.new(secret.encode(), body, getattr(hashlib, method)).hexdigest()}"


def token_of(channel_id, secret=SECRET):
    """The callback token of a channel: HMAC-SHA256 of the hub secret, 32 hex characters."""
    message = f"youtube-callback:{channel_id}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:32]


def callback(channel_id):
    """The path and query of the callback Keiko gives the hub for a channel."""
    return f"{URL}?token={token_of(channel_id)}"


def notify(hub, body, signature=None, url=None):
    """A delivery to a callback, by default the one Keiko gives the hub for PewDiePie."""
    headers = {"Content-Type": "application/atom+xml"}
    if signature is not None:
        headers["X-Hub-Signature"] = signature
    return hub.client.post(url or callback(PEWDIEPIE), data=body, headers=headers)


def verify(hub, mode, topic, challenge="hub-challenge", url=URL, extra=None):
    """A GET shaped like the hub's verification (WebSub 5.3), from anyone, on any callback."""
    path, _, query = url.partition("?")
    return hub.client.get(path, query_string={
        **dict(parse_qsl(query)),
        "hub.mode": mode,
        "hub.topic": topic,
        "hub.challenge": challenge,
        "hub.lease_seconds": "432000",
        **(extra or {}),
    })


def hub_verifies(hub, post, challenge="hub-challenge"):
    """The hub confirming a request Keiko posted, on the very callback Keiko gave it."""
    given = urlsplit(post["hub.callback"])
    url = f"{given.path}?{given.query}" if given.query else given.path
    return verify(hub, post["hub.mode"], post["hub.topic"], challenge, url=url)


def keiko_asks(hub, mode, channel_id):
    """Keiko's own subscribe or unsubscribe of a channel, as the feature and the start send it."""
    if mode == "subscribe":
        return hub.youtube.subscribe_to_new_video_event(channel_id)
    return hub.youtube.unsubscribe_from_new_video_event(channel_id)


def errors_in(caplog):
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def errors_of(caplog, flow):
    """The error records a flow logged with its context, whatever else was logged."""
    return [
        record for record in errors_in(caplog)
        if getattr(getattr(record, "context", None), "flow", None) == flow
    ]


def logged_text(caplog, traces):
    """Everything written to the log: records, tracebacks and every trace's timeline."""
    timeline = [line["message"] for trace in traces for line in trace.lines]
    return "\n".join([caplog.text, *timeline])


async def settle():
    """Let the announcement the request handed to the bot's loop reach the channels."""
    for _ in range(100):
        await asyncio.sleep(0.01)
        if all(task is asyncio.current_task() or task.done() for task in asyncio.all_tasks()):
            return


@contextmanager
def time_limit(seconds):
    """Interrupt the block when it runs longer: a catastrophic regex would never return."""
    def interrupt(signum, frame):
        raise TimeoutError(f"still running after {seconds} s")

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


async def test_a_signed_notice_announces_the_video(hub):
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    response = notify(hub, body, sign(body))
    await settle()

    assert response.status_code == 204
    assert [message.content for message in hub.channel._sent_messages] == [
        f"pewdiepie posted: {VIDEO_LINK}"
    ]


async def test_the_announcement_links_to_the_video(hub):
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    notify(hub, body, sign(body))
    await settle()

    assert hub.channel._sent_messages[0].embeds[0].url == VIDEO_LINK


async def test_an_unsigned_notice_posts_nothing(hub):
    hub.google.videos["video-1"] = PEWDIEPIE

    response = notify(hub, atom())
    await settle()

    assert response.status_code == 403
    assert hub.channel._sent_messages == []


@pytest.mark.parametrize("signature_of", [
    lambda body: sign(body, secret="someone-else"),
    lambda body: sign(body, method="sha256").replace("sha256=", "md5="),
    lambda body: "sha1=",
    lambda body: "sha1=ünicode",
], ids=["another-secret", "unknown-method", "empty-digest", "non-ascii-digest"])
async def test_a_wrongly_signed_notice_posts_nothing(hub, signature_of):
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    response = notify(hub, body, signature_of(body))
    await settle()

    assert response.status_code == 403
    assert hub.channel._sent_messages == []


@pytest.mark.parametrize("signed", [False, True], ids=["unsigned", "signed"])
async def test_a_hostile_64_kb_body_is_handled_in_bounded_time(hub, signed):
    with time_limit(1.0):
        response = notify(hub, HOSTILE_BODY, sign(HOSTILE_BODY) if signed else None)
    await settle()

    assert response.status_code in (204, 403)
    assert hub.channel._sent_messages == []


@pytest.mark.parametrize("second_notice", ["replayed", "edited"])
async def test_a_video_is_announced_once(hub, second_notice):
    """A replay, or YouTube's own second notice when a title is edited minutes later."""
    hub.google.videos["video-1"] = PEWDIEPIE
    first = atom()
    second = first if second_notice == "replayed" else atom(edited_after=timedelta(minutes=3))

    notify(hub, first, sign(first))
    notify(hub, second, sign(second))
    await settle()

    assert len(hub.channel._sent_messages) == 1


async def test_a_video_from_another_channel_is_ignored(hub):
    hub.google.videos["foreign"] = STRANGER
    body = atom(video_id="foreign", channel_id=PEWDIEPIE)

    response = notify(hub, body, sign(body))
    await settle()

    assert response.status_code == 204
    assert hub.channel._sent_messages == [], (
        "a followed channel's notice must not announce somebody else's video"
    )


async def test_a_video_youtube_cannot_see_yet_is_left_for_the_hub_to_retry(hub):
    """Any answer but 2xx makes the hub deliver again; a 204 here would lose the video."""
    body = atom()

    first = notify(hub, body, sign(body))
    hub.google.videos["video-1"] = PEWDIEPIE
    retry = notify(hub, body, sign(body))
    await settle()

    assert first.status_code == 503
    assert retry.status_code == 204
    assert len(hub.channel._sent_messages) == 1, "the retry is not mistaken for a replay"


@pytest.mark.parametrize("endpoint", ["channels", "videos"])
async def test_a_notice_youtube_fails_to_describe_is_left_for_the_hub_to_retry(hub, endpoint):
    """The claim waits for the channel, the video and the followers, so a retry still counts."""
    hub.google.videos["video-1"] = PEWDIEPIE
    hub.google.outages[endpoint] = 1
    body = atom()

    first = notify(hub, body, sign(body))
    retry = notify(hub, body, sign(body))
    await settle()

    assert first.status_code == 503
    assert retry.status_code == 204
    assert len(hub.channel._sent_messages) == 1


async def test_a_notice_that_cannot_reach_the_bot_is_left_for_the_hub_to_retry(
    hub, deps, caplog,
):
    """A claim taken for an announcement that was never handed over is given back."""
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    deps.bot.loop = None
    with caplog.at_level("INFO"):
        first = notify(hub, body, sign(body))
    deps.bot.loop = asyncio.get_running_loop()
    retry = notify(hub, body, sign(body))
    await settle()

    assert first.status_code == 503
    assert retry.status_code == 204
    assert len(hub.channel._sent_messages) == 1
    handover = errors_of(caplog, "youtube_notification")
    assert [record.context.extra["video_id"] for record in handover] == ["video-1"]
    assert handover[0].exc_info is not None, "the traceback travels with the error"


async def test_the_announcement_is_handed_to_the_bot_loop_from_the_flask_thread(hub):
    """In production Flask runs on its own thread, where asyncio forbids `create_task`."""
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()
    loop = asyncio.get_running_loop()

    loop.set_debug(True)
    try:
        response = await asyncio.to_thread(notify, hub, body, sign(body))
        await settle()
    finally:
        loop.set_debug(False)

    assert response.status_code == 204
    assert len(hub.channel._sent_messages) == 1


async def test_a_server_keiko_cannot_reach_does_not_stop_the_others(hub, deps, caplog):
    deps.mongo_client.guild.notifications_youtube_video.delete_many({})
    follow(deps, 4004, "pewdiepie")
    follow(deps, deps.guild.id, "pewdiepie")
    deps.bot.get_guild = lambda guild_id: deps.guild if int(guild_id) == deps.guild.id else None
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    with caplog.at_level("INFO"):
        response = notify(hub, body, sign(body))
        await settle()

    assert response.status_code == 204
    assert len(hub.channel._sent_messages) == 1, "the server after the missing one still hears"
    assert errors_in(caplog) == [], "a server Keiko left is a warning, not an error"


async def test_a_server_that_refuses_the_announcement_is_counted(
    hub, deps, analytics_events, caplog,
):
    closed = create_guild(id=5005, name="Closed", channels=["news"])
    closed.text_channels[0]._send.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
    )
    servers = {deps.guild.id: deps.guild, closed.id: closed}
    deps.bot.get_guild = lambda guild_id: servers.get(int(guild_id))
    follow(deps, closed.id, "pewdiepie")
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    with caplog.at_level("INFO"):
        notify(hub, body, sign(body))
        await settle()

    assert len(hub.channel._sent_messages) == 1
    refused = [event["guild_id"] for event in analytics_events
               if event["event"] == "value.blocked_by_permission"]
    assert refused == [str(closed.id)]
    assert errors_in(caplog) == [], "a missing permission is a warning, not an error"


async def test_an_unexpected_delivery_failure_is_an_error_with_its_context(hub, caplog):
    hub.channel._send.side_effect = RuntimeError("the embed is too long")
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    with caplog.at_level("INFO"):
        notify(hub, body, sign(body))
        await settle()

    errors = errors_of(caplog, "youtube_notification")
    assert [record.context.guild_id for record in errors] == [str(hub.channel.guild.id)]
    assert errors[0].exc_info is not None, "the traceback travels with the error"


async def test_an_unexpected_error_inside_the_send_is_an_error_not_a_missing_server(hub, caplog):
    """A real `KeyError` used to be caught as `LookupError` and read as a missing server."""
    hub.channel._send.side_effect = KeyError("embed")
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    with caplog.at_level("INFO"):
        notify(hub, body, sign(body))
        await settle()

    assert len(errors_of(caplog, "youtube_notification")) == 1
    assert "not found" not in caplog.text


async def test_an_announcement_waits_until_the_bot_is_ready(hub, deps):
    """A notice that arrives while the bot boots must not miss its guilds."""
    ready = asyncio.Event()
    deps.bot.wait_until_ready = ready.wait
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    response = notify(hub, body, sign(body))
    await asyncio.sleep(0.05)

    assert response.status_code == 204
    assert hub.channel._sent_messages == [], "nothing is sent before the bot sees its guilds"

    ready.set()
    await settle()

    assert len(hub.channel._sent_messages) == 1


async def test_the_claim_and_its_expiry_are_one_command(hub, deps, monkeypatch):
    """A Redis error between INCRBY and EXPIRE left a claim that never expired, and no video."""
    def expire_times_out(*args, **kwargs):
        raise redis.exceptions.TimeoutError("Timeout reading from socket")

    monkeypatch.setattr(deps.redis_client, "expire", expire_times_out)
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    response = notify(hub, body, sign(body))
    await settle()

    assert response.status_code == 204
    assert len(hub.channel._sent_messages) == 1
    assert deps.redis_client.ttl(CLAIM) == Commands.YOUTUBE_NOTIFIED_VIDEO_TTL_SECONDS


async def test_an_oversized_body_is_refused_with_413(hub):
    body = b"<" * (4 * 1024 * 1024)

    response = notify(hub, body, sign(body))

    assert response.status_code == 413


async def test_the_production_api_refuses_an_oversized_body_too(hub, monkeypatch):
    monkeypatch.setattr(api_config, "ssm", FakeSSM())
    body = b"<" * (4 * 1024 * 1024)

    response = create_api("prod").test_client().post(callback(PEWDIEPIE), data=body)

    assert response.status_code == 413


async def test_a_notice_without_a_token_is_gone(hub):
    """A subscription made before the token delivers to the bare callback until the hub
    drops it; a 410 asks the hub to drop it now."""
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    response = notify(hub, body, sign(body), url=URL)
    await settle()

    assert response.status_code == 410
    assert hub.channel._sent_messages == []


@pytest.mark.parametrize("url", [
    callback(MRBEAST),
    f"{URL}?token={'0' * 32}",
], ids=["another-channels-token", "made-up-token"])
async def test_a_notice_on_a_callback_that_is_not_its_channels_is_refused(hub, url):
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    response = notify(hub, body, sign(body), url=url)
    await settle()

    assert response.status_code == 403, "refused like a bad signature"
    assert hub.channel._sent_messages == []


async def test_the_hub_challenge_is_echoed_as_plain_text(hub):
    hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)

    response = hub_verifies(hub, hub.google.hub_posts[-1], "<script>alert(1)</script>")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "<script>alert(1)</script>"
    assert response.mimetype == "text/plain"


@pytest.mark.parametrize("keiko_asked", [False, True], ids=["nothing-asked", "keiko-just-asked"])
@pytest.mark.parametrize("topic", [
    TOPIC.format(PEWDIEPIE),
    TOPIC.format(STRANGER),
    "https://example.com/anything",
    "http://[",
], ids=["followed-channel", "unfollowed-channel", "foreign-topic", "malformed-topic"])
async def test_a_subscription_without_keikos_token_is_refused(hub, topic, keiko_asked):
    """The takeover path: someone asks the hub to subscribe Keiko's bare callback with their
    own secret, and the hub verifies it there, without the token."""
    if keiko_asked:
        hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)

    response = verify(hub, "subscribe", topic, "take-over")

    assert response.status_code == 404
    assert "take-over" not in response.get_data(as_text=True)


@pytest.mark.parametrize("stranger_url", [
    URL,
    f"{URL}?token={'0' * 32}",
    callback(MRBEAST),
], ids=["no-token", "made-up-token", "another-channels-token"])
@pytest.mark.parametrize("mode", ["subscribe", "unsubscribe"])
async def test_a_stranger_cannot_confirm_or_spend_keikos_verification(hub, mode, stranger_url):
    """A stranger's own GET used to spend Keiko's pending mark, so the hub's real
    verification was refused and the channel went silent when its lease ended."""
    keiko_asks(hub, mode, PEWDIEPIE)

    stranger = verify(hub, mode, TOPIC.format(PEWDIEPIE), "take-over", url=stranger_url)
    real = hub_verifies(hub, hub.google.hub_posts[-1])

    assert stranger.status_code == 404
    assert "take-over" not in stranger.get_data(as_text=True)
    assert real.status_code == 200, "Keiko's own verification is still confirmed"


async def test_a_topic_that_is_not_the_channels_feed_is_refused_on_its_callback(hub):
    """The token names a channel; the topic must still be that channel's own feed."""
    response = verify(
        hub, "subscribe", f"https://example.com/feed?channel_id={PEWDIEPIE}",
        url=callback(PEWDIEPIE),
    )

    assert response.status_code == 404


async def test_a_denial_from_the_hub_is_an_error_naming_the_channel_never_the_reason(
    hub, caplog,
):
    """Only the hub knows the token, so a denial on it is the hub's, like a renewal it refuses."""
    with caplog.at_level("INFO"):
        denied = verify(
            hub, "denied", TOPIC.format(PEWDIEPIE), url=callback(PEWDIEPIE),
            extra={"hub.reason": "**pwned-reason** [click](https://evil.test)"},
        )
        forged = verify(
            hub, "denied", TOPIC.format(PEWDIEPIE),
            extra={"hub.reason": "**pwned-reason** without the token"},
        )

    assert denied.status_code == 404 and forged.status_code == 404, "nothing is confirmed"
    denials = errors_of(caplog, "youtube_subscription")
    assert [record.context.extra["youtube_channel_id"] for record in denials] == [PEWDIEPIE], (
        "the hub's own denial is one error; a stranger's, without the token, is none"
    )
    assert "pwned" not in logged_text(caplog, hub.traces)


async def test_a_token_confirms_only_its_own_channel(hub):
    hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)

    response = verify(hub, "subscribe", TOPIC.format(MRBEAST), url=callback(PEWDIEPIE))

    assert response.status_code == 404


@pytest.mark.parametrize("mode", ["denied", "**everyone**", ""], ids=["denied", "made-up", "empty"])
async def test_only_a_subscribe_or_an_unsubscribe_is_confirmed(hub, mode):
    response = verify(hub, mode, TOPIC.format(PEWDIEPIE), url=callback(PEWDIEPIE))

    assert response.status_code == 404


@pytest.mark.parametrize("mode", ["subscribe", "unsubscribe"])
async def test_keikos_own_verification_is_confirmed(hub, mode):
    keiko_asks(hub, mode, PEWDIEPIE)

    response = hub_verifies(hub, hub.google.hub_posts[-1])

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "hub-challenge"


async def test_an_unsubscribe_without_the_token_is_refused(hub):
    """Only an unsubscription on the channel's tokened callback is confirmed."""
    response = verify(hub, "unsubscribe", TOPIC.format(PEWDIEPIE), "unsubscribe-me")

    assert response.status_code == 404
    assert "unsubscribe-me" not in response.get_data(as_text=True)


async def test_a_request_that_timed_out_can_still_be_confirmed(hub):
    """The hub may have queued it, so its verification can still arrive."""
    hub.google.outages["hub"] = 1

    with pytest.raises(requests.Timeout):
        keiko_asks(hub, "subscribe", PEWDIEPIE)

    assert hub_verifies(hub, hub.google.hub_posts[-1]).status_code == 200


async def test_a_refused_verification_names_its_mode_and_channel_never_what_a_stranger_wrote(
    hub, caplog,
):
    """The refusal reaches the log channel, so nothing a stranger chose is written there."""
    with caplog.at_level("INFO"):
        verify(hub, "subscribe", TOPIC.format(PEWDIEPIE), "first-secret-challenge")
        verify(hub, "unsubscribe", "https://example.com/anything", "second-secret-challenge")
        verify(hub, "subscribe", TOPIC.format(PEWDIEPIE), url=f"{URL}?token=made-up-token-text")
        verify(hub, "**pwned-mode** [click](https://evil.test)", TOPIC.format(PEWDIEPIE))
        verify(hub, "subscribe", TOPIC.format("UC **pwned-channel** @here"))
        verify(hub, "subscribe", TOPIC.format("UC_[click](https://evil.test)"))
        verify(hub, "subscribe", TOPIC.format("UC" + "LongStrangerChosenId" * 3))

    refusals = [
        record.getMessage() for record in caplog.records
        if "verification refused" in record.getMessage()
    ]
    assert len(refusals) == 7
    assert f"subscribe of channel {PEWDIEPIE}" in refusals[0]
    assert "unsubscribe of an unknown channel" in refusals[1]
    assert f"subscribe of channel {PEWDIEPIE}" in refusals[2]
    assert f"an unknown mode of channel {PEWDIEPIE}" in refusals[3]
    assert "subscribe of an unknown channel" in refusals[4]
    assert "subscribe of an unknown channel" in refusals[5]
    assert "subscribe of an unknown channel" in refusals[6], "a channel id is UC and 22 more"
    written = logged_text(caplog, hub.traces)
    for stranger_text in ("secret-challenge", "made-up-token-text", "pwned", "@here",
                          "evil.test", "[click]", "LongStrangerChosenId"):
        assert stranger_text not in written


async def test_the_callback_token_never_reaches_the_logs(hub, caplog):
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    with caplog.at_level("DEBUG"):
        hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)
        hub_verifies(hub, hub.google.hub_posts[-1])
        notify(hub, body, sign(body))
        notify(hub, body, sign(body), url=callback(MRBEAST))
        verify(hub, "subscribe", TOPIC.format(MRBEAST), url=callback(PEWDIEPIE))
        hub.youtube.unsubscribe_from_new_video_event(PEWDIEPIE)
        await settle()

    written = logged_text(caplog, hub.traces)
    assert len(hub.channel._sent_messages) == 1
    for secret in (token_of(PEWDIEPIE), token_of(MRBEAST), SECRET):
        assert secret not in written


async def test_subscribing_asks_the_hub_to_sign_every_notice(hub):
    hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)

    assert hub.google.hub_posts[-1]["hub.secret"] == SECRET
    assert hub.google.hub_posts[-1]["hub.topic"] == TOPIC.format(PEWDIEPIE)


async def test_keiko_gives_the_hub_a_callback_only_it_can_answer(hub):
    """Per channel, so a token confirms nothing but its own channel's subscription."""
    hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)
    hub.youtube.subscribe_to_new_video_event(MRBEAST)
    hub.youtube.unsubscribe_from_new_video_event(PEWDIEPIE)

    assert [post["hub.callback"] for post in hub.google.hub_posts] == [
        f"{WEBHOOK_URL}/youtube?token={token_of(PEWDIEPIE)}",
        f"{WEBHOOK_URL}/youtube?token={token_of(MRBEAST)}",
        f"{WEBHOOK_URL}/youtube?token={token_of(PEWDIEPIE)}",
    ]


async def test_every_call_to_youtube_gives_up_after_a_timeout(hub):
    hub.google.videos["video-1"] = PEWDIEPIE

    hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)
    hub.youtube.unsubscribe_from_new_video_event(PEWDIEPIE)
    hub.youtube.get_channel_id_from_username("pewdiepie")
    hub.youtube.get_channel_info(PEWDIEPIE)
    hub.youtube.get_video_info("video-1")

    assert hub.google.timeouts == [Commands.YOUTUBE_TIMEOUT_SECONDS] * 5


@pytest.mark.parametrize("lookup", [
    lambda youtube: youtube.get_channel_id_from_username("pewdiepie"),
    lambda youtube: youtube.get_channel_info(PEWDIEPIE),
    lambda youtube: youtube.get_video_info("video-1"),
], ids=["channel-id", "channel", "video"])
async def test_a_data_api_error_is_raised_never_read_as_not_found(hub, lookup):
    hub.google.videos["video-1"] = PEWDIEPIE
    hub.google.errors.update(channels=QUOTA_EXCEEDED, videos=QUOTA_EXCEEDED)

    with pytest.raises(youtube_integration.YoutubeAPIError) as raised:
        lookup(hub.youtube)

    assert "403" in str(raised.value) and "quotaExceeded" in str(raised.value)
    assert API_KEY not in str(raised.value)


async def test_a_channel_or_video_youtube_does_not_have_is_not_an_error(hub):
    assert hub.youtube.get_channel_id_from_username("nobody") is None
    assert hub.youtube.get_channel_info(STRANGER) is None
    assert hub.youtube.get_video_info("missing") is None


async def test_a_renewal_youtube_answers_with_an_error_is_tried_again_in_an_hour(hub, deps):
    """Read as a missing channel, a quota error moved the renewal four days on without
    subscribing; Keiko now tries again itself an hour later."""
    from app.data.reminder import insert_renewal_reminder
    from app.integrations.reminder_webhook import REMINDER_TIMEZONE
    from app.webhooks.reminder import renew_youtube_subscription

    insert_renewal_reminder(7001, "pewdiepie")
    hub.google.errors["channels"] = QUOTA_EXCEEDED

    renew_youtube_subscription(7001)

    moved = deps.bot.reminder.update_reminder.call_args
    zone = ZoneInfo(REMINDER_TIMEZONE)
    moment = datetime.combine(
        datetime.fromisoformat(str(moved.args[1])).date(),
        datetime.strptime(moved.kwargs["time_tz"], "%H:%M").time(),
        zone,
    )
    assert timedelta(minutes=54) < moment - datetime.now(zone) < timedelta(minutes=63)
    assert hub.google.hub_posts == []


async def test_a_data_api_failure_holds_no_trace_of_the_api_key(hub):
    """`from None` only hid the context from the traceback; it still held the exception
    whose message carries the URL, and so the key."""
    hub.google.outages["channels"] = 1

    with pytest.raises(youtube_integration.YoutubeAPIError) as raised:
        hub.youtube.get_channel_id_from_username("pewdiepie")

    error = raised.value
    assert API_KEY not in repr(error.__cause__) and API_KEY not in repr(error.__context__)
    assert API_KEY not in "".join(traceback.format_exception(error))


@pytest.mark.parametrize("endpoint", ["channels", "videos"])
async def test_the_api_key_never_reaches_the_logs(hub, caplog, endpoint):
    """`requests` names the URL it failed on, and the Data API takes its key in the URL."""
    hub.google.videos["video-1"] = PEWDIEPIE
    hub.google.outages[endpoint] = 1
    body = atom()

    with caplog.at_level("INFO"):
        response = notify(hub, body, sign(body))

    assert response.status_code == 503
    assert errors_of(caplog, "youtube_notification"), "the outage is still an error"
    assert API_KEY not in logged_text(caplog, hub.traces)


async def test_the_start_subscribes_every_followed_channel_again_with_the_secret(
    hub, deps, caplog,
):
    """Subscriptions made before the secret existed are unsigned until renewed."""
    follow(deps, 2002, "pewdiepie", "mrbeast")
    follow(deps, 3003, "stranger", enabled=False)
    follow(deps, 4004, "ghost")
    from app.cogs.notifications import Notifications

    with caplog.at_level("INFO"):
        await Notifications(deps.bot).resubscribe_youtube()

    assert sorted(post["hub.topic"] for post in hub.google.hub_posts) == sorted([
        TOPIC.format(MRBEAST), TOPIC.format(PEWDIEPIE),
    ])
    assert {post["hub.secret"] for post in hub.google.hub_posts} == {SECRET}
    assert {post["hub.callback"] for post in hub.google.hub_posts} == {
        f"{WEBHOOK_URL}/youtube?token={token_of(MRBEAST)}",
        f"{WEBHOOK_URL}/youtube?token={token_of(PEWDIEPIE)}",
    }, "the start moves every followed channel to its tokened callback"
    assert "**ghost**" in caplog.text, "a youtuber is named in bold, like the rest of the log"


async def test_the_start_counts_only_the_channels_the_hub_accepted(hub, deps, caplog):
    follow(deps, 2002, "mrbeast")
    hub.google.hub_statuses[TOPIC.format(MRBEAST)] = 503

    with caplog.at_level("INFO"):
        renewed = notifications_youtube_video.resubscribe_followed_channels()

    assert renewed == 1
    assert "renewed — 1 of 2" in caplog.text


async def test_the_start_stamps_every_youtuber_the_hub_took_again(hub, deps, monkeypatch):
    from app.data.reminder import find_reminder_by_value, insert_renewal_reminder
    from app.integrations import reminder_webhook

    moment = datetime(2026, 10, 1, 15, 0, tzinfo=timezone(timedelta(hours=-3)))

    class Clock:
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment

    monkeypatch.setattr(reminder_webhook, "datetime", Clock, raising=False)
    follow(deps, 2002, "mrbeast")
    insert_renewal_reminder(7001, "pewdiepie")
    insert_renewal_reminder(7002, "mrbeast")
    hub.google.hub_statuses[TOPIC.format(MRBEAST)] = 503

    notifications_youtube_video.resubscribe_followed_channels()

    stamped = find_reminder_by_value("pewdiepie").get("hub_confirmed_at")
    assert stamped is not None and stamped.astimezone(timezone.utc) == moment.astimezone(timezone.utc)
    assert find_reminder_by_value("mrbeast").get("hub_confirmed_at") is None, "the hub said 503"


async def test_an_answer_from_the_hub_keeps_neither_the_exception_nor_the_secrets_it_held(hub):
    """`requests` keeps the request on its exceptions, and the request's body holds the hub
    secret and the callback token."""
    from app.services.notifications_youtube_video import HubOutcome, ask_the_hub

    hub.google.outages["hub"] = 1

    answer = ask_the_hub("pewdiepie", "subscribe")

    assert answer.outcome is HubOutcome.TRY_LATER
    assert not any(isinstance(value, BaseException) for value in vars(answer).values())
    assert "Timeout" in answer.detail
    for secret in (SECRET, token_of(PEWDIEPIE)):
        assert secret not in repr(answer)


async def test_the_notifications_cog_renews_the_subscriptions_once_when_it_loads(hub, deps):
    from app.cogs.notifications import Notifications

    deps.bot.wait_until_ready = AsyncMock()
    cog = Notifications(deps.bot)

    await cog.cog_load()
    for _ in range(100):
        if not cog.resubscribe_youtube.is_running():
            break
        await asyncio.sleep(0.01)
    await cog.cog_unload()

    assert [post["hub.topic"] for post in hub.google.hub_posts] == [TOPIC.format(PEWDIEPIE)]


async def test_nothing_subscribes_at_start_in_dev(hub, deps):
    deps.bot.config.is_dev = lambda: True

    assert notifications_youtube_video.resubscribe_followed_channels() == 0
    assert hub.google.hub_posts == []


@pytest.mark.parametrize("error_code", ["ParameterNotFound", "AccessDeniedException"])
def test_the_bot_boots_when_ssm_cannot_give_the_hub_secret(boot, error_code):
    boot.setenv("APPLICATION_ENVIRONMENT", "PROD")
    ssm = FakeSSM(refused=[HUB_SECRET_PARAMETER], error_code=error_code)
    boot.setattr(config_module.boto3, "client", lambda *args, **kwargs: ssm)

    config = config_module.AppConfig()

    assert config.YOUTUBE_HUB_SECRET is None
    assert config.YOUTUBE_API_KEY == "value of /keiko/youtube/api_key"


def test_no_hub_secret_is_published_for_development(boot):
    boot.setenv("APPLICATION_ENVIRONMENT", "dev")
    boot.delenv("YOUTUBE_HUB_SECRET", raising=False)

    assert config_module.AppConfig().YOUTUBE_HUB_SECRET is None


async def test_without_a_hub_secret_every_notice_is_refused(hub):
    hub.config.YOUTUBE_HUB_SECRET = None
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    responses = [
        notify(hub, body, sign(body, secret=guess))
        for guess in ("", "keiko-local-youtube-hub-secret")
    ]
    await settle()

    assert [response.status_code for response in responses] == [403, 403]
    assert hub.channel._sent_messages == []


async def test_without_a_hub_secret_nothing_is_confirmed(hub):
    hub.config.YOUTUBE_HUB_SECRET = None

    responses = [
        verify(hub, "subscribe", TOPIC.format(PEWDIEPIE), url=url)
        for url in (callback(PEWDIEPIE), f"{URL}?token=", URL)
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]


async def test_without_a_hub_secret_keiko_asks_the_hub_for_nothing(hub):
    """`requests` drops a None field, so the hub would swap a signed subscription for an
    unsigned one; and without the secret there is no token to name the callback with."""
    hub.config.YOUTUBE_HUB_SECRET = None

    hub.youtube.subscribe_to_new_video_event(PEWDIEPIE)
    hub.youtube.unsubscribe_from_new_video_event(PEWDIEPIE)
    renewed = notifications_youtube_video.resubscribe_followed_channels()

    assert hub.google.hub_posts == []
    assert renewed == 0
