"""YouTube as the webhook and the Data API see it in a test: Google answered from
memory behind the real client and the real API app, and the hub's signed notices."""
import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

from app import integrations
from app.integrations.http import create_api
from app.integrations.youtube.client import YoutubeClient

URL = "/v1/webhooks/youtube"
WEBHOOK_URL = "https://keiko.test/v1/webhooks"
SECRET = "hub-secret-for-tests"
API_KEY = "AIza-keiko-test-api-key-0042"
PEWDIEPIE = "UC-lHJZR3Gqxm24_Vd_AJ5Yw"
MRBEAST = "UCX6OQ3DkcsbYNE6H8uQQuVA"
TOPIC = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={}"


class FakeGoogle:
    """YouTube's Data API and its WebSub hub, answered from memory."""

    def __init__(self):
        self.channels = {
            PEWDIEPIE: {"customUrl": "@pewdiepie", "title": "PewDiePie"},
            MRBEAST: {"customUrl": "@mrbeast", "title": "MrBeast"},
        }
        self.videos = {}
        self.lives = {}
        self.reads = []
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
        self.reads.append(endpoint)
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
            parts = query.get("part", "").split(",")
            items = [
                self.video(video_id, parts)
                for video_id in query.get("id", "").split(",")
                if self.videos.get(video_id)
            ]
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

    def video(self, video_id, parts):
        """One video as `videos.list` describes it, with the parts asked for."""
        broadcast, details = self.lives.get(video_id, ("none", None))
        snippet = {
            "title": "A new video",
            "channelId": self.videos[video_id],
            "description": "Hi",
            "liveBroadcastContent": broadcast,
        }
        item = {"id": video_id, "snippet": snippet}
        if details and "liveStreamingDetails" in parts:
            item["liveStreamingDetails"] = details
        return item

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


def on_fake_google(deps, monkeypatch):
    """The real API app and YouTube client of the bot, with Google answered from
    memory; needs the running loop the announcements are handed to."""
    google = FakeGoogle()
    monkeypatch.setattr(integrations, "requests", google)
    deps.bot.config.WEBHOOK_URL = WEBHOOK_URL
    deps.bot.config.YOUTUBE_API_KEY = API_KEY
    deps.bot.config.YOUTUBE_HUB_SECRET = SECRET
    deps.bot.config.is_dev = lambda: False
    deps.bot.youtube = YoutubeClient(deps.bot)
    deps.bot.loop = asyncio.get_running_loop()
    return SimpleNamespace(
        client=create_api("dev").test_client(),
        google=google,
        youtube=deps.bot.youtube,
        config=deps.bot.config,
    )


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


async def settle():
    """Let the announcement the request handed to the bot's loop reach the channels."""
    for _ in range(100):
        await asyncio.sleep(0.01)
        if all(task is asyncio.current_task() or task.done() for task in asyncio.all_tasks()):
            return
