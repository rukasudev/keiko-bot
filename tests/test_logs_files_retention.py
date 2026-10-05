"""Keiko's own files on the logs files channel stay only as long as their kind allows.

Broke as: every file Keiko posts on that channel stayed forever. The daily
backup, a sealed copy of the whole database, piled up one copy a day (review
round 2); the daily log exports, and the text log files before them, carry
guild, user and session ids and piled up for years; and the walk that deleted
the old backups paged the whole channel every day (review S6). Lucas set the
rule by kind: backups 30 days, daily logs (the `.jsonl.gz` export and the
`keiko_log.log` text file) 90 days, the monthly events archive for good, and
nothing else is ever deleted.

Shared behaviour: `logs_files.delete_old_files`, run by `Backup.post_daily_backup`
on the channel the daily log export, the text log file and the monthly archive
post to. Consumers that exposed it: the daily backup (roadmap #28) and the daily
log export (Lucas, review round 2 of v1 wave 2).

Broke as (closing review of round 4): nothing pinned the clear mark, so setting it
to now, or to the shortest age, or moving it on a day without a backup, would
have stopped the 90-day deletion with every test green; and the operator report
counted a file someone else had deleted as a failure, repeated one error once per
file, left the failed files out of what was left, and said "not deleted" for a
pass that had deleted files before it stopped.

Guaranteed: each kind is deleted past its age and kept inside it, recognized by
the bot as its author and by the exact names its producer gives (the text log's
from the rotating handler itself); the archive, the files of any other author,
any other name and a message mixing kinds are kept at any age; the old backups go
only on a day a backup went out, the old daily logs every day; a pass attempts at
most `LOGS_FILES_DELETES_PER_PASS` deletes, oldest first, failed ones included,
and says how many are left; once a pass leaves nothing behind, the next ones walk
only the history inside the longest retention, never the whole channel again,
yet a log that was young when the mark was set still goes once old, and a day
without a backup never moves the mark past an old backup; a file already gone
counts as deleted; a delete that fails is one warning per pass, each error once
with its count, its file counted as left and tried again on the next pass; and a
history read or a mark write that fails says the pass stopped, after how many,
and never fails the backup.
"""
import base64
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest
from bson import ObjectId
from pyrage import x25519

from app.cogs.backup import Backup
from app.constants import Commands
from app.logger import LoggerHooks
from app.services import archive, backup, logs_files
from app.services.logs_archive import FILENAME

pytestmark = pytest.mark.unit

BOT_ID = 4242
SOMEONE_ELSE = 777
TEN_MB = 10 * 1024 * 1024
PUBLIC_KEY = str(x25519.Identity.generate().to_public())
PASS_AT = datetime(2026, 10, 6, 1, tzinfo=timezone.utc)


def refused():
    return discord.Forbidden(
        type("Response", (), {"status": 403, "reason": "Forbidden"})(),
        {"code": 50013, "message": "Missing Permissions"},
    )


def gone():
    return discord.NotFound(
        type("Response", (), {"status": 404, "reason": "Not Found"})(),
        {"code": 10008, "message": "Unknown Message"},
    )


def noise(size):
    """Text that does not compress, so a test controls how large a file gets."""
    return base64.b64encode(os.urandom(size)).decode("ascii")


class Posted:
    """A message already on the channel: who posted it, its files, how old it is (from
    `now`, the real clock by default), and how its deletes go."""

    def __init__(
        self, *filenames, days_ago, author_id=BOT_ID, failures=0, gone_already=False, now=None
    ):
        self.author = SimpleNamespace(id=author_id)
        self.attachments = [SimpleNamespace(filename=name) for name in filenames]
        self.created_at = (now or datetime.now(timezone.utc)) - timedelta(days=days_ago)
        self.failures = failures
        self.gone_already = gone_already
        self.attempts = 0
        self.deleted = False

    @property
    def name(self):
        return " + ".join(attachment.filename for attachment in self.attachments)

    async def delete(self):
        self.attempts += 1
        if self.gone_already:
            self.deleted = True
            raise gone()
        if self.failures:
            self.failures -= 1
            raise refused()
        self.deleted = True


