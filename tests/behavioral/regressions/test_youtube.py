"""YouTube notifications (`notifications_youtube_video`): the WebSub webhook
(`/v1/webhooks/youtube`) announces a video only when YouTube's hub signed the notice, once
per video, and never leaks the key or the secret; a renewal that ends itself ends whole
however slow reminders-api is; and removing a youtuber or turning YouTube off never waits
on YouTube."""
import asyncio
import base64
import importlib
import logging
import signal
import traceback
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from time import sleep
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

import discord
import pytest
import redis
import requests
from botocore.exceptions import ClientError

from app import config as config_module
from app import logger as logger_module
from app.constants import Commands, DBConfigs
from app.core import trace as trace_service
from app.core.commands import Command, commands
from app.core.utils import ml
from app.data import bind_loop, data
from app.forms.features.subscriptions import SubscriptionFeature
from app.integrations import http as api_config
from app.integrations.http import create_api
from app.integrations.reminders import client as reminder_webhook
from app.integrations.reminders.client import (
    REMINDER_AUTH_USER,
    REMINDER_TIMEZONE,
    ReminderWebhook,
)
from app.integrations.youtube import client as youtube_integration
from app.integrations.youtube import hub as youtube_hub
from app.integrations.youtube.hub import (
    HubAnswer,
    HubOutcome,
    ask_the_hub,
    ensure_renewal,
    find_reminder_by_id,
    find_reminder_by_value,
    insert_renewal_reminder,
    renew_youtube_subscription,
)
from tests.behavioral.flows.common import GUILD_ID, open_manager, remove_item
from tests.behavioral.flows.youtube import (
    FORM,
    open_card,
    pick_channel,
    seed,
    seed_two_entries,
    submit_messages,
    submit_youtuber,
)
from tests.generators.moderations import recorded_server, seed_cog
from tests.helpers.youtube import (
    API_KEY,
    MRBEAST,
    PEWDIEPIE,
    SECRET,
    TOPIC,
    URL,
    WEBHOOK_URL,
    atom,
    callback,
    notify,
    on_fake_google,
    settle,
    sign,
    token_of,
)
from tests.mocks.discord import create_guild

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

HUB_SECRET_PARAMETER = "/keiko/youtube/hub_secret"
STRANGER = "UC-stranger-channel-0000"
VIDEO_LINK = "https://www.youtube.com/watch?v=video-1"
CLAIM = "claim:youtube:video:video-1"
HOSTILE_BODY = (
    "<yt:videoId></yt:videoId><yt:channelId></yt:channelId><published></published>" * 1000
).encode()[: 64 * 1024]
QUOTA_EXCEEDED = (403, {"error": {
    "code": 403,
    "message": "The request cannot be completed because you have exceeded your quota.",
    "errors": [{"reason": "quotaExceeded"}],
}})


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
    google = on_fake_google(deps, monkeypatch)
    follow(deps, deps.guild.id, "pewdiepie")

    return SimpleNamespace(
        **vars(google), channel=deps.guild.text_channels[0], traces=traces
    )


@pytest.fixture
def boot(monkeypatch):
    """`AppConfig` exactly as the bot builds it, reading no `.env` from disk."""
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: None)
    return monkeypatch


