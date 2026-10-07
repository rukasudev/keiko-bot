"""The daily log file's line names its day the way the structured export names its own.

The structured export and the backup lines drop the colon after "for" in this wave; the
line the rotating handler posts with the daily file, and the one `/admin logs` answers
with when it finds that file again, still read "Here is my log file for: **date**!".

Guaranteed: both read "Here is my log file for **date**!", and `/admin logs` still finds
the file by its date.
"""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.logger import CustomTimedRotatingFileHandler
from app.services.admin import send_log_file_from_channel_by_date

pytestmark = pytest.mark.unit


async def test_the_daily_file_is_posted_naming_its_day_without_a_colon(tmp_path):
    channel = SimpleNamespace(send=AsyncMock())
    handler = CustomTimedRotatingFileHandler(str(tmp_path / "keiko_log.log"), when="MIDNIGHT")
    handler.bot = SimpleNamespace(
        get_channel=lambda channel_id: channel,
        config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=1),
        loop=asyncio.get_running_loop(),
    )
    handler.emit(logging.makeLogRecord({"msg": "a line of the day"}))

    handler.doRollover()
    await asyncio.sleep(0)
    handler.close()

    line = channel.send.call_args.args[0]
    assert line.startswith(":package: Here is my log file for **")
    assert line.endswith("**!")


async def test_admin_logs_finds_the_file_and_names_its_day_without_a_colon():
    posted = SimpleNamespace(
        content=":package: Here is my log file for **2026-10-01**!",
        attachments=[SimpleNamespace(url="https://cdn.discordapp.com/keiko_log.log")],
    )

    async def history(limit=None):
        yield posted

    channel = SimpleNamespace(history=history)
    interaction = SimpleNamespace(
        client=SimpleNamespace(
            get_channel=lambda channel_id: channel,
            config=SimpleNamespace(ADMIN_LOGS_FILES_CHANNEL_ID=1),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )

    await send_log_file_from_channel_by_date("2026-10-01", interaction)

    assert interaction.followup.send.await_args.args[0] == (
        ":page_facing_up: Here is my log file for **2026-10-01**! "
        "https://cdn.discordapp.com/keiko_log.log"
    )
