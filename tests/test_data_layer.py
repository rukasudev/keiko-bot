"""The registry the repositories are reached through, and the bridge a thread job waits
on them with.

Guaranteed: `data()` names what is missing when nothing was installed; a thread job
reads through the loop the client lives on, never on a loop of its own once a bot loop
is expected, so a webhook that arrives before the bot's loop starts can never take the
client away from it; and a streamed read is closed once its reader stops.
"""

import asyncio
import threading

import pytest

import app.data as data_layer
from app.data import data, iterate, wait


def test_reading_before_anything_is_installed_names_what_is_missing(monkeypatch):
    monkeypatch.setattr(data_layer, "_INSTALLED", None)

    with pytest.raises(RuntimeError, match="not installed"):
        data()


async def test_a_thread_job_reads_on_the_loop_the_client_lives_on(deps, monkeypatch):
    loops = []

    async def find_settings(guild_id):
        loops.append(asyncio.get_running_loop())
        return {"guild_id": guild_id}

    monkeypatch.setattr(deps.data.guild_settings, "find_settings", find_settings)
    data_layer.bind_loop(asyncio.get_running_loop())

    found = await asyncio.to_thread(
        lambda: wait(data().guild_settings.find_settings("1"))
    )

    assert found == {"guild_id": "1"}
    assert loops == [asyncio.get_running_loop()]


def test_a_read_before_the_bot_loop_starts_waits_for_it(deps, monkeypatch):
    """Broke as: a webhook served before `setup_hook` read on a loop of its own, and the
    client, bound to that loop for good, refused every read the bot made after."""
    data_layer.expect_loop()
    loop = asyncio.new_event_loop()
    started = threading.Thread(target=loop.run_forever, daemon=True)
    answers = []

    def webhook():
        answers.append(wait(data().guild_settings.find_settings("1")))

    reader = threading.Thread(target=webhook)
    reader.start()
    reader.join(0.2)
    assert reader.is_alive(), "the read went ahead with no loop to read on"

    started.start()
    data_layer.bind_loop(loop)
    reader.join(5)
    loop.call_soon_threadsafe(loop.stop)
    started.join(5)
    loop.close()

    assert answers == [None]


def test_a_read_the_bot_loop_never_comes_for_is_refused(monkeypatch):
    monkeypatch.setattr(data_layer.DBConfigs, "MONGO_BOOT_WAIT_SECONDS", 0.01)
    data_layer.expect_loop()

    with pytest.raises(RuntimeError, match="has not started"):
        wait(data().guild_settings.find_settings("1"))


def test_a_stream_a_thread_stops_reading_is_closed(deps):
    closed = []

    async def rows():
        try:
            for number in range(5):
                yield number
        finally:
            closed.append(True)

    stream = iterate(rows())
    assert next(stream) == 0
    stream.close()

    assert closed == [True]
