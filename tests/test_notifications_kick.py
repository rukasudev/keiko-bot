"""Kick lives reach Keiko only when Kick signed them, once per message, as the start or
the end of a live on a lowercased channel; Keiko asks Kick for its app token, its
signing key and its subscriptions no more than it needs, always as KeikoBot.

A live then reaches every server that follows its streamer once, with the picture and
the category Kick shows for the channel; its end marks every notice ended with how
long it ran; a live that starts again within the hour edits its notices back instead
of announcing; and once a day every followed streamer gets its subscription again.
"""

import asyncio
import base64
import json
import logging
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app import integrations
from app.commands.kick.cog import KickResyncLoop
from app.constants import Commands
from app.core import dispatch
from app.core.commands import WebhookReply
from app.core.utils import fill, ml
from app.data import data
from app.integrations.http import create_api
from app.integrations.kick import KickAPIError, KickClient
from app.integrations.kick import client as kick_client
from tests.behavioral.harness.copy_keys import copy_text
from tests.behavioral.flows import kick as kick_flows
from tests.behavioral.flows.common import seed_document
from tests.generators.moderations import recorded_server
from tests.helpers.youtube import settle
from tests.mocks import create_guild

pytestmark = pytest.mark.unit

URL = "/v1/webhooks/kick"
LIVE = Commands.KICK_LIVE_STATUS_EVENT
TIMESTAMP = "2026-11-22T12:00:00Z"
FIRST = "01JDKICK0000000000000000A1"
SECOND = "01JDKICK0000000000000000B2"
THIRD = "01JDKICK0000000000000000C3"
ANNOUNCEMENT = "notifications_kick.ui.announcement"
THUMBNAIL = "https://images.kick.com/video_thumbnails/xqcow/live.webp"


class Answer:
    """One answer of Kick's, as `requests` hands it over."""

    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeKick:
    """Kick's id and public APIs answered from memory, every request kept."""

    def __init__(self, key):
        self.key = key
        self.sent = []
        self.tokens = 0
        self.token_lifetimes = []
        self.refusals = []
        self.refused_methods = {}
        self.key_outage = False
        self.channels = {
            "xqcow": {
                "broadcaster_user_id": 676, "slug": "xqcow", "stream_title": "Live",
            },
        }
        self.subscriptions = {}

    def get(self, url, **options):
        return self._answer("GET", url, options)

    def post(self, url, **options):
        return self._answer("POST", url, options)

    def delete(self, url, **options):
        return self._answer("DELETE", url, options)

    def holds(self, subscription_id, broadcaster_user_id, event=LIVE):
        self.subscriptions[subscription_id] = {
            "id": subscription_id,
            "broadcaster_user_id": broadcaster_user_id,
            "event": event,
            "version": 1,
            "method": "webhook",
        }

    def key_reads(self):
        return sum(1 for request in self.sent if request.path.endswith("/public-key"))

    def bearers(self, path):
        return [
            request.headers["Authorization"].removeprefix("Bearer ")
            for request in self.sent
            if request.path.endswith(path)
        ]

    def _answer(self, method, url, options):
        path = urlsplit(url).path
        self.sent.append(SimpleNamespace(method=method, url=url, path=path, **options))
        if url == Commands.KICK_TOKEN_URL:
            self.tokens += 1
            lifetime = self.token_lifetimes.pop(0) if self.token_lifetimes else 3600
            answer = {"access_token": f"token-{self.tokens}", "token_type": "Bearer"}
            if lifetime is not None:
                answer["expires_in"] = lifetime
            return Answer(200, answer)
        if path.endswith("/public-key"):
            if self.key_outage:
                return Answer(502, {"message": "Bad gateway"})
            return Answer(200, {"data": {"public_key": pem(self.key)}, "message": "OK"})
        if self.refusals:
            return Answer(self.refusals.pop(0), {"message": "Unauthorized"})
        if method in self.refused_methods:
            return Answer(self.refused_methods[method], {"message": "Bad request"})
        if path.endswith("/channels"):
            params = options.get("params") or []
            slugs = [value for name, value in params if name == "slug"]
            found = [self.channels[slug] for slug in slugs if slug in self.channels]
            return Answer(200, {"data": found, "message": "OK"})
        if method == "POST":
            subscription_id = f"sub-new-{len(self.subscriptions)}"
            self.holds(subscription_id, options["json"]["broadcaster_user_id"])
            return Answer(200, {"data": [{
                "name": LIVE, "version": 1, "subscription_id": subscription_id,
            }], "message": "OK"})
        if method == "DELETE":
            self.subscriptions.pop(options["params"]["id"], None)
            return Answer(204)
        return Answer(200, {"data": list(self.subscriptions.values()), "message": "OK"})