class Channel:
    """The logs files channel: what is on it, what the backup sends to it, and what
    each pass read of its history."""

    def __init__(self, *posted, history_fails=False, history_fails_after=None):
        self.guild = SimpleNamespace(filesize_limit=TEN_MB)
        self.posted = list(posted)
        self.history_fails = history_fails
        self.history_fails_after = history_fails_after
        self.sends = []
        self.walks = []

    async def send(self, content=None, file=None):
        self.sends.append((content, file))

    def history(self, limit=100, before=None, after=None, oldest_first=None):
        """What Discord answers: what is still there between `after` and `before`,
        oldest first when asked or when `after` is given, newest first otherwise."""
        if self.history_fails:
            raise refused()
        oldest = oldest_first if oldest_first is not None else after is not None
        walked = []
        self.walks.append(walked)

        async def walk():
            ordered = sorted(
                self.posted, key=lambda posted: posted.created_at, reverse=not oldest
            )
            for message in ordered:
                inside = (before is None or message.created_at < before) and (
                    after is None or message.created_at > after
                )
                if inside and not message.deleted:
                    if len(walked) == self.history_fails_after:
                        raise refused()
                    walked.append(message)
                    yield message

        return walk()


async def run_backup(channel, public_key=PUBLIC_KEY):
    """Drives the cog's task body without starting its loop."""
    bot = SimpleNamespace(
        config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=7, BACKUP_AGE_PUBLIC_KEY=public_key),
        get_channel=lambda _id: channel,
        user=SimpleNamespace(id=BOT_ID),
    )
    return await Backup.post_daily_backup.coro(SimpleNamespace(bot=bot))


def still_there(posted):
    return [message.name for message in posted if not message.deleted]


def deleted(posted):
    return [message.name for message in posted if message.deleted]


def warnings_with(caplog, text):
    return [record for record in caplog.records if text in record.getMessage()]


@pytest.fixture
def stored(deps):
    deps.mongo_client.guild.moderations.insert_one({"_id": ObjectId(), "guild_id": "1"})
    deps.mongo_client.events.block_links.insert_one({"_id": ObjectId(), "event": "enabled"})


@pytest.fixture
def text_log_name(tmp_path, monkeypatch):
    """The name Discord gets for the text log the rotating handler posts."""
    monkeypatch.chdir(tmp_path)
    hooks = SimpleNamespace()
    LoggerHooks.set_timed_rotating_file_handler(hooks)
    posted = discord.File(hooks.file_handler.baseFilename)
    posted.close()
    hooks.file_handler.close()
    return posted.filename


async def test_each_kind_goes_past_its_age_and_stays_inside_it(stored):
    past = [
        Posted("keiko_backup_2026-09-01.zip.age", days_ago=31),
        Posted("keiko_backup_2026-08-20_1-of-2.zip.age", days_ago=45),
        Posted("keiko_backup_2026-08-20_2-of-2.zip.age", days_ago=45),
        Posted("keiko_logs_2026-07-01.jsonl.gz", days_ago=91),
        Posted("keiko_log.log", days_ago=91),
        Posted("keiko_log.log", days_ago=500),
    ]
    inside = [
        Posted("keiko_backup_2026-09-10.zip.age", days_ago=29),
        Posted("keiko_logs_2026-07-10.jsonl.gz", days_ago=89),
        Posted("keiko_log.log", days_ago=89),
        Posted("keiko_events_2025-08.jsonl.gz.age", days_ago=400),
        Posted("keiko_events_2025-09_1-of-2.jsonl.gz.age", days_ago=380),
    ]
    channel = Channel(*past, *inside)

    await run_backup(channel)

    assert channel.sends, "today's backup was posted"
    assert still_there(past) == [], "a file past its kind's age stayed"
    assert deleted(inside) == [], "a file inside its kind's age, or the archive, was deleted"
    assert (Commands.BACKUP_RETENTION_DAYS, Commands.DAILY_LOGS_RETENTION_DAYS) == (30, 90)


