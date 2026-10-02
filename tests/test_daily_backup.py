"""The daily backup: Keiko's own collections, posted where the daily logs go.

There was no backup at all: the database runs on a tier without one and the VPS
has no cron for it, so a wrong click, a bad write or a manual mistake could not
be undone. Once a day the `Backup` cog zips Keiko's collections as canonical
Extended JSON Lines and posts the file on the logs files channel, beside the
daily log export.

Broke as (first version): the dump copied every database the Mongo user could
list, so a secret in any other database reached Discord, and so did
`audit.errors`, which keeps raw exception text that can quote a URL with an API
key in it. Then (second version) a dump over the upload limit fell back one
database at a time, so a large analytics collection kept every setting of the
`guild` database out of the backup, with a warning nobody had to act on.

Guaranteed: one file with every collection of Keiko's own databases (and of
`configs`, only the collections named), whose documents come back with their
`_id` and their types; nothing from any other database, and no third-party
credentials, error audit or debug logs (already archived daily in the same
channel); a dump over the channel's upload limit is packed one collection at a
time into files under it, settings first and expiring analytics last, and a
collection too large on its own is named in the pass's one summary message and
in a warning; a failing pass is logged as an error and never stops the loop; a
missing channel builds nothing and says so; and the logs tool, which ingests
every `.log` and `.jsonl.gz` of that channel, never takes the backup for a log
file.
"""
import base64
import io
import json
import logging
import os
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId, json_util

from app.cogs.backup import Backup
from app.services import backup
from app.services.utils import ml

pytestmark = pytest.mark.unit

JOINED = datetime(2025, 3, 1, 12, 30)
SECRET = "sk-not-for-discord"
TEN_MB = 10 * 1024 * 1024


class Channel:
    def __init__(self, limit=TEN_MB):
        self.guild = SimpleNamespace(filesize_limit=limit)
        self.sends = []

    async def send(self, content=None, file=None):
        self.sends.append((content, file))


async def run_backup(channel):
    """Drives the cog's task body without starting its loop."""
    bot = SimpleNamespace(
        config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=7),
        get_channel=lambda _id: channel,
    )
    return await Backup.post_daily_backup.coro(SimpleNamespace(bot=bot))


@pytest.fixture
def stored(deps):
    """A small Keiko database: settings, the audit trail, birthdays and configs."""
    client = deps.mongo_client
    moderation_id = ObjectId()
    client.guild.moderations.insert_one(
        {"_id": moderation_id, "guild_id": "1", "block_links": True, "created_at": JOINED}
    )
    client.guild.moderations.insert_one(
        {"_id": ObjectId(), "guild_id": "2", "block_links": False}
    )
    client.guild.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "1", "enabled": True}
    )
    client.events.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "1", "event": "enabled"}
    )
    client.reminders.birthdays.insert_one(
        {"_id": ObjectId(), "guild_id": "1", "user_id": "555", "date": "05-12"}
    )
    client.configs.data.insert_one({"_id": ObjectId(), "status": "online"})
    client.configs.integrations.insert_one(
        {"_id": ObjectId(), "name": "openai", "configs": {"openai_api_key": SECRET}}
    )
    client.guild.logs.insert_one({"_id": ObjectId(), "message": "a debug line"})
    return SimpleNamespace(moderation_id=moderation_id)


def files_in(sent):
    with zipfile.ZipFile(io.BytesIO(sent.fp.read())) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def noise(size):
    """Text that does not compress, so a test controls how large a zip gets."""
    return base64.b64encode(os.urandom(size)).decode("ascii")


async def test_one_file_holds_every_collection(stored):
    channel = Channel()

    await run_backup(channel)

    assert len(channel.sends) == 1, "one message, one file"
    content, sent = channel.sends[0]
    assert sent.filename == f"keiko_backup_{today()}.zip"
    files = files_in(sent)
    assert sorted(files) == [
        "configs/data.jsonl",
        "events/block_links.jsonl",
        "guild/block_links.jsonl",
        "guild/moderations.jsonl",
        "manifest.json",
        "reminders/birthdays.jsonl",
    ]
    assert json.loads(files["manifest.json"])["collections"] == {
        "configs.data": 1,
        "events.block_links": 1,
        "guild.block_links": 1,
        "guild.moderations": 2,
        "reminders.birthdays": 1,
    }
    assert content == ml("messages.admin-logs.daily-backup", "en-us").format(
        date=today(), collections=5, documents=6
    )


async def test_documents_come_back_with_their_ids_and_types(stored):
    channel = Channel()

    await run_backup(channel)

    lines = files_in(channel.sends[0][1])["guild/moderations.jsonl"].splitlines()
    restored = [json_util.loads(line) for line in lines]
    first = next(document for document in restored if document["guild_id"] == "1")
    assert first["_id"] == stored.moderation_id
    assert first["created_at"] == JOINED
    assert first["block_links"] is True


async def test_credentials_and_debug_logs_stay_out(stored):
    channel = Channel()

    await run_backup(channel)

    files = files_in(channel.sends[0][1])
    assert "configs/integrations.jsonl" not in files
    assert "guild/logs.jsonl" not in files
    assert not any(SECRET in text for text in files.values())