def pem(private_key):
    return private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def sign(private_key, message_id, body, timestamp=TIMESTAMP):
    message = f"{message_id}.{timestamp}.".encode() + body
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


def live(
    slug="xQcOW",
    is_live=True,
    started_at="2026-11-22T12:00:00Z",
    ended_at="2026-11-22T14:00:00Z",
):
    """A `livestream.status.updated` body, as Kick documents it."""
    return {
        "broadcaster": {
            "user_id": 676,
            "username": "xQc",
            "channel_slug": slug,
            "profile_picture": "https://files.kick.com/images/user/676/profile.webp",
            "is_verified": True,
        },
        "is_live": is_live,
        "title": "Live now",
        "started_at": started_at,
        "ended_at": None if is_live else ended_at,
    }


@pytest.fixture(scope="module")
def keys():
    """Kick's signing key, the one it rotates to, and a stranger's."""
    return SimpleNamespace(**{
        name: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for name in ("kick", "rotated", "stranger")
    })


@pytest.fixture
def kick_app(deps, monkeypatch, keys):
    """The real route, Kick client and command, Kick answered from memory."""
    web = FakeKick(keys.kick)
    clock = [1000.0]
    monkeypatch.setattr(integrations, "requests", web)
    monkeypatch.setattr(kick_client, "monotonic", lambda: clock[0])
    deps.bot.config.KICK_CLIENT_ID = "kick-client-id"
    deps.bot.config.KICK_CLIENT_SECRET = "kick-client-secret"
    deps.bot.kick = KickClient(deps.bot)

    return SimpleNamespace(
        web=web,
        clock=clock,
        client=deps.bot.kick,
        redis=deps.redis_client,
        post=create_api("dev").test_client().post,
        dispatched=[],
        replies=[],
    )


@pytest.fixture
def kick(kick_app, monkeypatch):
    """The real route and Kick client, the dispatch kept instead of run."""

    def handed(event):
        kick_app.dispatched.append(event)
        reply = kick_app.replies.pop(0) if kick_app.replies else None
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(dispatch, "webhook", handed)
    return kick_app


def deliver(kick, payload, message_id=FIRST, signed_with=None, headers=None):
    """Post one message to the route the way Kick does, signed with Kick's key."""
    body = json.dumps(payload).encode()
    sent = {
        "Kick-Event-Message-Id": message_id,
        "Kick-Event-Subscription-Id": "sub-1",
        "Kick-Event-Signature": sign(signed_with or kick.web.key, message_id, body),
        "Kick-Event-Message-Timestamp": TIMESTAMP,
        "Kick-Event-Type": LIVE,
        "Kick-Event-Version": "1",
        **(headers or {}),
    }
    return kick.post(URL, data=body, headers=sent, content_type="application/json")


def claim(message_id):
    return Commands.REDIS_KICK_EVENT_CLAIM.format(message_id=message_id)


def past_the_key_window(kick):
    """Kick's clock moved past the time Keiko waits between two reads of its key."""
    kick.clock[0] += Commands.KICK_PUBLIC_KEY_RETRY_SECONDS


def test_a_message_kick_signed_is_handed_to_the_live_command(kick):
    response = deliver(kick, live())

    assert response.status_code == 200
    assert [event.name for event in kick.dispatched] == ["kick.stream.online"]