def follow(deps, guild_id, *youtubers, enabled=True):
    recorded_server(deps.mongo_client, guild_id)
    seed_cog(deps.mongo_client, "notifications_youtube_video", {
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
    """A notice the hub signed announces the video it names.

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
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    response = notify(hub, body, sign(body))
    await settle()

    assert response.status_code == 204
    assert [message.content for message in hub.channel.get_all_messages()] == [
        f"pewdiepie posted: {VIDEO_LINK}"
    ]


async def test_the_announcement_links_to_the_video(hub):
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    notify(hub, body, sign(body))
    await settle()

    assert hub.channel.get_all_messages()[0].embeds[0].url == VIDEO_LINK


async def test_an_unsigned_notice_posts_nothing(hub):
    hub.google.videos["video-1"] = PEWDIEPIE

    response = notify(hub, atom())
    await settle()

    assert response.status_code == 403
    assert hub.channel.get_all_messages() == []


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
    assert hub.channel.get_all_messages() == []


@pytest.mark.parametrize("signed", [False, True], ids=["unsigned", "signed"])
async def test_a_hostile_64_kb_body_is_handled_in_bounded_time(hub, signed):
    with time_limit(1.0):
        response = notify(hub, HOSTILE_BODY, sign(HOSTILE_BODY) if signed else None)
    await settle()

    assert response.status_code in (204, 403)
    assert hub.channel.get_all_messages() == []


@pytest.mark.parametrize("second_notice", ["replayed", "edited"])
async def test_a_video_is_announced_once(hub, second_notice):
    """A replay, or YouTube's own second notice when a title is edited minutes later."""
    hub.google.videos["video-1"] = PEWDIEPIE
    first = atom()
    second = first if second_notice == "replayed" else atom(edited_after=timedelta(minutes=3))

    notify(hub, first, sign(first))
    notify(hub, second, sign(second))
    await settle()

    assert len(hub.channel.get_all_messages()) == 1


async def test_a_video_from_another_channel_is_ignored(hub):
    hub.google.videos["foreign"] = STRANGER
    body = atom(video_id="foreign", channel_id=PEWDIEPIE)

    response = notify(hub, body, sign(body))
    await settle()

    assert response.status_code == 204
    assert hub.channel.get_all_messages() == [], (
        "a followed channel's notice must not announce somebody else's video"
    )


def live_details(state):
    """What YouTube says of a live that is scheduled, on air or over."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if state == "scheduled":
        return "upcoming", {"scheduledStartTime": (now + timedelta(hours=2)).isoformat()}
    if state == "live-now":
        return "live", {"actualStartTime": (now - timedelta(minutes=1)).isoformat()}
    return "none", {
        "actualStartTime": (now - timedelta(hours=2)).isoformat(),
        "actualEndTime": now.isoformat(),
    }


@pytest.mark.parametrize("state", ["scheduled", "live-now", "finished"])
async def test_a_live_is_never_announced_as_a_new_video(hub, state):
    """A live, scheduled, on air or over, never reaches the video announcement.

    Broke as: the video command read only the snippet of the video a hub notice named and
    announced it as a new video whatever it was, so a scheduled live was posted as a video
    the moment it was scheduled, a live when YouTube sent its notice, and the moment a
    scheduled live started was never announced.

    Shared behaviour: the YouTube webhook (`app/integrations/youtube/webhook.py`), which
    now reads the notice's video once, with its live details, and hands it to the command
    of its kind. Exposed by: the PR12 plan, reading the code for YouTube lives.

    Guaranteed: a notice whose video carries live details never posts the video
    announcement, and the webhook answers it with 204.
    """
    hub.google.videos["video-1"] = PEWDIEPIE
    hub.google.lives["video-1"] = live_details(state)
    body = atom()

    response = notify(hub, body, sign(body))
    await settle()

    assert response.status_code == 204
    assert hub.channel.get_all_messages() == [], "a live is not a new video"


async def test_a_notice_reads_its_video_once(hub):
    """The webhook reads the video to tell a live from a video, and the announcement
    reuses it: one Data API unit per notice for the video, never two."""
    hub.google.videos["video-1"] = PEWDIEPIE
    body = atom()

    notify(hub, body, sign(body))
    await settle()

    assert len(hub.channel.get_all_messages()) == 1
    assert hub.google.reads.count("videos") == 1


async def test_a_video_youtube_cannot_see_yet_is_left_for_the_hub_to_retry(hub):
    """Any answer but 2xx makes the hub deliver again; a 204 here would lose the video."""
    body = atom()

    first = notify(hub, body, sign(body))
    hub.google.videos["video-1"] = PEWDIEPIE
    retry = notify(hub, body, sign(body))
    await settle()

    assert first.status_code == 503
    assert retry.status_code == 204
    assert len(hub.channel.get_all_messages()) == 1, "the retry is not mistaken for a replay"


async def test_a_video_youtube_never_shows_is_left_for_the_hub_to_retry_a_few_times(
    hub, deps
):
    """A private or deleted video YouTube never shows again is not delivered forever:
    the hub is asked to deliver it again a few times, then gets a 204."""
    body = atom()

    answers = [
        notify(hub, body, sign(body)).status_code
        for _ in range(Commands.YOUTUBE_UNSEEN_RETRIES + 1)
    ]
    await settle()

    assert answers == [503] * Commands.YOUTUBE_UNSEEN_RETRIES + [204]
    assert hub.channel.get_all_messages() == []
    counted = Commands.REDIS_YOUTUBE_UNSEEN_WINDOW.format(video_id="video-1")
    assert 0 < deps.redis_client.ttl(counted) <= Commands.YOUTUBE_UNSEEN_WINDOW_SECONDS


async def test_a_video_youtube_does_not_show_while_redis_is_down_is_left_to_the_hub(
    hub, deps, monkeypatch
):
    """Without Redis the deliveries cannot be counted, so the hub delivers again."""
    def redis_is_down(*args, **kwargs):
        raise redis.exceptions.ConnectionError("Connection refused")

    monkeypatch.setattr(deps.redis_client, "pipeline", redis_is_down)
    body = atom()

    response = notify(hub, body, sign(body))

    assert response.status_code == 503


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
    assert len(hub.channel.get_all_messages()) == 1


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
    assert len(hub.channel.get_all_messages()) == 1
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
    assert len(hub.channel.get_all_messages()) == 1


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
    assert len(hub.channel.get_all_messages()) == 1, "the server after the missing one still hears"
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

    assert len(hub.channel.get_all_messages()) == 1
    refused = [event["guild_id"] for event in analytics_events
               if event["type"] == "value.blocked_by_permission"]
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
    assert hub.channel.get_all_messages() == [], "nothing is sent before the bot sees its guilds"

    ready.set()
    await settle()

    assert len(hub.channel.get_all_messages()) == 1


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
    assert len(hub.channel.get_all_messages()) == 1
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
    assert hub.channel.get_all_messages() == []


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
    assert hub.channel.get_all_messages() == []


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
    assert len(hub.channel.get_all_messages()) == 1
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
    await insert_renewal_reminder(7001, "pewdiepie")
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
    from app.commands.youtube.cog import YoutubeRenewals

    with caplog.at_level("INFO"):
        await YoutubeRenewals(deps.bot).resubscribe_youtube()

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
        renewed = await youtube_hub.resubscribe_followed_channels()

    assert renewed == 1
    assert "renewed — 1 of 2" in caplog.text


async def test_the_start_stamps_every_youtuber_the_hub_took_again(hub, deps, monkeypatch):

    moment = datetime(2026, 10, 1, 15, 0, tzinfo=timezone(timedelta(hours=-3)))

    class Clock:
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz is not None else moment

    monkeypatch.setattr(reminder_webhook, "datetime", Clock, raising=False)
    follow(deps, 2002, "mrbeast")
    await insert_renewal_reminder(7001, "pewdiepie")
    await insert_renewal_reminder(7002, "mrbeast")
    hub.google.hub_statuses[TOPIC.format(MRBEAST)] = 503

    await youtube_hub.resubscribe_followed_channels()

    stamped = (await data().keiko_subscriptions.subscription_state("youtube", "pewdiepie")).get("hub_confirmed_at")
    assert stamped is not None and stamped.astimezone(timezone.utc) == moment.astimezone(timezone.utc)
    assert await data().keiko_subscriptions.subscription_state("youtube", "mrbeast") == {}, "the hub said 503"


async def test_an_answer_from_the_hub_keeps_neither_the_exception_nor_the_secrets_it_held(hub):
    """`requests` keeps the request on its exceptions, and the request's body holds the hub
    secret and the callback token."""
    hub.google.outages["hub"] = 1

    answer = ask_the_hub("pewdiepie", "subscribe")

    assert answer.outcome is HubOutcome.TRY_LATER
    assert not any(isinstance(value, BaseException) for value in vars(answer).values())
    assert "Timeout" in answer.detail
    for secret in (SECRET, token_of(PEWDIEPIE)):
        assert secret not in repr(answer)


async def test_the_youtube_renewals_cog_renews_the_subscriptions_once_when_it_loads(hub, deps):
    from app.commands.youtube.cog import YoutubeRenewals

    deps.bot.wait_until_ready = AsyncMock()
    cog = YoutubeRenewals(deps.bot)

    await cog.cog_load()
    for _ in range(100):
        if not cog.resubscribe_youtube.is_running():
            break
        await asyncio.sleep(0.01)
    await cog.cog_unload()

    assert [post["hub.topic"] for post in hub.google.hub_posts] == [TOPIC.format(PEWDIEPIE)]


async def test_nothing_subscribes_at_start_in_dev(hub, deps):
    deps.bot.config.is_dev = lambda: True

    assert await youtube_hub.resubscribe_followed_channels() == 0
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
    assert hub.channel.get_all_messages() == []


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
    renewed = await youtube_hub.resubscribe_followed_channels()

    assert hub.google.hub_posts == []
    assert renewed == 0


REMINDER_URL = "/v1/webhooks/reminder"
PASSWORD = "reminders-api-password"
RENEWAL_REMINDER = 7001
SLOW_SECONDS = 0.5
WAIT_SECONDS = 0.1


class SlowReminders(ReminderWebhook):
    """The real credential check, with a reminders-api slower than the webhook waits."""

    def __init__(self):
        super().__init__(SimpleNamespace(config=SimpleNamespace(
            WEBHOOK_URL="https://keiko.test/v1/webhooks",
            REMINDER_APPLICATION_ID="1",
            REMINDER_API_KEY="api-key",
            REMINDER_AUTH_PASSWORD=PASSWORD,
        )))
        self.created = []
        self.deleted = []

    def create_reminder(self, data):
        sleep(SLOW_SECONDS)
        self.created.append(data["notes"])
        return {"id": 8000 + len(self.created)}

    def delete_reminder(self, reminder_id):
        sleep(SLOW_SECONDS)
        self.deleted.append(reminder_id)
        return {}


@pytest.fixture
async def keiko(deps, monkeypatch):
    """The real API app, its waits bridged to this loop and cut short."""
    monkeypatch.setattr(DBConfigs, "MONGO_THREAD_WAIT_SECONDS", WAIT_SECONDS)
    bind_loop(asyncio.get_running_loop())
    deps.bot.reminder = SlowReminders()
    await insert_renewal_reminder(RENEWAL_REMINDER, "pewdiepie")
    return SimpleNamespace(
        client=create_api("dev").test_client(),
        reminders=deps.bot.reminder,
        youtube=deps.youtube,
        mongo=deps.mongo_client,
    )


def renewal_fired(keiko):
    token = base64.b64encode(f"{REMINDER_AUTH_USER}:{PASSWORD}".encode()).decode()
    return keiko.client.post(
        REMINDER_URL,
        json={"reminders_notified": [
            {"id": RENEWAL_REMINDER, "title": "youtube_notification", "notes": "pewdiepie"},
        ]},
        headers={"Authorization": f"Basic {token}"},
    )


async def settled(keiko):
    """The renewal's row once the work the webhook stopped waiting for is over."""
    for _ in range(100):
        await asyncio.sleep(0.05)
        if keiko.reminders.deleted:
            break
    await asyncio.sleep(0.1)
    return await find_reminder_by_id(RENEWAL_REMINDER)


async def test_a_renewal_slower_than_the_webhook_waits_still_forgets_its_row(keiko):
    """A renewal that ends itself ends whole, however long reminders-api takes.

    Reported in the design review: the renewals take turns on an `asyncio.Lock`, held across
    the calls to reminders-api, and the reminder webhook reaches the renewal that ends itself
    (`drop_unfollowed_renewal`) through `app.data.wait`, which gives up after
    `DBConfigs.MONGO_THREAD_WAIT_SECONDS` and cancels the work it waited on. The thread lock it
    replaced waited without a limit. So a slow reminders-api cancelled the drop half done: the
    reminder was deleted on reminders-api while its schedule row stayed in Mongo, or the drop
    never ran because it was still waiting for a subscribe that held the lock.

    Shared behaviour: `drop_unfollowed_renewal` (`app/commands/youtube/service.py`), reached
    by the reminder webhook through the loop bridge `app.data.wait`, and the lock it shares
    with `ensure_renewal`. Exposed by: the review of the move from the thread lock.

    Guaranteed: the renewal that ends itself holds the lock only while it counts the followers
    and forgets its row, and deletes the reminder after, so a webhook that stops waiting never
    leaves a row whose reminder is gone; a callback it was late for answers 500, so
    reminders-api may send it again, and that resend either finds nothing left to do or, when
    the drop never ran, drops it whole.
    """
    response = await asyncio.to_thread(renewal_fired, keiko)

    assert response.status_code == 500, "the callback it was late for is sent again"
    assert await settled(keiko) is None, "the reminder went, so its row goes too"
    assert keiko.reminders.deleted == [str(RENEWAL_REMINDER)]

    resent = await asyncio.to_thread(renewal_fired, keiko)

    assert resent.status_code == 200
    assert keiko.reminders.deleted == [str(RENEWAL_REMINDER)], "nothing left to delete"
    assert keiko.youtube.subscribe_calls == []


async def test_a_renewal_waiting_behind_a_slow_subscribe_ends_whole_when_sent_again(
    keiko,
):
    seed_cog(keiko.mongo, "notifications_youtube_video", {
        "guild_id": "4343",
        "enabled": True,
        "notifications": {"values": [{"youtuber": {"value": "mrbeast"}}]},
    })
    taken = HubAnswer(HubOutcome.DONE, "the hub answered 202", 202)
    subscribing = asyncio.ensure_future(ensure_renewal("mrbeast", taken))
    await asyncio.sleep(0.05)

    response = await asyncio.to_thread(renewal_fired, keiko)
    await subscribing

    assert response.status_code == 500
    assert keiko.reminders.deleted == [], "a drop that never ran leaves both in place"
    assert await find_reminder_by_id(RENEWAL_REMINDER) is not None

    resent = await asyncio.to_thread(renewal_fired, keiko)

    assert resent.status_code == 500, "its reminder is still slower than the wait"
    assert await settled(keiko) is None
    assert keiko.reminders.deleted == [str(RENEWAL_REMINDER)]
    assert keiko.reminders.created == ["mrbeast"]


CLOCK = datetime(2026, 10, 1, 15, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


@pytest.fixture
def clock(monkeypatch):
    """The moment a new renewal is scheduled from, where reminders-api reads it."""
    class Clock:
        @classmethod
        def now(cls, tz=None):
            return CLOCK.astimezone(tz) if tz is not None else CLOCK

    monkeypatch.setattr(reminder_webhook, "datetime", Clock, raising=False)


def youtube_answers(deps, trouble):
    """YouTube timing out, refusing with a quota error, or its hub answering 503."""
    if trouble == "hub-503":
        deps.youtube.unsubscribe_from_new_video_event = (
            lambda channel_id: SimpleNamespace(status_code=503)
        )
        return

    def fail(*args, **kwargs):
        if trouble == "timeout":
            raise requests.Timeout("YouTube did not answer in 10 s")
        raise youtube_integration.YoutubeAPIError("YouTube's channels answered 403 (quotaExceeded)")

    deps.youtube.get_channel_id_from_username = fail


async def followed(deps):
    document = await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, FORM)
    if document is None:
        return None
    return [item["youtuber"]["value"] for item in document["notifications"]["values"]]


def unsubscribe_errors(caplog):
    return [
        record.context.extra["youtuber"] for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == "youtube_unsubscribe"
    ]


def told_it_could_not_be_processed(scenario):
    title = ml("errors.generic.title", locale="en-us")
    return any(title in (embed.title or "") for embed in scenario.current_message.embeds or [])


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error", "hub-503"])
async def test_removing_a_youtuber_completes_whatever_youtube_answers(
    scenario_factory, deps, caplog, trouble,
):
    """Removing a youtuber completes whatever YouTube answers.

    Reported in the review of the callback token: once an error answer from the Data API
    raised instead of reading as "not found", the YouTube hooks the settings engine runs
    before it stores a change raised too. During a quota or key error, or a hub that timed out,
    an admin could not remove a youtuber or disable YouTube; at the base both went through.

    Shared behaviour: `unsubscribe_youtube_new_video` and `subscribe_youtube_new_video`, the
    subscription hooks the YouTube feature hands the settings engine.
    Exposed by: a YouTube quota error, a revoked API key, a hub that times out.

    The check of the next pass found that an edit whose unsubscribe of the old name went through
    and whose subscribe of the new name then failed deleted the renewal of a youtuber still
    followed; that removing the last enabled follower of a youtuber a paused server follows
    unsubscribed the hub; and that setup ignored the hub's answer, so a youtuber added while
    the hub could not take it waited four days for its renewal.

    Guaranteed: removing a youtuber and disabling YouTube complete whatever YouTube answers;
    the hub's unsubscribe is best-effort, logged as an error with its context when it fails,
    and is not sent while another server, paused or not, still follows the youtuber. The
    unsubscribe never deletes the renewal: the renewal drops itself at its next run once no
    server follows, so the subscription ends with its lease. Setup, adding and editing a
    youtuber need the channel, so a failed lookup fails them as a timeout does: the admin is
    told the command could not be processed, nothing is saved, and the youtuber still followed
    keeps its renewal. Once the channel is found, the hub's answer no longer decides whether
    the change is saved: a hub that cannot take it now gets Keiko's renewal an hour later, with
    a warning, and one that refuses it is an error with its context.
    """
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, seed_two_entries)
    await insert_renewal_reminder(7001, "pewdiepie")
    youtube_answers(deps, trouble)

    with caplog.at_level("INFO"):
        await remove_item(scenario, "notifications$0")

    assert await followed(deps) == ["mrbeast"]
    assert unsubscribe_errors(caplog) == ["pewdiepie"]
    assert await find_reminder_by_value("pewdiepie") is not None, "kept until it finds nobody follows"


@pytest.mark.parametrize("trouble", [None, "data-api-error"], ids=["hub-takes-it", "data-api-error"])
async def test_the_renewal_an_unsubscribe_leaves_behind_drops_itself(
    scenario_factory, deps, trouble,
):
    """The unsubscribe never deletes the renewal, whatever the hub says; the renewal does."""
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, seed_two_entries)
    await insert_renewal_reminder(7001, "pewdiepie")
    if trouble:
        youtube_answers(deps, trouble)
    await remove_item(scenario, "notifications$0")

    assert await find_reminder_by_value("pewdiepie") is not None, "the unsubscribe leaves it"
    await asyncio.to_thread(renew_youtube_subscription, 7001)

    assert await find_reminder_by_id(7001) is None
    deps.bot.reminder.delete_reminder.assert_called_once_with("7001")
    deps.bot.reminder.update_reminder.assert_not_called()


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error", "hub-503"])
async def test_turning_youtube_off_completes_whatever_youtube_answers(
    scenario_factory, deps, caplog, trouble,
):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, seed)
    youtube_answers(deps, trouble)

    with caplog.at_level("INFO"):
        await scenario.click("disable")
        await scenario.click("delete_all")

    scenario.expect_not_persisted("guild", FORM, {"guild_id": GUILD_ID})
    assert unsubscribe_errors(caplog) == ["pewdiepie"]


