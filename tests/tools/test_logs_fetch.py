"""Reading the archive off Discord: pagination, rate limits, and refusals.

This runs against production credentials on a channel holding years of history,
so the two ways it can go wrong both cost something real: stopping early leaves
a silent hole in the index, and ignoring a 429 gets the token rate limited.
"""
import io
import json
import os
import sys
import urllib.error

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.keiko.logs import cli, fetch  # noqa: E402

pytestmark = pytest.mark.unit


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN_PROD", "test-token")
    monkeypatch.setenv("ADMIN_LOGS_FILES_CHANNEL_ID", "12345")


def message(index, filename="keiko_log.log"):
    return {
        "id": str(index),
        "attachments": [
            {"filename": filename, "url": f"https://cdn/{index}", "size": 10}
        ],
    }


def fake_urlopen(pages):
    """Serves prepared pages in order and records the URLs requested."""
    calls = []

    def opener(request, timeout=None):
        calls.append(request.full_url)
        return FakeResponse(json.dumps(pages.pop(0)).encode())

    opener.calls = calls
    return opener


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

def test_it_walks_every_page_until_the_channel_runs_out(monkeypatch):
    first = [message(index) for index in range(fetch.PAGE)]
    second = [message(1000), message(1001)]
    opener = fake_urlopen([first, second])
    monkeypatch.setattr(urllib.request, "urlopen", opener)

    collected = list(fetch.iter_messages())

    assert len(collected) == fetch.PAGE + 2
    assert "before=" not in opener.calls[0]
    assert f"before={first[-1]['id']}" in opener.calls[1]


def test_a_short_page_ends_the_walk_without_another_request(monkeypatch):
    opener = fake_urlopen([[message(1)]])
    monkeypatch.setattr(urllib.request, "urlopen", opener)

    assert len(list(fetch.iter_messages())) == 1
    assert len(opener.calls) == 1


def test_only_log_shaped_attachments_are_offered(monkeypatch):
    page = [
        message(1, "keiko_log_2023_01_01.log"),
        message(2, "keiko_logs_2026-08-20.jsonl.gz"),
        message(3, "screenshot.png"),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen([page]))

    names = [item["filename"] for item in fetch.iter_log_attachments()]

    assert names == ["keiko_log_2023_01_01.log", "keiko_logs_2026-08-20.jsonl.gz"]


# --------------------------------------------------------------------------
# Rate limits and refusals
# --------------------------------------------------------------------------

def test_a_rate_limit_is_waited_out_rather_than_hammered(monkeypatch):
    slept = []
    monkeypatch.setattr(fetch.time, "sleep", slept.append)

    attempts = {"count": 0}

    def opener(request, timeout=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "Too Many Requests", {"Retry-After": "3"}, None
            )
        return FakeResponse(json.dumps([]).encode())

    monkeypatch.setattr(urllib.request, "urlopen", opener)

    list(fetch.iter_messages())

    assert attempts["count"] == 2
    assert slept and slept[0] >= 3


def test_a_refused_token_says_what_to_check(monkeypatch):
    def opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", opener)

    with pytest.raises(fetch.FetchError) as error:
        list(fetch.iter_messages())

    assert "admin guild" in str(error.value)


def test_a_missing_token_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN_PROD", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    with pytest.raises(fetch.FetchError) as error:
        fetch.token()

    assert "DISCORD_BOT_TOKEN_PROD" in str(error.value)


# --------------------------------------------------------------------------
# CLI argument handling
# --------------------------------------------------------------------------

def test_relative_windows_become_absolute_timestamps():
    assert cli.parse_since(None) is None
    assert cli.parse_since("2026-08-01") == "2026-08-01"
    assert cli.parse_since("24h") < cli.parse_since("1h")
    assert cli.parse_since("7d") < cli.parse_since("24h")