@pytest.mark.parametrize("forgery", [
    lambda keys, body: {"Kick-Event-Signature": sign(keys.stranger, FIRST, body)},
    lambda keys, body: {"Kick-Event-Signature": sign(keys.kick, FIRST, b"{}")},
    lambda keys, body: {"Kick-Event-Signature": ""},
    lambda keys, body: {"Kick-Event-Signature": "not base64 at all!"},
    lambda keys, body: {"Kick-Event-Message-Id": ""},
], ids=["stranger-key", "another-body", "no-signature", "garbled", "no-message-id"])
def test_a_message_kick_did_not_sign_is_refused_with_403(kick, keys, forgery):
    """A forged or unsigned live is refused, announces nothing and claims nothing."""
    payload = live()
    forged = forgery(keys, json.dumps(payload).encode())

    response = deliver(kick, payload, headers=forged)

    assert response.status_code == 403
    assert kick.dispatched == []
    assert kick.redis.keys("claim:kick:*") == []


def test_a_retried_message_is_handled_once(kick):
    """Kick sends a message again when it got no answer in time: the retry is taken
    with a 200 and announces nothing twice; the claim expires after a day."""
    first = deliver(kick, live())
    retry = deliver(kick, live())

    assert (first.status_code, retry.status_code) == (200, 200)
    assert len(kick.dispatched) == 1
    assert kick.redis.ttl(claim(FIRST)) == Commands.KICK_EVENT_TTL_SECONDS


def test_a_message_the_command_could_not_take_is_handled_again_on_retry(kick):
    kick.replies = [WebhookReply(503, "Not scheduled")]

    first = deliver(kick, live())
    retry = deliver(kick, live())

    assert (first.status_code, retry.status_code) == (503, 200)
    assert len(kick.dispatched) == 2


def test_a_message_the_command_raised_on_is_handled_again_on_retry(kick):
    """The message's claim is given back when the command raises too, so Kick's retry
    is handled instead of taken as a copy."""
    kick.replies = [RuntimeError("the bot's loop is gone")]

    with pytest.raises(RuntimeError):
        deliver(kick, live())
    retry = deliver(kick, live())

    assert retry.status_code == 200
    assert len(kick.dispatched) == 2


@pytest.mark.parametrize("is_live, name", [
    (True, "kick.stream.online"),
    (False, "kick.stream.offline"),
], ids=["start", "end"])
def test_a_live_start_and_end_are_dispatched_by_their_lowercased_channel(
    kick, is_live, name
):
    """Kick's slug may carry capitals; the event names the channel in lowercase and
    carries Kick's body whole, and with no command to take it Kick still gets a 200."""
    payload = live(slug="xQcOW", is_live=is_live)

    response = deliver(kick, payload)

    assert response.status_code == 200
    [event] = kick.dispatched
    assert (event.name, event.subject, event.payload) == (name, "xqcow", payload)


@pytest.mark.parametrize("payload, headers", [
    (live(), {"Kick-Event-Type": "chat.message.sent"}),
    ({"is_live": True, "broadcaster": {}}, {}),
    ({"broadcaster": {"channel_slug": "xqcow"}}, {}),
    (["not", "an", "object"], {}),
], ids=["another-event", "no-channel", "no-live-status", "not-an-object"])
def test_a_signed_message_with_no_live_to_announce_is_taken_and_dispatches_nothing(
    kick, payload, headers
):
    response = deliver(kick, payload, headers=headers)

    assert response.status_code == 200
    assert kick.dispatched == []


def test_a_rotated_key_is_read_again_once_and_the_message_passes(kick, keys):
    deliver(kick, live(), message_id=FIRST)
    kick.web.key = keys.rotated
    past_the_key_window(kick)

    response = deliver(kick, live(is_live=False), message_id=SECOND)

    assert response.status_code == 200
    assert len(kick.dispatched) == 2
    assert kick.web.key_reads() == 2