async def _set_up(scenario_factory, deps):
    scenario = await open_card(scenario_factory, deps, "en-us")
    await pick_channel(scenario)
    await submit_youtuber(scenario, "mrbeast")
    await submit_messages(scenario)
    return scenario, ("done", "confirm")


async def _add(scenario_factory, deps):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, seed)
    await scenario.click("add")
    await pick_channel(scenario)
    await submit_youtuber(scenario, "mrbeast")
    await submit_messages(scenario)
    return scenario, ("done",)


async def _edit(scenario_factory, deps):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, seed)
    await scenario.click("section:notifications$0")
    await submit_youtuber(scenario, "mrbeast")
    return scenario, ("done",)


CHANGES = {"setup": _set_up, "add": _add, "edit": _edit}
SAVED_BEFORE = {"setup": None, "add": ["pewdiepie"], "edit": ["pewdiepie"]}


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error"])
@pytest.mark.parametrize("change", ["setup", "add", "edit"])
async def test_a_change_that_needs_the_channel_fails_as_a_timeout_does(
    scenario_factory, deps, change, trouble,
):
    deps.bot.config.is_dev = lambda: False
    scenario, commit = await CHANGES[change](scenario_factory, deps)
    youtube_answers(deps, trouble)

    for step in commit:
        if step == "confirm":
            await scenario.confirm()
        else:
            await scenario.click(step)

    assert told_it_could_not_be_processed(scenario)
    assert await followed(deps) == SAVED_BEFORE[change], "nothing is saved"


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error"])
async def test_a_failed_edit_leaves_the_youtuber_still_followed_with_its_renewal(
    scenario_factory, deps, trouble,
):
    """The new youtuber is subscribed before the edit is saved; its lookup fails here."""
    deps.bot.config.is_dev = lambda: False
    await insert_renewal_reminder(7001, "pewdiepie")
    scenario, commit = await _edit(scenario_factory, deps)
    youtube_answers(deps, trouble)

    await scenario.click("done")

    assert await followed(deps) == ["pewdiepie"]
    assert await find_reminder_by_value("pewdiepie") is not None, (
        "a youtuber still followed without a renewal would go quiet within five days"
    )