@pytest.mark.shared_contract("logs_files")
async def test_every_name_keiko_gives_its_files_is_known_by_its_kind(
    stored, deps, text_log_name
):
    for collection in (deps.mongo_client.guild.moderations, deps.mongo_client.events.block_links):
        collection.insert_one({"_id": ObjectId(), "noise": noise(3000)})
    month = datetime(2025, 9, 1, tzinfo=timezone.utc)
    for day in range(0, 28, 3):
        deps.mongo_client.guild.analytics_events.insert_one(
            {
                "_id": ObjectId(),
                "event": "feature.setup_opened",
                "ts": month + timedelta(days=day),
                "props": {"padding": noise(1500)},
            }
        )
    recipient = x25519.Identity.generate().to_public()
    whole, _skipped = backup.build_backup(month, TEN_MB, recipient)
    split, _skipped = backup.build_backup(month, len(whole[0].payload) - 1, recipient)
    archived = [
        *archive.build_month_files(month, TEN_MB, recipient),
        *archive.build_month_files(month, 5000, recipient),
    ]
    backups = [Posted(part.filename, days_ago=400) for part in [*whole, *split]]
    logs = [
        Posted(FILENAME.format(date="2025-09-01"), days_ago=400),
        Posted(text_log_name, days_ago=400),
    ]
    events = [Posted(part.filename, days_ago=400) for part in archived]
    channel = Channel(*backups, *logs, *events)

    await run_backup(channel)

    assert len(split) == 2 and len(archived) >= 3
    assert still_there(backups) == [], "a name the backup gives was not known"
    assert still_there(logs) == [], "a name the daily logs have was not known"
    assert deleted(events) == [], "the monthly events archive was deleted"


async def test_other_authors_and_other_names_stay_at_any_age(stored):
    kept = [
        Posted("keiko_backup_2026-08-01.zip.age", days_ago=400, author_id=SOMEONE_ELSE),
        Posted("keiko_logs_2025-08-01.jsonl.gz", days_ago=400, author_id=SOMEONE_ELSE),
        Posted("keiko_log.log", days_ago=400, author_id=SOMEONE_ELSE),
        Posted("keiko_logs_2025-08-01.jsonl.gz", "notes.txt", days_ago=400),
        Posted("keiko_backup_2025-08-02.zip.age", "keiko_logs_2025-08-02.jsonl.gz", days_ago=400),
        Posted("keiko_logs_2025-08-03.jsonl.gzip", days_ago=400),
        Posted("keiko_logs_2025-08-04.jsonl.gz.bak", days_ago=400),
        Posted("old_keiko_log.log", days_ago=400),
        Posted("keiko_log.log.1", days_ago=400),
        Posted("keiko_2025-08-05.log", days_ago=400),
        Posted("keiko_backup_latest.zip.age", days_ago=400),
        Posted("keiko_backup_2025-08-06.zip", days_ago=400),
        Posted(days_ago=400),
    ]
    channel = Channel(*kept)

    await run_backup(channel)

    assert deleted(kept) == []


