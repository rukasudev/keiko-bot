import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlsplit
from xml.etree import ElementTree

import requests
from requests import RequestException

from app import logger
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.integrations import http_client

VIDEO_FEED_TOPIC = "https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}"
DATA_API_URL = "https://www.googleapis.com/youtube/v3/{resource}"
HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
HUB_MODES = ("subscribe", "unsubscribe")
CALLBACK_TOKEN_PARAMETER = "token"
CHANNEL_ID_SHAPE = re.compile(r"UC[A-Za-z0-9_-]{22}")
ATOM_NAMESPACE = "{http://www.w3.org/2005/Atom}"
YOUTUBE_NAMESPACE = "{http://www.youtube.com/xml/schemas/2015}"
HUB_SIGNATURE_METHODS = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


class YoutubeAPIError(RequestException):
    """YouTube's Data API answered with an error, or did not answer at all."""


@dataclass(frozen=True)
class VideoNotice:
    """A new or updated video, as the hub's Atom feed announces it."""

    video_id: str
    channel_id: str
    published: str
    updated: str


def parse_video_notice(body: bytes) -> Optional[VideoNotice]:
    """The video a hub notice is about, or None when it names none."""
    try:
        entry = ElementTree.fromstring(body).find(f"{ATOM_NAMESPACE}entry")
    except ElementTree.ParseError:
        return None
    if entry is None:
        return None

    fields = [
        (entry.findtext(f"{YOUTUBE_NAMESPACE}videoId") or "").strip(),
        (entry.findtext(f"{YOUTUBE_NAMESPACE}channelId") or "").strip(),
        (entry.findtext(f"{ATOM_NAMESPACE}published") or "").strip(),
        (entry.findtext(f"{ATOM_NAMESPACE}updated") or "").strip(),
    ]
    return VideoNotice(*fields) if all(fields) else None


def channel_of_topic(topic: Optional[str]) -> str:
    """The channel a YouTube feed topic names, or "" for any other topic."""
    try:
        query = urlsplit(str(topic or "")).query
    except ValueError:
        return ""

    channel_id = (parse_qs(query).get("channel_id") or [""])[0]

    if not CHANNEL_ID_SHAPE.fullmatch(channel_id):
        return ""
    if topic != VIDEO_FEED_TOPIC.format(channel_id=channel_id):
        return ""
    return channel_id


def _error_reason(answer: Any) -> str:
    try:
        return str(answer["error"]["errors"][0]["reason"])
    except (KeyError, IndexError, TypeError):
        return "no reason given"


class YoutubeClient:
    def __init__(self, bot):
        from app import DiscordBot

        self.bot: DiscordBot = bot
        self.webhook_url = f"{self.bot.config.WEBHOOK_URL}/youtube"
        self.youtube_api_key = self.bot.config.YOUTUBE_API_KEY

    def get_channel_id_from_username(self, username: str) -> Optional[str]:
        """The id of the channel behind a handle, or None when YouTube has none by that name."""
        items = self._read_data_api("channels", {"part": "id", "forHandle": username}).get("items")
        return items[0].get("id") if items else None

    def get_channel_info(self, channel_id: str) -> Optional[dict]:
        """The channel's snippet, or None when YouTube does not show the channel."""
        items = self._read_data_api("channels", {"part": "snippet", "id": channel_id}).get("items")
        return items[0].get("snippet") if items else None

    def get_video_info(self, video_id: str) -> Optional[dict]:
        """The video, with its `id` and `snippet`, or None when YouTube does not show it."""
        items = self._read_data_api("videos", {"part": "snippet", "id": video_id}).get("items")
        return items[0] if items else None

    def _read_data_api(self, resource: str, params: Dict[str, str]) -> Dict[str, Any]:
        try:
            response = http_client.get(
                "youtube",
                DATA_API_URL.format(resource=resource),
                params=params,
                headers={"X-Goog-Api-Key": self.youtube_api_key},
                timeout=Commands.YOUTUBE_TIMEOUT_SECONDS,
            )
            answer = response.json()
        except (RequestException, ValueError) as error:
            failure = type(error).__name__
        else:
            failure = None

        if failure is not None:
            raise YoutubeAPIError(f"YouTube's {resource} did not answer: {failure}")
        if not isinstance(answer, dict) or "error" in answer or not 200 <= response.status_code < 300:
            raise YoutubeAPIError(
                f"YouTube's {resource} answered {response.status_code} ({_error_reason(answer)})"
            )
        return answer

    def verify_hub_signature(self, body: bytes, signature: str) -> bool:
        """Whether the hub signed this body with the secret Keiko subscribes with."""
        secret = self.bot.config.YOUTUBE_HUB_SECRET
        method, _, digest = str(signature or "").partition("=")
        algorithm = HUB_SIGNATURE_METHODS.get(method.strip().lower())
        if not secret or algorithm is None:
            return False

        expected = hmac.new(str(secret).encode("utf-8"), body, algorithm).hexdigest()
        return hmac.compare_digest(
            expected.encode("utf-8"), digest.strip().lower().encode("utf-8")
        )

    def callback_token_matches(self, channel_id: str, token: Optional[str]) -> bool:
        """Whether a request came on the callback Keiko gives the hub for this channel."""
        expected = self._callback_token(channel_id)
        if expected is None:
            return False
        return hmac.compare_digest(expected.encode("utf-8"), str(token or "").encode("utf-8"))

    def _callback_token(self, channel_id: str) -> Optional[str]:
        secret = self.bot.config.YOUTUBE_HUB_SECRET
        if not secret or not channel_id:
            return None
        digest = hmac.new(
            str(secret).encode("utf-8"),
            f"youtube-callback:{channel_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest[: Commands.YOUTUBE_CALLBACK_TOKEN_LENGTH]

    def _callback(self, channel_id: str) -> str:
        token = self._callback_token(channel_id)
        return f"{self.webhook_url}?{urlencode({CALLBACK_TOKEN_PARAMETER: token})}"

    def _post_to_hub(self, body: Dict[str, str]) -> requests.Response:
        return http_client.post(
            "youtube", HUB_URL, data=body, timeout=Commands.YOUTUBE_TIMEOUT_SECONDS
        )

    def subscribe_to_new_video_event(self, channel_id: str) -> Optional[requests.Response]:
        secret = self.bot.config.YOUTUBE_HUB_SECRET
        if not secret:
            logger.warn(
                f"not subscribed — channel {channel_id}: no hub secret is configured",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return None

        response = self._post_to_hub({
            "hub.callback": self._callback(channel_id),
            "hub.mode": "subscribe",
            "hub.topic": VIDEO_FEED_TOPIC.format(channel_id=channel_id),
            "hub.verify": "async",
            "hub.secret": secret,
        })
        logger.info(f"subscribed — channel {channel_id} ({response.status_code})", log_type=logconstants.COMMAND_INFO_TYPE)
        return response

    def unsubscribe_from_new_video_event(self, channel_id: str) -> Optional[requests.Response]:
        if not self.bot.config.YOUTUBE_HUB_SECRET:
            logger.warn(
                f"not unsubscribed — channel {channel_id}: no hub secret is configured",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return None

        response = self._post_to_hub({
            "hub.callback": self._callback(channel_id),
            "hub.mode": "unsubscribe",
            "hub.topic": VIDEO_FEED_TOPIC.format(channel_id=channel_id),
            "hub.verify": "async",
        })
        logger.info(f"unsubscribed — channel {channel_id} ({response.status_code})", log_type=logconstants.COMMAND_INFO_TYPE)
        return response