async def test_an_edit_that_fails_after_the_old_name_was_unsubscribed_keeps_its_renewal(
    scenario_factory, deps,
):
    """The new name is subscribed before the edit is saved and the old one is let go only
    after it; here the lookup of the new name failed, so neither happened."""
    deps.bot.config.is_dev = lambda: False
    await insert_renewal_reminder(7001, "pewdiepie")
    scenario, commit = await _edit(scenario_factory, deps)
    look_up = deps.youtube.get_channel_id_from_username

    def only_the_old_name(username):
        if username.lower().lstrip("@") == "mrbeast":
            raise youtube_integration.YoutubeAPIError("YouTube's channels did not answer: ReadTimeout")
        return look_up(username)

    deps.youtube.get_channel_id_from_username = only_the_old_name

    await scenario.click("done")

    assert await followed(deps) == ["pewdiepie"], "nothing is saved"
    assert await find_reminder_by_value("pewdiepie") is not None, (
        "a youtuber still followed keeps its renewal, which subscribes it again"
    )


async def test_removing_a_youtuber_a_paused_server_still_follows_keeps_the_hub_subscribed(
    scenario_factory, deps,
):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, seed_two_entries)
    seed_cog(deps.mongo_client, FORM, {
        "guild_id": "999",
        "enabled": False,
        "notifications": {"values": [{"youtuber": {"value": "pewdiepie"}}]},
    })
    await insert_renewal_reminder(7001, "pewdiepie")

    await remove_item(scenario, "notifications$0")

    assert await followed(deps) == ["mrbeast"]
    assert deps.youtube.unsubscribe_calls == [], "the paused server unpauses still subscribed"
    assert await find_reminder_by_value("pewdiepie") is not None