def test_a_key_rotated_right_after_a_read_asks_kick_to_send_the_message_later(
    kick, keys
):
    """While the key was read less than a window ago, a message it refuses may be
    signed with the key Kick just rotated to: Kick is asked to send it again (503,
    not 403, which would drop it), and its retry past the window passes."""
    deliver(kick, live(), message_id=FIRST)
    kick.web.key = keys.rotated

    first = deliver(kick, live(is_live=False), message_id=SECOND)
    past_the_key_window(kick)
    retry = deliver(kick, live(is_live=False), message_id=SECOND)

    assert (first.status_code, retry.status_code) == (503, 200)
    assert [event.name for event in kick.dispatched] == [
        "kick.stream.online", "kick.stream.offline",
    ]
    assert kick.web.key_reads() == 2


def test_forged_messages_read_the_key_again_at_most_once_a_window(kick, keys):
    """A stream of forgeries cannot make Keiko ask Kick for its key on every one:
    inside the window they are sent back unread, past it the key is read once more
    and a forgery it still refuses is refused."""
    deliver(kick, live(), message_id=FIRST)

    inside = [
        deliver(kick, live(), message_id=message_id, signed_with=keys.stranger)
        for message_id in (SECOND, THIRD)
    ]
    past_the_key_window(kick)
    past = [
        deliver(kick, live(), message_id=message_id, signed_with=keys.stranger)
        for message_id in (SECOND, THIRD)
    ]

    assert [response.status_code for response in inside] == [503, 503]
    assert [response.status_code for response in past] == [403, 503]
    assert kick.web.key_reads() == 2
    assert len(kick.dispatched) == 1


def test_a_key_kick_cannot_hand_over_asks_kick_to_send_the_message_later(kick):
    kick.web.key_outage = True

    response = deliver(kick, live())

    assert response.status_code == 503
    assert kick.dispatched == []
    assert kick.redis.keys("claim:kick:*") == []


def test_a_key_kick_cannot_hand_over_is_asked_for_once_a_window(kick):
    """Before Keiko ever held Kick's key, a Kick that cannot hand it over is asked
    once a window, every message in between sent back for later unread."""
    kick.web.key_outage = True

    refused = [deliver(kick, live(), message_id=message_id) for message_id in (FIRST, SECOND)]
    kick.web.key_outage = False
    past_the_key_window(kick)
    taken = deliver(kick, live(), message_id=THIRD)

    assert [response.status_code for response in refused] == [503, 503]
    assert taken.status_code == 200
    assert kick.web.key_reads() == 2


def test_the_token_is_reused_until_it_expires(kick):
    """A token is kept until shortly before Kick says it expires, then replaced."""
    kick.web.token_lifetimes = [Commands.KICK_TOKEN_MARGIN_SECONDS, 3600]

    for _ in range(3):
        kick.client.channels(["xqcow"])

    assert kick.web.tokens == 2
    assert kick.web.bearers("/channels") == ["token-1", "token-2", "token-2"]


def test_a_token_kick_gives_no_lifetime_is_kept_for_the_default_one(kick):
    kick.web.token_lifetimes = [None]

    for _ in range(3):
        kick.client.channels(["xqcow"])

    assert kick.web.tokens == 1


def test_a_token_kick_refuses_is_replaced_and_the_call_sent_once_more(kick):
    kick.client.channels(["xqcow"])
    kick.web.refusals = [401]

    found = kick.client.channels(["xqcow"])

    assert set(found) == {"xqcow"}
    assert kick.web.bearers("/channels") == ["token-1", "token-1", "token-2"]


