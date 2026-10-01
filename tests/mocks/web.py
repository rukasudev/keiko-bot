"""The web as Keiko's picture downloads see it, answered without a network.

Discord's CDN answers with a picture and the headers it really sends; every
other host refuses, the way i.sstatic.net answers 403 to every client. Bodies
are streams that count what was read from them, so a test can tell a download
refused before reading from one refused after reading everything.

Uso:
    web = FakeWeb()
    monkeypatch.setattr(requests, "get", web.get)
    web.serve(url, png((2048, 2048)))
"""

from io import BytesIO
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from PIL import Image

DISCORD_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}


def png(size: Tuple[int, int] = (64, 64), color: str = "orange") -> bytes:
    """A small, valid PNG."""
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class CountedBody(BytesIO):
    """A response body that adds every byte read from it to `read[url]`."""

    def __init__(self, content: bytes, url: str, read: Dict[str, int]):
        super().__init__(content)
        self.url = url
        self.counted = read

    def read(self, size: Optional[int] = -1) -> bytes:
        chunk = super().read(size)
        self.counted[self.url] = self.counted.get(self.url, 0) + len(chunk)
        return chunk


def respond(
    url: str,
    content: bytes,
    status: int = 200,
    content_type: str = "image/png",
    declared_length: Optional[int] = None,
    chunked: bool = False,
    read: Optional[Dict[str, int]] = None,
) -> requests.Response:
    """A `requests.Response` as a server sends it: headers first, the body as a stream.

    `declared_length` overrides the Content-Length; `chunked` sends none at all.
    """
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.headers["Content-Type"] = content_type
    if not chunked:
        length = len(content) if declared_length is None else declared_length
        response.headers["Content-Length"] = str(length)
    response.raw = CountedBody(content, url, read if read is not None else {})
    return response


class FakeWeb:
    """`requests.get` for the tests: Discord's CDN answers, other hosts refuse."""

    def __init__(self):
        self.requested: List[str] = []
        self.answers: Dict[str, int] = {}
        self.files: Dict[str, dict] = {}
        self.read: Dict[str, int] = {}

    def serve(self, url: str, content: bytes, **headers) -> None:
        """Answer `url` with `content`, and with any header option `respond` takes."""
        self.files[url] = {"content": content, **headers}

    def get(self, url, *args, **kwargs) -> requests.Response:
        self.requested.append(url)
        answers = url in self.files or urlparse(url).hostname in DISCORD_HOSTS
        status = self.answers.get(url, 200 if answers else 403)
        if status != 200:
            return respond(url, b"<html>no</html>", status, "text/html", read=self.read)

        served = self.files.get(url, {"content": png()})
        return respond(url, status=status, read=self.read, **served)