class OtherYouTubeFeature(SubscriptionFeature):
    """A second command that follows YouTube channels, as YouTube lives will."""

    item_key = "youtuber"
    external = "youtube"

    def __init__(self):
        super().__init__("other_youtube_feature")
        self.names = set()

        async def count(name):
            return int(name in self.names)

        self.followers = count
        self.announcers = count

    async def followed(self):
        return sorted(self.names)


@pytest.fixture
def other_youtube_feature(monkeypatch):
    """The other YouTube command, declared after every real one."""
    feature = OtherYouTubeFeature()
    listed = importlib.import_module("app.commands")
    declared = (*listed.COMMANDS, Command("other_youtube_feature", feature=feature))
    monkeypatch.setattr(listed, "COMMANDS", declared)
    commands.cache_clear()
    yield feature
    commands.cache_clear()


async def test_a_channel_another_youtube_command_follows_keeps_its_hub_subscription(
    scenario_factory, deps, other_youtube_feature,
):
    """Letting a youtuber go from the videos never ends the hub subscription another
    YouTube command still needs.

    Broke as: the video command counted only its own followers before it unsubscribed a
    channel from the hub, so a channel followed for its lives would stop sending notices
    the moment its videos were unfollowed.

    Shared behaviour: the subscription base (`SubscriptionFeature.release`) and the YouTube
    hub follow (`app/integrations/youtube/hub.py`), counted over every command of the same
    platform. Exposed by: the PR12 plan, which adds YouTube lives beside the videos.

    Guaranteed: a channel stays subscribed, with its renewal, while any YouTube command
    follows it.
    """
    other_youtube_feature.names.add("pewdiepie")
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, seed_two_entries)
    await insert_renewal_reminder(7001, "pewdiepie")

    await remove_item(scenario, "notifications$0")

    assert await followed(deps) == ["mrbeast"]
    assert deps.youtube.unsubscribe_calls == [], "the other command still needs it"
    assert await find_reminder_by_value("pewdiepie") is not None