def test_a_token_kick_is_slow_to_hand_over_holds_up_no_signature_check(
    kick, monkeypatch
):
    """Kick's signing key is read while another call still waits for Kick to hand
    over the app token."""
    asked, answered = threading.Event(), threading.Event()
    post = kick.web.post

    def slow(url, **options):
        if url == Commands.KICK_TOKEN_URL:
            asked.set()
            answered.wait(5)
        return post(url, **options)

    monkeypatch.setattr(kick.web, "post", slow)
    read = []
    waiting = threading.Thread(target=kick.client.channels, args=(["xqcow"],))
    reader = threading.Thread(target=lambda: read.append(kick.client.public_key()))
    waiting.start()
    try:
        assert asked.wait(5)
        reader.start()
        reader.join(2)
        read_meanwhile = bool(read)
    finally:
        answered.set()
        waiting.join(5)
        reader.join(5)

    assert read_meanwhile
    assert kick.web.key_reads() == 1


def test_channels_are_found_by_slug_whatever_their_capitals(kick):
    found = kick.client.channels(["xQcOW", "nobody-streams-here"])

    assert found == {"xqcow": kick.web.channels["xqcow"]}


def test_the_resync_creates_only_the_missing_and_deletes_the_unfollowed(kick):
    """Each followed broadcaster ends with one live subscription, one nobody follows
    or a second copy is deleted, and a subscription to another event stays."""
    kick.web.holds("sub-followed", 676)
    kick.web.holds("sub-copy", 676)
    kick.web.holds("sub-unfollowed", 9000)
    kick.web.holds("sub-chat", 9000, event="chat.message.sent")

    changed = kick.client.resync({676, 1234})

    [created] = changed.created
    assert created.broadcaster_user_id == 1234
    assert {gone.id for gone in changed.deleted} == {"sub-copy", "sub-unfollowed"}
    assert sorted(
        (entry["broadcaster_user_id"], entry["event"])
        for entry in kick.web.subscriptions.values()
    ) == [(676, LIVE), (1234, LIVE), (9000, "chat.message.sent")]
    [subscribe] = [
        request for request in kick.web.sent
        if request.method == "POST" and request.path.endswith("/subscriptions")
    ]
    assert subscribe.json == {
        "broadcaster_user_id": 1234,
        "events": [{"name": LIVE, "version": 1}],
        "method": "webhook",
    }


def test_a_resync_step_kick_refuses_is_left_for_the_next_resync(kick):
    kick.web.holds("sub-unfollowed", 9000)
    kick.web.refused_methods = {"POST": 400}

    changed = kick.client.resync({1234})

    assert changed.created == ()
    assert [gone.id for gone in changed.deleted] == ["sub-unfollowed"]


def test_every_call_to_kick_names_keiko_as_its_user_agent(kick):
    """Kick's Cloudflare refuses the default User-Agent of Python's HTTP client."""
    kick.client.channels(["xqcow"])
    subscription_id = kick.client.subscribe_live_status(676)
    kick.client.subscriptions()
    kick.client.delete_subscription(subscription_id)
    kick.client.public_key()

    assert {request.path.rsplit("/", 1)[-1] for request in kick.web.sent} == {
        "token", "channels", "subscriptions", "public-key",
    }
    assert {request.headers["User-Agent"] for request in kick.web.sent} == {
        Commands.KICK_USER_AGENT
    }


def test_without_credentials_every_call_refuses_without_reaching_kick(kick, caplog):
    """Keiko starts without Kick's credentials: each call refuses cleanly and the
    missing credentials are logged once, however many calls refuse."""
    kick.client.bot.config.KICK_CLIENT_ID = ""

    with caplog.at_level(logging.WARNING):
        for call in (
            lambda: kick.client.channels(["xqcow"]),
            lambda: kick.client.subscribe_live_status(676),
            lambda: kick.client.resync({676}),
        ):
            with pytest.raises(KickAPIError):
                call()

    assert kick.web.sent == []
    warned = [
        record for record in caplog.records if "KICK_CLIENT_ID" in record.getMessage()
    ]
    assert len(warned) == 1


