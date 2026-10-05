"""The daily backup reads, zips and encrypts the database without holding the loop.

The backup reads every collection Keiko keeps, once a day, from a loop task, and
zips and encrypts it: all of it synchronous work that would stop the gateway,
every interaction and every listener for as long as it took.

Broke as: the first version of this test sat in `test_event_loop_is_never_blocked.py`
and used `AsyncMock`, which that module no longer imports since the runtime PR,
so it would fail with a NameError rather than on the loop. It lives here, with
its own imports.

Shared behaviour: the `asyncio.to_thread` seam of `Backup.post_daily_backup`.
Consumer that exposed it: the daily backup (roadmap #28). Guaranteed: the loop
keeps ticking while the backup is built, and the one message still goes out;
and it keeps ticking while the pass reads and moves how far back the logs files
channel is known clear of old files (`logs_files.delete_old_files`).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pyrage import x25519

from app.cogs.backup import Backup
from app.services import backup
from tests.behavioral.regressions.test_event_loop_is_never_blocked import (
    MIN_TICKS,
    blocking,
    ticks_while,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("event_loop"),
]


async def test_the_daily_backup_never_freezes_the_bot(deps, monkeypatch):
    deps.mongo_client.guild.moderations.insert_one({"guild_id": "1"})
    monkeypatch.setattr("app.data.backup.iter_documents", blocking([{"guild_id": "1"}]))
    async def nothing_older():
        return
        yield

    channel = SimpleNamespace(
        guild=SimpleNamespace(filesize_limit=10 * 1024 * 1024),
        send=AsyncMock(),
        history=lambda **kwargs: nothing_older(),
    )
    bot = SimpleNamespace(
        config=SimpleNamespace(
            ADMIN_LOGS_FILES_CHANNEL_ID=7,
            BACKUP_AGE_PUBLIC_KEY=str(x25519.Identity.generate().to_public()),
        ),
        get_channel=lambda _id: channel,
        user=SimpleNamespace(id=4242),
    )

    ticks = await ticks_while(Backup.post_daily_backup.coro(SimpleNamespace(bot=bot)))

    assert ticks >= MIN_TICKS
    channel.send.assert_awaited_once()


@pytest.mark.parametrize("call", ["find_retention", "update_retention"])
async def test_clearing_the_old_files_never_freezes_the_bot(monkeypatch, call):
    monkeypatch.setattr(f"app.data.logs_files.{call}", blocking(None))
    posted = backup.BackupFile("keiko_backup_2026-10-05.zip.age", b"sealed", {"guild.x": 1})
    monkeypatch.setattr(backup, "build_backup", lambda *args: ([posted], []))
    walks = []

    def history(**kwargs):
        async def nothing_older():
            walks.append(kwargs)
            return
            yield

        return nothing_older()

    channel = SimpleNamespace(
        guild=SimpleNamespace(filesize_limit=10 * 1024 * 1024),
        send=AsyncMock(),
        history=history,
    )
    bot = SimpleNamespace(
        config=SimpleNamespace(
            ADMIN_LOGS_FILES_CHANNEL_ID=7,
            BACKUP_AGE_PUBLIC_KEY=str(x25519.Identity.generate().to_public()),
        ),
        get_channel=lambda _id: channel,
        user=SimpleNamespace(id=4242),
    )

    ticks = await ticks_while(Backup.post_daily_backup.coro(SimpleNamespace(bot=bot)))

    assert ticks >= MIN_TICKS
    assert len(walks) == 1, "the old files were not looked for"