async def test_the_start_subscribes_the_channels_every_youtube_command_follows(
    hub, deps, other_youtube_feature,
):
    other_youtube_feature.names.add("mrbeast")
    from app.commands.youtube.cog import YoutubeRenewals

    await YoutubeRenewals(deps.bot).resubscribe_youtube()

    assert sorted(post["hub.topic"] for post in hub.google.hub_posts) == sorted([
        TOPIC.format(MRBEAST), TOPIC.format(PEWDIEPIE),
    ])


def renewal_created(deps):
    """When the renewal reminder of a new youtuber fires, read in reminders-api's zone."""
    sent = deps.bot.reminder.create_reminder.call_args.args[0]
    assert sent.get("time_tz"), "the hour is sent as well as the date"
    return datetime.combine(
        date.fromisoformat(str(sent["date_tz"])),
        time.fromisoformat(sent["time_tz"]),
        ZoneInfo("America/Sao_Paulo"),
    )


@pytest.mark.parametrize("status", [503, 429])
async def test_a_youtuber_added_while_the_hub_cannot_take_it_is_saved_and_tried_again_in_an_hour(
    scenario_factory, deps, caplog, clock, status,
):
    deps.bot.config.is_dev = lambda: False
    deps.bot.reminder.create_reminder.return_value = {"id": 8001}
    scenario, commit = await _add(scenario_factory, deps)
    deps.youtube.subscribe_to_new_video_event = lambda channel_id: SimpleNamespace(status_code=status)

    with caplog.at_level("INFO"):
        await scenario.click("done")

    assert await followed(deps) == ["pewdiepie", "mrbeast"], "the hub's answer does not undo the save"
    assert renewal_created(deps) - CLOCK == timedelta(hours=1), "Keiko's own retry covers it"
    assert await find_reminder_by_value("mrbeast") is not None
    assert any(
        record.levelno == logging.WARNING and "**mrbeast**" in record.getMessage()
        for record in caplog.records
    )


async def test_a_youtuber_added_while_the_hub_refuses_it_is_saved_with_an_error(
    scenario_factory, deps, caplog, clock,
):
    deps.bot.config.is_dev = lambda: False
    deps.bot.reminder.create_reminder.return_value = {"id": 8001}
    scenario, commit = await _add(scenario_factory, deps)
    deps.youtube.subscribe_to_new_video_event = lambda channel_id: SimpleNamespace(status_code=400)

    with caplog.at_level("INFO"):
        await scenario.click("done")

    assert await followed(deps) == ["pewdiepie", "mrbeast"]
    refusals = [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == "youtube_subscribe"
    ]
    assert [(record.context.extra["youtuber"], record.context.extra["status"]) for record in refusals] == [
        ("mrbeast", 400)
    ]
    assert renewal_created(deps) - CLOCK == timedelta(days=4), "the normal cadence"