@pytest.mark.parametrize("failure", ["no key", "nothing fit", "post refused"])
async def test_old_backups_go_only_on_a_day_a_backup_went_out_old_logs_every_day(
    stored, monkeypatch, failure
):
    old_backup = Posted("keiko_backup_2026-08-01.zip.age", days_ago=40)
    old_logs = [
        Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=120),
        Posted("keiko_log.log", days_ago=120),
    ]
    channel = Channel(old_backup, *old_logs)
    public_key = None if failure == "no key" else PUBLIC_KEY
    if failure == "nothing fit":
        monkeypatch.setattr(backup, "build_backup", lambda *args: ([], ["guild.moderations"]))
    if failure == "post refused":

        async def refuse(content=None, file=None):
            raise refused()

        channel.send = refuse

    await run_backup(channel, public_key=public_key)

    assert not old_backup.deleted, "the last copies were deleted on a day nothing was backed up"
    assert still_there(old_logs) == [], "the old daily logs waited for a backup"


async def test_a_pass_deletes_at_most_its_cap_oldest_first_and_says_how_many_are_left(
    stored, monkeypatch, caplog
):
    monkeypatch.setattr(Commands, "LOGS_FILES_DELETES_PER_PASS", 3, raising=False)
    old = [
        Posted(f"keiko_backup_2026-06-0{day}.zip.age", days_ago=110 - day)
        for day in range(1, 8)
    ]
    channel = Channel(*old)

    with caplog.at_level(logging.INFO):
        await run_backup(channel)

    assert [message.deleted for message in old] == [True] * 3 + [False] * 4, (
        "a pass deleted more than its cap, or not the oldest first"
    )
    assert "4 left" in caplog.text

    await run_backup(channel)
    await run_backup(channel)

    assert still_there(old) == [], "what a capped pass left was never deleted"


async def test_once_a_pass_leaves_nothing_the_next_walks_only_inside_the_longest_retention(
    stored,
):
    kept_forever = [
        Posted(f"keiko_events_2025-{month:02d}.jsonl.gz.age", days_ago=400 - 30 * month)
        for month in range(1, 13)
    ]
    someone_else = [Posted("notes.txt", days_ago=600, author_id=SOMEONE_ELSE)]
    old_backup = Posted("keiko_backup_2026-08-01.zip.age", days_ago=40)
    channel = Channel(*kept_forever, *someone_else, old_backup)

    await run_backup(channel)
    await run_backup(channel)

    assert old_backup.deleted
    edge = datetime.now(timezone.utc) - timedelta(days=91)
    walked_again = [message.name for message in channel.walks[-1] if message.created_at < edge]
    assert walked_again == [], "the next pass paged the whole channel again"


async def test_a_log_young_when_the_clear_mark_was_set_still_goes_once_old(deps):
    young = Posted("keiko_log.log", days_ago=40, now=PASS_AT)
    channel = Channel(young)

    await logs_files.delete_old_files(channel, BOT_ID, PASS_AT, with_backups=True)
    await logs_files.delete_old_files(
        channel, BOT_ID, PASS_AT + timedelta(days=60), with_backups=True
    )

    assert young.deleted, "the clear mark hid a log that was not old yet when it was set"


async def test_a_day_without_a_backup_never_moves_the_mark_past_old_backups(deps):
    old_backup = Posted("keiko_backup_2026-06-01.zip.age", days_ago=100, now=PASS_AT)
    channel = Channel(old_backup)

    await logs_files.delete_old_files(channel, BOT_ID, PASS_AT, with_backups=False)
    await logs_files.delete_old_files(
        channel, BOT_ID, PASS_AT + timedelta(days=1), with_backups=True
    )

    assert old_backup.deleted, "a pass without backups moved the mark past an old backup"


async def test_when_every_delete_fails_the_attempts_stay_within_the_cap(
    stored, monkeypatch
):
    monkeypatch.setattr(Commands, "LOGS_FILES_DELETES_PER_PASS", 3)
    refusing = [
        Posted(f"keiko_logs_2026-05-0{day}.jsonl.gz", days_ago=130 - day, failures=99)
        for day in range(1, 8)
    ]
    channel = Channel(*refusing)

    await run_backup(channel)

    assert sum(message.attempts for message in refusing) == 3, (
        "failed deletes did not count against the cap"
    )