@pytest.fixture
async def followers(deps, kick_app):
    """Two servers, in English, following xqcow's Kick lives, and Kick showing the
    live with its picture and category."""
    deps.bot.loop = asyncio.get_running_loop()
    kick_app.web.channels["xqcow"] = {
        "broadcaster_user_id": 676,
        "slug": "xqcow",
        "stream_title": "Live now",
        "category": {"name": "Just Chatting"},
        "stream": {"is_live": True, "thumbnail": THUMBNAIL},
    }
    first = create_guild(id=5001, name="First", channels=["lives"])
    second = create_guild(id=5002, name="Second", channels=["streams"])
    known = {first.id: first, second.id: second}
    deps.bot.get_guild = lambda guild_id: known.get(int(guild_id))
    for guild in known.values():
        recorded_server(deps.mongo_client, guild.id, language="en-us")
        notice = kick_flows.notification(str(guild.text_channels[0].id), "xqcow")
        seed_document(deps, kick_flows.FORM, {
            **kick_flows.ENABLED,
            "guild_id": str(guild.id),
            "notifications": {"style": "composition", "values": [notice]},
        })
    return SimpleNamespace(kick=kick_app, both=(first, second))


def notices(guild):
    return [
        message
        for channel in guild.text_channels
        for message in channel.get_all_messages()
    ]


def footer(status, duration=None):
    """The status line of a live notice, as the shared copy writes it in English."""
    if status == "online":
        line = copy_text("commands.live_notice.footer", "en-us")
    elif duration:
        line = copy_text("commands.live_notice.overrides.0.footer", "en-us", duration=duration)
    else:
        line = copy_text("commands.live_notice.overrides.1.footer", "en-us")
    return f"• {line}"


async def announced(followers, payload, message_id):
    """Kick's message about a live, and what the bot did with it."""
    response = deliver(followers.kick, payload, message_id=message_id)
    await settle()
    return response


@pytest.mark.behavioral
async def test_a_live_is_announced_once_in_every_server_with_its_channels_picture(
    deps, followers
):
    """The notice carries the picture and the category Kick shows for the channel,
    links to it and is kept to be marked ended; a second message about the same
    live posts nothing more."""
    await announced(followers, live(), FIRST)
    await announced(followers, live(), SECOND)

    for guild in followers.both:
        [notice] = notices(guild)
        embed = notice.embeds[0]
        assert (embed.image.url, embed.url) == (THUMBNAIL, "https://kick.com/xqcow")
        category = ml(f"{ANNOUNCEMENT}.fields.category", "en-us")
        assert (category, "Just Chatting") in [
            (field.name, field.value) for field in embed.fields
        ]
        assert embed.footer.text == footer("online")
        saved = await data().guild_messages.find_message(
            guild.id, guild.text_channels[0].id, kick_flows.FORM, "xqcow"
        )
        assert saved["message_id"] == str(notice.id)


@pytest.mark.behavioral
async def test_a_live_whose_channel_kick_cannot_show_is_announced_from_its_event(
    deps, followers
):
    """Kick's channels refusing (a 500) does not lose the live: the notice is posted
    with the title and the streamer Kick's event carries, without the picture."""
    followers.kick.web.refused_methods = {"GET": 500}

    await announced(followers, live(), FIRST)

    for guild in followers.both:
        [notice] = notices(guild)
        embed = notice.embeds[0]
        assert (embed.title, embed.image.url) == ("Live now", None)
        streamer = ml(f"{ANNOUNCEMENT}.fields.streamer", "en-us")
        assert (streamer, "xQc") in [(field.name, field.value) for field in embed.fields]


@pytest.mark.behavioral
async def test_a_live_another_delivery_already_claimed_posts_nothing(deps, followers):
    """The live's own claim, taken by a delivery Kick sent under another message id,
    keeps this one from posting it a second time."""
    followers.kick.redis.set(
        Commands.REDIS_KICK_LIVE_CLAIM.format(
            streamer="xqcow", started_at="2026-11-22T12:00:00Z"
        ),
        "1",
    )

    await announced(followers, live(), FIRST)

    assert [notices(guild) for guild in followers.both] == [[], []]


@pytest.mark.behavioral
async def test_the_end_of_a_live_marks_every_notice_ended_with_how_long_it_ran(
    deps, followers
):
    await announced(followers, live(), FIRST)

    await announced(followers, live(is_live=False), SECOND)

    for guild in followers.both:
        [notice] = notices(guild)
        assert notice.embeds[0].footer.text == footer("offline", duration="2h")