async def test_a_secret_outside_keikos_databases_or_in_error_audit_never_leaves(
    stored, deps
):
    deps.mongo_client.scratch.tokens.insert_one({"_id": ObjectId(), "token": SECRET})
    deps.mongo_client.audit.errors.insert_one(
        {
            "_id": ObjectId(),
            "error_message": f"HTTPError for url: https://youtube.example/v3?key={SECRET}",
        }
    )
    deps.mongo_client.audit.reminders.insert_one({"_id": ObjectId(), "value": "x"})
    channel = Channel()

    await run_backup(channel)

    files = files_in(channel.sends[0][1])
    assert "audit/reminders.jsonl" in files, "Keiko's own audit is still copied"
    assert "audit/errors.jsonl" not in files
    assert not [name for name in files if name.startswith("scratch/")]
    assert not any(SECRET in text for text in files.values())


async def test_a_collection_added_to_configs_later_is_not_shipped(stored, deps):
    deps.mongo_client.configs.admin.insert_one({"_id": ObjectId(), "admin_guild_id": "1"})
    deps.mongo_client.configs.webhooks.insert_one({"_id": ObjectId(), "secret": SECRET})
    channel = Channel()

    await run_backup(channel)

    files = files_in(channel.sends[0][1])
    assert {"configs/admin.jsonl", "configs/data.jsonl"} <= set(files)
    assert "configs/webhooks.jsonl" not in files, "a new configs collection was shipped"
    assert not any(SECRET in text for text in files.values())


def test_expiring_collections_are_packed_after_the_settings(stored, deps):
    deps.mongo_client.guild.analytics_events.insert_one({"_id": ObjectId(), "event": "x"})
    deps.mongo_client.guild.blocked_links.insert_one({"_id": ObjectId(), "guild_id": "1"})

    order = [f"{database}.{name}" for database, name in backup.backed_up_collections()]

    assert set(order[-2:]) == {"guild.analytics_events", "guild.blocked_links"}


async def test_a_backup_over_the_limit_is_packed_into_files_under_it(stored, deps):
    """About 3 KB of settings and 3 KB of audit: together over 5 KB, apart under."""
    deps.mongo_client.guild.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "3", "answer": noise(2400)}
    )
    deps.mongo_client.events.block_links.insert_one(
        {"_id": ObjectId(), "guild_id": "3", "event": noise(2400)}
    )
    channel = Channel(limit=5000)

    await run_backup(channel)

    parts = [part for _content, part in channel.sends]
    assert len(parts) >= 2
    assert all(len(part.fp.getvalue()) <= 5000 for part in parts)
    assert [part.filename for part in parts] == [
        f"keiko_backup_{today()}_{index}-of-{len(parts)}.zip"
        for index in range(1, len(parts) + 1)
    ]
    assert channel.sends[0][0], "the first message says what the pass sent"
    assert [content for content, _part in channel.sends[1:]] == [None] * (len(parts) - 1)
    members = set()
    for part in parts:
        members |= {name for name in files_in(part) if name != "manifest.json"}
    assert members == {
        "configs/data.jsonl",
        "events/block_links.jsonl",
        "guild/block_links.jsonl",
        "guild/moderations.jsonl",
        "reminders/birthdays.jsonl",
    }


async def test_the_settings_go_out_when_only_they_fit_under_the_limit(
    stored, deps, caplog
):
    for index in range(40):
        deps.mongo_client.guild.analytics_events.insert_one(
            {"_id": ObjectId(), "guild_id": str(index), "event": noise(1500)}
        )
    channel = Channel(limit=8 * 1024)

    with caplog.at_level(logging.WARNING):
        await run_backup(channel)

    sent = set()
    for _content, part in channel.sends:
        if part is not None:
            sent |= set(files_in(part))
    assert {
        "configs/data.jsonl",
        "events/block_links.jsonl",
        "guild/block_links.jsonl",
        "guild/moderations.jsonl",
        "reminders/birthdays.jsonl",
    } <= sent, "the settings were left out with the analytics"
    assert "guild/analytics_events.jsonl" not in sent
    assert "guild.analytics_events" in channel.sends[0][0], "the summary names it"
    assert "guild.analytics_events" in caplog.text


async def test_a_failing_pass_is_an_error_and_never_takes_down_the_loop(
    monkeypatch, caplog
):
    def explode(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(backup, "build_backup", explode)

    with caplog.at_level(logging.WARNING):
        assert await run_backup(Channel()) is None

    failures = [
        record for record in caplog.records if "Daily backup failed" in record.getMessage()
    ]
    assert failures and failures[0].levelno == logging.ERROR
    assert "mongo is down" in caplog.text


async def test_a_missing_channel_builds_and_sends_nothing_and_says_so(
    monkeypatch, caplog
):
    built = []
    monkeypatch.setattr(backup, "build_backup", lambda *args: built.append(args))

    with caplog.at_level(logging.WARNING):
        await run_backup(None)

    assert built == [], "the database was read for a channel that does not exist"
    assert "Daily backup not posted" in caplog.text, "nobody was told"


def test_the_logs_tool_never_takes_the_backup_for_a_log_file(stored, monkeypatch):
    from tools.keiko.logs import fetch

    now = datetime(2026, 1, 1)
    whole, _skipped = backup.build_backup(now, TEN_MB)
    split, _skipped = backup.build_backup(now, len(whole[0].payload) - 1)
    log_name = "keiko_logs_2026-01-01.jsonl.gz"
    attachments = [
        {"filename": part.filename, "url": f"https://cdn.example.com/{index}"}
        for index, part in enumerate([*whole, *split])
    ]
    attachments.append({"filename": log_name, "url": "https://cdn.example.com/log"})
    message = {"id": "1", "attachments": attachments}
    monkeypatch.setattr(fetch, "iter_messages", lambda channel=None: iter([message]))

    picked = [attachment["filename"] for attachment in fetch.iter_log_attachments("1")]

    assert picked == [log_name]