async def test_failures_are_grouped_by_error_and_counted_as_left(
    stored, monkeypatch, caplog
):
    monkeypatch.setattr(Commands, "LOGS_FILES_DELETES_PER_PASS", 3)
    refusing = [
        Posted(f"keiko_logs_2026-05-0{day}.jsonl.gz", days_ago=130 - day, failures=99)
        for day in range(1, 8)
    ]
    channel = Channel(*refusing)

    with caplog.at_level(logging.WARNING):
        await run_backup(channel)

    [warning] = warnings_with(caplog, "could not be deleted")
    report = warning.getMessage()
    assert report.count("Missing Permissions") == 1, "one error was repeated for each file"
    assert "Missing Permissions (3)" in report
    assert "7 left" in report, "the files whose delete failed were not counted as left"


async def test_a_file_already_gone_counts_as_deleted(stored, caplog):
    gone_already = Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=120, gone_already=True)
    kept_forever = Posted("keiko_events_2025-01.jsonl.gz.age", days_ago=400)
    channel = Channel(gone_already, kept_forever)

    with caplog.at_level(logging.INFO):
        await run_backup(channel)
        await run_backup(channel)

    assert "could not be deleted" not in caplog.text, "a file already gone was a failure"
    assert "left for the next passes" not in caplog.text
    assert kept_forever not in channel.walks[-1], "a file already gone kept the mark back"


async def test_a_history_that_breaks_after_some_deletes_says_the_pass_stopped(
    stored, caplog
):
    old = [
        Posted(f"keiko_logs_2026-05-0{day}.jsonl.gz", days_ago=130 - day)
        for day in range(1, 5)
    ]
    channel = Channel(*old, history_fails_after=2)

    with caplog.at_level(logging.WARNING):
        await run_backup(channel)

    assert channel.sends, "the backup went out"
    [warning] = warnings_with(caplog, "logs files channel")
    assert warning.levelno == logging.WARNING
    assert "stopped after 2 deleted" in warning.getMessage()
    assert "Daily backup failed" not in caplog.text


async def test_a_mark_that_cannot_be_written_says_the_pass_stopped(
    stored, monkeypatch, caplog
):
    def refuse(*_args):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr("app.data.logs_files.update_retention", refuse)
    old = Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=120)
    channel = Channel(old)

    with caplog.at_level(logging.WARNING):
        await run_backup(channel)

    assert old.deleted
    [warning] = warnings_with(caplog, "logs files channel")
    assert "stopped after 1 deleted" in warning.getMessage()
    assert "ConnectionError: mongo is down" in warning.getMessage()


async def test_a_delete_that_fails_only_warns_and_is_tried_again(stored, caplog):
    stuck = Posted("keiko_logs_2026-06-01.jsonl.gz", days_ago=121, failures=1)
    old = Posted("keiko_backup_2026-08-02.zip.age", days_ago=40)
    channel = Channel(stuck, old)

    with caplog.at_level(logging.WARNING):
        await run_backup(channel)

    assert channel.sends, "the backup went out"
    assert old.deleted and not stuck.deleted, "one failed delete stopped the others"
    warnings = [
        record for record in caplog.records if "could not be deleted" in record.getMessage()
    ]
    assert warnings, "a failed delete was not reported"
    assert all(record.levelno == logging.WARNING for record in warnings)
    assert "Daily backup failed" not in caplog.text

    await run_backup(channel)

    assert stuck.deleted, "the next pass skipped what a failed delete left"


async def test_a_history_that_cannot_be_read_only_warns(stored, caplog):
    channel = Channel(history_fails=True)

    with caplog.at_level(logging.WARNING):
        await run_backup(channel)

    assert channel.sends, "the backup went out"
    [warning] = warnings_with(caplog, "logs files channel")
    assert warning.levelno == logging.WARNING
    assert "stopped after 0 deleted" in warning.getMessage()
    assert "Daily backup failed" not in caplog.text