@pytest.mark.behavioral
async def test_a_live_restarted_within_the_hour_edits_its_notices_back_to_online(
    deps, followers
):
    """A live that drops and starts again less than an hour after it started is the
    same live: its notices read online again, and nothing is posted twice."""
    await announced(followers, live(), FIRST)
    await announced(followers, live(is_live=False, ended_at="2026-11-22T12:30:00Z"), SECOND)

    await announced(followers, live(started_at="2026-11-22T12:50:00Z"), THIRD)

    for guild in followers.both:
        [notice] = notices(guild)
        assert notice.embeds[0].footer.text == footer("online")


@pytest.mark.behavioral
async def test_the_daily_resync_subscribes_every_followed_streamer_and_keeps_its_id(
    deps, followers
):
    """Kick drops a subscription whose endpoint failed for a day and loses some on
    its own: once a day each streamer a server follows gets its subscription again,
    one nobody follows loses it, and Keiko keeps what Kick gave it."""
    deps.bot.config.is_dev = lambda: False
    followers.kick.web.holds("sub-unfollowed", 9000)

    await KickResyncLoop(deps.bot).resync_kick()

    held = followers.kick.web.subscriptions
    state = await data().keiko_subscriptions.subscription_state("kick", "xqcow")
    assert state["broadcaster_user_id"] == 676
    assert [held[state["subscription_id"]]["broadcaster_user_id"]] == [
        entry["broadcaster_user_id"] for entry in held.values()
    ] == [676]


@pytest.mark.behavioral
async def test_a_resync_over_two_copies_keeps_one_and_the_state_names_it(
    deps, followers
):
    """Kick holds two copies of a followed streamer's subscription and Keiko kept the
    id of the one the resync drops: afterwards Keiko names the copy Kick still holds."""
    deps.bot.config.is_dev = lambda: False
    followers.kick.web.holds("sub-first", 676)
    followers.kick.web.holds("sub-second", 676)
    await data().keiko_subscriptions.update_subscription_state(
        "kick", "xqcow", {"broadcaster_user_id": 676, "subscription_id": "sub-second"}
    )

    await KickResyncLoop(deps.bot).resync_kick()

    state = await data().keiko_subscriptions.subscription_state("kick", "xqcow")
    assert list(followers.kick.web.subscriptions) == [state["subscription_id"]]


@pytest.mark.behavioral
async def test_a_resync_forgets_the_id_of_a_subscription_it_deleted(deps, followers):
    """Broke as: the resync deleted the subscription of a streamer no server follows
    any more and Keiko still named it, so a later unsubscribe asked Kick to delete
    an id it no longer held.

    Guaranteed: a subscription the resync deletes is no longer named in Keiko's
    state, and the broadcaster behind it stays known."""
    deps.bot.config.is_dev = lambda: False
    followers.kick.web.holds("sub-unfollowed", 9000)
    await data().keiko_subscriptions.update_subscription_state(
        "kick", "adinross", {"broadcaster_user_id": 9000, "subscription_id": "sub-unfollowed"}
    )

    await KickResyncLoop(deps.bot).resync_kick()

    assert "sub-unfollowed" not in followers.kick.web.subscriptions
    assert await data().keiko_subscriptions.subscription_state("kick", "adinross") == {
        "broadcaster_user_id": 9000, "subscription_id": None,
    }


@pytest.mark.behavioral
async def test_the_resync_asks_kick_nothing_in_dev(deps, followers):
    """A dev bot shares Kick's app with production: its resync would delete the
    subscriptions production holds."""
    deps.bot.config.is_dev = lambda: True
    followers.kick.web.holds("sub-production", 9000)

    await KickResyncLoop(deps.bot).resync_kick()

    assert followers.kick.web.sent == []
    assert list(followers.kick.web.subscriptions) == ["sub-production"]
